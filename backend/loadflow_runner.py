#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import glob
import io
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pypowsybl.loadflow as lf
import pypowsybl.security as sa

from backend.dyd_parser import parse_dyd
from backend.par_writer import relocate_table_files
from backend.powsybl_config import get_dynaflow_home

_JOBS_NS = "http://www.rte-france.com/dynawo"

# All fields that pypowsybl.loadflow.Parameters *may* expose across versions.
# Only the ones actually present on the installed version will be used.
_KNOWN_FIELDS = [
    'voltage_init_mode',
    'transformer_voltage_control_on',
    'no_generator_reactive_limits',
    'phase_shifter_regulation_on',
    'twt_split_shunt_admittance',
    'simul_shunt',
    'read_slack_bus',
    'write_slack_bus',
    'distributed_slack',
    'balance_type',
    'dc_use_transformer_ratio',
    'countries_to_balance',
    'connected_component_mode',
]

# Known enum fields and their class names inside the lf module
_ENUM_FIELDS: dict[str, str] = {
    'voltage_init_mode':        'VoltageInitMode',
    'balance_type':             'BalanceType',
    'connected_component_mode': 'ConnectedComponentMode',
}

# Safe fallback options in case enum introspection fails for this pybind11 version
_ENUM_FALLBACK: dict[str, list[str]] = {
    'voltage_init_mode':        ['UNIFORM_VALUES', 'PREVIOUS_VALUES', 'DC_VALUES'],
    'balance_type':             ['PROPORTIONAL_TO_GENERATION_P_MAX', 'PROPORTIONAL_TO_GENERATION_P',
                                 'PROPORTIONAL_TO_LOAD', 'PROPORTIONAL_TO_CONFORM_LOAD'],
    'connected_component_mode': ['MAIN', 'ALL'],
}


def _is_enum_val(val: Any) -> bool:
    """Detect pybind11 enum values by duck-typing (they have .name and .value)."""
    return (
        not isinstance(val, (str, int, float, bool))
        and hasattr(val, 'name')
        and isinstance(getattr(val, 'name'), str)
        and hasattr(val, 'value')
    )


def _enum_members(cls) -> list[str]:
    """Return enum member names for a pybind11 enum class, using several fallback strategies."""
    # Strategy 1: pybind11 >= 2.6 exposes __entries as {name: (value, docstring)}
    entries = getattr(cls, '__entries', None)
    if isinstance(entries, dict):
        return list(entries.keys())
    # Strategy 2: standard __members__ dict (works on some versions)
    members = getattr(cls, '__members__', None)
    if isinstance(members, dict):
        return list(members.keys())
    # Strategy 3: iterate the class itself (works on some pybind11 versions)
    try:
        return [m.name for m in cls]
    except TypeError:
        pass
    return []


def get_provider_names() -> list[str]:
    try:
        return lf.get_provider_names()
    except Exception:
        return []


def params_defaults() -> dict:
    p = lf.Parameters()
    result: dict[str, Any] = {}
    for name in _KNOWN_FIELDS:
        if not hasattr(p, name):
            continue
        val = getattr(p, name)
        if _is_enum_val(val):
            result[name] = val.name
        elif isinstance(val, (list, tuple)):
            result[name] = list(val)
        elif isinstance(val, dict):
            result[name] = dict(val)
        else:
            result[name] = val
    return result


def enum_options() -> dict:
    p = lf.Parameters()
    result: dict[str, list[str]] = {}
    for field, cls_name in _ENUM_FIELDS.items():
        if not hasattr(p, field):
            continue
        cls = getattr(lf, cls_name, None)
        members = _enum_members(cls) if cls is not None else []
        result[field] = members or _ENUM_FALLBACK.get(field, [])
    return result


def _parse_bracket_list(s: str) -> list[str]:
    """Parse pypowsybl's "[A, B, C]" spec-string format into a list."""
    s = (s or "").strip()
    if not (s.startswith("[") and s.endswith("]")):
        return []
    inner = s[1:-1].strip()
    return [v.strip() for v in inner.split(",")] if inner else []


