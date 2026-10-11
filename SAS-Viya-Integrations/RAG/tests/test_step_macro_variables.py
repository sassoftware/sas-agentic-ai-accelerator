# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Every macro variable a RAG step resolves has a value in every run.

A step that skips, or whose Python program stops early, still runs the SAS
behind `endsubmit;`. A macro variable that only the Python program sets is
then resolved without ever having been set, and SAS writes

    WARNING: Apparent symbolic reference _RGLD_NRUNS not resolved.

That was found on a live system in a skipped RAG - Load Vector Store: the two
counts of the run history were handed to the swap macro as
`%_rgld_swap(_RUNS, &_rgld_nruns.)`, and only the Python set them, while the
count of the ledger beside them started at 0.

Nothing here runs SAS. The test reads the step files: what the SAS around the
Python resolves, and what gives each of those names a value - a %let, a
%global or %local, a symputx, the uuidmac of the CAS session, a parameter of a
macro, or a control of the step, which SAS Studio hands over as a macro
variable.
"""
import json
import pathlib
import re

import pytest

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"

# set by SAS itself
AUTOMATIC = {"syscc", "sysjobid"}


def _step(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _around_python(sas):
    head, rest = sas.split("\nsubmit;\n", 1)
    return head + "\n" + rest.split("\nendsubmit;", 1)[1]


def _ids(node, found):
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            found.add(node["id"].lower())
        node = list(node.values())
    if isinstance(node, list):
        for value in node:
            _ids(value, found)
    return found


def _given_a_value(step):
    """The names something outside the Python program gives a value."""
    sas = _around_python(step["templates"]["SAS"])
    names = set(re.findall(r"%let\s+(\w+)\s*=", sas))
    for group in re.findall(r"%(?:global|local)\s+([^;]+);", sas):
        names.update(group.split())
    names.update(re.findall(r'symputx\("(\w+)"', sas))
    names.update(re.findall(r"uuidmac=(\w+)", sas))
    for parameters in re.findall(r"%macro\s+\w+\(([^)]*)\)", sas):
        names.update(name.split("=")[0].strip() for name in parameters.split(","))
    names = {name.lower() for name in names} | AUTOMATIC
    # the controls of the step and its ports
    _ids(json.loads(step["ui"]), names)
    _ids(step.get("flowMetadata"), names)
    return names


def _resolved(step):
    sas = re.sub(r"/\*.*?\*/", "", _around_python(step["templates"]["SAS"]),
                 flags=re.DOTALL)
    return {name.lower() for name in re.findall(r"&+(\w+)", sas)}


@pytest.mark.parametrize("path", sorted(STEPS.glob("RAG - *.step")),
                         ids=lambda path: path.stem)
def test_no_step_resolves_a_macro_variable_only_its_python_sets(path):
    step = _step(path)
    missing = sorted(_resolved(step) - _given_a_value(step))
    assert not missing, missing


def test_the_counts_of_the_run_history_start_like_the_count_of_the_ledger():
    """The case that was found: a skipped step returns from its Python at
    once, and the calls of the swap macro still resolve all three counts."""
    sas = _step(STEPS / "RAG - Load Vector Store.step")["templates"]["SAS"]
    before = sas.split("\nproc python restart;", 1)[0]
    for count in ("_rgld_nledger", "_rgld_nruns", "_rgld_nevents"):
        assert "%%let %s = 0;" % count in before, count
    after = sas.split("\nendsubmit;", 1)[1]
    assert "%_rgld_swap(_RUNS, &_rgld_nruns.);" in after
    assert "%_rgld_swap(_DOC_EVENTS, &_rgld_nevents.);" in after
