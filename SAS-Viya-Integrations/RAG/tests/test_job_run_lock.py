# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The run lock of Ingest-Documents.sas is handed back, and only its own.

The job appended its lock row before its Python program looked for the lock
of another run, and nothing took the row out again unless the run went
through and stored a ledger. So every run that failed left a fresh lock row
behind - a run that the lock of another run refused, too, which then left a
second one - and the next run was answered

    another ingestion run appears active for this ledger (run lock held) -
    retry later

for 30 minutes, with nothing running. Since a failed run of the job is shown
as failed, the retry is what follows it.

The row could not be handed back either: its run_id was empty, so nothing
told it from the lock of any other run.

Now the job makes the id of the run in SAS, the way RAG - List Documents
does, and writes it into the lock row; it looks for the fresh lock of another
run BEFORE it appends, and a run that is refused takes none; and a run that
failed takes out the lock row that carries its id, the way the RAG steps do.
The newest lock row decides whether a ledger is locked, because the row of a
run that crashed can stand beside the row of a run that is at work.

A ledger can hold lock rows of three writers: the job as it was (run_id
empty), the job as it is and the RAG steps (run_id set), in a CHAR ledger
(values padded with blanks) or a VARCHAR one. The Python of the job is run on
such ledgers here; the SAS around it is read, and what is tested of it is the
order of its statements. Nothing here runs SAS.
"""
import ast
import json
import os
import pathlib
import re
import textwrap
import time

import pytest

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"
JOB = RAG.parent / "RAG-Ingestion" / "Ingest-Documents.sas"

REFUSED = ("another ingestion run appears active for this ledger "
           "(run lock held) - retry later")
OWN = "run-1791812345-0123456789"


def _job():
    return JOB.read_text(encoding="utf-8").replace("\r\n", "\n")


def _python():
    return _job().split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _after_python():
    return _job().split("\nendsubmit;", 1)[1]


def _macro(name):
    """One macro of the job without its comments, %macro to %mend."""
    found = re.search(r"%macro " + name + r"\b.*?%mend " + name + ";",
                      _job(), re.DOTALL)
    assert found, name
    return re.sub(r"/\*.*?\*/", "", found.group(0), flags=re.DOTALL)


def _step_macro(step, name):
    sas = json.loads((STEPS / (step + ".step")).read_text(
        encoding="utf-8"))["templates"]["SAS"]
    found = re.search(r"%macro " + name + r"\b.*?%mend " + name + ";", sas, re.DOTALL)
    assert found, name
    return re.sub(r"/\*.*?\*/", "", found.group(0), flags=re.DOTALL)


def _in_order(text, *markers):
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), \
        [marker for _position, marker in sorted(zip(positions, markers))]


# ---------------------------------------------------------------------------
# the lock row says whose it is
# ---------------------------------------------------------------------------
def test_the_lock_row_of_the_job_carries_the_id_of_the_run():
    """The defect: `run_id = '';` - a row no run could call its own."""
    export = _macro("_rag_export_ledger")
    append = export[export.index("data _ragcl.&ledgerTable.(append=yes);"):]
    append = append[:append.index("run;")]
    assert "doc_id = '__run_lock__';" in append
    assert 'run_id = "&_rag_runId.";' in append
    assert "run_id = '';" not in _job()
    # the id exists before the row is written, and a run id is made once
    _in_order(export, "%let _rag_runId = run-", "data _ragcl.&ledgerTable.(append=yes);")
    assert _job().count("%let _rag_runId = run-") == 1


def test_the_python_of_the_job_reads_the_id_the_lock_row_carries():
    python = _python()
    assert python.count('SAS.symget("_rag_runId")') == 1
    # one id for the lock row, the milestone, the ledger and the run history
    assert 'run_id = str(SAS.symget("_rag_runId") or "").strip()' in python
    assert len(re.findall(r"^ +run_id = ", python, re.MULTILINE)) == 2


# ---------------------------------------------------------------------------
# a run that the lock of another run refuses takes none
# ---------------------------------------------------------------------------
def test_the_job_looks_for_the_lock_of_another_run_before_it_appends_its_own():
    """The defect: the append stood in front of every look at another lock,
    so a run that was refused had already written a row."""
    export = _macro("_rag_export_ledger")
    _in_order(export,
              "%_rag_reload_saved(_ragcas, &ledgerCaslib., &ledgerTable., ",
              "rag_ledger_in.json",
              # every lock row is read, and the newest one is kept
              'set _ragcl.&ledgerTable.(where=(doc_id = "__run_lock__"));',
              "taken = input(mtime, ?? best32.);",
              "if taken > newest then do;",
              'call symputx("_rag_lockHeld", newest, "G");',
              # fresh: the run fails here, with the line the log ends on
              "%if %sysevalf(&_rag_now. - &_rag_lockHeld. < 1800) %then %do;",
              "%let _rag_state = fail;",
              "%let _rag_error = " + REFUSED + ";",
              # and only a run that is still one asks for types and appends
              "%if &_rag_state. EQ run %then %if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;",
              "%_rag_ledger_types(_ragcas, &ledgerCaslib., &ledgerTable., char);",
              "data _ragcl.&ledgerTable.(append=yes);",
              "%let _rag_lockTaken = 1;",
              "rag_ledger_lock.json",
              "libname _ragcl clear;")
    assert export.count("%let _rag_state = fail;") == 1
    assert export.count("(append=yes);") == 1
    # the append and the second export stand in ONE group, the one a run
    # that was refused does not enter
    group = export.split("%if &_rag_state. EQ run %then %if ", 1)[1]
    group = group[:group.index("    %end;\n    libname _ragcl clear;")]
    assert "(append=yes);" in group and "rag_ledger_lock.json" in group
    assert "%end;" not in group
    # the three variables of the lock start empty with every run of the
    # program, before the macro that sets them
    job = _job()
    for start in ("%let _rag_runId = ;", "%let _rag_lockHeld = ;",
                  "%let _rag_lockTaken = 0;"):
        assert job.count(start) == 1
        assert job.index(start) < job.index("%macro _rag_reload_saved(")


def test_the_job_refuses_by_the_age_the_steps_refuse_by():
    """The same comparison as in RAG - List Documents: now minus the time
    the lock was taken, under 1800 seconds, both read from the SAS clock."""
    lock = _step_macro("RAG - List Documents", "_rgls_lock")
    assert "%if %sysevalf(&_rgls_now. - &_rgls_lockHeld. < 1800) %then %do;" in lock
    assert "%let _rgls_now = %sysevalf(%sysfunc(datetime()) - 315619200);" in lock
    export = _macro("_rag_export_ledger")
    assert "%let _rag_now = %sysevalf(%sysfunc(datetime()) - 315619200);" in export
    # and the row is stamped with the same clock
    assert "mtime = strip(put(datetime() - 315619200, best20.));" in export


class _Sas:
    def __init__(self, macros):
        self.macros = dict(macros)
        self.log = []

    def symget(self, name):
        return self.macros.get(name, "")

    def symput(self, name, value):
        self.macros[name] = value

    def logMessage(self, message, *_):
        self.log.append(message)

    @property
    def workpath(self):
        raise AssertionError("the program went on past its early returns")


def test_a_refused_run_says_so_as_the_milestone_of_a_failed_run():
    """SAS refuses, the Python program says why and does nothing else: the
    milestone is what the Prompt Builder reads a failed run by, and the
    message is the one the last line of the log is put together from."""
    said = re.search(r"%let _rag_error = (another ingestion run [^;]*);", _job())
    assert said and said.group(1) == REFUSED
    # text the macro processor and SAS.logMessage leave as it is
    assert not set("'\"%&;,") & set(REFUSED)
    assert REFUSED.count("(") == REFUSED.count(")")
    sas = _Sas({"_rag_state": "fail", "_rag_error": REFUSED, "_rag_rc": "1",
                "_rag_runId": OWN})
    exec(compile(_python(), "Ingest-Documents.sas", "exec"), {"SAS": sas})
    assert sas.log == ["RAGINGEST Ingestion failed: " + REFUSED]
    assert sas.macros["_rag_rc"] == "1" and sas.macros["_rag_error"] == REFUSED


# ---------------------------------------------------------------------------
# the Python program and the lock rows of a ledger, run
# ---------------------------------------------------------------------------
NOW = time.time()
DOC = {"doc_id": "d1", "mtime": "1791800000", "status": "ingested",
       "config_hash": "cfg-1", "run_id": "run-1791700000-0000000001"}


def _lock(age, run_id="", padded=False):
    """A lock row taken `age` seconds ago. The job as it was wrote an empty
    run_id; a CHAR ledger pads every value with blanks."""
    row = {"doc_id": "__run_lock__", "mtime": "%.3f" % (NOW - age),
           "status": "lock", "run_id": run_id, "config_hash": ""}
    if padded:
        row = {"doc_id": row["doc_id"], "mtime": row["mtime"].ljust(32),
               "status": row["status"].ljust(12), "run_id": run_id.ljust(32),
               "config_hash": " " * 32}
    return row


def _look_for_another_lock(ledger):
    """The lines of the job's Python that sort the exported ledger and look
    for the lock of another run, run on `ledger`. Returns (the message the
    run is refused with or "", the milestones, the document rows)."""
    python = _python()
    start = python.index("        real_rows = [r for r in ledger")
    end = python.index("        # ---- the lock of THIS run, read back")
    block = textwrap.dedent(python[start:end])
    said = []
    scope = {"time": time, "ledger": [dict(row) for row in ledger],
             "cfg_hash": "cfg-1", "M": said.append}
    try:
        exec(compile(block, "Ingest-Documents.sas", "exec"), scope)
    except RuntimeError as error:
        return str(error), said, scope.get("real_rows")
    return "", said, scope["real_rows"]


STALE_NOTE = ["stale run lock ignored (held > 30 minutes)"]


@pytest.mark.parametrize("padded", [False, True], ids=["varchar", "char"])
@pytest.mark.parametrize("run_id", ["", "run-1791812000-0000000344"],
                         ids=["the job as it was", "a run with an id"])
def test_a_fresh_lock_refuses_whoever_wrote_it(run_id, padded):
    refused, said, _rows = _look_for_another_lock([DOC, _lock(60, run_id, padded)])
    assert refused == REFUSED and said == []


@pytest.mark.parametrize("padded", [False, True], ids=["varchar", "char"])
@pytest.mark.parametrize("run_id", ["", "run-1791812000-0000000344"],
                         ids=["the job as it was", "a run with an id"])
def test_a_stale_lock_is_ignored_whoever_wrote_it(run_id, padded):
    refused, said, rows = _look_for_another_lock([DOC, _lock(1801, run_id, padded)])
    assert refused == "" and said == STALE_NOTE
    assert rows == [DOC]


def test_the_newest_lock_decides_not_the_first_row_of_the_export():
    """The defect: lock_rows[0]. The lock row a run of the job as it was
    left behind hours ago, in front of the row of a flow that started a
    minute ago, let the job in beside that flow."""
    crashed = _lock(7200)                                     # the old job
    at_work = _lock(60, "run-1791812000-0000000344")          # a flow
    for ledger in ([DOC, crashed, at_work], [DOC, at_work, crashed],
                   [crashed, DOC, at_work]):
        refused, said, _rows = _look_for_another_lock(ledger)
        assert refused == REFUSED and said == [], ledger
    # two rows that are both stale are both ignored, in one line
    refused, said, rows = _look_for_another_lock(
        [DOC, crashed, _lock(1900, "run-1791810000-0000000001")])
    assert refused == "" and said == STALE_NOTE and rows == [DOC]


def test_a_lock_row_whose_time_cannot_be_read_does_not_hide_one_that_can():
    unreadable = dict(_lock(0), mtime="not a number")
    refused, said, _rows = _look_for_another_lock([DOC, unreadable])
    assert refused == "" and said == STALE_NOTE        # as before: stale
    refused, said, _rows = _look_for_another_lock([DOC, unreadable, _lock(30)])
    assert refused == REFUSED


def test_a_ledger_without_a_lock_row_is_read_as_before():
    refused, said, rows = _look_for_another_lock([DOC])
    assert refused == "" and said == [] and rows == [DOC]


def _read_back(tmp_path, before, after):
    """The job's read-back of its own lock, run on the two exports."""
    python = _python()
    start = python.index("        # ---- the lock of THIS run, read back")
    end = python.index("        # run-<epoch>-<digits>:")
    ledger_path = tmp_path / "rag_ledger_in.json"
    lock_path = tmp_path / "rag_ledger_lock.json"
    ledger_path.write_text(json.dumps(before), encoding="utf-8")
    lock_path.write_text(json.dumps(after), encoding="utf-8")

    class _Names:
        @staticmethod
        def symget(name):
            return {"ledgerCaslib": "Public", "ledgerTable": "HR_LEDGER"}[name]

    scope = {"os": os, "json": json, "SAS": _Names,
             "WORKPATH": str(tmp_path) + os.sep, "ledger_path": str(ledger_path),
             "lock_rows": [row for row in before
                           if row.get("doc_id") == "__run_lock__"]}
    try:
        exec(compile(textwrap.dedent(python[start:end]), "Ingest-Documents.sas",
                     "exec"), scope)
    except RuntimeError as error:
        return str(error)
    return ""