def _parse_default(raw: str, ptype: str):
    """Parse a provider-parameter default into a JSON-friendly native type."""
    raw = raw or ""
    if ptype == "BOOLEAN":
        return raw.strip().lower() == "true"
    if ptype == "STRING_LIST":
        return _parse_bracket_list(raw)
    if ptype in ("DOUBLE", "INTEGER"):
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if val != val:  # NaN
            return None
        return int(val) if ptype == "INTEGER" else val
    return raw


def provider_parameter_specs(provider: str) -> list[dict]:
    """Return the provider-specific parameter specs (the ones forwarded via
    Parameters.provider_parameters) for a given pypowsybl loadflow provider.

    Unlike the generic Parameters() fields, these differ per provider — e.g.
    DynaFlow exposes ~11 parameters, OpenLoadFlow ~80 — so they must be
    fetched per-provider rather than from a single shared defaults object.
    """
    try:
        df = lf.get_provider_parameters(provider)
    except Exception:
        return []
    specs = []
    for name, row in df.iterrows():
        ptype = row.get("type") or "STRING"
        specs.append({
            "name": name,
            "category": row.get("category_key") or "",
            "description": row.get("description") or "",
            "type": ptype,
            "default": _parse_default(row.get("default", ""), ptype),
            "possible_values": _parse_bracket_list(row.get("possible_values", "")),
        })
    return specs


def _serialize_provider_param(val: Any, ptype: str) -> str | None:
    """Convert a native-typed UI value back into the string wire format
    pypowsybl.loadflow.Parameters.provider_parameters expects.

    Returns None when the value should be omitted entirely (unset/empty)
    rather than sent as an empty string: some providers (e.g. DynaFlow's
    `precision`, whose default is NaN) try to parse the string as a number
    on the Java side and raise NumberFormatException: empty String if given "".
    Omitting the key lets the provider fall back to its own internal default.
    """
    if ptype == "BOOLEAN":
        return "true" if val else "false"
    if ptype == "STRING_LIST":
        if isinstance(val, (list, tuple)):
            return ",".join(str(v) for v in val) if val else None
        return None if val in (None, "") else str(val)
    if val is None or val == "":
        return None
    return str(val)


def build_params(req_parameters: dict[str, Any], provider: str | None = None) -> lf.Parameters:
    """Apply request values onto a fresh lf.Parameters(), skipping unknown fields."""
    p = lf.Parameters()
    provider_param_types = (
        {s["name"]: s["type"] for s in provider_parameter_specs(provider)} if provider else {}
    )
    for name, val in req_parameters.items():
        # DynaFlow's voltage_init_mode is governed by the global StartingPointMode
        # (load-flow-default-parameters.voltageInitMode in the worker's config.yml).
        # The JVM reads that at startup; explicitly setting it here would override
        # the worker's freshly-loaded config, defeating the JVM-restart on mode change.
        if name == 'voltage_init_mode' and provider == 'DynaFlow':
            continue
        if not hasattr(p, name):
            continue
        if name == "provider_parameters" and isinstance(val, dict):
            serialised = {}
            for k, v in val.items():
                s = _serialize_provider_param(v, provider_param_types.get(k, "STRING"))
                if s is not None:
                    serialised[k] = s
            try:
                setattr(p, name, serialised)
            except Exception:
                pass
        elif name in _ENUM_FIELDS:
            cls = getattr(lf, _ENUM_FIELDS[name], None)
            if cls is None:
                continue
            # pybind11 enums: members are attributes on the class
            member = getattr(cls, str(val), None)
            if member is not None:
                try:
                    setattr(p, name, member)
                except Exception:
                    pass
        else:
            try:
                setattr(p, name, val)
            except Exception:
                pass
    return p


