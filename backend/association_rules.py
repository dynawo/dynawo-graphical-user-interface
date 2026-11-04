#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import json
import os
import re

import pandas as pd

from backend.association_catalogue import EQUIPMENT

# Dynamic-association rules: a model and a parameter set put on the IIDM
# elements of a type meeting all conditions. A rule is a dict:
#     {element_type, conditions: [{attribute, operator, value}], model,
#      par_source, par_file, parameter_set_id, fields}
# Rules apply in order; a later rule overrides an earlier one. A model of an
# automation-system category gives each element its own automation system,
# alongside its equipment model; `fields` holds its other fields (e.g. side).

ELEMENT_TYPES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "resources", "association_element_types.json"
)

NUMBER = "number"
BOOLEAN = "boolean"
TEXT = "text"

# Voltage-level attributes joined onto element tables, with the side suffix
# of the voltage_level_id column (nominal_v1, nominal_v2 for a line).
_VOLTAGE_LEVEL_ID = re.compile(r"^voltage_level(\d*)_id$")
_VOLTAGE_LEVEL_ATTRIBUTES = ("nominal_v", "substation_id", "country")

# Text attributes with at most this many distinct values list them.
_MAX_LISTED_VALUES = 30

# Ids of the elements without a model returned per element type.
MAX_LISTED_UNMATCHED = 200

# operator -> (applicable kinds, pandas filter, equivalent code for the script
# export). Both forms must stay identical.
OPERATORS = {
    "=":       ((NUMBER, BOOLEAN, TEXT), lambda s, v: s == v,                 "{s} == {v}"),
    "!=":      ((NUMBER, BOOLEAN, TEXT), lambda s, v: s != v,                 "{s} != {v}"),
    "<":       ((NUMBER,),               lambda s, v: s < v,                  "{s} < {v}"),
    "<=":      ((NUMBER,),               lambda s, v: s <= v,                 "{s} <= {v}"),
    ">":       ((NUMBER,),               lambda s, v: s > v,                  "{s} > {v}"),
    ">=":      ((NUMBER,),               lambda s, v: s >= v,                 "{s} >= {v}"),
    "in":      ((NUMBER, BOOLEAN, TEXT), lambda s, v: s.isin(v),              "{s}.isin({v})"),
    # Regular expression searched in the text.
    "matches": ((TEXT,),                 lambda s, v: s.fillna("").astype(str).str.contains(v, regex=True),
                                                                              "{s}.fillna('').astype(str).str.contains({v}, regex=True)"),
}

_types_cache: tuple[float, dict] | None = None


def _load_types_file() -> dict:
    """resources/association_element_types.json, re-read when it changes."""
    global _types_cache
    try:
        mtime = os.stat(ELEMENT_TYPES_PATH).st_mtime
    except OSError:
        return {}
    if _types_cache is None or _types_cache[0] != mtime:
        with open(ELEMENT_TYPES_PATH, encoding="utf-8") as fh:
            _types_cache = (mtime, json.load(fh))
    return _types_cache[1]


def load_element_types() -> list[dict]:
    return _load_types_file().get("element_types", [])


def load_automation_systems() -> list[dict]:
    return _load_types_file().get("automation_systems", [])


def load_network_models() -> dict:
    return _load_types_file().get("network_models", {"categories": [], "element_types": []})


def network_models_requirement() -> str:
    """What a network-element model implies, in words."""
    labels = [get_element_type(t)["label"].lower() for t in load_network_models()["element_types"]]
    categories = load_network_models()["categories"]
    return (f"a {', '.join(categories[:-1])} or {categories[-1]} model makes PowSyBl model the whole network: "
            f"all {', '.join(labels[:-1])} and {labels[-1]} then need a dynamic model")


def get_element_type(type_id: str) -> dict | None:
    return next((t for t in load_element_types() if t["id"] == type_id), None)


def get_automation_system(category: str) -> dict | None:
    return next((a for a in load_automation_systems() if a["category"] == category), None)


def model_library(model: str, category: str | None) -> str:
    """Dynawo library of a model (automation-system models may be named otherwise)."""
    automation = get_automation_system(category) if category else None
    return (automation or {}).get("libs", {}).get(model, model)


# ── Element tables ────────────────────────────────────────────────────────────

