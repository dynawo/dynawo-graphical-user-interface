#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import os
import shutil
import xml.etree.ElementTree as ET

_NS = "http://www.rte-france.com/dynawo"


def write_par_values(
    par_path: str, set_id: str, updates: dict[str, str], types: dict[str, str] | None = None,
) -> None:
    """Overwrite <par value=...> entries in set_id within the par file at par_path.

    Lookup order for each updated parameter:
    1. A direct <par> child of the target <set> — overwritten in place.
    2. Otherwise, if the set references a <macroParSet>, a <par> with that
       name inside the matching <macroParameterSet> block — overwritten in
       place too. This is a deliberate shared edit: it changes the value for
       every other <set> that references the same macro, not just this one
       (backend/par_parser.py's `shared`/macro_usage() expose this to the
       caller so the UI can warn about it up front).
    3. Otherwise (parameter not found anywhere — a genuinely new name), a
       new direct <par> is added to the target set. `types` (name -> Dynawo
       type string, e.g. "DOUBLE") is required to materialize this case
       well-formed; the caller already has it from parse_par()'s resolved
       view. An update with no known type is skipped rather than guessed.

    Sets/macros other than the ones actually touched above are preserved
    untouched.
    """
    ET.register_namespace("", _NS)
    tree = ET.parse(par_path)
    root = tree.getroot()
    types = types or {}

    target_set = next(
        (s for s in root.findall(f"{{{_NS}}}set") if s.get("id") == set_id), None,
    )
    if target_set is None:
        return

    remaining = dict(updates)
    for p in target_set.findall(f"{{{_NS}}}par"):
        name = p.get("name", "")
        if name in remaining:
            p.set("value", remaining.pop(name))

    if remaining:
        macro_ref = target_set.find(f"{{{_NS}}}macroParSet")
        macro_id = macro_ref.get("id") if macro_ref is not None else None
        if macro_id is not None:
            macro = next(
                (m for m in root.findall(f"{{{_NS}}}macroParameterSet") if m.get("id") == macro_id), None,
            )
            if macro is not None:
                for p in macro.findall(f"{{{_NS}}}par"):
                    name = p.get("name", "")
                    if name in remaining:
                        p.set("value", remaining.pop(name))

    for name, value in remaining.items():
        ptype = types.get(name)
        if ptype is None:
            continue
        new_par = ET.SubElement(target_set, f"{{{_NS}}}par")
        new_par.set("name", name)
        new_par.set("type", ptype)
        new_par.set("value", value)

    ET.indent(tree, space="  ")
    tree.write(par_path, xml_declaration=True, encoding="UTF-8")


def relocate_table_files(par_path: str, run_dir: str, subdir: str = "Tables") -> list[str]:
    """Copy any file a <par type="STRING"> in this .par references — when
    that file lives under run_dir — into a `subdir` folder next to par_path,
    and rewrite those values to the new "<subdir>/<basename>" reference.
    Returns the copied filenames (each prefixed with "<subdir>/").

    DynaFlow generates lookup tables for some models (e.g. HVDC reactive
    power limits) as separate files alongside the .dyd/.par, referenced by
    *absolute path* into the run's own working directory — e.g.
    ".../dynaflow_<id>/powsybl_dynawo_Diagram/HVDC1_CONV1_Diagram.txt". That
    directory is a worker's private tmp-dir (backend/powsybl_config.py) and
    is deleted right after the run. Without this, a captured .par still
    "works" for display, but is unusable for a later, standalone Dynawo run
    (the whole point of capturing it) since the table file it points at is
    already gone. Detection is generic — any STRING value that resolves to
    a real file under run_dir, not a hardcoded "Diagram" pattern — since
    other models could reference table files the same way.

    Grouped under one `subdir` (rather than dumped flat next to the .par)
    since there can be many of these — one pair per HVDC/generator with a
    reactive-power table — and they'd otherwise clutter the session's file
    list. Keyed by basename only (not the full original relative path)
    since DynaFlow already names them uniquely per component
    (HVDC1_CONV1_Diagram.txt, GH1_Diagram.txt, ...).

    par_path is assumed to already be a *copy* (e.g. one
    backend/loadflow_runner.py's debug-capture just wrote into the session's
    working directory) — this mutates that copy in place; the original under
    run_dir is left untouched (it's about to be deleted by the caller
    anyway).
    """
    ET.register_namespace("", _NS)
    tree = ET.parse(par_path)
    root = tree.getroot()
    tables_dir = os.path.join(os.path.dirname(par_path), subdir)
    run_dir_abs = os.path.abspath(run_dir)

    relocated: dict[str, str] = {}  # normalized source path -> new "<subdir>/<basename>" reference
    copied: list[str] = []
    changed = False
    for p in root.iter(f"{{{_NS}}}par"):
        if p.get("type") != "STRING":
            continue
        value = p.get("value", "")
        if not value:
            continue
        candidate = os.path.normpath(value)
        try:
            under_run_dir = os.path.commonpath([candidate, run_dir_abs]) == run_dir_abs
        except ValueError:
            under_run_dir = False
        if not under_run_dir or not os.path.isfile(candidate):
            continue
        if candidate not in relocated:
            os.makedirs(tables_dir, exist_ok=True)
            basename = os.path.basename(candidate)
            shutil.copy2(candidate, os.path.join(tables_dir, basename))
            rel_name = f"{subdir}/{basename}"
            relocated[candidate] = rel_name
            copied.append(rel_name)
        p.set("value", relocated[candidate])
        changed = True

    if changed:
        ET.indent(tree, space="  ")
        tree.write(par_path, xml_declaration=True, encoding="UTF-8")
    return copied
