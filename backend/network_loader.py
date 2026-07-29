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
import tempfile

import pypowsybl as ppb


def load_network_from_path(file_path: str) -> ppb.network.Network:
    return ppb.network.load(file_path)


def load_network_from_bytes(file_bytes: bytes, filename: str) -> ppb.network.Network:
    suffix = os.path.splitext(filename)[1]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        return ppb.network.load(tmp_path)
    finally:
        os.unlink(tmp_path)


def network_summary(network: ppb.network.Network) -> dict:
    return {
        "Substations": len(network.get_substations()),
        "Voltage levels": len(network.get_voltage_levels()),
        "Buses": len(network.get_buses()),
        "Lines": len(network.get_lines()),
        "Generators": len(network.get_generators()),
        "Loads": len(network.get_loads()),
    }


def voltage_level_ids(network: ppb.network.Network) -> list:
    return network.get_voltage_levels().index.tolist()


def network_area_diagram_svg(network: ppb.network.Network,
                              voltage_level_ids: list[str] | None = None,
                              depth: int = 0) -> str:
    return str(network.get_network_area_diagram(voltage_level_ids=voltage_level_ids, depth=depth))


def network_area_diagram_diff_svg(net_orig: ppb.network.Network,
                                   net_final: ppb.network.Network,
                                   params=None,
                                   voltage_level_ids: list[str] | None = None,
                                   depth: int = 0) -> str:
    from backend.network_diff import compute_nad_profile
    profile = compute_nad_profile(net_orig, net_final, params)
    return str(net_final.get_network_area_diagram(
        nad_profile=profile, voltage_level_ids=voltage_level_ids, depth=depth))


def single_line_diagram_svg(network: ppb.network.Network, voltage_level_id: str) -> str:
    return str(network.get_single_line_diagram(voltage_level_id))


def get_sld(network: ppb.network.Network, voltage_level_id: str):
    """Return the raw Svg object exposing .svg and .metadata attributes."""
    return network.get_single_line_diagram(voltage_level_id)


def get_sld_diff(net_orig: ppb.network.Network,
                 net_final: ppb.network.Network,
                 voltage_level_id: str,
                 params=None):
    """Return an SLD Svg with elements coloured by how much they changed."""
    from backend.network_diff import compute_sld_profile
    profile = compute_sld_profile(net_orig, net_final, params)
    return net_final.get_single_line_diagram(voltage_level_id, sld_profile=profile)


def get_searchable_elements(network: ppb.network.Network) -> list:
    """Return every searchable element in the network for the unified SLD search.

    Each entry: {"id": str, "name": str|None, "type": str, "vl_ids": list[str]}
    All getters are guarded so missing element types never raise.
    """
    elements: list[dict] = []

    def _name(df, eid) -> str | None:
        if "name" not in df.columns:
            return None
        try:
            val = str(df.at[eid, "name"])
            return val if val and val not in ("nan", str(eid)) else None
        except Exception:
            return None

    # Voltage levels
    try:
        df = network.get_voltage_levels()
        for eid in df.index:
            elements.append({"id": str(eid), "name": _name(df, eid),
                              "type": "voltage_level", "vl_ids": [str(eid)]})
    except Exception:
        pass

    # Single-VL element types
    _single: list[tuple[str, object]] = [
        ("generator",              network.get_generators),
        ("load",                   network.get_loads),
        ("shunt_compensator",      network.get_shunt_compensators),
        ("static_var_compensator", network.get_static_var_compensators),
        ("dangling_line",          network.get_dangling_lines),
        ("battery",                network.get_batteries),
        ("lcc_converter_station",  network.get_lcc_converter_stations),
        ("vsc_converter_station",  network.get_vsc_converter_stations),
        ("busbar_section",         network.get_busbar_sections),
    ]
    for type_name, getter in _single:
        try:
            df = getter()
            if df.empty:
                continue
            for eid in df.index:
                vl = df.at[eid, "voltage_level_id"] if "voltage_level_id" in df.columns else None
                if not vl or str(vl) == "nan":
                    continue
                elements.append({"id": str(eid), "name": _name(df, eid),
                                  "type": type_name, "vl_ids": [str(vl)]})
        except Exception:
            pass

    # Two-VL elements (lines, 2W transformers)
    _two: list[tuple[str, object]] = [
        ("line",                     network.get_lines),
        ("two_winding_transformer",  network.get_2_windings_transformers),
    ]
    for type_name, getter in _two:
        try:
            df = getter()
            if df.empty:
                continue
            for eid in df.index:
                vl_ids = [
                    str(df.at[eid, c])
                    for c in ("voltage_level1_id", "voltage_level2_id")
                    if c in df.columns and str(df.at[eid, c]) not in ("", "nan")
                ]
                if vl_ids:
                    elements.append({"id": str(eid), "name": _name(df, eid),
                                     "type": type_name, "vl_ids": vl_ids})
        except Exception:
            pass

    # Three-winding transformers
    try:
        df = network.get_3_windings_transformers()
        if not df.empty:
            for eid in df.index:
                vl_ids = [
                    str(df.at[eid, c])
                    for c in ("voltage_level1_id", "voltage_level2_id", "voltage_level3_id")
                    if c in df.columns and str(df.at[eid, c]) not in ("", "nan")
                ]
                if vl_ids:
                    elements.append({"id": str(eid), "name": _name(df, eid),
                                     "type": "three_winding_transformer", "vl_ids": vl_ids})
    except Exception:
        pass

    return elements