@pytest.mark.parametrize("padded", [False, True], ids=["varchar", "char"])
def test_the_read_back_finds_a_lock_row_with_an_id_beside_one_without(tmp_path, padded):
    """A stale row of the job as it was, and the row this run appended: the
    read-back counts lock rows by the time they were taken, so it reads a
    row with a run_id and a row without one alike."""
    old = _lock(7200)
    ours = _lock(0, OWN, padded)
    assert _read_back(tmp_path, [DOC, old], [DOC, old, ours]) == ""
    assert _read_back(tmp_path, [DOC], [DOC, ours]) == ""
    # the append was refused: the stale row of the old job is not this lock
    assert "refusing to ingest unguarded" in _read_back(tmp_path, [DOC, old], [DOC, old])


# ---------------------------------------------------------------------------
# a run that failed hands its lock back
# ---------------------------------------------------------------------------
def test_the_job_has_a_release_and_runs_it_behind_the_store():
    """The defect: there was none. A ledger was written by a run that went
    through, and by nothing else."""
    after = _after_python()
    assert after.count("%_rag_release;") == 1
    _in_order(after, "proc python terminate;", "%_rag_store_ledger;",
              "%macro _rag_release;", "%mend _rag_release;\n%_rag_release;",
              'line = "ERROR: RAG ingestion failed: " || text;',
              "%macro _rag_job_state;")
    # nothing in front of the Python program hands a lock back
    assert "_rag_release" not in re.sub(
        r"/\*.*?\*/", "", _job().split("\nsubmit;\n", 1)[0], flags=re.DOTALL)


