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
import xml.etree.ElementTree as ET

from backend.models import SimulationConfig, SolverInfo

_NS = "http://www.rte-france.com/dynawo"


def read_simulation_config(jobs_path: str) -> SimulationConfig:
    """Read startTime, stopTime and outputs directory from a jobs XML file."""
    try:
        root = ET.parse(jobs_path).getroot()
        sim = root.find(f".//{{{_NS}}}simulation")
        outputs = root.find(f".//{{{_NS}}}outputs")
        return SimulationConfig(
            start_time=float(sim.get("startTime", 0)) if sim is not None else 0.0,
            stop_time=float(sim.get("stopTime", 30)) if sim is not None else 30.0,
            output_dir=outputs.get("directory", "outputs") if outputs is not None else "outputs",
        )
    except Exception:
        return SimulationConfig(start_time=0.0, stop_time=30.0, output_dir="outputs")


def write_simulation_config(jobs_path: str, config: SimulationConfig) -> None:
    """Write startTime, stopTime and outputs directory into the jobs XML.

    Also updates modeler/compileDir when the outputs directory name changes.
    """
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(jobs_path)
    root = tree.getroot()

    sim = root.find(f".//{{{_NS}}}simulation")
    if sim is not None:
        sim.set("startTime", f"{config.start_time:g}")
        sim.set("stopTime", f"{config.stop_time:g}")

    outputs = root.find(f".//{{{_NS}}}outputs")
    if outputs is not None:
        old_dir = outputs.get("directory", "outputs")
        outputs.set("directory", config.output_dir)

        modeler = root.find(f".//{{{_NS}}}modeler")
        if modeler is not None:
            compile_dir = modeler.get("compileDir", "")
            if compile_dir == old_dir or compile_dir.startswith(old_dir + "/"):
                modeler.set("compileDir", config.output_dir + compile_dir[len(old_dir):])

    ET.indent(tree, space="  ")
    tree.write(jobs_path, xml_declaration=True, encoding="UTF-8")


def read_solver_info(jobs_path: str) -> SolverInfo | None:
    """Return SolverInfo from the <solver> element of a jobs XML file."""
    try:
        root = ET.parse(jobs_path).getroot()
        solver = root.find(f".//{{{_NS}}}solver")
        if solver is None:
            return None
        return SolverInfo(
            lib=solver.get("lib", ""),
            par_file=solver.get("parFile", ""),
            par_id=solver.get("parId", ""),
        )
    except Exception:
        return None


def read_log_filename(jobs_path: str) -> str:
    """Return the main log appender's file attribute from a jobs XML file.

    A jobs file can declare several <appender tag=".." file=".."/> entries, each
    writing its own file under outputs/logs/ — picks the one with tag="" (the
    root/default appender Dynawo always logs to) if present, else the first one.
    Falls back to "dynawo.log" (Dynawo's own default) when absent.
    """
    try:
        root = ET.parse(jobs_path).getroot()
        logs = root.find(f".//{{{_NS}}}logs")
        if logs is None:
            return "dynawo.log"
        appenders = logs.findall(f"{{{_NS}}}appender")
        if not appenders:
            return "dynawo.log"
        for appender in appenders:
            if appender.get("tag", "") == "":
                return appender.get("file") or "dynawo.log"
        return appenders[0].get("file") or "dynawo.log"
    except Exception:
        return "dynawo.log"


def read_crv_input_file(jobs_path: str) -> str | None:
    """Return the inputFile attribute of the <curves> element, or None."""
    try:
        root = ET.parse(jobs_path).getroot()
        outputs = root.find(f".//{{{_NS}}}outputs")
        if outputs is None:
            return None
        curves_elem = outputs.find(f"{{{_NS}}}curves")
        if curves_elem is None:
            return None
        return curves_elem.get("inputFile") or None
    except Exception:
        return None


def find_final_state_iidm(working_dir: str, output_dir: str) -> str | None:
    """Return the absolute path to the final-state IIDM file, or None.

    Dynawo writes the file to {output_dir}/finalState/ with a name and
    extension that depend on the version and configuration. We detect the
    file by its XML content (root local name 'network') rather than by name.
    """
    from backend.file_type_detector import detect_dynawo_type

    final_state_dir = os.path.join(working_dir, output_dir, "finalState")
    if not os.path.isdir(final_state_dir):
        return None
    for fname in os.listdir(final_state_dir):
        fpath = os.path.join(final_state_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "rb") as fh:
                content = fh.read()
            if detect_dynawo_type(content) == "iidm":
                return fpath
        except Exception:
            continue
    return None


