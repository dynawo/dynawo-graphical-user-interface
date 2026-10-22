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
import os
import xml.etree.ElementTree as ET

# Parameter sets of a dynamic association. A rule's set comes from:
#   FILE    a set of a session .par file
#   GENERIC the model's set in the generic .par of resources/
#   MANUAL  an empty set, filled by the user
# PowSyBl takes a single models .par, built by assemble_par.

FILE = "file"
GENERIC = "generic"
MANUAL = "manual"
SOURCES = (FILE, GENERIC, MANUAL)

_NS = "http://www.rte-france.com/dynawo"

# Optional: the generic source is offered only when this file exists.
GENERIC_PAR_PATH = os.path.join(
    os.path.dirname(__file__), "..", "resources", "association_generic_models.par"
)


def generic_par() -> bytes | None:
    try:
        with open(GENERIC_PAR_PATH, "rb") as fh:
            return fh.read()
    except OSError:
        return None


# path -> ((mtime_ns, size), set ids): large .par files are parsed once per version.
_set_ids_cache: dict[str, tuple[tuple[int, int], list[str]]] = {}


def file_set_ids(path: str) -> list[str] | None:
    """Set ids of a .par file, cached until the file changes; None if absent."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    version = (st.st_mtime_ns, st.st_size)
    cached = _set_ids_cache.get(path)
    if cached is None or cached[0] != version:
        with open(path, "rb") as fh:
            cached = (version, set_ids(fh.read()))
        _set_ids_cache[path] = cached
    return cached[1]


def set_ids(content: bytes) -> list[str]:
    """Ids of the <set>s of a .par file, in file order; [] if unreadable."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    return [s.get("id", "") for s in root.findall(f"{{{_NS}}}set")]


def spec_key(rule: dict) -> tuple[str, str, str]:
    """Key of a rule's parameter set: (source, file, set id)."""
    source = rule.get("par_source") or FILE
    if source == FILE:
        return FILE, rule.get("par_file", ""), rule.get("parameter_set_id", "")
    if source == GENERIC:
        return GENERIC, "", rule.get("model", "")
    # Manual sets default to the model name.
    return MANUAL, "", rule.get("parameter_set_id") or rule.get("model", "")


def check_rule_parameters(rule: dict, par_sets: dict[str, set[str]], generic_sets: set[str] | None) -> list[str]:
    """Errors in a rule's parameter set, [] if none.

    `par_sets`: set ids of each session .par file; `generic_sets`: those of
    the generic .par, None if it is absent."""
    source = rule.get("par_source") or FILE
    if source not in SOURCES:
        return [f"unknown parameter source '{source}'"]
    _, par_file, set_id = spec_key(rule)
    if source == FILE:
        if not par_file:
            return ["no parameters file chosen"]
        if par_file not in par_sets:
            return [f"parameters file '{par_file}' is not in the session"]
        if not set_id:
            return ["no parameter set chosen"]
        if set_id not in par_sets[par_file]:
            return [f"parameter set '{set_id}' is not in '{par_file}'"]
    elif source == GENERIC:
        if generic_sets is None:
            return ["the generic parameters file is not available yet"]
        if set_id not in generic_sets:
            return [f"the generic parameters file has no set for model '{set_id}'"]
    elif not set_id:
        return ["no parameter set name"]
    return []


def for_powsybl(content: bytes, placeholders: dict[str, list[str]] | None = None) -> bytes:
    """Copy of a .par readable by PowSyBl.

    PowSyBl accepts only <par> and <reference>: macros are expanded into the
    sets, <refPar> and <parTable> are dropped. `placeholders` ({set id: names})
    adds these parameters with the value 1.
    """
    root = ET.fromstring(content)
    macros = {m.get("id"): m for m in root.findall(f"{{{_NS}}}macroParameterSet")}
    for macro in macros.values():
        root.remove(macro)
    kept = (f"{{{_NS}}}par", f"{{{_NS}}}reference")
    for s in root.findall(f"{{{_NS}}}set"):
        macro_ref = s.find(f"{{{_NS}}}macroParSet")
        own = {c.get("name") for c in s if c.tag in kept}
        for child in list(s):
            if child.tag not in kept:
                s.remove(child)
        macro = macros.get(macro_ref.get("id")) if macro_ref is not None else None
        if macro is not None:
            for child in macro:
                if child.tag in kept and child.get("name") not in own:
                    s.append(copy.deepcopy(child))
        for name in (placeholders or {}).get(s.get("id"), []):
            ET.SubElement(s, f"{{{_NS}}}par", {"type": "DOUBLE", "name": name, "value": "1"})
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8")


