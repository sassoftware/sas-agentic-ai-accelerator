# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The run lock is handed back by the run that holds it, and by no other.

A step that fails hands the lock of its run back: it copies the ledger
without that one row, drops the table, promotes the copy and saves it. The
macro that does it ran for runs that held no lock of their own as well, and
then it released nothing and rewrote the ledger all the same:

* a run that the fresh lock of another run refused - the saved file then held
  the lock row of the OTHER run, which a ledger that is loaded back from its
  file brings into the next run;
* RAG - Register Setup (or any step run again) after RAG - Load Vector Store
  had already written the ledger without the lock - seen on a live system: the
  failing step rewrote the three rows it found and said that the run lock was
  released;
* every run of RAG - Purge Documents, because its macro was told to run
  whatever the outcome: a purge that had gone through rewrote the ledger a
  second time, and a preview - which has to leave everything as it is -
  promoted and saved the ledger to take off the lock it had taken. Both said
  that the run lock was released so the run can be retried (seen live).

The macro now asks the ledger for the lock row of its own run and leaves the
table alone when it is not there. RAG - Purge Documents releases only after a
failure, like the other steps, and takes no lock for a preview.

Nothing here runs SAS: the step files are read, and the order of the
statements in the two macros is what is tested.
"""
import json
import pathlib
import re

import pytest

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"

# every step but RAG - Retrieve Context, which reads the collection only
RELEASING = {
    "RAG - Chunk Documents": "rgch", "RAG - Embed Chunks": "rgem",
    "RAG - Enrich Chunks": "rgen", "RAG - Extract Text": "rgex",
    "RAG - List Documents": "rgls", "RAG - Load Vector Store": "rgld",
    "RAG - Purge Documents": "rgpu", "RAG - Register Setup": "rgrg",
}


def _sas(name):
    return json.loads((STEPS / (name + ".step")).read_text(
        encoding="utf-8"))["templates"]["SAS"]


def _python(name):
    return _sas(name).split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _macro(name, macro):
    """One macro of a step without its comments, %macro to %mend."""
    found = re.search(r"%macro " + macro + r"\b.*?%mend " + macro + ";",
                      _sas(name), re.DOTALL)
    assert found, macro
    return re.sub(r"/\*.*?\*/", "", found.group(0), flags=re.DOTALL)


def _in_order(text, *markers):
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), \
        [marker for _position, marker in sorted(zip(positions, markers))]


def test_every_step_that_reads_or_writes_the_ledger_has_the_macro():
    for path in sorted(STEPS.glob("RAG - *.step")):
        code = json.loads(path.read_text(encoding="utf-8"))["templates"]["SAS"]
        assert ("_release;" in code) == (path.stem in RELEASING), path.stem


# ---------------------------------------------------------------------------
# the release
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(RELEASING))
def test_the_ledger_is_rewritten_only_for_the_lock_row_of_this_run(name):
    """The defect: nothing between the test for the table and the rewrite
    asked whether the run holds a lock at all."""
    prefix = RELEASING[name]
    release = _macro(name, "_%s_release" % prefix)
    own = ('set %sout.&_%s_ragProject._LEDGER(where=(doc_id = "__run_lock__" '
           'and run_id = "&_%s_runId."));' % (prefix, prefix, prefix))
    _in_order(release,
              "%if &failing. EQ 0 %then %return;",
              "%%if %%length(%%superq(_%s_runId)) EQ 0 %%then %%return;" % prefix,
              "%%if not %%sysfunc(exist(%sout.&_%s_ragProject._LEDGER)) %%then %%return;"
              % (prefix, prefix),
              # the ledger is asked for the row ...
              "%let held = ;", own, 'call symputx("held", run_id, "L");',
              # ... and without it nothing below runs
              "%if %length(&held.) EQ 0 %then %return;",
              "_LEDGER_R(replace=yes);",
              "droptable casdata=", "promote casdata=", "save casdata=",
              "the run lock was released so the run can be retried")
    assert release.count("%return;") == 5
    assert "%local failing held;" in release
    # the row is looked for the way the lock macro reads its own lock back,
    # and taken out by the same two conditions, negated
    assert ('where=(not (doc_id = "__run_lock__"\n'
            + " " * 33 + 'and run_id = "&_%s_runId.")));' % prefix) in release


@pytest.mark.parametrize("name", sorted(RELEASING))
def test_only_a_run_that_failed_hands_its_lock_back(name):
    """A run that went through keeps the lock for the steps after it, or has
    written its ledger without it. RAG - Purge Documents set the flag for
    every run."""
    prefix = RELEASING[name]
    release = _macro(name, "_%s_release" % prefix)
    assert re.findall(r"^\s*(.*%let failing = \d;)$", release, re.MULTILINE) == [
        "%let failing = 0;",
        "%%if &_%s_state. EQ fail %%then %%let failing = 1;" % prefix,
        "%%if &_%s_state. EQ run %%then %%if &_%s_rc. NE 0 %%then %%let failing = 1;"
        % (prefix, prefix)]


@pytest.mark.parametrize("name", sorted(RELEASING))
def test_the_note_is_written_once_and_only_behind_the_rewrite(name):
    sas = _sas(name)
    note = ('putLog "NOTE: %s: the run lock was released so the run can be '
            'retried";' % name)
    assert sas.count("the run lock was released") == 1
    release = _macro(name, "_%s_release" % RELEASING[name])
    assert release.index("save casdata=") < release.index(note)
    assert release.index("%if %length(&held.) EQ 0 %then %return;") \
        < release.index(note)


@pytest.mark.parametrize("name", ["RAG - List Documents", "RAG - Purge Documents"])
def test_a_run_another_lock_refused_has_taken_none(name):
    """The lock macro returns before it writes a row: the release then finds
    no row of this run, whose id the macro has set by then."""
    prefix = RELEASING[name]
    lock = _macro(name, "_%s_lock" % prefix)
    refused = lock.index("another ingestion run holds the lock on this ledger")
    _in_order(lock, "%%let _%s_runId = run-" % prefix,
              "%%let _%s_state = fail;" % prefix,
              "another ingestion run holds the lock on this ledger")
    assert lock.index("%return;", refused) < lock.index("(append=yes);")
    assert lock.index("%return;", refused) < lock.index("(promote=yes);")


# ---------------------------------------------------------------------------
# RAG - Purge Documents in preview mode
# ---------------------------------------------------------------------------
def test_a_preview_takes_no_lock_and_starts_no_ledger():
    lock = _macro("RAG - Purge Documents", "_rgpu_lock")
    _in_order(lock,
              # refused by the fresh lock of another run, as before
              "another ingestion run holds the lock on this ledger",
              "a stale run lock (held longer than 30 minutes) is ignored",
              # then out, before the table is asked, appended to or created
              "%if not %symexist(_rgpu_mode) %then %return;",
              "%if %superq(_rgpu_mode) NE purge %then %return;",
              "%_rgpu_ledger_types(", "(append=yes);", "droptable casdata=",
              "(promote=yes);")


def test_the_step_and_its_python_agree_on_what_a_preview_is():
    """Everything but the value purge. A run the SAS took for a purge and the
    Python for a preview would keep its lock: the ledger is then not
    rewritten, and a run that went through does not release."""
    python = _python("RAG - Purge Documents")
    assert 'dry_run = (P["_rgpu_mode"] or "preview") != "purge"' in python
    assert '"_rgpu_mode",' in python
    ui = json.loads(json.loads((STEPS / "RAG - Purge Documents.step").read_text(
        encoding="utf-8"))["ui"])
    assert ui["values"]["_rgpu_mode"]["value"] == "preview"

    def find(node):
        if isinstance(node, dict):
            if node.get("id") == "_rgpu_mode":
                return node
            node = list(node.values())
        if isinstance(node, list):
            for value in node:
                found = find(value)
                if found:
                    return found
        return None

    assert [item["value"] for item in find(ui)["items"]] == ["preview", "purge"]


def test_a_preview_leaves_the_ledger_as_it_is():
    """No lock, so nothing to hand back; and the swap keeps the table for
    the count the Python gives a preview."""
    sas = _sas("RAG - Purge Documents")
    python = _python("RAG - Purge Documents")
    assert "n_ledger = -1 if dry_run else cas_stage(" in python
    swap = _macro("RAG - Purge Documents", "_rgpu_swap")
    _in_order(swap, "%if &count. LT 0 %then %return;", "droptable casdata=")
    assert "%_rgpu_swap(_LEDGER, &_rgpu_nledger.);" in sas
    # Nothing else promotes or saves the ledger: the swap (which returns)
    # and the release (which finds no row of this run) hold the only two
    # pairs of statements that do, and the lock macro (not reached) the
    # only DATA steps that write to the table.
    code = re.sub(r"/\*.*?\*/", "", sas.split("\nsubmit;\n", 1)[0]
                  + sas.split("\nendsubmit;", 1)[1], flags=re.DOTALL)
    for statement in ("promote casdata=", "save casdata="):
        assert code.count(statement) == 2
        assert swap.count(statement) == 1
        assert _macro("RAG - Purge Documents", "_rgpu_release").count(statement) == 1
    lock = _macro("RAG - Purge Documents", "_rgpu_lock")
    written = re.findall(r"data rgpuout\.&_rgpu_ragProject\._LEDGER\((\w+)=yes\);", code)
    assert written == ["append", "promote"]
    assert all("_LEDGER(%s=yes);" % option in lock for option in written)
