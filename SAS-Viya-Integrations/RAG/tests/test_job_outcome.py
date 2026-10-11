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


def test_no_export_of_an_earlier_run_is_left_for_this_one():
    """rag_ledger_in.json and rag_ledger_lock.json stay in WORK as well. A
    run that finds no ledger in memory exports none, and in a SAS session
    that ran the job before, the Python program then read the two files of
    the EARLIER run: that ledger as the ledger of this run, and its lock as
    the lock that was written. Both are deleted before anything is exported."""
    job = _job()
    head = re.sub(r"/\*.*?\*/", "", job.split("\nsubmit;\n", 1)[0], flags=re.DOTALL)
    step = head[head.index('    do file = "rag_ledger_in.json", "rag_ledger_lock.json";'):]
    step = step[:step.index("\nrun;")]
    order = ['ref = "_ragold";',
             'if filename(ref, cats("%sysfunc(pathname(work))/", file)) = 0 then do;',
             "if fexist(ref) then do;", "if fdelete(ref) = 0 then do;",
             "was deleted from WORK", "else do;",
             'call symputx("_rag_state", "fail", "G");',
             'call symputx("_rag_error", strip(file) || ',
             "rc = filename(ref);"]
    positions = [step.index(text) for text in order]
    assert positions == sorted(positions)
    # the files are named here and where they are written and read, nowhere
    # else - and here first, in open code, before the macro that exports
    for name in ("rag_ledger_in.json", "rag_ledger_lock.json"):
        assert head.count(name) == 2
        assert head.index(name) < head.index("%macro _rag_export_ledger;")
    assert head.index("%let _rag_state = run;") < head.index("    do file = ") \
        < head.index("%macro _rag_export_ledger;") < head.index("%_rag_export_ledger;")
    assert len("_ragold") <= 8                    # a fileref
    # rag_ledger_out.json is not among them: only a run that reports success
    # stores it, and that run has written it (the test above)
    assert "rag_ledger_out.json" not in step


def test_an_export_that_cannot_be_deleted_stops_the_run():
    """The file would be read as the ledger of this run. The run fails the
    way it fails for a saved ledger that could not be loaded: the Python
    program says why, as a milestone, and returns before it reads a file."""
    said = re.search(r'call symputx\("_rag_error", strip\(file\) \|\| "([^"]*)", "G"\);',
                     _job())
    assert said
    message = "rag_ledger_in.json" + said.group(1)
    assert message.endswith("could not be deleted from WORK - stopped, because the "
                            "run would read the ledger of that run as its own")
    sas = _run({"_rag_state": "fail", "_rag_error": message, "_rag_rc": "1"})
    assert sas.log == ["RAGINGEST Ingestion failed: " + message]
    assert _builder_says_failed("completed", sas.log)
    assert sas.macros["_rag_rc"] == "1"


def test_the_reload_still_keeps_its_own_condition_code_out_of_the_run():
    """The job fails through _rag_rc at its end, not through what PROC CAS
    left behind: a load another session won is no failure of the run."""
    reload = _macro(_job(), "_rag_reload_saved")
    assert reload.index("%let _rag_reload_syscc = &syscc.;") \
        < reload.index("proc cas;") \
        < reload.index("%let syscc = &_rag_reload_syscc.;") \
        < reload.index("%let &statevar. = fail;")


# ---------------------------------------------------------------------------
# the retrieval test job
#
# Test-Retrieval.sas is read by the Prompt Builder the same way, by another
# word: a probe that did not work says FAILED <reason>. Its one early return
# wrote a line without that word, which the Builder read as nothing - and
# showed as a probe that returned no chunks.
# ---------------------------------------------------------------------------
def _probe_python():
    probe = (JOBS / "Test-Retrieval.sas").read_text(encoding="utf-8").replace(
        "\r\n", "\n")
    return probe.split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def test_the_builder_reads_a_failed_probe_by_the_word_failed():
    reader = (BUILDER / "objects" / "rag-retrieval-log.ts").read_text(
        encoding="utf-8")
    assert r"const message = raw.replace(/^RAGRETRIEVE\s+/, '');" in reader
    assert "} else if (message.startsWith('FAILED ')) {" in reader


