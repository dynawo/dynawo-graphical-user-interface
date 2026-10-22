#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import copy
import glob
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET

# Generation of a dynamic association: pypowsybl's dynamic simulation is run
# against the fake Dynawo of a pypowsybl worker, so that PowSyBl only writes
# the .jobs/.dyd/.par. generate_in_worker runs in the worker; the other
# functions adapt the files in the API process.

_NS = "http://www.rte-france.com/dynawo"

# Prefix of generated files, and their tag in uploaded_files_info.
GENERATED_PREFIX = "dynamic_association"
ORIGIN = "dynamic_association"

# PowSyBl file names -> session file names; other files get the prefix.
_SESSION_NAMES = {
    "powsybl_dynawo.jobs": f"{GENERATED_PREFIX}.jobs",
    "powsybl_dynawo.dyd":  f"{GENERATED_PREFIX}.dyd",
    "models.par":          f"{GENERATED_PREFIX}_models.par",
    "network.par":         f"{GENERATED_PREFIX}_network.par",
    "solvers.par":         f"{GENERATED_PREFIX}_solvers.par",
}

# Dynawo solver libraries → PowSyBl's solver.type.
_SOLVER_TYPES = {"dynawo_SolverIDA": "IDA", "dynawo_SolverSIM": "SIM"}


# ── Worker side ───────────────────────────────────────────────────────────────

def _dynawo_run_dirs(tmp_dir: str) -> set[str]:
    """Run directories under the worker's tmp-dir, version checks excluded."""
    return {p for p in glob.glob(os.path.join(tmp_dir, "dynawo_*"))
            if not os.path.basename(p).startswith("dynawo_version_")}


def _cleanup_dynawo_run_dirs(tmp_dir: str, new_dirs: set[str]) -> None:
    for new_dir in new_dirs:
        shutil.rmtree(new_dir, ignore_errors=True)
    for version_dir in glob.glob(os.path.join(tmp_dir, "dynawo_version_*")):
        shutil.rmtree(version_dir, ignore_errors=True)


def build_model_mapping(assignments: list[dict]):
    """ModelMapping of the assignments, one dataframe per category."""
    import pandas as pd
    import pypowsybl.dynamic as dyn

    mapping = dyn.ModelMapping()
    by_category: dict[str, list[dict]] = {}
    for a in assignments:
        by_category.setdefault(a["category"], []).append(a)
    for category, rows in by_category.items():
        df = pd.DataFrame({
            "static_id":        [r["static_id"] for r in rows],
            "parameter_set_id": [r["parameter_set_id"] for r in rows],
            "model_name":       [r["model"] for r in rows],
        }).set_index("static_id")
        mapping.add_dynamic_model(category, df)
    return mapping


def generate_in_worker(
    iidm_path: str, assignments: list[dict], provider_parameters: dict[str, str],
    start_time: float, stop_time: float, dynawo_version: str,
) -> dict:
    """Worker entry point: returns {files: {name: bytes}, report, error}.

    `error` is set when PowSyBl raises; the FAILURE status of the run is ignored.
    """
    import pypowsybl.dynamic as dyn
    import pypowsybl.report as rep

    from backend.network_loader import load_network_from_path
    from backend.powsybl_config import worker_fake_dynawo_dir, worker_tmp_dir

    tmp_dir = worker_tmp_dir()
    fake_dir = worker_fake_dynawo_dir()
    if not tmp_dir or not fake_dir:
        return {"files": {}, "report": "", "error": "not running in a pypowsybl worker"}
    # Answered by the fake to PowSyBl's version query.
    with open(os.path.join(fake_dir, "version"), "w", encoding="utf-8") as fh:
        fh.write(dynawo_version + "\n")

    try:
        network = load_network_from_path(iidm_path)
    except Exception as exc:
        return {"files": {}, "report": "", "error": f"Failed to load network: {exc}"}

    report = rep.ReportNode()
    before = _dynawo_run_dirs(tmp_dir)
    error = None
    try:
        dyn.Simulation().run(
            network, build_model_mapping(assignments),
            parameters=dyn.Parameters(start_time, stop_time, provider_parameters),
            report_node=report,
        )
    except Exception as exc:
        error = str(exc)
    new_dirs = _dynawo_run_dirs(tmp_dir) - before

    files: dict[str, bytes] = {}
    if error is None:
        for run_dir in new_dirs:
            for path in glob.glob(os.path.join(run_dir, "*.jobs")) + glob.glob(os.path.join(run_dir, "*.dyd")) \
                    + glob.glob(os.path.join(run_dir, "*.par")):
                with open(path, "rb") as fh:
                    files[os.path.basename(path)] = fh.read()
        if "powsybl_dynawo.dyd" not in files:
            error = "PowSyBl did not write the dynamic models file"
    _cleanup_dynawo_run_dirs(tmp_dir, new_dirs)
    return {"files": files, "report": str(report), "error": error}


