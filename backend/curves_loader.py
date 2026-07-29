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

import pandas as pd

_NS = "http://www.rte-france.com/dynawo"


def read_crv_info(crv_path: str) -> dict[str, tuple[str, str]]:
    """Return {col_name: (model, variable)} from a .crv file."""
    try:
        root = ET.parse(crv_path).getroot()
        result: dict[str, tuple[str, str]] = {}
        for c in root.findall(f".//{{{_NS}}}curve"):
            model = c.get("model", "")
            variable = c.get("variable", "")
            result[f"{model}_{variable}"] = (model, variable)
        return result
    except Exception:
        return {}


def load_curves_csv(output_dir: str) -> pd.DataFrame | None:
    """Load curves/curves.csv from the given output directory, or None if not found."""
    csv_path = os.path.join(output_dir, "curves", "curves.csv")
    if not os.path.isfile(csv_path):
        return None
    try:
        return pd.read_csv(csv_path, sep=";")
    except Exception:
        return None


def curves_time_and_signals(df: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    """Return (time series, list of signal column names) from a curves DataFrame."""
    return df[df.columns[0]], list(df.columns[1:])