def test_only_a_run_that_failed_and_appended_a_row_looks_for_it():
    release = _macro("_rag_release")
    own = ('set _ragcl.&ledgerTable.(where=(doc_id = "__run_lock__" '
           'and run_id = "&_rag_runId."));')
    _in_order(release,
              # a run that went through has stored a ledger without lock rows
              "%if &_rag_rc. EQ 0 %then %return;",
              # refused, a failed reload, a first run: no row, no CAS session
              "%if &_rag_lockTaken. NE 1 %then %return;",
              # an empty id would match the lock row of the job as it was
              "%if %length(%superq(_rag_runId)) EQ 0 %then %return;",
              "cas _ragcas3;",
              "%if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;",
              # the ledger is asked for the row of this run ...
              "%let held = ;", own, 'call symputx("held", run_id, "L");',
              # ... and is rewritten only when it holds it
              "%if %length(&held.) GT 0 %then %do;",
              "data _ragcl.&ledgerTable._R(replace=yes);",
              "droptable casdata=", "promote casdata=", "save casdata=",
              "the run lock was released so the run can be retried",
              "libname _ragcl clear;", "cas _ragcas3 terminate;")
    assert release.count("%return;") == 3
    assert "%local held;" in release
    # the session is closed again on every way through the macro: nothing
    # returns once it is open
    assert "%return;" not in release[release.index("cas _ragcas3;"):]