def resolve_output_name(network_name: str, requested_name: str, provider: str = "OpenLoadFlow") -> str:
    """Return the output filename to use, always with a .xiidm extension.

    requested_name is treated as a base name; any extension supplied by the
    caller is stripped since pypowsybl always writes XIIDM content. The
    default suffix differs per provider so a DynaFlow run never silently
    overwrites a same-named OpenLoadFlow result, or vice versa.
    """
    network_stem = Path(network_name).stem
    raw = requested_name.strip()
    base_name = Path(raw).stem if raw else ""
    if not base_name:
        suffix = "_DynaFlow" if provider == "DynaFlow" else "_lf"
        base_name = f"{network_stem}{suffix}"
    return f"{base_name}.xiidm"


def check_provider_ready(provider: str) -> str | None:
    """Return an error message if the provider can't actually run, else None.

    DynaFlow's PowSyBl provider does not reliably raise an exception when its
    launcher binary is missing/misconfigured — it sometimes silently returns
    a generic FAILED component result instead, which looks like a normal (if
    unconverged) run. Catching this ourselves up front gives a clear, honest
    error instead of a misleading "didn't converge" warning.
    """
    if provider != "DynaFlow":
        return None
    home = get_dynaflow_home()
    if not home:
        return "DynaFlow-launcher is not configured — set it up in the DynaFlow Launcher card above"
    if not os.path.isdir(home) or not os.path.isfile(os.path.join(home, "dynaflow-launcher.sh")):
        return f"DynaFlow-launcher not found at the configured path: {home}"
    return None


def run(net, ac: bool, parameters: dict[str, Any], provider: str):
    """Run AC or DC load flow with the given pypowsybl network and provider."""
    not_ready = check_provider_ready(provider)
    if not_ready:
        raise RuntimeError(not_ready)
    params = build_params(parameters, provider)
    return (lf.run_ac if ac else lf.run_dc)(net, parameters=params, provider=provider)


