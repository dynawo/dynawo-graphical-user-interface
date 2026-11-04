#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import pandas as pd

from backend.association_catalogue import AUTOMATION_SYSTEM, EQUIPMENT, build_catalogue, category_kind, parse_attributes

# The `attribute` texts of pypowsybl's categories.
LOAD = "index : static_id (str), parameter_set_id (str), model_name (str)"
TAP_CHANGER = "index : dynamic_model_id (str), parameter_set_id (str), model_name (str), static_id (str), side (str)"


def test_attributes_give_the_index_and_columns():
    assert parse_attributes(LOAD) == [{"name": None, "index": "static_id", "columns": [
        {"name": "static_id", "type": "str"}, {"name": "parameter_set_id", "type": "str"},
        {"name": "model_name", "type": "str"}]}]


def test_attributes_of_several_dataframes_keep_their_names():
    frames = parse_attributes('[dataframe "Main"] index : dynamic_model_id (str), model_name (str) / '
                              '[dataframe "Lines"] index : dynamic_model_id (str), line (str)')

    assert [(f["name"], f["index"], len(f["columns"])) for f in frames] == [("Main", "dynamic_model_id", 2),
                                                                             ("Lines", "dynamic_model_id", 2)]
    assert category_kind(frames) == AUTOMATION_SYSTEM


def test_equipment_is_one_dataframe_indexed_by_static_id():
    assert category_kind(parse_attributes(LOAD)) == EQUIPMENT
    assert category_kind(parse_attributes(TAP_CHANGER)) == AUTOMATION_SYSTEM


def test_catalogue_merges_categories_and_their_models():
    categories = pd.DataFrame({"description": ["Standard load", "Tap changer"], "attribute": [LOAD, TAP_CHANGER]},
                              index=pd.Index(["Load", "TapChanger"], name="name"))
    models = pd.DataFrame({"description": ["b", "a", "c", "orphan"], "category": ["Load", "Load", "TapChanger", "New"]},
                          index=pd.Index(["LoadPQ", "LoadAlphaBeta", "TapChangerAutomationSystem", "Mystery"], name="name"))

    catalogue = {c["name"]: c for c in build_catalogue(categories, models)}

    assert list(catalogue) == ["Load", "New", "TapChanger"]
    assert [m["name"] for m in catalogue["Load"]["models"]] == ["LoadAlphaBeta", "LoadPQ"]
    assert (catalogue["Load"]["kind"], catalogue["TapChanger"]["kind"]) == (EQUIPMENT, AUTOMATION_SYSTEM)
    assert catalogue["New"]["models"] == [{"name": "Mystery", "description": "orphan"}]   # category not described