def test_the_row_taken_out_is_the_row_of_this_run_and_no_other():
    """The copy keeps every row but the one with the lock marker AND the id
    of this run: the lock of another run stays, and so does a lock row with
    an empty run_id, which the job as it was has written."""
    release = _macro("_rag_release")
    kept = ('set _ragcl.&ledgerTable.(where=(not (doc_id = "__run_lock__"\n'
            + " " * 41 + 'and run_id = "&_rag_runId.")));')
    assert kept in release
    assert release.count('run_id = "&_rag_runId."') == 2
    assert release.count("__run_lock__") == 2
    # the copy declares nothing, so the ledger keeps its column types
    copy = release[release.index("data _ragcl.&ledgerTable._R(replace=yes);"):]
    copy = copy[:copy.index("run;")]
    assert not re.search(r"\blength\b", copy, re.IGNORECASE)
    assert 'promote casdata="&ledgerTable._R" casout="&ledgerTable.";' in release
    assert 'save casdata="&ledgerTable." casout="&ledgerTable." replace;' in release


def test_the_job_takes_the_row_out_the_way_the_steps_do():
    """One construction: the statements between the look for the row and the
    NOTE, with the names of the job put in place of the names of a step."""
    step = _step_macro("RAG - List Documents", "_rgls_release")

    def statements(text, first, last):
        text = text[text.index(first):text.index(last)]
        return [" ".join(statement.split()) for statement in text.split(";")
                if statement.strip() and not statement.strip().startswith("%")]

    of_the_step = statements(step, "%let held = ;", "data _null_;\n        putLog")
    of_the_job = statements(_macro("_rag_release"), "%let held = ;",
                            "data _null_;\n                putLog")
    renamed = [statement.replace("rglsout.&_rgls_ragProject._LEDGER", "_ragcl.&ledgerTable.")
               .replace('"&_rgls_ragProject._LEDGER', '"&ledgerTable.')
               .replace("&_rgls_tablesCaslib.", "&ledgerCaslib.")
               .replace("&_rgls_runId.", "&_rag_runId.")
               .replace("sessref=rglscas", "sessref=_ragcas3")
               for statement in of_the_step]
    assert renamed == of_the_job
    # the look, the copy, and the four statements of PROC CASUTIL with its quit
    assert [statement.split(" ", 1)[0] for statement in of_the_job] == [
        "data", "set", "call", "run", "data", "set", "run",
        "proc", "droptable", "promote", "save", "quit"]


