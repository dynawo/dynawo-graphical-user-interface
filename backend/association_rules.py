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
#      par_source, par_file, parameter_set_id}
# Rules apply in order; a later rule overrides an earlier one.

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

_types_cache: tuple[float, list[dict]] | None = None


def load_element_types() -> list[dict]:
    """Element types of resources/association_element_types.json, re-read when it changes."""
    global _types_cache
    try:
        mtime = os.stat(ELEMENT_TYPES_PATH).st_mtime
    except OSError:
        return []
    if _types_cache is None or _types_cache[0] != mtime:
        with open(ELEMENT_TYPES_PATH, encoding="utf-8") as fh:
            _types_cache = (mtime, json.load(fh).get("element_types", []))
    return _types_cache[1]


def get_element_type(type_id: str) -> dict | None:
    return next((t for t in load_element_types() if t["id"] == type_id), None)


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


def condition_code(condition: dict, value, df_name: str = "df") -> str:
    """pandas code equivalent to condition_mask, for the script export."""
    template = OPERATORS[condition["operator"]][2]
    return "(" + template.format(s=f"{df_name}[{condition['attribute']!r}]", v=repr(value)) + ")"


# ── Models ────────────────────────────────────────────────────────────────────

def models_for_element_type(element_type: dict | None, catalogue: list[dict]) -> list[dict]:
    """Equipment models, those fitting the element type first."""
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
    return suggested + others


def _known_categories() -> set[str]:
    return {c for t in load_element_types() for c in t["categories"]}


# ── Resolution ────────────────────────────────────────────────────────────────

def resolve(network, rules: list[dict], catalogue: list[dict], check_parameters=None, tables: dict | None = None) -> dict:
    """Apply the rules to the network; a rule with errors is skipped.

    `check_parameters(rule) -> list[str]` optionally reports parameter-set errors;
`tables` optionally caches the element tables of this network by type id.
    Returns {rules: [{index, matched, applied, errors, warnings}],
    assignments: [{element_type, static_id, model, category, rule}],
    element_types: [{id, label, total, unmatched_count, unmatched}]}, `unmatched`
holding at most MAX_LISTED_UNMATCHED ids.
    """
    model_category = {m["name"]: c["name"] for c in catalogue if c["kind"] == EQUIPMENT for m in c["models"]}
    known_categories = _known_categories()
    if tables is None:
        tables = {}

    def table(type_entry: dict) -> pd.DataFrame:
        if type_entry["id"] not in tables:
            tables[type_entry["id"]] = element_table(network, type_entry)
        return tables[type_entry["id"]]

    owner: dict[tuple[str, str], int] = {}
    reports = []
    for index, rule in enumerate(rules):
        report = {"index": index, "matched": 0, "applied": 0, "errors": [], "warnings": []}
        reports.append(report)
        type_entry = get_element_type(rule.get("element_type", ""))
        if type_entry is None:
            report["errors"].append(f"unknown element type '{rule.get('element_type')}'")
            continue
        category = model_category.get(rule.get("model", ""))
        if category is None:
            report["errors"].append(f"unknown model '{rule.get('model')}'")
        elif category in known_categories and category not in type_entry["categories"]:
            report["warnings"].append(
                f"model '{rule['model']}' ({category}) is not made for {type_entry['label'].lower()}: "
                "PowSyBl will probably leave these elements out"
            )
        if check_parameters is not None:
            report["errors"].extend(check_parameters(rule))
        df = table(type_entry)
        mask = pd.Series(True, index=df.index)
        for condition in rule.get("conditions", []):
            value, error = check_condition(df, condition)
            if error:
                report["errors"].append(error)
            else:
                mask &= condition_mask(df, condition, value)
        if report["errors"]:
            continue
        ids = df.loc[mask, "id"]
        report["matched"] = len(ids)
        for static_id in ids:
            owner[(type_entry["id"], static_id)] = index

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