def test_a_probe_in_a_context_without_requests_says_failed(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)   # import raises
    sas = _Sas({})
    exec(compile(_probe_python(), "Test-Retrieval.sas", "exec"), {"SAS": sas})
    assert len(sas.log) == 1
    message = re.sub(r"^RAGRETRIEVE\s+", "", sas.log[0])
    assert message.startswith("FAILED ")
    assert "lacks the requests package" in message


def test_no_way_out_of_the_probe_is_silent():
    main = ast.parse(_probe_python()).body[0]
    returns = 0
    for block in _blocks(main):
        for position, statement in enumerate(block):
            if isinstance(statement, ast.Return) and statement.value is None:
                returns += 1
                said = [ast.unparse(earlier.value.args[0])
                        for earlier in block[:position]
                        if isinstance(earlier, ast.Expr)
                        and isinstance(earlier.value, ast.Call)
                        and getattr(earlier.value.func, "id", "") == "M"]
                assert any(text.startswith("'FAILED ") for text in said), \
                    "line %d returns without FAILED" % statement.lineno
    assert returns == 1
    assert 'M("FAILED " + sas_safe(' in _probe_python()


# ---------------------------------------------------------------------------
# the steps
#
# A guard, not the test of a defect: the steps were read for the same
# silence and do not have it. Their Python returns early in three places -
# a step that is not running, and the two imports - and writes nothing
# there, but what a step says is said by the SAS behind it: a return code
# the Python never set to 0 makes the status macro fail the step, with its
# ERROR line and the two flags a flow and its job read.
# ---------------------------------------------------------------------------
STEPS = REPO / "SAS-Viya-Integrations" / "Custom-Steps"
RAG_STEPS = {
    "RAG - Chunk Documents": "rgch", "RAG - Embed Chunks": "rgem",
    "RAG - Enrich Chunks": "rgen", "RAG - Extract Text": "rgex",
    "RAG - List Documents": "rgls", "RAG - Load Vector Store": "rgld",
    "RAG - Purge Documents": "rgpu", "RAG - Register Setup": "rgrg",
    "RAG - Retrieve Context": "rgrt",
}


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_an_early_return_of_a_step_is_a_failed_step_with_its_error_line(name):
    import json

    prefix = "_" + RAG_STEPS[name] + "_"
    sas = json.loads((STEPS / (name + ".step")).read_text(
        encoding="utf-8"))["templates"]["SAS"]
    python = sas.split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]
    # the return code starts as a failure and one statement makes it 0
    assert sas.index("%%let %src = 1;" % prefix) < sas.index("proc python restart;")
    assert len(re.findall(r"SAS\.symput\(['\"]%src['\"]" % prefix, python)) == 1
    main = ast.parse(python).body[0]
    returns = [statement for block in _blocks(main) for statement in block
               if isinstance(statement, ast.Return)]
    # none of them stands behind the statement that reports success
    done = re.search(r"SAS\.symput\(['\"]%src['\"], ['\"]0['\"]\)" % prefix, python)
    done_line = python[:done.start()].count("\n") + 1
    assert returns and all(statement.lineno < done_line for statement in returns)
    status = _macro(sas, prefix + "status")
    failing = status.index("%%if &%sstate. EQ run %%then %%if &%src. NE 0 %%then"
                           % (prefix, prefix))
    fail = status[status.index("%%if &%sstate. EQ fail %%then %%do;" % prefix):]
    assert failing < status.index("%%if &%sstate. EQ fail %%then %%do;" % prefix)
    for said in ("%let RAG_PIPELINE_FAILED = 1;", "%let RAG_JOB_FAILED = 1;",
                 'line = "ERROR: %s failed: " || message;' % name):
        assert said in fail.split("%return;", 1)[0], said