def test_the_note_is_written_once_and_only_behind_the_rewrite():
    job = _job()
    note = 'putLog "NOTE: RAG ingestion: the run lock was released so the run can be retried";'
    assert job.count(note) == 1
    code = re.sub(r"/\*.*?\*/", "", job, flags=re.DOTALL)
    assert code.count("the run lock was released") == 1
    release = _macro("_rag_release")
    assert release.index("save casdata=") < release.index(note)


def test_no_other_statement_of_the_job_writes_the_ledger():
    """The lock is appended in one place, and the table is replaced in two:
    by a run that went through, and by a run that failed with its own lock
    row in the ledger."""
    job = _job()
    code = re.sub(r"/\*.*?\*/", "", job.split("\nsubmit;\n", 1)[0]
                  + job.split("\nendsubmit;", 1)[1], flags=re.DOTALL)
    for statement in ("droptable casdata=", "promote casdata=", "save casdata="):
        assert code.count(statement) == 2
        assert _macro("_rag_store_ledger").count(statement) == 1
        assert _macro("_rag_release").count(statement) == 1
    assert re.findall(r"data _ragcl\.&ledgerTable\.(\w*)\((\w+)=yes\);", code) == [
        ("", "append"), ("_R", "replace")]


def test_every_way_the_program_fails_leaves_the_run_a_failed_one():
    """The release asks _rag_rc. One statement sets it to 0, the last one of
    a run that did all its work; every other way out leaves it at 1 - the
    two early returns and the handler every exception ends in."""
    job = _job()
    assert job.index("%let _rag_rc = 1;") < job.index("proc python restart;")
    program = _python()
    assert program.count('SAS.symput("_rag_rc"') == 1
    main = ast.parse(program).body[0]
    handler = main.body[-1]
    assert isinstance(handler, ast.Try)
    assert ast.unparse(handler.body[-1]) == "SAS.symput('_rag_rc', '0')"
