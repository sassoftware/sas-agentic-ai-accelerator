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
# a ledger that holds more than one lock row
#
# A run that crashes leaves its lock row behind, and it stays until a run that
# goes through writes the ledger: after 30 minutes the next run ignores it and
# appends its own beside it. Whoever looks at that ledger then finds two lock
# rows - and took one. The lock macro kept the time of the row its DATA step
# read last, the Python of the steps the first row CAS returned: with the
# stale row in that place, the fresh lock of the run at work was not seen.
# The rows can be of three writers: the job Ingest-Documents.sas as it was
# before 2.3.0 (run_id empty), the job as it is and the steps (run_id set).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["RAG - List Documents", "RAG - Purge Documents"])
def test_the_lock_macro_keeps_the_newest_lock_row(name):
    prefix = RELEASING[name]
    lock = _macro(name, "_%s_lock" % prefix)
    look = lock[lock.index('set %sout.&_%s_ragProject._LEDGER(where=(doc_id = '
                           '"__run_lock__"));' % (prefix, prefix)):]
    look = look[:look.index("run;")]
    # the defect: the time of whichever row came last
    assert 'call symputx("_%s_lockHeld", mtime, "G");' % prefix not in lock
    _in_order(look, "retain newest;", "taken = input(mtime, ?? best32.);",
              "if taken > newest then do;", "newest = taken;",
              'call symputx("_%s_lockHeld", newest, "G");' % prefix, "end;")
    # what the macro then does with it is what it did
    _in_order(lock, "retain newest;",
              "%%if %%length(&_%s_lockHeld.) GT 0 %%then %%do;" % prefix,
              "%%if %%sysevalf(&_%s_now. - &_%s_lockHeld. < 1800) %%then %%do;"
              % (prefix, prefix),
              "another ingestion run holds the lock on this ledger",
              "a stale run lock (held longer than 30 minutes) is ignored")


READING = ["RAG - List Documents", "RAG - Load Vector Store", "RAG - Purge Documents"]
OWN = "run-1791812345-0123456789"
HELD = ("another ingestion run holds the lock on this ledger - wait for it to "
        "finish, or retry in 30 minutes if it crashed")
STALE = ["stale run lock ignored (held longer than 30 minutes)"]


def _lock_row(age, run_id=""):
    import time
    return {"doc_id": "__run_lock__", "mtime": "%.3f" % (time.time() - age),
            "status": "lock", "run_id": run_id}


def _look(name, ledger):
    """The lines of a step's Python that look for the lock of another run,
    run on a ledger the step has read (its values already without the
    blanks of a CHAR column). Returns (the refusal or "", the milestones)."""
    import textwrap
    import time

    python = _python(name)
    start = python.index('        real_rows = [r for r in ledger if r.get("doc_id")')
    last = '            M("stale run lock ignored (held longer than 30 minutes)")\n'
    block = textwrap.dedent(python[start:python.index(last) + len(last)])
    said = []
    scope = {"time": time, "ledger": ledger, "own_run_id": OWN, "M": said.append}
    try:
        exec(compile(block, name, "exec"), scope)
    except RuntimeError as error:
        return str(error), said
    return "", said


def test_the_steps_that_look_for_another_lock_are_these_three():
    for name in sorted(RELEASING):
        assert ("lock_rows" in _python(name)) == (name in READING), name
    assert len({_python(name)[_python(name).index("        if lock_rows:"):]
                .split("M(\"stale run lock ignored", 1)[0] for name in READING}) == 1


@pytest.mark.parametrize("name", READING)
def test_a_stale_lock_row_does_not_hide_a_fresh_one(name):
    """The defect: lock_rows[0]. The row of the job as it was (no run_id),
    left hours ago, in front of the row of a run that started a minute ago."""
    crashed = _lock_row(7200)
    at_work = _lock_row(60, "run-1791812000-0000000344")
    for ledger in ([crashed, at_work], [at_work, crashed]):
        assert _look(name, ledger) == (HELD, [])
    # both stale: ignored, and said once
    assert _look(name, [crashed, _lock_row(1900, "run-1791810000-0000000001")]) \
        == ("", STALE)
    # a time that cannot be read is stale, as before, and hides nothing
    unreadable = dict(_lock_row(0), mtime="")
    assert _look(name, [unreadable]) == ("", STALE)
    assert _look(name, [dict(unreadable, mtime="x"), at_work]) == (HELD, [])


@pytest.mark.parametrize("name", READING)
@pytest.mark.parametrize("run_id", ["", "run-1791812000-0000000344"],
                         ids=["the job as it was", "a run with an id"])
def test_the_lock_row_of_the_job_is_the_lock_of_another_run(name, run_id):
    """With or without an id: fresh it refuses the step, stale it is
    ignored. The lock row of the step's own run is neither."""
    assert _look(name, [_lock_row(60, run_id)]) == (HELD, [])
    assert _look(name, [_lock_row(1801, run_id)]) == ("", STALE)
    assert _look(name, [_lock_row(60, OWN)]) == ("", [])
    assert _look(name, [_lock_row(60, OWN), _lock_row(5, run_id)]) == (HELD, [])


def test_the_about_tab_of_the_three_steps_says_which_lock_row_decides():
    for path in sorted(STEPS.glob("RAG - *.step")):
        ui = json.loads(path.read_text(encoding="utf-8"))["ui"]
        # the newest entry of the changelog: from the first version line of
        # the day to the next version line
        newest = ui.split("(11OCT2026)", 1)[1].split("* Version: ", 1)[0]
        assert ("Where the ledger holds more than one lock row, the newest one "
                "decides whether another run holds the lock" in newest) \
            == (path.stem in READING), path.stem


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


def test_the_about_tab_does_not_say_a_preview_keeps_an_ingestion_out():
    """It said that neither a purge nor a preview runs while a flow or the
    job is writing the ledger. A preview is refused by a fresh lock and takes
    none (the test above), so an ingestion can start after that look - which
    is what the description says now, as the guide does."""
    ui = json.loads(json.loads((STEPS / "RAG - Purge Documents.step").read_text(
        encoding="utf-8"))["ui"])
    about = json.dumps(ui, ensure_ascii=False)
    assert "neither runs while" not in about
    assert ("A preview only reads: it takes no run lock and does not write the "
            "ledger. It is still refused while another run holds a lock that is "
            "less than 30 minutes old, and an ingestion can start while a "
            "preview reads.") in about
    # what is said of a purge is what the lock macro does: refused by a
    # fresh lock, and a lock of its own behind that
    assert ("A purge takes the same run lock as an ingestion: it is refused "
            "while a flow or the scheduled job holds a fresh lock on the same "
            "ledger, and they are refused while it holds its own.") in about
    lock = _macro("RAG - Purge Documents", "_rgpu_lock")
    _in_order(lock, "another ingestion run holds the lock on this ledger",
              "%if %superq(_rgpu_mode) NE purge %then %return;", "(append=yes);")


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
