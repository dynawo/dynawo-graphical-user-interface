#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""A Dynawo install for the tests, simulating nothing: dynawo.sh answers
`version`, the ddb describes a few libraries, and updateXML/update.py applies
one "ticket" (it adds a connect to every .dyd, as Dynawo's ticket 4223 does
for NodeFaults)."""

import os
from pathlib import Path

NS = "http://www.rte-france.com/dynawo"

_DESCS = {
    "LoadAlphaBeta": (["load_terminal", "load_PPu"], ["load_alpha", "load_beta"]),
    "DYNModelOmegaRef": ([], ["nbGen"]),
}

_UPDATE = '''import argparse, glob, os, shutil, sys
parser = argparse.ArgumentParser()
for option in ("--job", "--origin", "--version", "-o"):
    parser.add_argument(option)
parser.add_argument("--log", action="store_true")
args = parser.parse_args()
if not os.path.isfile(args.job):
    sys.exit(f"{args.job} not found")
out = os.path.join(args.o, os.path.splitext(os.path.basename(args.job))[0])
os.makedirs(out)
for path in glob.glob(os.path.join(os.path.dirname(args.job), "*")):
    if path.endswith((".jobs", ".dyd", ".par", ".crv")):
        shutil.copy(path, out)
for dyd in glob.glob(os.path.join(out, "*.dyd")):
    with open(dyd) as fh:
        text = fh.read()
    if "fault_switchOffSignal1" not in text:
        text = text.replace("</dyn:dynamicModelsArchitecture>",
                            '<dyn:connect id1="F" var1="fault_switchOffSignal1" id2="NETWORK" var2="B_switchOff"/>'
                            "</dyn:dynamicModelsArchitecture>")
    with open(dyd, "w") as fh:
        fh.write(text)
with open(os.path.join(out, "applied_tickets.log.1"), "w") as fh:
    fh.write("4223\\n")
print("Apply ticket 4223")
'''


def make_fake_dynawo(root: Path, version: str = "2.0.0", scripts: tuple[str, ...] = ("2.0.0.1",)) -> str:
    """A Dynawo install in `root`; returns its dynawo.sh. `scripts` are the
    versions of the update scripts its update tool holds."""
    root.mkdir(parents=True, exist_ok=True)
    exe = root / "dynawo.sh"
    exe.write_text(f'#!/bin/sh\n[ "$1" = version ] && echo "{version} (rev:test)"\nexit 0\n')
    os.chmod(exe, 0o755)
    (root / "lib").mkdir(exist_ok=True)
    (root / "lib" / "dynawo_SolverIDA.so").write_bytes(b"")
    (root / "ddb").mkdir(exist_ok=True)
    for lib, (variables, parameters) in _DESCS.items():
        body = "".join(f'<variable name="{v}" valueType="DOUBLE"/>' for v in variables)
        body += "".join(f'<parameter name="{p}" valueType="DOUBLE"/>' for p in parameters)
        (root / "ddb" / f"{lib}.desc.xml").write_text(f'<model xmlns="{NS}"><elements>{body}</elements></model>')
    tool = root / "sbin" / "updateXML"
    tool.mkdir(parents=True, exist_ok=True)
    (tool / "update.py").write_text(_UPDATE)
    for script in scripts:
        (tool / f"update{script}.py").write_text("")
    return str(exe)
