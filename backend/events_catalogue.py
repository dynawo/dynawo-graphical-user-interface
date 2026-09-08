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

# The catalogue shipped with the application: resources/events.xml. It is the
# only place the offered events are declared — nothing here hard-codes one.
CATALOGUE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "resources", "events.xml"
)


def parse_events_catalogue(content: bytes) -> list[dict]:
    """Return one flat entry per <event> of an events.xml catalogue.

    Flat rather than nested: every consumer (target matching, the parameter
    form, the .dyd writer to come) works on a single event at a time, and each
    entry carries its family's scope so nothing has to walk back up the tree.

    Each entry: {id, scope, family_label, family_description, equipment_type,
                 label, lib, automatic, destination, patterns}
    where `destination` is "NETWORK" or "MODEL" and `patterns` is one
    {var1, var2} fragment pair per connection to build.
    """
    root = ET.fromstring(content)
    events: list[dict] = []
    for family in root.findall("eventFamily"):
        scope = family.get("scope", "")
        for ev in family.findall("event"):
            ev_id = ev.get("id")
            if not ev_id:
                continue
            destination = ev.find("destination")
            events.append({
                "id":                 ev_id,
                "scope":              scope,
                "family_label":       family.get("label", scope),
                "family_description": family.get("description", ""),
                "equipment_type":     (ev.get("appliesTo") or "").upper(),
                "label":              ev.get("label", ev_id),
                "lib":                ev.get("lib", ""),
                "automatic":          _parse_automatic(ev),
                "destination":        (destination.get("to") or "" if destination is not None else "").upper(),
                "patterns":           _parse_patterns(ev),
            })
    return events


def _parse_automatic(ev: ET.Element) -> bool:
    """<automatic>true</automatic>, absent meaning no automatic connection.

    Opt-in rather than opt-out: an event whose ports the catalogue does not
    describe well enough to guess is better left to the user than wired wrong."""
    node = ev.find("automatic")
    return node is not None and (node.text or "").strip().lower() == "true"


def _parse_patterns(ev: ET.Element) -> list[dict]:
    patterns: list[dict] = []
    for p in ev.findall("connectPattern"):
        patterns.append({
            "var1": (p.get("valueVar1") or "").strip(),
            "var2": (p.get("valueVar2") or "").strip(),
        })
    return patterns


def load_events_catalogue(path: str = CATALOGUE_PATH) -> list[dict]:
    """Read and parse the catalogue file, or return [] if it is missing or invalid."""
    try:
        with open(path, "rb") as fh:
            return parse_events_catalogue(fh.read())
    except (OSError, ET.ParseError):
        return []


# ── Pattern matching ──────────────────────────────────────────────────────────

def _rank(name: str, pattern: str) -> tuple:
    """Sort key ordering the variables containing `pattern`, closest first.

    A fragment like "state" hits nine variables of the network model
    (@ID@_state_value, @ID@_lineState_value, @ID@_GENERATOR_state_value…), so
    the order decides what an automatic connection proposes. Ranked by how
    exactly the variable names the fragment: a whole "_"-delimited word beats a
    fragment buried in a longer word, the declared case beats a case-insensitive
    hit, and the shortest name breaks the remaining ties — which is what makes
    @ID@_state_value win over @ID@_GENERATOR_state_value.
    """
    words = name.split("_")
    exact_word = 0 if pattern in words else 1
    exact_word_ci = 0 if pattern.lower() in [w.lower() for w in words] else 1
    same_case = 0 if pattern in name else 1
    return (exact_word, exact_word_ci, same_case, len(name), name)


def match_variables(pattern: str, variables: list[str]) -> list[str]:
    """Every variable containing `pattern`, closest match first.

    Matching is case-insensitive: a fragment is meant to be read as the port's
    name ("switchOffSignal2", "state"), not as an exact spelling."""
    if not pattern:
        return []
    low = pattern.lower()
    return sorted((v for v in variables if low in v.lower()), key=lambda v: _rank(v, pattern))


def match_connection(pattern: dict, var1_variables: list[str], var2_variables: list[str]) -> dict:
    """Apply one <connectPattern> to the variables of the two sides.

    Returns the candidates found on each side and the pair that would be wired
    — `resolved` when both sides matched, `ambiguous` when at least one side
    matched several variables, in which case the proposal is a best guess the
    user is expected to confirm.
    """
    var1_matches = match_variables(pattern["var1"], var1_variables)
    var2_matches = match_variables(pattern["var2"], var2_variables)
    return {
        "pattern_var1": pattern["var1"],
        "pattern_var2": pattern["var2"],
        "var1_matches": var1_matches,
        "var2_matches": var2_matches,
        "var1":         var1_matches[0] if var1_matches else None,
        "var2":         var2_matches[0] if var2_matches else None,
        "resolved":     bool(var1_matches and var2_matches),
        "ambiguous":    len(var1_matches) > 1 or len(var2_matches) > 1,
    }


def patterns_resolve(event: dict, var1_variables: list[str], var2_variables: list[str]) -> bool:
    """Whether every connection of `event` can be built from these variables.

    What decides that an event is applicable to a target: the catalogue may
    offer it, but it is only wirable when both sides expose the ports it needs.
    An event declaring no pattern is left to the user, hence applicable.
    """
    return all(
        match_connection(p, var1_variables, var2_variables)["resolved"]
        for p in event["patterns"]
    )
