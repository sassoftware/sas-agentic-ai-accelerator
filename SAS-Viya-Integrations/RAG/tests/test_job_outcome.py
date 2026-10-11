# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a run of Ingest-Documents.sas leaves for whoever reads its outcome.

The program never raises a SAS error: an aborted session never sends SAS Job
Execution its completion handshake. So it ends normally whatever happened,
and two readers have to be told otherwise.

The Prompt Builder (Launch ingestion) takes a run for failed when the state
of the job is not `completed`, or when one of the milestones - the lines
SAS.logMessage writes as `NOTE: Python-Subprocess - RAGINGEST ...` - begins
with `Ingestion failed`. A schedule reads the state of the job and nothing
else.

A saved ledger that could not be loaded left neither. The Python program
returned before its first milestone, and the reload restores SYSCC on purpose,
so the job ended as completed with the reason only in its last line: the
Builder showed "Run finished", for a run that had done nothing. The same held
for a compute context without the requests package.

Every way the program ends early now writes the milestone, and a run that
failed sets SYSCC as the last statement of the program - for every failure,
also the ones that did write the milestone before, which ended as completed
jobs.

Nothing here runs SAS. The Python of the job is run against a stub of SAS;
the SAS around it is read. The reading of the Builder is rebuilt from the two
expressions its source holds, and the test that reads the source says so when
they change.
"""
import ast
import pathlib
import re
import sys

import pytest

from rag_core import registration

RAG = pathlib.Path(__file__).resolve().parents[1]
REPO = RAG.parents[1]
JOBS = REPO / "SAS-Viya-Integrations" / "RAG-Ingestion"
JOB = JOBS / "Ingest-Documents.sas"
BUILDER = REPO / "LLM-Prompt-Builder" / "src"

RELOAD_ERROR = ("the saved table Public.HR_LEDGER could not be loaded into "
                "memory (CAS messages above) - stopped, because an empty table "
                "would re-embed the corpus and overwrite the saved file")


def _job():
    return JOB.read_text(encoding="utf-8").replace("\r\n", "\n")


def _python():
    return _job().split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _after_python():
    return _job().split("\nendsubmit;", 1)[1]


def _macro(sas, name):
    return sas[sas.index("%macro " + name):sas.index("%mend " + name + ";")]


# ---------------------------------------------------------------------------
# the Prompt Builder's reading of a run
# ---------------------------------------------------------------------------
def test_the_builder_reads_a_run_the_way_these_tests_rebuild_it():
    """The Builder is not run here. Its two expressions are in its source as
    they are rebuilt below; when they change, this is the test that says so."""
    api = (BUILDER / "api" / "jobexec-api.ts").read_text(encoding="utf-8")
    page = (BUILDER / "objects" / "rag-builder.ts").read_text(encoding="utf-8")
    assert r"const PROGRESS_PREFIX = /NOTE: Python-Subprocess\s*-\s?(.*)$/;" in api
    assert r"message.replace(/^RAGINGEST\s+/, '')" in page
    assert ("const failed = state !== 'completed' || "
            r"lines.some((line) => /^Ingestion failed\b/i.test(line));") in page


def _builder_says_failed(state, messages):
    """ingestionOutcome of rag-builder.ts, for what SAS.logMessage wrote."""
    log = ["NOTE: Python-Subprocess - " + message for message in messages]
    milestones = [found.group(1) for found in (
        re.search(r"NOTE: Python-Subprocess\s*-\s?(.*)$", line) for line in log)
        if found]
    lines = [re.sub(r"^RAGINGEST\s+", "", milestone) for milestone in milestones]
    return state != "completed" or any(
        re.match(r"Ingestion failed\b", line, re.IGNORECASE) for line in lines)


# ---------------------------------------------------------------------------
# the Python program, run until it returns
# ---------------------------------------------------------------------------
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


def _run(macros):
    sas = _Sas(macros)
    exec(compile(_python(), "Ingest-Documents.sas", "exec"), {"SAS": sas})
    return sas


def test_a_saved_ledger_that_could_not_be_loaded_is_a_failed_run_in_the_builder():
    sas = _run({"_rag_state": "fail", "_rag_error": RELOAD_ERROR, "_rag_rc": "1"})
    assert sas.log == ["RAGINGEST Ingestion failed: " + RELOAD_ERROR]
    # the state the job had before: completed. The milestone alone decides.
    assert _builder_says_failed("completed", sas.log)
    # the reason stays what the reload wrote, and the run stays a failed one
    assert sas.macros["_rag_error"] == RELOAD_ERROR
    assert sas.macros["_rag_rc"] == "1"


def test_a_run_without_a_milestone_was_shown_as_finished():
    """What the Builder made of the run before: nothing it reads was there."""
    assert not _builder_says_failed("completed", [])
    assert _builder_says_failed("failed", [])


def test_a_context_without_requests_is_a_failed_run_in_the_builder(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)   # import raises
    sas = _run({"_rag_state": "run", "_rag_rc": "1"})
    assert len(sas.log) == 1
    assert sas.log[0].startswith("RAGINGEST Ingestion failed: ")
    assert "lacks the requests package" in sas.log[0]
    assert _builder_says_failed("completed", sas.log)
    # the last line of the job says the same
    assert sas.macros["_rag_error"] == sas.log[0][len("RAGINGEST Ingestion failed: "):]


def _blocks(node):
    """Every list of statements under a node, without those of the functions
    defined inside it."""
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            yield block
            for statement in block:
                if not isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
                    yield from _blocks(statement)
    for handler in getattr(node, "handlers", []):
        yield handler.body
        for statement in handler.body:
            yield from _blocks(statement)


def _says_failed(statement):
    return (isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and getattr(statement.value.func, "id", "") == "M"
            and ast.unparse(statement.value.args[0]).startswith("'Ingestion failed: '"))


def test_no_way_out_of_the_program_is_silent():
    """Every `return` of the program stands behind the milestone, and so does
    the handler every exception of the run ends in. A new early return
    without it is the defect again."""
    main = ast.parse(_python()).body[0]
    assert isinstance(main, ast.FunctionDef) and main.name == "main"
    returns = 0
    for block in _blocks(main):
        for position, statement in enumerate(block):
            if isinstance(statement, ast.Return):
                returns += 1
                assert any(_says_failed(earlier) for earlier in block[:position]), \
                    "line %d returns without the milestone" % statement.lineno
    assert returns == 2
    handler = main.body[-1]
    assert isinstance(handler, ast.Try) and len(handler.handlers) == 1
    assert any(_says_failed(statement) for statement in handler.handlers[0].body)
    # the milestone is written with what the program defines before it
    program = _python()
    assert program.index("def M(msg):") < program.index('M("Ingestion failed: "')


# ---------------------------------------------------------------------------
# the state of the job
# ---------------------------------------------------------------------------
def test_a_run_that_failed_ends_as_a_failed_job():
    after = _after_python()
    macro = _macro(after, "_rag_job_state")
    assert "%if &_rag_rc. NE 0 %then %let SYSCC = 1012;" in macro
    # the value a job generated from a flow ends with, which SAS Job
    # Execution reported as failed when that was measured
    assert "%let SYSCC = 1012;" in registration.JOB_EPILOGUE
    # the last statement of the program, behind the line that says why
    assert after.rstrip().endswith("%mend _rag_job_state;\n%_rag_job_state;")
    assert after.index('line = "ERROR: RAG ingestion failed: " || text;') \
        < after.index("%macro _rag_job_state;")
    assert after.count("SYSCC = ") == 1
    # nothing aborts: an aborted session never reports to SAS Job Execution
    assert not re.search(r"\babort\b", re.sub(r"/\*.*?\*/", "", _job(),
                                              flags=re.DOTALL), re.IGNORECASE)


def test_only_a_run_that_went_through_is_not_a_failed_one():
    """_rag_rc starts at 1 and one statement sets it to 0: the last one of a
    run that did all its work. A failed reload, a program that did not run
    and every exception leave it at 1."""
    job = _job()
    assert job.index("%let _rag_rc = 1;") < job.index("proc python restart;")
    program = _python()
    assert program.count('SAS.symput("_rag_rc"') == 1
    done = program.index('SAS.symput("_rag_rc", "0")')
    assert program.index('"rag_ledger_out.json"') < done \
        < program.index("except Exception as error:")


def test_a_run_that_failed_stores_no_ledger():
    """rag_ledger_out.json stays in WORK. In a SAS session that ran the job
    before, a run that stopped early found the file of the earlier run and
    stored that ledger again - after a failed reload over the very file that
    could not be loaded. The file alone decided; now the run does."""
    after = _after_python()
    store = _macro(after, "_rag_store_ledger")
    guard = store.index("%if &_rag_rc. NE 0 %then %return;")
    assert guard < store.index("fileexist(") < store.index("droptable casdata=") \
        < store.index("promote casdata=") < store.index("save casdata=")
    # nothing of the store stands in front of the guard
    assert store[:guard].strip() == "%macro _rag_store_ledger;"
    # and a run that reports success wrote the file itself, just before
    program = _python()
    assert program.count('"rag_ledger_out.json"') == 1
    assert program.index('"rag_ledger_out.json"') \
        < program.index('SAS.symput("_rag_rc", "0")')
    assert after.index("%_rag_store_ledger;") < after.index("%macro _rag_job_state;")


def test_the_reload_still_keeps_its_own_condition_code_out_of_the_run():
    """The job fails through _rag_rc at its end, not through what PROC CAS
    left behind: a load another session won is no failure of the run."""
    reload = _macro(_job(), "_rag_reload_saved")
    assert reload.index("%let _rag_reload_syscc = &syscc.;") \
        < reload.index("proc cas;") \
        < reload.index("%let syscc = &_rag_reload_syscc.;") \
        < reload.index("%let &statevar. = fail;")
