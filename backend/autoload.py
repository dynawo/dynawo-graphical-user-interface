#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""
Shared pipeline for auto-loading Dynawo case files.

Given a jobs file and a base directory where the original files live, this
module resolves all referenced paths, copies files into the session working
directory (flat layout), and rewrites the jobs + DYD XML so all paths become
bare filenames.  No FastAPI dependency — usable and testable in isolation.

Note: 'dyd' in raw_refs / resolved is always a list[str] / list[Path] because
a jobs file can contain multiple <dynModels dydFile="..."/> elements.
"""

import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = "http://www.rte-france.com/dynawo"

# Single-value roles only — dyd is handled separately (can be multiple)
_JOBS_SINGLE_ATTRS: list[tuple[str, str, str]] = [
    ("solver_par",  f".//{{{_NS}}}solver",    "parFile"),
    ("iidm",        f".//{{{_NS}}}network",   "iidmFile"),
    ("network_par", f".//{{{_NS}}}network",   "parFile"),
    ("crv",         f".//{{{_NS}}}outputs/{{{_NS}}}curves", "inputFile"),
]


# ── Path resolution ────────────────────────────────────────────────────────────

def _abs(raw: str, base_dir: Path) -> Path:
    p = Path(raw)
    return (base_dir / p).resolve() if not p.is_absolute() else p.resolve()


def resolve_paths(raw_refs: dict, base_dir: Path) -> dict:
    """Resolve raw path strings relative to base_dir.

    Returns a dict where single-value roles map to Path | None and
    'dyd' maps to list[Path].
    """
    resolved: dict = {}
    for role, raw in raw_refs.items():
        if role == "dyd":
            resolved["dyd"] = [_abs(r, base_dir) for r in raw]
        elif raw:
            resolved[role] = _abs(raw, base_dir)
        else:
            resolved[role] = None
    return resolved


def collect_dyd_par_refs(dyd_path: Path) -> list[tuple[str, Path]]:
    """Return [(raw_parFile, resolved_abs_path)] for every unique parFile in one DYD."""
    seen: dict[str, Path] = {}
    try:
        root = ET.parse(str(dyd_path)).getroot()
        base = dyd_path.parent
        for bbm in root.findall(f".//{{{_NS}}}blackBoxModel"):
            raw = bbm.get("parFile")
            if not raw or raw in seen:
                continue
            seen[raw] = _abs(raw, base)
    except Exception:
        pass
    return list(seen.items())


# ── File copying ───────────────────────────────────────────────────────────────

def _unique_name(dest_dir: Path, filename: str) -> str:
    """Return a filename that does not already exist in dest_dir."""
    stem, ext = os.path.splitext(filename)
    candidate = filename
    counter = 2
    while (dest_dir / candidate).exists():
        candidate = f"{stem}_{counter}{ext}"
        counter += 1
    return candidate


def copy_files_to_dir(
    resolved: dict,
    dyd_par_pairs: list[tuple[str, Path]],
    dest_dir: Path,
) -> tuple[dict[Path, str], list[str]]:
    """
    Copy all source files into dest_dir (flat layout, conflict renaming).

    Returns:
      filename_map  — {absolute_source_path: final_filename_in_dest_dir}
      missing       — list of role names whose source file was not found
    """
    filename_map: dict[Path, str] = {}
    missing: list[str] = []

    def _copy(src: Path, role: str) -> None:
        if src in filename_map:
            return
        if not src.exists():
            missing.append(role)
            return
        dest_name = _unique_name(dest_dir, src.name)
        shutil.copy2(str(src), str(dest_dir / dest_name))
        filename_map[src] = dest_name

    for role, path in resolved.items():
        if role == "dyd":
            for i, dyd_path in enumerate(path):
                _copy(dyd_path, f"dyd_{i}")
        elif path is not None:
            _copy(path, role)

    for raw, abs_path in dyd_par_pairs:
        _copy(abs_path, f"dyd_par:{raw}")

    return filename_map, missing


# ── XML rewriting ──────────────────────────────────────────────────────────────

def _src_to_dest(raw: str | None, base_dir: Path, filename_map: dict[Path, str]) -> str | None:
    """Resolve a raw path and look it up in filename_map. Return dest filename or None."""
    if not raw:
        return None
    return filename_map.get(_abs(raw, base_dir))


def rewrite_jobs(jobs_path: Path, filename_map: dict[Path, str]) -> None:
    """Overwrite jobs XML replacing every file reference with its dest filename."""
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(str(jobs_path))
    root = tree.getroot()
    base = jobs_path.parent

    for _role, xpath, attr in _JOBS_SINGLE_ATTRS:
        elem = root.find(xpath)
        if elem is None:
            continue
        dest = _src_to_dest(elem.get(attr), base, filename_map)
        if dest:
            elem.set(attr, dest)

    # Multiple dynModels elements — rewrite each one
    for elem in root.findall(f".//{{{_NS}}}dynModels"):
        dest = _src_to_dest(elem.get("dydFile"), base, filename_map)
        if dest:
            elem.set("dydFile", dest)

    ET.indent(tree, space="  ")
    tree.write(str(jobs_path), xml_declaration=True, encoding="UTF-8")


def rewrite_dyd(dyd_path: Path, filename_map: dict[Path, str]) -> None:
    """Overwrite DYD XML replacing every parFile with its dest filename."""
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(str(dyd_path))
    root = tree.getroot()
    base = dyd_path.parent

    for bbm in root.findall(f".//{{{_NS}}}blackBoxModel"):
        dest = _src_to_dest(bbm.get("parFile"), base, filename_map)
        if dest:
            bbm.set("parFile", dest)

    ET.indent(tree, space="  ")
    tree.write(str(dyd_path), xml_declaration=True, encoding="UTF-8")


# ── Top-level pipeline ─────────────────────────────────────────────────────────

def run_pipeline(jobs_path: Path, base_dir: Path, dest_dir: Path) -> dict:
    """
    Full auto-load pipeline: resolve → copy → rewrite jobs + all dyds.

    Returns {'loaded': [...], 'missing': [...], 'warnings': [...]}.
    The jobs file itself must already be in dest_dir before calling this.
    """
    from backend.jobs_parser import read_all_file_refs

    raw_refs = read_all_file_refs(str(jobs_path))
    resolved = resolve_paths(raw_refs, base_dir)

    # Collect PAR refs from every DYD file that exists
    dyd_par_pairs: list[tuple[str, Path]] = []
    for dyd_abs in resolved.get("dyd", []):
        if dyd_abs.exists():
            dyd_par_pairs.extend(collect_dyd_par_refs(dyd_abs))

    filename_map, missing = copy_files_to_dir(resolved, dyd_par_pairs, dest_dir)

    # Rewrite jobs (already in dest_dir)
    jobs_dest = dest_dir / jobs_path.name
    rewrite_jobs(jobs_dest, filename_map)

    # Rewrite every DYD that was copied
    for dyd_src in resolved.get("dyd", []):
        if dyd_src in filename_map:
            rewrite_dyd(dest_dir / filename_map[dyd_src], filename_map)

    warnings: list[str] = []
    for src, dest_name in filename_map.items():
        if dest_name != src.name:
            warnings.append(f"{src.name} renamed to {dest_name} to avoid conflict")

    return {
        "loaded":   list(filename_map.values()),
        "missing":  missing,
        "warnings": warnings,
    }


# ── Fix-paths pipeline (mode 3) ────────────────────────────────────────────────

def fix_paths_in_session(jobs_path: Path, session_dir: Path) -> dict:
    """
    Rewrite jobs (and all DYDs) so every reference becomes a bare filename,
    matching files already present in session_dir by basename.

    Returns {'fixed': [...], 'unresolved': [...]}.
    """
    from backend.jobs_parser import read_all_file_refs

    available = {f.name: f for f in session_dir.iterdir() if f.is_file()}
    raw_refs = read_all_file_refs(str(jobs_path))

    raw_to_dest: dict[str, str] = {}
    unresolved: list[str] = []

    def _register(raw: str | None, role: str) -> None:
        if not raw:
            return
        basename = Path(raw).name
        if basename in available:
            raw_to_dest[raw] = basename
        else:
            unresolved.append(role)

    for role, raw in raw_refs.items():
        if role == "dyd":
            for i, r in enumerate(raw):
                _register(r, f"dyd_{i}")
        else:
            _register(raw, role)

    _rewrite_jobs_by_raw(jobs_path, raw_to_dest)

    for dyd_raw in raw_refs.get("dyd", []):
        dyd_basename = Path(dyd_raw).name
        if dyd_basename in available:
            _rewrite_dyd_by_available(session_dir / dyd_basename, available)

    return {
        "fixed":      list(raw_to_dest.values()),
        "unresolved": unresolved,
    }


def _rewrite_jobs_by_raw(jobs_path: Path, raw_to_dest: dict[str, str]) -> None:
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(str(jobs_path))
    root = tree.getroot()

    for _role, xpath, attr in _JOBS_SINGLE_ATTRS:
        elem = root.find(xpath)
        if elem is None:
            continue
        raw = elem.get(attr)
        if raw and raw in raw_to_dest:
            elem.set(attr, raw_to_dest[raw])

    for elem in root.findall(f".//{{{_NS}}}dynModels"):
        raw = elem.get("dydFile")
        if raw and raw in raw_to_dest:
            elem.set("dydFile", raw_to_dest[raw])

    ET.indent(tree, space="  ")
    tree.write(str(jobs_path), xml_declaration=True, encoding="UTF-8")


def _rewrite_dyd_by_available(dyd_path: Path, available: dict[str, Path]) -> None:
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(str(dyd_path))
    root = tree.getroot()

    for bbm in root.findall(f".//{{{_NS}}}blackBoxModel"):
        raw = bbm.get("parFile")
        if raw:
            basename = Path(raw).name
            if basename in available:
                bbm.set("parFile", basename)

    ET.indent(tree, space="  ")
    tree.write(str(dyd_path), xml_declaration=True, encoding="UTF-8")