def read_output_dir(jobs_path: str) -> str:
    """Return the outputs directory from a jobs XML file."""
    return read_simulation_config(jobs_path).output_dir


def write_crv_reference_to_jobs(jobs_path: str, crv_filename: str) -> None:
    """Add or update the <curves inputFile="..."> element in a jobs XML file.

    Creates an <outputs> element (with directory="outputs") if none exists.
    Preserves exportMode on existing <curves> elements; defaults to "CSV" when adding.
    """
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(jobs_path)
    root = tree.getroot()

    outputs = root.find(f".//{{{_NS}}}outputs")
    if outputs is None:
        job = root.find(f".//{{{_NS}}}job")
        if job is None:
            raise ValueError("No <job> element found in jobs file")
        outputs = ET.SubElement(job, f"{{{_NS}}}outputs")
        outputs.set("directory", "outputs")

    existing = outputs.find(f"{{{_NS}}}curves")
    if existing is not None:
        existing.set("inputFile", crv_filename)
    else:
        curves_elem = ET.SubElement(outputs, f"{{{_NS}}}curves")
        curves_elem.set("inputFile", crv_filename)
        curves_elem.set("exportMode", "CSV")

    ET.indent(tree, space="  ")
    tree.write(jobs_path, xml_declaration=True, encoding="UTF-8")


def read_network_iidm(jobs_path: str) -> str | None:
    """Return the iidmFile attribute from the <network> element, or None."""
    try:
        root = ET.parse(jobs_path).getroot()
        network = root.find(f".//{{{_NS}}}network")
        if network is None:
            return None
        return network.get("iidmFile") or None
    except Exception:
        return None


def write_network_iidm(jobs_path: str, iidm_filename: str) -> None:
    """Patch the iidmFile attribute in the <network> element of a jobs XML file."""
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(jobs_path)
    root = tree.getroot()
    network = root.find(f".//{{{_NS}}}network")
    if network is None:
        raise ValueError("No <network> element found in jobs file")
    network.set("iidmFile", iidm_filename)
    ET.indent(tree, space="  ")
    tree.write(jobs_path, xml_declaration=True, encoding="UTF-8")


def read_final_state_info(jobs_path: str) -> dict:
    """Return {'exports_iidm': bool, 'relative_iidm_path': str | None}.

    relative_iidm_path is relative to the session working directory, e.g.
    'outputs_run1/finalState/outputIIDM.iidm'. It is None when exportIIDMFile
    is absent or false.
    """
    try:
        root = ET.parse(jobs_path).getroot()
        outputs = root.find(f".//{{{_NS}}}outputs")
        if outputs is None:
            return {"exports_iidm": False, "relative_iidm_path": None}
        output_dir = outputs.get("directory", "outputs")
        final_state = outputs.find(f"{{{_NS}}}finalState")
        if final_state is None:
            return {"exports_iidm": False, "relative_iidm_path": None}
        exports = final_state.get("exportIIDMFile", "false").lower() == "true"
        if not exports:
            return {"exports_iidm": False, "relative_iidm_path": None}
        return {
            "exports_iidm": True,
            "relative_iidm_path": f"{output_dir}/finalState/outputIIDM.iidm",
        }
    except Exception:
        return {"exports_iidm": False, "relative_iidm_path": None}


def read_all_file_refs(jobs_path: str) -> dict:
    """Return file references from the jobs XML.

    Single-value keys ('solver_par', 'iidm', 'network_par', 'crv'): str | None.
    Multi-value key 'dyd': list[str] — empty when absent, can hold several entries
    because a jobs file may have multiple <dynModels dydFile="..."/> elements.
    """
    refs: dict = {
        "solver_par":  None,
        "iidm":        None,
        "network_par": None,
        "dyd":         [],
        "crv":         None,
    }
    try:
        root = ET.parse(jobs_path).getroot()
        solver = root.find(f".//{{{_NS}}}solver")
        if solver is not None:
            refs["solver_par"] = solver.get("parFile") or None

        network = root.find(f".//{{{_NS}}}network")
        if network is not None:
            refs["iidm"]        = network.get("iidmFile") or None
            refs["network_par"] = network.get("parFile")  or None

        for dyn in root.findall(f".//{{{_NS}}}dynModels"):
            dyd = dyn.get("dydFile")
            if dyd:
                refs["dyd"].append(dyd)

        outputs = root.find(f".//{{{_NS}}}outputs")
        if outputs is not None:
            curves = outputs.find(f"{{{_NS}}}curves")
            if curves is not None:
                refs["crv"] = curves.get("inputFile") or None
    except Exception:
        pass
    return refs