# ── API side ──────────────────────────────────────────────────────────────────

def read_dynawo_version(exe: str) -> str:
    """First line of `<exe> version`, e.g. "1.7.1 (rev:v1.7-1250b93)". Raises RuntimeError."""
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not run '{exe} version': {exc}") from None
    lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    if out.returncode != 0 or not lines:
        raise RuntimeError(f"'{exe} version' gave no version (exit code {out.returncode})")
    return lines[0]


def solver_type(lib: str) -> str | None:
    """PowSyBl's solver.type for a Dynawo solver library, None if PowSyBl has none."""
    return _SOLVER_TYPES.get(lib)


def session_names(generated: list[str], taken: set[str]) -> dict[str, str]:
    """PowSyBl file name -> session file name, avoiding the names in `taken`."""
    names = {}
    for name in generated:
        target = _SESSION_NAMES.get(name, f"{GENERATED_PREFIX}_{name}")
        stem, ext = os.path.splitext(target)
        n = 2
        while target in taken or target in names.values():
            target = f"{stem}_{n}{ext}"
            n += 1
        names[name] = target
    return names


def _parse(content: bytes) -> ET.ElementTree:
    return ET.ElementTree(ET.fromstring(content))


def _serialise(tree: ET.ElementTree) -> bytes:
    ET.register_namespace("dyn", _NS)
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), xml_declaration=True, encoding="UTF-8")


def _rename_references(root: ET.Element, names: dict[str, str]) -> None:
    for el in root.iter():
        for attr in ("parFile", "dydFile"):
            value = el.get(attr)
            if value in names:
                el.set(attr, names[value])


def adapt_jobs(content: bytes, names: dict[str, str], iidm_name: str, reference_jobs: bytes | None) -> bytes:
    """Generated .jobs for the session: references renamed, network set to the
    session IIDM, outputs (without curves) taken from the reference job.
    """
    tree = _parse(content)
    root = tree.getroot()
    _rename_references(root, names)
    network = root.find(f".//{{{_NS}}}network")
    if network is not None:
        network.set("iidmFile", iidm_name)
    if reference_jobs:
        ref_outputs = ET.fromstring(reference_jobs).find(f".//{{{_NS}}}outputs")
        job = root.find(f"{{{_NS}}}job")
        outputs = job.find(f"{{{_NS}}}outputs") if job is not None else None
        if ref_outputs is not None and outputs is not None:
            new_outputs = copy.deepcopy(ref_outputs)
            for curves in new_outputs.findall(f"{{{_NS}}}curves"):
                new_outputs.remove(curves)
            job[list(job).index(outputs)] = new_outputs
    return _serialise(tree)


def adapt_dyd(content: bytes, names: dict[str, str]) -> bytes:
    tree = _parse(content)
    _rename_references(tree.getroot(), names)
    return _serialise(tree)


def dropped_assignments(assignments: list[dict], dyd: bytes, report: str) -> list[dict]:
    """Assignments missing from the .dyd, with the reason from PowSyBl's report."""
    root = ET.fromstring(dyd)
    written = {bbm.get("staticId") for bbm in root.iter(f"{{{_NS}}}blackBoxModel")}
    lines = [l.strip() for l in report.splitlines() if l.strip()]
    dropped = []
    for a in assignments:
        if a["static_id"] in written:
            continue
        reason = next((l for l in lines if f"'{a['static_id']}'" in l), None) \
            or next((l for l in lines if f"Model {a['model']} not found" in l), None) \
            or "left out by PowSyBl (no reason in its report)"
        dropped.append({**a, "reason": reason})
    return dropped