def element_table(network, element_type: dict) -> pd.DataFrame:
    """Elements of a type: `id`, pypowsybl attributes and voltage-level attributes."""
    df = getattr(network, element_type["getter"])(all_attributes=True)
    df = df.reset_index().rename(columns={df.index.name or "index": "id"})
    vl_columns = [c for c in df.columns if _VOLTAGE_LEVEL_ID.match(c)]
    if vl_columns:
        vls = network.get_voltage_levels(all_attributes=True)[["nominal_v", "substation_id"]]
        countries = network.get_substations(all_attributes=True)["country"]
        vls = vls.assign(country=vls["substation_id"].map(countries))
        for column in vl_columns:
            side = _VOLTAGE_LEVEL_ID.match(column).group(1)
            for attribute in _VOLTAGE_LEVEL_ATTRIBUTES:
                name = f"{attribute}{side}"
                if name not in df.columns:
                    df[name] = df[column].map(vls[attribute])
    return df


def connection_notes(df: pd.DataFrame) -> dict[str, str]:
    """{id: why the element is not connected} for the elements of a table that
    are not fully connected ("not connected", "not connected on side 1"…)."""
    notes: dict[str, str] = {}
    if "connected" in df.columns:
        for i in df.loc[df["connected"].eq(False), "id"]:
            notes[i] = "not connected"
    sides = [c for c in ("connected1", "connected2") if c in df.columns]
    if sides:
        for _, row in df.loc[df[sides].eq(False).any(axis=1), ["id", *sides]].iterrows():
            open_sides = [c[-1] for c in sides if not row[c]]
            notes[row["id"]] = "not connected" if len(open_sides) == len(sides) else f"not connected on side {open_sides[0]}"
    return notes


def attribute_kind(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return BOOLEAN
    if pd.api.types.is_numeric_dtype(series):
        return NUMBER
    return TEXT


def describe_attributes(df: pd.DataFrame) -> list[dict]:
    """Attributes a condition can test: [{name, kind, operators, values}]."""
    attributes = []
    for name in df.columns:
        kind = attribute_kind(df[name])
        values = None
        if kind == TEXT:
            distinct = df[name].dropna().unique()
            if len(distinct) <= _MAX_LISTED_VALUES:
                values = sorted(str(v) for v in distinct)
        attributes.append({
            "name":      name,
            "kind":      kind,
            "operators": [op for op, (kinds, _, _) in OPERATORS.items() if kind in kinds],
            "values":    values,
        })
    return attributes


# ── Conditions ────────────────────────────────────────────────────────────────

def _coerce_one(kind: str, raw):
    if kind == NUMBER:
        return float(raw)
    if kind == BOOLEAN:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "false"):
            return text == "true"
        raise ValueError(f"'{raw}' is not true or false")
    return str(raw)


def coerce_value(kind: str, operator: str, raw):
    """Condition value converted to the attribute kind (a list for "in"). Raises ValueError."""
    if operator == "in":
        items = raw if isinstance(raw, list) else [x.strip() for x in str(raw).split(",") if x.strip()]
        if not items:
            raise ValueError("the list of values is empty")
        return [_coerce_one(kind, x) for x in items]
    if operator == "matches":
        re.compile(str(raw))
        return str(raw)
    return _coerce_one(kind, raw)


def attribute_values(df: pd.DataFrame, attribute: str, search: str, limit: int) -> tuple[list[str], int]:
    """Distinct values of an attribute containing `search` (case-insensitive):
    (first `limit` sorted, number matching)."""
    values = pd.Series(df[attribute].dropna().unique()).astype(str)
    if search:
        values = values[values.str.contains(search, case=False, regex=False)]
    return sorted(values)[:limit], len(values)


def check_condition(df: pd.DataFrame, condition: dict) -> tuple[object, str | None]:
    """(coerced value, None) if the condition applies to the table, else (None, reason)."""
    attribute = condition.get("attribute")
    operator = condition.get("operator")
    if attribute not in df.columns:
        return None, f"unknown attribute '{attribute}'"
    if operator not in OPERATORS:
        return None, f"unknown operator '{operator}'"
    kind = attribute_kind(df[attribute])
    if kind not in OPERATORS[operator][0]:
        return None, f"operator '{operator}' does not apply to {kind} attribute '{attribute}'"
    try:
        return coerce_value(kind, operator, condition.get("value")), None
    except (ValueError, TypeError, re.error) as exc:
        return None, f"invalid value for '{attribute}': {exc}"