class LoadFlowStageError(Exception):
    """Raised by run_loadflow_in_worker, tagged with which stage failed so the
    caller can reproduce the same per-stage error messages it used to produce
    when load/run/export all happened in the main process."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage

    def __reduce__(self):
        # concurrent.futures round-trips exceptions through pickle to hand them
        # back from the worker process — the default reduce only replays
        # cls(*self.args), which would drop `stage` and break __init__'s arity.
        return (self.__class__, (self.stage, self.args[0] if self.args else ""))


def _dynaflow_run_dirs(tmp_dir: str) -> set[str]:
    """Working directories DynaFlow has created under this worker's private
    tmp-dir, excluding the lightweight dynaflow_version_* version-check dirs.
    """
    pattern = os.path.join(tmp_dir, "dynaflow_*")
    return {p for p in glob.glob(pattern) if not os.path.basename(p).startswith("dynaflow_version_")}


def _dynaflow_debug_files(run_dir: str) -> list[str]:
    """Files worth copying out of a DynaFlow run directory before it's
    deleted: every .par/.dyd file and config.json present, plus any .par
    referenced from a found .dyd's blackBoxModel/parFile attribute that the
    glob alone missed (verified empirically: DynaFlow's generated .dyd only
    cross-references .par files this way — Network.par/solver.par are
    fixed-name conventions, never referenced from anywhere parseable, so the
    glob is what actually catches those two).
    """
    found = {
        os.path.basename(p): p
        for p in glob.glob(os.path.join(run_dir, "*.par")) + glob.glob(os.path.join(run_dir, "*.dyd"))
    }
    config_json = os.path.join(run_dir, "config.json")
    if os.path.isfile(config_json):
        found["config.json"] = config_json
    for name, path in list(found.items()):
        if not name.endswith(".dyd"):
            continue
        try:
            with open(path, "rb") as fh:
                models = parse_dyd(fh.read())
        except Exception:
            continue
        for info in models.values():
            par_file = info.get("parFile")
            if par_file and par_file not in found:
                candidate = os.path.join(run_dir, par_file)
                if os.path.isfile(candidate):
                    found[par_file] = candidate
    return list(found.values())


def _copy_dynaflow_debug_files(run_dir: str, dest_dir: str) -> list[str]:
    """Copy this run's .dyd/.par files into dest_dir (the session's working
    directory — same filesystem, different process, no IPC needed) before
    the run directory gets deleted. Copied under their original names,
    deliberately not renamed: the .dyd references its .par files by exact
    name (blackBoxModel/parFile), so renaming one without rewriting that
    reference would break it — keeping original names sidesteps that
    entirely. A later DynaFlow run's capture overwrites an earlier one's;
    these are inspection copies, not a history.

    Each copied .par is also scanned for STRING-typed values pointing to a
    file under run_dir (e.g. DynaFlow's generated HVDC reactive-power lookup
    tables) — those get copied too and the .par's reference rewritten,
    otherwise the captured .par would point at a path that's deleted along
    with run_dir right after this function returns (see
    backend.par_writer.relocate_table_files).

    Returns the resulting filenames for the caller (api/routers/loadflow.py,
    which has the session object this worker doesn't) to register.
    """
    copied = []
    for src in _dynaflow_debug_files(run_dir):
        name = os.path.basename(src)
        dest_path = os.path.join(dest_dir, name)
        shutil.copy2(src, dest_path)
        copied.append(name)
        if name.endswith(".par"):
            copied.extend(relocate_table_files(dest_path, run_dir))
    return copied


_DYNAFLOW_OUTPUT_FILES = {
    os.path.join("timeLine", "timeline.xml"): "timeline.xml",
    os.path.join("constraints", "constraints.xml"): "constraints.xml",
    os.path.join("logs", "dynawo.log"): "dynawo.log",
    os.path.join("lostEquipments", "lostEquipments.xml"): "lostEquipments.xml",
}


def _copy_dynaflow_output_files(run_dir: str, dest_dir: str, basename: str) -> list[str]:
    """Copy this run's timeline/constraints/log/lostEquipments out of run_dir's
    "outputs" dir (the fixed directory name DynaFlow-launcher always writes to in
    our non-contingency usage — see _reconstruct_dynaflow_job_xml) into dest_dir,
    renamed with basename's prefix to avoid collisions across repeated runs
    and with the .dyd/.par debug capture.
    """
    copied = []
    for rel_src, suffix in _DYNAFLOW_OUTPUT_FILES.items():
        src = os.path.join(run_dir, "outputs", rel_src)
        if os.path.isfile(src):
            name = f"{basename}_{suffix}"
            shutil.copy2(src, os.path.join(dest_dir, name))
            copied.append(name)
    return copied


_DYNAFLOW_OUTPUT_KINDS = {
    os.path.join("timeLine", "timeline.xml"): ("timeline", "timeline.xml"),
    os.path.join("constraints", "constraints.xml"): ("constraints", "constraints.xml"),
    os.path.join("logs", "dynawo.log"): ("log", "dynawo.log"),
    os.path.join("lostEquipments", "lostEquipments.xml"): ("lost_equipments", "lostEquipments.xml"),
}

# (subdir under run_dir, kind, local file prefix, extension) — matches DynaFlow-launcher's
# security-analysis aggregation, e.g. run_dir/timeLine/timeline_{contingencyId}.xml,
# run_dir/logs/log_{contingencyId}.log. Verified against a real pypowsybl + DynaFlow
# security-analysis run dir (no "outputs"/"outputs-{id}" involved at all — that convention
# is Job.cpp's, for the single-state load-flow case _copy_dynaflow_output_files handles;
# dynawo-algorithms' SystematicAnalysisLauncher, which actually drives security analysis,
# aggregates per-contingency results into these flat top-level folders instead).
_DYNAFLOW_SA_OUTPUT_KINDS = [
    ("timeLine", "timeline", "timeline", "xml"),
    ("constraints", "constraints", "constraints", "xml"),
    ("logs", "log", "log", "log"),
    ("lostEquipments", "lost_equipments", "lostEquipments", "xml"),
]


def _copy_dynaflow_security_outputs(
    run_dir: str, dest_dir: str, basename: str, contingency_ids: list[str],
) -> dict[str, dict[str, str]]:
    """Copy timeline/constraints/log/lostEquipments out of a DynaFlow security analysis
    run_dir into dest_dir. There is no pre-contingency (N) output in pure security-analysis
    mode — only per-contingency results exist, named after the contingency id.

    Returns {contingency_id: {kind: filename}}; kind is one of
    "timeline"/"constraints"/"log"/"lost_equipments".
    """
    result: dict[str, dict[str, str]] = {}

    # Primary: DynaFlow-launcher's own per-kind aggregation at run_dir's top level.
    for subdir, kind, local_prefix, ext in _DYNAFLOW_SA_OUTPUT_KINDS:
        folder = os.path.join(run_dir, subdir)
        if not os.path.isdir(folder):
            continue
        prefix, suffix = f"{local_prefix}_", f".{ext}"
        for fname in os.listdir(folder):
            if not (fname.startswith(prefix) and fname.endswith(suffix)):
                continue
            cid = fname[len(prefix):-len(suffix)]
            if not cid:
                continue
            dest_name = f"{basename}_{cid}_{local_prefix}{suffix}"
            shutil.copy2(os.path.join(folder, fname), os.path.join(dest_dir, dest_name))
            result.setdefault(cid, {})[kind] = dest_name

    # Fallback: some DynaFlow versions only duplicate certain kinds (logs, in the verified
    # case) into each contingency's own outputs/ subdir (run_dir/{contingencyId}/outputs/...,
    # same fixed names _DYNAFLOW_OUTPUT_KINDS already knows) rather than the aggregated
    # top-level folders above — catches whatever the primary pass above missed.
    for cid in contingency_ids:
        captured = result.get(cid, {})
        for rel_src, (kind, file_suffix) in _DYNAFLOW_OUTPUT_KINDS.items():
            if kind in captured:
                continue
            src = os.path.join(run_dir, cid, "outputs", rel_src)
            if os.path.isfile(src):
                dest_name = f"{basename}_{cid}_{file_suffix}"
                shutil.copy2(src, os.path.join(dest_dir, dest_name))
                result.setdefault(cid, {})[kind] = dest_name

    return result


def _reconstruct_dynaflow_job_xml(
    parameters: dict[str, Any], basename: str, iidm_filename: str, dyd_filenames: list[str],
) -> bytes:
    """Rebuild the .jobs file DynaFlow-launcher would have written for this run.

    DynaFlow-launcher generates its own jobs/dyd/par/solver.par from the
    network and provider parameters, runs Dynawo against them, then deletes
    everything — pypowsybl never exposes the jobs file itself. This mirrors
    dynaflow-launcher's Outputs/src/Job.cpp::exportJob from the same
    provider parameters DynaFlow-launcher itself consumes, so the rest
    (solver lib/parFile/parId, compileDir, Network.par/parId, log level)
    are the fixed values that file always writes — only startTime, stopTime,
    precision and chosenOutputs vary with the run's parameters.
    """
    pp = parameters.get("provider_parameters") or {}
    start_time = pp.get("startTime", 0.0)
    stop_time = pp.get("stopTime", 100.0)
    precision = pp.get("precision")
    if precision is None or precision != precision:  # NaN: unset, falls back to DynaFlow's own default
        precision = 1e-4
    chosen_outputs = pp.get("chosenOutputs") or ["TIMELINE"]
    if isinstance(chosen_outputs, str):
        chosen_outputs = _parse_bracket_list(chosen_outputs) or [chosen_outputs]
    chosen_outputs = set(chosen_outputs)

    ET.register_namespace("dyn", _JOBS_NS)
    jobs = ET.Element(f"{{{_JOBS_NS}}}jobs")
    job = ET.SubElement(jobs, f"{{{_JOBS_NS}}}job", {"name": basename})

    ET.SubElement(job, f"{{{_JOBS_NS}}}solver", {
        "lib": "dynawo_SolverSIM", "parFile": "solver.par", "parId": "SimplifiedSolver",
    })

    modeler = ET.SubElement(job, f"{{{_JOBS_NS}}}modeler", {"compileDir": "outputs/compilation"})
    ET.SubElement(modeler, f"{{{_JOBS_NS}}}network", {
        "iidmFile": iidm_filename, "parFile": "Network.par", "parId": "Network",
    })
    for dyd_filename in dyd_filenames:
        ET.SubElement(modeler, f"{{{_JOBS_NS}}}dynModels", {"dydFile": dyd_filename})
    ET.SubElement(modeler, f"{{{_JOBS_NS}}}precompiledModels", {"useStandardModels": "true"})
    ET.SubElement(modeler, f"{{{_JOBS_NS}}}modelicaModels", {"useStandardModels": "true"})

    ET.SubElement(job, f"{{{_JOBS_NS}}}simulation", {
        "startTime": f"{start_time:g}", "stopTime": f"{stop_time:g}", "precision": f"{precision:g}",
    })

    outputs = ET.SubElement(job, f"{{{_JOBS_NS}}}outputs", {"directory": "outputs"})
    if "CONSTRAINTS" in chosen_outputs:
        ET.SubElement(outputs, f"{{{_JOBS_NS}}}constraints", {"exportMode": "XML", "filter": "DYNAFLOW"})
    if "TIMELINE" in chosen_outputs:
        ET.SubElement(outputs, f"{{{_JOBS_NS}}}timeline", {"exportMode": "XML"})
    ET.SubElement(outputs, f"{{{_JOBS_NS}}}finalState", {
        "exportIIDMFile": "true" if "STEADYSTATE" in chosen_outputs else "false",
        "exportDumpFile": "false",
    })
    if "LOSTEQ" in chosen_outputs:
        ET.SubElement(outputs, f"{{{_JOBS_NS}}}lostEquipments", {})
    logs = ET.SubElement(outputs, f"{{{_JOBS_NS}}}logs")
    ET.SubElement(logs, f"{{{_JOBS_NS}}}appender", {"tag": "", "file": "dynawo.log", "lvlFilter": "INFO"})

    tree = ET.ElementTree(jobs)
    ET.indent(tree, space="  ")
    buf = io.BytesIO()
    tree.write(buf, xml_declaration=True, encoding="UTF-8")
    return buf.getvalue()


def _cleanup_dynaflow_run_dirs(tmp_dir: str, new_dirs: set[str]) -> None:
    """Delete this run's working directories, plus any version-check dirs
    (never worth keeping). Safe to call unconditionally — a ProcessPoolExecutor
    worker only runs one task at a time, so `new_dirs` is an unambiguous
    snapshot of what this run created; no other run could have touched the
    same tmp-dir meanwhile.
    """
    for new_dir in new_dirs:
        shutil.rmtree(new_dir, ignore_errors=True)
    for version_dir in glob.glob(os.path.join(tmp_dir, "dynaflow_version_*")):
        shutil.rmtree(version_dir, ignore_errors=True)


def run_loadflow_in_worker(
    src_path: str, out_path: str, ac: bool, parameters: dict[str, Any], provider: str, iidm_version: str,
    debug_dest_dir: str | None = None, outputs_dest_dir: str | None = None,
) -> dict:
    """Entry point for ProcessPoolExecutor workers. Only picklable arguments in/out —
    the network never crosses the process boundary, each worker loads it from
    disk itself, giving it its own embedded JVM and PlatformConfig resolution.

    Both dest-dir parameters are only meaningful for provider == "DynaFlow", and both
    point at the session's working directory so the caller can register the copied
    files as session files — but on different conditions:
    - outputs_dest_dir is always set by the caller: this run's timeline/constraints/log
      are copied there whenever produced, regardless of "keep input files".
    - debug_dest_dir is only set when the user asked to keep input files: this run's
      .dyd/.par files are copied there, and its .jobs file is reconstructed from
      `parameters` (DynaFlow never writes one pypowsybl can read — see
      _reconstruct_dynaflow_job_xml).

    Either way, copying happens before this run's working directory is deleted, and
    only on the success path (run() returning, regardless of CONVERGED/FAILED status):
    debugging a non-converged run is the realistic use case; a raised exception (e.g.
    DynaFlow misconfigured) generally means the launcher never even started, so
    there's nothing to capture.
    """
    from backend.network_loader import load_network_from_path
    from backend.powsybl_config import worker_tmp_dir

    try:
        net = load_network_from_path(src_path)
    except Exception as exc:
        raise LoadFlowStageError("load", str(exc)) from None

    tmp_dir = worker_tmp_dir() if provider == "DynaFlow" else None
    before = _dynaflow_run_dirs(tmp_dir) if tmp_dir else set()
    try:
        results = run(net, ac, parameters, provider)
    except Exception as exc:
        if tmp_dir:
            _cleanup_dynaflow_run_dirs(tmp_dir, _dynaflow_run_dirs(tmp_dir) - before)
        raise LoadFlowStageError("run", str(exc)) from None

    debug_files: list[str] = []
    if tmp_dir:
        new_dirs = _dynaflow_run_dirs(tmp_dir) - before
        if new_dirs:
            basename = Path(src_path).stem
            if outputs_dest_dir:
                for run_dir in new_dirs:
                    debug_files.extend(_copy_dynaflow_output_files(run_dir, outputs_dest_dir, basename))
            if debug_dest_dir:
                for run_dir in new_dirs:
                    debug_files.extend(_copy_dynaflow_debug_files(run_dir, debug_dest_dir))
                dyd_filenames = [f for f in debug_files if f.endswith(".dyd")]
                job_xml = _reconstruct_dynaflow_job_xml(
                    parameters, basename, os.path.basename(src_path), dyd_filenames,
                )
                job_name = f"{basename}.jobs"
                with open(os.path.join(debug_dest_dir, job_name), "wb") as fh:
                    fh.write(job_xml)
                debug_files.append(job_name)
        _cleanup_dynaflow_run_dirs(tmp_dir, new_dirs)

    try:
        export_xiidm(net, out_path, iidm_version)
    except Exception as exc:
        raise LoadFlowStageError("export", str(exc)) from None
    result = serialise_results(results, provider, os.path.basename(out_path))
    result["debug_files"] = debug_files
    return result


def _enum_name(val: Any) -> Any:
    return val.name if hasattr(val, "name") else val


def _serialize_limit_violation(lv: Any) -> dict:
    return {
        "subject_id": lv.subject_id,
        "subject_name": lv.subject_name,
        "limit_type": _enum_name(lv.limit_type),
        "limit_name": lv.limit_name,
        "limit": lv.limit,
        "acceptable_duration": lv.acceptable_duration,
        "limit_reduction": lv.limit_reduction,
        "value": lv.value,
        "side": _enum_name(lv.side),
    }


def run_security_analysis_in_worker(
    src_path: str, element_ids: list[str], parameters: dict[str, Any],
    contingencies_start_time: float | None,
    debug_dest_dir: str | None = None, outputs_dest_dir: str | None = None,
) -> dict:
    """Entry point for ProcessPoolExecutor workers — N-1 security analysis, DynaFlow only.

    Mirrors run_loadflow_in_worker's structure (own process, own embedded JVM, network
    loaded fresh from disk) and its debug/outputs capture, generalised to every state a
    security analysis run produces — DynaFlow-launcher runs once, but with one
    "outputs"/"outputs-{contingencyId}" results directory per state (see
    _copy_dynaflow_security_outputs), and dyd/par files for every state directly under
    run_dir's top level (see _copy_dynaflow_debug_files — its flat glob already covers
    these regardless of state, no change needed there).

    Same dest-dir gating as run_loadflow_in_worker: outputs_dest_dir is always set by the
    caller; debug_dest_dir only when the user asked to keep input files.
    """
    from backend.network_loader import load_network_from_path
    from backend.powsybl_config import worker_tmp_dir

    not_ready = check_provider_ready("DynaFlow")
    if not_ready:
        raise RuntimeError(not_ready)

    try:
        net = load_network_from_path(src_path)
    except Exception as exc:
        raise LoadFlowStageError("load", str(exc)) from None

    lf_params = build_params(parameters, "DynaFlow")
    sa_provider_params = (
        {"contingenciesStartTime": str(contingencies_start_time)}
        if contingencies_start_time is not None else {}
    )
    analysis = sa.create_analysis()
    analysis.add_single_element_contingencies(element_ids)
    sa_params = sa.Parameters(load_flow_parameters=lf_params, provider_parameters=sa_provider_params)

    tmp_dir = worker_tmp_dir()
    before = _dynaflow_run_dirs(tmp_dir)
    try:
        result = analysis.run_ac(net, parameters=sa_params, provider="DynaFlow")
    except Exception as exc:
        _cleanup_dynaflow_run_dirs(tmp_dir, _dynaflow_run_dirs(tmp_dir) - before)
        raise LoadFlowStageError("run", str(exc)) from None

    debug_files: list[str] = []
    output_files_by_label: dict[str, dict[str, str]] = {}
    new_dirs = _dynaflow_run_dirs(tmp_dir) - before
    if new_dirs:
        basename = Path(src_path).stem
        if outputs_dest_dir:
            for run_dir in new_dirs:
                for label, kinds in _copy_dynaflow_security_outputs(
                    run_dir, outputs_dest_dir, basename, element_ids,
                ).items():
                    output_files_by_label.setdefault(label, {}).update(kinds)
                    debug_files.extend(kinds.values())
        if debug_dest_dir:
            for run_dir in new_dirs:
                debug_files.extend(_copy_dynaflow_debug_files(run_dir, debug_dest_dir))
            base_dyd = [f for f in debug_files if f == f"{basename}.dyd"] or \
                [f for f in debug_files if f.endswith(".dyd")]
            job_xml = _reconstruct_dynaflow_job_xml(parameters, basename, os.path.basename(src_path), base_dyd)
            job_name = f"{basename}_N.jobs"
            with open(os.path.join(debug_dest_dir, job_name), "wb") as fh:
                fh.write(job_xml)
            debug_files.append(job_name)
    _cleanup_dynaflow_run_dirs(tmp_dir, new_dirs)

    pre = result.pre_contingency_result
    return {
        "ok": True,
        "debug_files": debug_files,
        "pre_contingency": {
            "status": _enum_name(pre.status),
            "limit_violations": [_serialize_limit_violation(lv) for lv in pre.limit_violations],
            "output_files": output_files_by_label.get("N", {}),
        },
        "contingencies": [
            {
                "contingency_id": cid,
                "status": _enum_name(pcr.status),
                "limit_violations": [_serialize_limit_violation(lv) for lv in pcr.limit_violations],
                "output_files": output_files_by_label.get(cid, {}),
            }
            for cid, pcr in result.post_contingency_results.items()
        ],
    }


def export_xiidm(net, out_path: str, iidm_version: str) -> None:
    net.dump(out_path, format="XIIDM", parameters={"iidm.export.xml.version": iidm_version})


def serialise_results(results, provider: str, out_name: str) -> dict:
    components = []
    for i, r in enumerate(results):
        slack_buses = [
            {"id": sbr.id, "active_power_mismatch": sbr.active_power_mismatch}
            for sbr in r.slack_bus_results
        ]
        components.append({
            "num": i,
            "status": r.status.name,
            "iteration_count": r.iteration_count,
            "slack_bus_results": slack_buses,
        })
    return {
        "ok": True,
        "provider": provider,
        "output_filename": out_name,
        "components": components,
    }
