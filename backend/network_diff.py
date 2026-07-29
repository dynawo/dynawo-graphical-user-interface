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
Compute NadProfile / SldProfile highlighting elements that changed between
two network states (e.g. uploaded IIDM vs. final-state IIDM after simulation).

Default colour scheme:
  amber (#faad14) : relative change between 1 % and 5 %
  red   (#f5222d) : relative change > 5 %
  no override     : change < 1 % or no valid data for comparison
"""
import math
from dataclasses import dataclass

import pandas as pd
import pypowsybl as ppb
from pypowsybl.network.impl.nad_profile import NadProfile
from pypowsybl.network.impl.sld_profile import SldProfile

_AMBER = '#faad14'
_RED   = '#f5222d'

LEGEND = [
    {'colour': _AMBER, 'label': 'Moderate change (1 – 5 %)'},
    {'colour': _RED,   'label': 'Large change (> 5 %)'},
]


_ALL_QUANTITIES: frozenset[str] = frozenset({'voltage', 'current', 'active_power'})


@dataclass
class DiffParams:
    moderate_threshold: float = 0.01
    large_threshold: float = 0.05
    # colours for increase (or magnitude when signed=False)
    moderate_colour: str = _AMBER
    large_colour: str = _RED
    # colours for decrease (only used when signed=True)
    neg_moderate_colour: str = '#69c0ff'
    neg_large_colour: str = '#1890ff'
    signed: bool = False
    quantities: frozenset[str] = _ALL_QUANTITIES

    def colour(self, delta: float | None) -> str:
        if delta is None:
            return ''
        mag = abs(delta)
        if mag < self.moderate_threshold:
            return ''
        if self.signed and delta < 0:
            return self.neg_large_colour if mag >= self.large_threshold else self.neg_moderate_colour
        return self.large_colour if mag >= self.large_threshold else self.moderate_colour


def _finite(v: float) -> bool:
    return math.isfinite(v) and v == v  # also catches NaN


def _rel(old: float, new: float, eps: float = 1.0) -> float | None:
    """Signed relative change (new-old) / max(|old|, eps). Returns None when data is missing."""
    if not (_finite(old) and _finite(new)):
        return None
    return (new - old) / max(abs(old), eps)


# ── per-element helpers ────────────────────────────────────────────────────────

def _bus_colours(net_orig: ppb.network.Network,
                 net_final: ppb.network.Network,
                 params: DiffParams) -> dict[str, str]:
    """Return {bus_id: colour} for buses whose v_mag changed significantly."""
    try:
        bo = net_orig.get_buses()[['v_mag', 'voltage_level_id']]
        bf = net_final.get_buses()[['v_mag', 'voltage_level_id']]
        vl = net_orig.get_voltage_levels()[['nominal_v']]
    except Exception:
        return {}

    result: dict[str, str] = {}
    for bid in bo.index.intersection(bf.index):
        vl_id = bo.at[bid, 'voltage_level_id']
        nom_v = float(vl.at[vl_id, 'nominal_v']) if vl_id in vl.index else 0.0
        eps = nom_v if nom_v > 0 else 1.0
        c = params.colour(_rel(bo.at[bid, 'v_mag'], bf.at[bid, 'v_mag'], eps=eps))
        if c:
            result[bid] = c
    return result


def _branch_colours(net_orig: ppb.network.Network,
                    net_final: ppb.network.Network,
                    params: DiffParams) -> dict[str, str]:
    """Return {branch_id: colour} for lines/transformers whose current changed."""
    result: dict[str, str] = {}
    for getter in (
        lambda n: n.get_lines()[['i1', 'i2']],
        lambda n: n.get_2_windings_transformers()[['i1', 'i2']],
    ):
        try:
            bo = getter(net_orig)
            bf = getter(net_final)
        except Exception:
            continue
        for bid in bo.index.intersection(bf.index):
            candidates = [
                r for r in (
                    _rel(bo.at[bid, 'i1'], bf.at[bid, 'i1'], eps=1.0),
                    _rel(bo.at[bid, 'i2'], bf.at[bid, 'i2'], eps=1.0),
                ) if r is not None
            ]
            if not candidates:
                continue
            d = max(candidates, key=abs)
            c = params.colour(d)
            if c:
                result[bid] = c
    return result


def _injection_colours(net_orig: ppb.network.Network,
                       net_final: ppb.network.Network,
                       params: DiffParams) -> dict[str, str]:
    """Return {element_id: colour} for generators and loads whose P changed."""
    result: dict[str, str] = {}
    for getter in (
        lambda n: n.get_generators()[['p']],
        lambda n: n.get_loads()[['p']],
    ):
        try:
            bo = getter(net_orig)
            bf = getter(net_final)
        except Exception:
            continue
        for eid in bo.index.intersection(bf.index):
            c = params.colour(_rel(bo.at[eid, 'p'], bf.at[eid, 'p'], eps=1.0))
            if c:
                result[eid] = c
    return result


# ── public API ─────────────────────────────────────────────────────────────────

def compute_nad_profile(net_orig: ppb.network.Network,
                        net_final: ppb.network.Network,
                        params: DiffParams | None = None) -> NadProfile:
    if params is None:
        params = DiffParams()

    bus_cols = (
        _bus_colours(net_orig, net_final, params)
        if 'voltage' in params.quantities else {}
    )
    branch_cols = (
        _branch_colours(net_orig, net_final, params)
        if 'current' in params.quantities else {}
    )

    bus_styles = None
    if bus_cols:
        bus_styles = pd.DataFrame(
            [{'id': bid, 'fill': c, 'edge': c, 'edge-width': ''} for bid, c in bus_cols.items()]
        ).set_index('id')

    edge_styles = None
    if branch_cols:
        edge_styles = pd.DataFrame(
            [{'id': bid, 'edge1': c, 'width1': '', 'dash1': '',
                         'edge2': c, 'width2': '', 'dash2': ''}
             for bid, c in branch_cols.items()]
        ).set_index('id')

    return NadProfile(bus_node_styles=bus_styles, edge_styles=edge_styles)


def compute_sld_profile(net_orig: ppb.network.Network,
                        net_final: ppb.network.Network,
                        params: DiffParams | None = None) -> SldProfile:
    if params is None:
        params = DiffParams()
    colours: dict[str, str] = {}
    if 'voltage' in params.quantities:
        colours.update(_bus_colours(net_orig, net_final, params))
    if 'current' in params.quantities:
        colours.update(_branch_colours(net_orig, net_final, params))
    if 'active_power' in params.quantities:
        colours.update(_injection_colours(net_orig, net_final, params))

    if not colours:
        return SldProfile()

    styles = pd.DataFrame(
        [{'id': eid, 'color': c, 'bus_width': '', 'width': '', 'dash': ''}
         for eid, c in colours.items()]
    ).set_index('id')
    return SldProfile(styles=styles)