def condition_mask(df: pd.DataFrame, condition: dict, value) -> pd.Series:
    """Rows meeting the condition."""
    return OPERATORS[condition["operator"]][1](df[condition["attribute"]], value).fillna(False).astype(bool)


def _id_lookup(condition: dict) -> bool:
    """Whether a condition selects elements by id (looked up instead of scanned)."""
    return condition.get("attribute") == "id" and condition.get("operator") in ("=", "in")


def condition_code(condition: dict, value, df_name: str = "df") -> str:
    """pandas code equivalent to condition_mask, for the script export."""
    template = OPERATORS[condition["operator"]][2]
    return "(" + template.format(s=f"{df_name}[{condition['attribute']!r}]", v=repr(value)) + ")"


# ── Models ────────────────────────────────────────────────────────────────────

def requirement_text(automation: dict) -> str | None:
    """What an automation system needs on its element, in words."""
    requires = automation.get("requires")
    if not requires:
        return None
    if "by" not in requires:
        return f"needs a {', '.join(requires['categories']['*'])} model on its element"
    by_categories: dict[tuple, list[str]] = {}
    for value, categories in requires["categories"].items():
        by_categories.setdefault(tuple(categories), []).append(value)
    return "needs on its element " + ", ".join(f"a {' or '.join(c)} model for {requires['by']} {' or '.join(v)}"
                                               for c, v in by_categories.items())


def models_for_element_type(element_type: dict | None, catalogue: list[dict]) -> list[dict]:
    """Equipment models, those fitting the element type first, then the
    automation systems that apply to it (flag `automation`, with their fields)."""
    fitting = element_type["categories"] if element_type else []
    suggested, others = [], []
    for category in catalogue:
        if category["kind"] != EQUIPMENT:
            continue
        fits = category["name"] in fitting
        for model in category["models"]:
            entry = {**model, "category": category["name"], "suggested": fits}
            (suggested if fits else others).append(entry)
    suggested.sort(key=lambda m: (fitting.index(m["category"]), m["name"]))
    others.sort(key=lambda m: (m["category"], m["name"]))
    automations = []
    by_name = {c["name"]: c for c in catalogue}
    for automation in load_automation_systems():
        category = by_name.get(automation["category"])
        if category is None or not element_type or element_type["id"] not in automation["element_types"]:
            continue
        for model in category["models"]:
            automations.append({
                **model, "category": category["name"], "suggested": True, "automation": True,
                "lib": model_library(model["name"], category["name"]),
                "fields": automation.get("fields", []), "requires": requirement_text(automation),
                "id_prefix": automation["id_prefix"], "note": automation.get("note"),
            })
    return suggested + others + automations


def _known_categories() -> set[str]:
    return {c for t in load_element_types() for c in t["categories"]}


def automation_fields(automation: dict, given: dict | None) -> tuple[dict[str, str], list[str]]:
    """(field values, errors) of an automation-system rule, defaults filled in."""
    given = given or {}
    values, errors = {}, []
    for field in automation.get("fields", []):
        allowed = [v["value"] for v in field["values"]]
        value = given.get(field["name"]) or allowed[0]
        if value not in allowed:
            errors.append(f"invalid {field['label'].lower()} '{value}' (one of {', '.join(allowed)})")
        values[field["name"]] = value
    return values, errors


def automation_id(automation: dict, static_id: str, fields: dict[str, str]) -> str:
    """Dynamic model id of the automation system put on an element."""
    suffix = "".join(f"_{fields[f['name']]}" for f in automation.get("fields", [])
                     if f.get("in_id") and fields[f["name"]] != f["values"][0]["value"])
    return f"{automation['id_prefix']}_{static_id}{suffix}"


def required_categories(automation: dict, fields: dict[str, str]) -> list[str] | None:
    requires = automation.get("requires")
    if not requires:
        return None
    categories = requires["categories"]
    return categories.get(fields.get(requires.get("by", ""), ""), categories.get("*"))


def _shown(ids: list[str]) -> str:
    return ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")


# ── Resolution ────────────────────────────────────────────────────────────────