def _unique(wanted: str, used: set[str]) -> str:
    name, n = wanted, 2
    while name in used:
        name = f"{wanted}_{n}"
        n += 1
    used.add(name)
    return name


class _Assembler:
    """Builds one parametersSet from sets of several files, renaming clashing ids."""

    def __init__(self) -> None:
        ET.register_namespace("dyn", _NS)
        self.root = ET.Element(f"{{{_NS}}}parametersSet")
        self._set_ids: set[str] = set()
        self._macro_ids: set[str] = set()
        self._macros: dict[tuple[str, str], str] = {}  # (source label, macro id) → id in the result

    def copy_set(self, source_label: str, source_root: ET.Element, set_id: str, wanted_id: str) -> str:
        original = next(s for s in source_root.findall(f"{{{_NS}}}set") if s.get("id") == set_id)
        new = copy.deepcopy(original)
        new.set("id", _unique(wanted_id, self._set_ids))
        macro_ref = new.find(f"{{{_NS}}}macroParSet")
        if macro_ref is not None:
            macro_ref.set("id", self._copy_macro(source_label, source_root, macro_ref.get("id", "")))
        self.root.append(new)
        return new.get("id")

    def empty_set(self, wanted_id: str) -> str:
        new_id = _unique(wanted_id, self._set_ids)
        ET.SubElement(self.root, f"{{{_NS}}}set", {"id": new_id})
        return new_id

    def is_empty(self, set_id: str) -> bool:
        s = next(s for s in self.root.findall(f"{{{_NS}}}set") if s.get("id") == set_id)
        return len(s) == 0

    def _copy_macro(self, source_label: str, source_root: ET.Element, macro_id: str) -> str:
        key = (source_label, macro_id)
        if key not in self._macros:
            macro = next((m for m in source_root.findall(f"{{{_NS}}}macroParameterSet")
                          if m.get("id") == macro_id), None)
            new_id = _unique(macro_id, self._macro_ids)
            if macro is not None:
                new = copy.deepcopy(macro)
                new.set("id", new_id)
                # Macros first.
                self.root.insert(len(self._macros), new)
            self._macros[key] = new_id
        return self._macros[key]

    def to_bytes(self) -> bytes:
        tree = ET.ElementTree(self.root)
        ET.indent(tree, space="  ")
        return ET.tostring(self.root, xml_declaration=True, encoding="UTF-8")


def assemble_par(
    specs: list[tuple[str, str, str]], par_files: dict[str, bytes], generic: bytes | None,
    previous: bytes | None = None,
) -> tuple[bytes, dict[tuple[str, str, str], str], list[str]]:
    """Single .par holding the set of each spec, each copied once.

    A manual set present in `previous` (the file being replaced) is kept.
    Returns (content, spec -> set id, ids of the sets left empty).
    """
    assembler = _Assembler()
    parsed: dict[str, ET.Element] = {}

    def root_of(label: str, content: bytes) -> ET.Element:
        if label not in parsed:
            parsed[label] = ET.fromstring(content)
        return parsed[label]

    previous_ids = set(set_ids(previous)) if previous else set()
    ids: dict[tuple[str, str, str], str] = {}
    to_fill: list[str] = []
    # Copied sets first, so that they keep their ids.
    for spec in sorted(dict.fromkeys(specs), key=lambda s: s[0] == MANUAL):
        source, par_file, set_id = spec
        if source == FILE:
            ids[spec] = assembler.copy_set(f"file:{par_file}", root_of(f"file:{par_file}", par_files[par_file]), set_id, set_id)
        elif source == GENERIC:
            ids[spec] = assembler.copy_set("generic", root_of("generic", generic), set_id, set_id)
        elif set_id in previous_ids:
            ids[spec] = assembler.copy_set("previous", root_of("previous", previous), set_id, set_id)
            if assembler.is_empty(ids[spec]):
                to_fill.append(ids[spec])
        else:
            ids[spec] = assembler.empty_set(set_id)
            to_fill.append(ids[spec])
    return assembler.to_bytes(), ids, to_fill
