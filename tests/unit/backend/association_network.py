#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""A small network and model catalogue for the dynamic association tests,
without pypowsybl."""

import pandas as pd

from backend.association_catalogue import AUTOMATION_SYSTEM, EQUIPMENT


def _table(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(index=pd.Index([], name="id"))
    return pd.DataFrame(rows).set_index("id")


class FakeNetwork:
    """The part of a pypowsybl network the association reads: one table per
    getter (`get_loads`…), indexed by id; getters not given return no element."""

    def __init__(self, **tables: list[dict]) -> None:
        self._tables = {getter: _table(rows) for getter, rows in tables.items()}

    def __getattr__(self, name: str):
        if not name.startswith("get_"):
            raise AttributeError(name)
        return lambda all_attributes=False: self._tables.get(name, _table([]))


def network() -> FakeNetwork:
    """Two voltage levels in two countries; loads, generators, a line, two
    transformers (one with a phase tap changer) and a bus."""
    return FakeNetwork(
        get_substations=[{"id": "S1", "country": "FR"}, {"id": "S2", "country": "BE"}],
        get_voltage_levels=[{"id": "VL1", "nominal_v": 63.0, "substation_id": "S1"},
                            {"id": "VL2", "nominal_v": 225.0, "substation_id": "S2"}],
        get_loads=[{"id": "L1", "p0": 5.0, "voltage_level_id": "VL1", "connected": True},
                   {"id": "L2", "p0": 20.0, "voltage_level_id": "VL2", "connected": False},
                   {"id": "L3", "p0": 30.0, "voltage_level_id": "VL2", "connected": True}],
        get_generators=[{"id": "G1", "energy_source": "HYDRO", "voltage_level_id": "VL1", "connected": True},
                        {"id": "G2", "energy_source": "NUCLEAR", "voltage_level_id": "VL2", "connected": True}],
        get_lines=[{"id": "LN1", "voltage_level1_id": "VL1", "voltage_level2_id": "VL2",
                    "connected1": True, "connected2": False}],
        get_2_windings_transformers=[
            {"id": "T1", "voltage_level1_id": "VL1", "voltage_level2_id": "VL2", "connected1": True, "connected2": True},
            {"id": "T2", "voltage_level1_id": "VL1", "voltage_level2_id": "VL2", "connected1": True, "connected2": True}],
        get_phase_tap_changers=[{"id": "T1", "tap": 0}],
        get_bus_breaker_view_buses=[{"id": "B1", "voltage_level_id": "VL1"}],
    )


def _category(name: str, kind: str, *models: str) -> dict:
    return {"name": name, "description": "", "kind": kind, "dataframes": [],
            "models": [{"name": m, "description": ""} for m in models]}


CATALOGUE = [
    _category("Load", EQUIPMENT, "LoadAlphaBeta", "LoadPQ"),
    _category("LoadOneTransformer", EQUIPMENT, "LoadOneTransformer"),
    _category("LoadTwoTransformers", EQUIPMENT, "LoadTwoTransformers"),
    _category("SynchronousGenerator", EQUIPMENT, "GeneratorSynchronousFourWindings"),
    _category("Line", EQUIPMENT, "Line"),
    _category("Transformer", EQUIPMENT, "TransformerFixedRatio"),
    _category("TapChanger", AUTOMATION_SYSTEM, "TapChangerAutomationSystem"),
    _category("PhaseShifterI", AUTOMATION_SYSTEM, "PhaseShifterI"),
    _category("UnderVoltageAutomationSystem", AUTOMATION_SYSTEM, "UnderVoltage"),
    _category("OverloadManagementSystem", AUTOMATION_SYSTEM, "OverloadManagementSystem"),
]


def rule(element_type: str, model: str, *conditions: tuple, set_id: str = "S", **extra) -> dict:
    """A rule; each condition is (attribute, operator, value)."""
    return {"element_type": element_type, "model": model, "par_source": "manual", "par_file": "",
            "parameter_set_id": set_id,
            "conditions": [{"attribute": a, "operator": o, "value": v} for a, o, v in conditions], **extra}