def resolve(network, rules: list[dict], catalogue: list[dict], check_parameters=None, tables: dict | None = None) -> dict:
    """Apply the rules to the network; a rule with errors is skipped.

    `check_parameters(rule) -> list[str]` optionally reports parameter-set errors;
`tables` optionally caches the element tables of this network by type id.
    Returns {rules: [{index, matched, applied, errors, warnings}],
    assignments: [{element_type, static_id, model, category, rule}],
    element_types: [{id, label, total, unmatched_count, unmatched}]}, `unmatched`
holding at most MAX_LISTED_UNMATCHED ids. Automation-system assignments also
    hold `automation: True`, `dynamic_model_id` and `fields` (all its fields);
    elements count as matched by their equipment model only.
    """
    model_category = {m["name"]: c["name"] for c in catalogue for m in c["models"]
                      if c["kind"] == EQUIPMENT or get_automation_system(c["name"])}
    known_categories = _known_categories()
    if tables is None:
        tables = {}
    indexes: dict[str, pd.Index] = {}

    def table(type_entry: dict) -> pd.DataFrame:
        if type_entry["id"] not in tables:
            tables[type_entry["id"]] = element_table(network, type_entry)
        return tables[type_entry["id"]]

    def id_positions(type_id: str, df: pd.DataFrame, ids) -> list[int]:
        if type_id not in indexes:
            indexes[type_id] = pd.Index(df["id"])
        positions = indexes[type_id].get_indexer(ids if isinstance(ids, list) else [ids])
        return sorted(set(positions[positions >= 0].tolist()))

    owner: dict[tuple[str, str], int] = {}
    # dynamic model id -> (rule index, element type, element id, fields)
    automation_owner: dict[str, tuple[int, str, str, dict]] = {}
    reports = []
    for index, rule in enumerate(rules):
        report = {"index": index, "matched": 0, "applied": 0, "errors": [], "warnings": []}
        reports.append(report)
        type_entry = get_element_type(rule.get("element_type", ""))
        if type_entry is None:
            report["errors"].append(f"unknown element type '{rule.get('element_type')}'")
            continue
        category = model_category.get(rule.get("model", ""))
        automation = get_automation_system(category) if category else None
        fields: dict[str, str] = {}
        if category is None:
            report["errors"].append(f"unknown model '{rule.get('model')}'")
        elif automation is not None:
            if type_entry["id"] not in automation["element_types"]:
                labels = [get_element_type(t)["label"].lower() for t in automation["element_types"]]
                report["errors"].append(f"automation system '{rule['model']}' applies to {' and '.join(labels)} only")
            fields, errors = automation_fields(automation, rule.get("fields"))
            report["errors"].extend(errors)
        elif category in known_categories and category not in type_entry["categories"]:
            report["warnings"].append(
                f"model '{rule['model']}' ({category}) is not made for {type_entry['label'].lower()}: "
                "PowSyBl will probably leave these elements out"
            )
        if check_parameters is not None:
            report["errors"].extend(check_parameters(rule))
        df = table(type_entry)
        checked = []
        for condition in rule.get("conditions", []):
            value, error = check_condition(df, condition)
            if error:
                report["errors"].append(error)
            else:
                checked.append((condition, value))
        if report["errors"]:
            continue
        # Elements selected by id are looked up, the other conditions tested on them.
        lookup = next((i for i, (condition, _) in enumerate(checked) if _id_lookup(condition)), None)
        others = [c for i, c in enumerate(checked) if i != lookup]
        if lookup is not None:
            positions = id_positions(type_entry["id"], df, checked[lookup][1])
            if not others:
                ids = indexes[type_entry["id"]][positions]
            else:
                df = df[list(dict.fromkeys(["id", *(c["attribute"] for c, _ in others)]))].iloc[positions]
        if lookup is None or others:
            mask = pd.Series(True, index=df.index)
            for condition, value in others:
                mask &= condition_mask(df, condition, value)
            ids = df.loc[mask, "id"]
        report["matched"] = len(ids)
        for static_id in ids:
            if automation is None:
                owner[(type_entry["id"], static_id)] = index
            else:
                automation_owner[automation_id(automation, static_id, fields)] = (index, type_entry["id"], static_id, fields)

    # Applied elements that are not connected: PowSyBl writes their models anyway.
    notes = {type_id: connection_notes(df) for type_id, df in tables.items()}
    not_connected: dict[int, list[str]] = {}
    applied = [(index, type_id, static_id) for (type_id, static_id), index in owner.items()] \
        + [(index, type_id, static_id) for index, type_id, static_id, _ in automation_owner.values()]
    for index, type_id, static_id in applied:
        if static_id in notes.get(type_id, {}):
            not_connected.setdefault(index, []).append(static_id)
    for index, ids in not_connected.items():
        reports[index]["warnings"].append(f"{len(ids)} element(s) not connected: {_shown(ids)}")

    # Automation systems whose element lacks the equipment model they connect to.
    lacking: dict[int, tuple[list[str], list[str]]] = {}
    for index, type_id, static_id, fields in automation_owner.values():
        required = required_categories(get_automation_system(model_category[rules[index]["model"]]), fields)
        equipment = owner.get((type_id, static_id))
        if required and (equipment is None or model_category[rules[equipment]["model"]] not in required):
            lacking.setdefault(index, (required, []))[1].append(static_id)
    # Automation systems whose element lacks the IIDM part they act on.
    parts: dict[str, set] = {}
    lacking_part: dict[int, tuple[str, list[str]]] = {}
    for index, _, static_id, _ in automation_owner.values():
        part = get_automation_system(model_category[rules[index]["model"]]).get("requires_part")
        if part is None:
            continue
        if part["getter"] not in parts:
            parts[part["getter"]] = set(getattr(network, part["getter"])().index)
        if static_id not in parts[part["getter"]]:
            lacking_part.setdefault(index, (part["name"], []))[1].append(static_id)
    for index, (name, ids) in lacking_part.items():
        reports[index]["warnings"].append(f"{len(ids)} element(s) without a {name}: PowSyBl will leave them out: {_shown(ids)}")
    for index, (required, ids) in lacking.items():
        reports[index]["warnings"].append(
            f"{len(ids)} element(s) without the model this automation system connects to "
            f"({', '.join(required)}): PowSyBl will leave it out: {_shown(ids)}")
    # Dynamic model ids are shared by equipment and automation systems.
    clashing = sorted(set(automation_owner) & {static_id for _, static_id in owner})
    for dynamic_id in clashing:
        reports[automation_owner[dynamic_id][0]]["errors"].append(
            f"automation system id '{dynamic_id}' is also the id of an element with a model")

    # A network-element model needs one on every element of these types.
    network_models = load_network_models()
    network_rules = sorted({index for index in owner.values()
                            if model_category[rules[index]["model"]] in network_models["categories"]})
    if network_rules:
        missing = []
        for type_id in network_models["element_types"]:
            type_entry = get_element_type(type_id)
            ids = [i for i in table(type_entry)["id"] if (type_id, i) not in owner]
            if ids:
                missing.append(f"{len(ids)} {type_entry['label'].lower()} ({_shown(ids)})")
        if missing:
            for index in network_rules:
                reports[index]["warnings"].append(
                    f"{network_models_requirement()}; without one: {'; '.join(missing)}. PowSyBl will refuse to generate")

    assignments = []
    for (type_id, static_id), index in owner.items():
        rule = rules[index]
        reports[index]["applied"] += 1
        assignments.append({
            "element_type": type_id,
            "static_id":    static_id,
            "model":        rule["model"],
            "category":     model_category[rule["model"]],
            "rule":         index,
        })
    for dynamic_id, (index, type_id, static_id, fields) in automation_owner.items():
        if reports[index]["errors"]:
            continue
        rule = rules[index]
        category = model_category[rule["model"]]
        automation = get_automation_system(category)
        reports[index]["applied"] += 1
        assignments.append({
            "element_type":     type_id,
            "static_id":        static_id,
            "model":            rule["model"],
            "category":         category,
            "rule":             index,
            "automation":       True,
            "dynamic_model_id": dynamic_id,
            "fields":           {**{f: static_id for f in automation["element_fields"]}, **fields},
        })

    element_types = []
    for type_entry in load_element_types():
        ids = table(type_entry)["id"]
        unmatched = [i for i in ids if (type_entry["id"], i) not in owner]
        element_types.append({
            "id":              type_entry["id"],
            "label":           type_entry["label"],
            "total":           len(ids),
            "unmatched_count": len(unmatched),
            "unmatched":       unmatched[:MAX_LISTED_UNMATCHED],
        })

    return {"rules": reports, "assignments": assignments, "element_types": element_types}
