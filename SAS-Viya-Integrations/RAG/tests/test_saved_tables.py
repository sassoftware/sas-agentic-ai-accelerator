# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A saved pipeline table is loaded back before anything reads it.

The ledger and the embedded chunks are promoted AND saved, so they survive a
CAS server restart - on disk. Every reader tests only the in-memory table, so
after a restart a saved ledger read as "no previous run": every document was
classified new, the corpus was embedded again and the saved file was
overwritten with the result.

The reload cannot live in rag_core: the first test of the ledger is the run
lock, in SAS, before proc python starts. So it is one macro, worded
identically in every program that needs it, and these tests hold the three
things that make it work - it is there, it is the same everywhere, and it
runs before the first look at the table.

No SAS runs here; what the macro does on a server is verified live.
"""
import json
import pathlib
import re

import pytest

RAG = pathlib.Path(__file__).resolve().parents[1]
REPO = RAG.parents[1]
STEPS = REPO / "SAS-Viya-Integrations" / "Custom-Steps"
JOB = REPO / "SAS-Viya-Integrations" / "RAG-Ingestion" / "Ingest-Documents.sas"

MACRO_START = "%macro _rag_reload_saved(sessref, caslib, table, statevar, errorvar);"
MACRO_END = "%mend _rag_reload_saved;"

# step -> (control prefix, the saved tables it reads before writing them)
RELOADING_STEPS = {
    "RAG - List Documents": ("rgls", ["_LEDGER"]),
    "RAG - Purge Documents": ("rgpu", ["_LEDGER"]),
    "RAG - Register Setup": ("rgrg", ["_LEDGER"]),
    "RAG - Embed Chunks": ("rgem", ["_EMBEDDED"]),
    "RAG - Enrich Chunks": ("rgen", ["_EMBEDDED"]),
    "RAG - Load Vector Store": ("rgld", ["_EMBEDDED", "_LEDGER"]),
}


def step_code(name: str) -> str:
    step = json.loads((STEPS / f"{name}.step").read_text(encoding="utf-8"))
    return step["templates"]["SAS"]


def job_code() -> str:
    return JOB.read_text(encoding="utf-8").replace("\r\n", "\n")


def macro_of(code: str) -> str:
    assert code.count(MACRO_START) == 1, "the reload macro is defined once"
    return code[code.index(MACRO_START):
                code.index(MACRO_END) + len(MACRO_END)]


def reload_call(prefix: str, suffix: str) -> str:
    return (f"%_rag_reload_saved({prefix}cas, &_{prefix}_tablesCaslib., "
            f"&_{prefix}_ragProject.{suffix}, _{prefix}_state, "
            f"_{prefix}_error);")


# ---------------------------------------------------------------------------
# the step files themselves
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", sorted(STEPS.glob("RAG - *.step")),
                         ids=lambda path: path.stem)
def test_a_step_file_is_still_what_sas_studio_exports(path):
    """One line of JSON, no trailing newline - an edit made by hand, or with
    another serialisation, shows up as a diff of the whole file."""
    raw = path.read_bytes()
    assert json.dumps(json.loads(raw.decode("utf-8"))).encode("utf-8") == raw


# ---------------------------------------------------------------------------
# the macro
# ---------------------------------------------------------------------------
def test_the_reload_macro_is_worded_identically_everywhere():
    """Seven copies of one block: a fix made in one of them is a bug in the
    other six."""
    reference = macro_of(job_code())
    for name in RELOADING_STEPS:
        assert macro_of(step_code(name)) == reference, name


def test_the_reload_looks_before_it_loads():
    macro = macro_of(job_code())
    in_memory = macro.index("table.tableExists")
    listing = macro.index("table.fileInfo")
    load = macro.index("table.loadTable")
    # only a table that is NOT in memory is looked for on disk, and only a
    # file that is there is loaded - an in-memory table is never replaced by
    # its older saved copy
    assert in_memory < macro.index("if found.exists == 0 then do;") \
        < listing < macro.index('if saved != "" then do;') < load
    assert "promote = true" in macro[load:], \
        "a session-scope table would vanish with the step's CAS session"


def test_a_load_another_session_won_is_not_a_failure():
    """Two runs starting after a restart both find the table missing; the
    second load is refused. What counts is whether the table is there."""
    macro = macro_of(job_code())
    after_load = macro[macro.index("table.loadTable"):]
    assert after_load.index("table.tableExists") \
        < after_load.index('outcome = "loaded"') \
        < after_load.index('outcome = "failed"')
    # the refused action leaves an error condition behind; the run's own
    # outcome must not inherit it
    assert macro.index("%let _rag_reload_syscc = &syscc.;") \
        < macro.index("proc cas;") < macro.index("quit;") \
        < macro.index("%let syscc = &_rag_reload_syscc.;")


def test_a_saved_table_that_cannot_be_loaded_fails_the_run():
    """Carrying on would treat the corpus as new and then save an almost
    empty ledger over the file that could not be read."""
    macro = macro_of(job_code())
    failed = macro[macro.index("%if &_rag_reload_outcome. EQ failed %then %do;"):]
    assert failed.index("%let &statevar. = fail;") \
        < failed.index("%let &errorvar. =") < failed.index("%return;")


def test_the_reload_stays_out_of_a_run_that_is_not_running():
    macro = macro_of(job_code())
    assert macro.index("%if %superq(&statevar.) NE run %then %return;") \
        < macro.index("proc cas;")


def test_the_reload_reads_its_outcome_from_the_right_symbol_table():
    """CASL's symputx writes to the GLOBAL symbol table unless told otherwise
    (verified live); the macro reads a local, which would stay 'unknown'."""
    macro = macro_of(job_code())
    assert "%local _rag_reload_outcome" in macro
    assert 'call symputx("_rag_reload_outcome", outcome, "L");' in macro


def _said_for(macro, condition):
    """The lines the macro writes to the log under one %if or %else %if."""
    after = macro[macro.index("%if &_rag_reload_outcome. EQ loaded %then"):]
    return re.findall(r"%put ((?:NOTE|WARNING): [^;]*);",
                      after.split(condition, 1)[1].split("%end;", 1)[0])


def test_a_look_that_failed_is_a_warning_that_says_what_the_run_then_does():
    """The run goes on when the caslib cannot be listed - with no table in
    memory, which is the case the reload is there for: no previous run, the
    corpus embedded again, the saved file overwritten. That was a NOTE that
    said the run continues with what is in memory."""
    macro = macro_of(job_code())
    assert 'else outcome = "unlisted";' in macro
    unlisted = _said_for(
        macro, "%else %if &_rag_reload_outcome. EQ unlisted %then %do;")
    assert len(unlisted) == 2
    assert all(line.startswith("WARNING: RAG: ") for line in unlisted)
    said = " ".join(unlisted)
    # unlisted is set only where the table is NOT in memory
    assert "&caslib..&table. is not in memory" in said
    assert "the files of the caslib &caslib. could not be listed" in said
    assert "a saved copy could not be looked for" in said
    assert "the run continues without the table" in said
    assert "saves over a copy that may be there" in said
    assert ("A ledger that is not in memory means that every document counts "
            "as new and is embedded again") in said
    # nothing else fails the run: only the outcome failed sets the state
    assert macro.count("%let &statevar. = fail;") == 1


def test_a_look_that_did_not_finish_is_a_warning_too():
    """PROC CAS ended before it handed back an outcome: whether the table is
    in memory is then not known, and the lines do not claim it."""
    macro = macro_of(job_code())
    assert "%let _rag_reload_outcome = unknown;" in macro
    unknown = _said_for(
        macro, "%else %if &_rag_reload_outcome. NE memory and "
               "&_rag_reload_outcome. NE absent %then %do;")
    assert len(unknown) == 2
    assert all(line.startswith("WARNING: RAG: ") for line in unknown)
    said = " ".join(unknown)
    assert "PROC CAS ended before it said whether the table is in memory or saved" in said
    assert "The run continues with what is in memory" in said
    assert "if the table is not in memory, the run saves over a copy" in said
    assert "every document counts as new" in said
    assert "is not in memory, and" not in said


def test_the_lines_of_the_reload_are_text_the_macro_processor_leaves_alone():
    """They are %put statements inside a macro: a % or a quotation mark in
    them would be read, and a semicolon would end them early."""
    macro = macro_of(job_code())
    lines = re.findall(r"%put ([^;]*);", macro)
    assert len(lines) == 5
    for line in lines:
        assert not set(line) & set("%'\""), line
        # every & starts one of the three names the macro is given
        assert set(re.findall(r"&(\w+)", line)) <= {"caslib", "table"}, line
        assert line.count("(") == line.count(")"), line
    # the only NOTE left is the one for a table that was loaded back
    assert [line for line in lines if line.startswith("NOTE: ")] == [
        "NOTE: RAG: &caslib..&table. was saved but not in memory and has "
        "been loaded from its saved file."]


# ---------------------------------------------------------------------------
# the steps: every saved table is reloaded before its first use
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(RELOADING_STEPS))
def test_a_step_reloads_its_saved_tables_before_looking_at_them(name):
    prefix, suffixes = RELOADING_STEPS[name]
    code = step_code(name)
    libname = code.index(f"libname {prefix}out cas ")
    defined = code.index(MACRO_END)
    assert libname < code.index(MACRO_START), \
        "the macro needs the step's CAS session and caslib"

    # everything that can look at a pipeline table: the run lock, any
    # exist() on the tables libref, and the Python program
    first_uses = [code.index("\nproc python restart;")]
    for marker in (f"%macro _{prefix}_lock;", f"%_{prefix}_lock;",
                   f"exist({prefix}out."):
        if marker in code:
            first_uses.append(code.index(marker))
    first_use = min(first_uses)

    for suffix in suffixes:
        call = reload_call(prefix, suffix)
        assert code.count(call) == 1, f"{name} does not reload {suffix}"
        assert defined < code.index(call) < first_use, \
            f"{name} reloads {suffix} after it has already been looked at"

    # the state and error variables the macro writes exist by then, or its
    # %let would create locals the step never sees
    assert code.index(f"%let _{prefix}_state = run;") < libname
    assert code.index(f"%let _{prefix}_error = ") < libname


def test_the_steps_that_take_the_run_lock_reload_the_ledger_first():
    """The lock macro is the first reader: without the ledger in memory it
    CREATES one holding only the lock row, and the saved file is later
    overwritten from it."""
    locking = [name for name in RELOADING_STEPS
               if f"%macro _{RELOADING_STEPS[name][0]}_lock;" in step_code(name)]
    assert sorted(locking) == ["RAG - List Documents", "RAG - Purge Documents"]
    for name in locking:
        prefix = RELOADING_STEPS[name][0]
        code = step_code(name)
        assert code.index(reload_call(prefix, "_LEDGER")) \
            < code.index(f"%macro _{prefix}_lock;")


@pytest.mark.parametrize("path", sorted(STEPS.glob("RAG - *.step")),
                         ids=lambda path: path.stem)
def test_no_step_reads_a_saved_table_it_did_not_reload(path):
    """A step that starts reading the ledger or the embedded chunks has to
    reload them too - this is the test that notices."""
    code = json.loads(path.read_text(encoding="utf-8"))["templates"]["SAS"]
    read = set(re.findall(
        r"""cas_read\(\s*project_table\(\s*["'](_LEDGER|_EMBEDDED)["']""", code))
    reloaded = set(re.findall(
        r"%_rag_reload_saved\([^)]*_ragProject\.(_LEDGER|_EMBEDDED),", code))
    assert read <= reloaded, \
        f"{path.stem} reads {sorted(read - reloaded)} without reloading"
    assert reloaded == set(RELOADING_STEPS.get(path.stem, ("", []))[1])


# ---------------------------------------------------------------------------
# the standalone job
# ---------------------------------------------------------------------------
def test_the_job_reloads_its_ledger_before_exporting_it():
    code = job_code()
    call = ("%_rag_reload_saved(_ragcas, &ledgerCaslib., &ledgerTable., "
            "_rag_state, _rag_error);")
    assert code.count(call) == 1
    export = code[code.index("%macro _rag_export_ledger;"):
                  code.index("%mend _rag_export_ledger;")]
    assert code.index(MACRO_END) < code.index("%macro _rag_export_ledger;"), \
        "a macro cannot be defined inside the macro that calls it"
    assert export.index('libname _ragcl cas caslib="&ledgerCaslib."') \
        < export.index(call) < export.index("%sysfunc(exist(_ragcl.&ledgerTable.))")
    assert code.index("%let _rag_state = run;") < code.index(MACRO_START)


def test_the_job_does_not_ingest_when_its_saved_ledger_could_not_be_loaded():
    """The Python program is the part that would read an empty ledger and
    write a new one; it has to stop before it does anything but say why."""
    code = job_code()
    program = code[code.index("def main():"):code.index("endsubmit;")]
    guard = program.index('if str(SAS.symget("_rag_state") or "run").strip() != "run":')
    block = program[guard:].split("\n\n", 1)[0].split("\n")[1:]
    assert block[0].strip() == 'M("Ingestion failed: "'
    assert block[-1].strip() == "return"
    assert guard < program.index("import requests")
    assert guard < program.index("rag_ledger_in.json")


# ---------------------------------------------------------------------------
# the reload and the run lock of 2.3.0
#
# The run lock asks the ledger for the types of its text columns, is appended
# with those types and is read back (test_ledger_writers.py). All three look
# at the in-memory table, so the reload has to come first: load back, column
# types, lock, read back.
# ---------------------------------------------------------------------------
LOCKING_STEPS = {"RAG - List Documents": "rgls", "RAG - Purge Documents": "rgpu"}

# what the About tab says was measured of the reload, by the step it was
# measured in (SAS Viya 2026.09, pgvector, the saved table dropped from
# memory)
MEASURED_RELOAD = {
    "RAG - List Documents": ("the step loaded a CHAR and a VARCHAR ledger back, "
                             "took its run lock and listed every document as "
                             "unchanged"),
    "RAG - Embed Chunks": ("the step loaded the table back and found nothing "
                           "left to embed"),
}


def _in_order(text, *markers):
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), \
        [marker for _position, marker in sorted(zip(positions, markers))]


@pytest.mark.parametrize("name", sorted(LOCKING_STEPS))
def test_a_step_loads_its_ledger_back_before_it_asks_for_the_column_types(name):
    prefix = LOCKING_STEPS[name]
    code = step_code(name)
    lock = code[code.index(f"%macro _{prefix}_lock;"):
                code.index(f"%mend _{prefix}_lock;")]
    # the lock macro runs once, in open code, after the reload
    assert code.count(f"%_{prefix}_lock;") == 1
    _in_order(code, reload_call(prefix, "_LEDGER"), f"%_{prefix}_lock;",
              "\nproc python restart;")
    # and the table is asked for its types nowhere else - a question put to
    # a ledger that is still on disk would be answered with the fallback
    question = f"%_{prefix}_ledger_types("
    assert code.count(question) == 1 and question in lock
    _in_order(lock,
              # a stale or foreign lock is looked for in the loaded table
              f'set {prefix}out.&_{prefix}_ragProject._LEDGER(where=(doc_id = "__run_lock__"));',
              question,
              f"data {prefix}out.&_{prefix}_ragProject._LEDGER(append=yes);",
              # the read-back of this run's own lock
              f'(where=(doc_id = "__run_lock__" and run_id = "&_{prefix}_runId."));')


@pytest.mark.parametrize("name", sorted(LOCKING_STEPS))
def test_a_step_whose_ledger_could_not_be_loaded_takes_no_lock(name):
    """The lock macro would otherwise find no ledger, START one that holds
    only its lock row, and the step's release would save that over the file
    that could not be read."""
    prefix = LOCKING_STEPS[name]
    lock = step_code(name)
    lock = lock[lock.index(f"%macro _{prefix}_lock;"):
                lock.index(f"%mend _{prefix}_lock;")]
    _in_order(lock, f"%if &_{prefix}_state. NE run %then %return;",
              f"exist({prefix}out.&_{prefix}_ragProject._LEDGER)",
              "promote=yes")


def test_the_job_loads_its_ledger_back_before_types_lock_and_read_back():
    code = job_code()
    export = code[code.index("%macro _rag_export_ledger;"):
                  code.index("%mend _rag_export_ledger;")]
    assert code.count("%_rag_ledger_types(") == 1
    assert code.count("%_rag_export_ledger;") == 1
    _in_order(export,
              "%_rag_reload_saved(_ragcas, &ledgerCaslib., &ledgerTable., ",
              "%if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;",
              "rag_ledger_in.json",
              "%_rag_ledger_types(_ragcas, &ledgerCaslib., &ledgerTable., char);",
              "data _ragcl.&ledgerTable.(append=yes);",
              "rag_ledger_lock.json")
    # the session the reload uses is the one the export and the lock use
    _in_order(export, "cas _ragcas;", "%_rag_reload_saved(_ragcas,",
              "cas _ragcas terminate;")


class _StopAfterTheGuard(Exception):
    pass


class _JobSas:
    """What the job's Python sees of SAS before it bootstraps rag_core."""

    # what the reload leaves in _rag_error when a saved file cannot be loaded
    ERROR = ("the saved table Public.HR_LEDGER could not be loaded into memory "
             "(CAS messages above) - stopped, because an empty table would "
             "re-embed the corpus and overwrite the saved file")

    def __init__(self, state):
        self.state = state
        self.written = []

    def symget(self, name):
        return {"_rag_state": self.state, "_rag_error": self.ERROR}.get(name, "")

    def symput(self, name, value):
        self.written.append((name, value))

    def logMessage(self, message):
        self.written.append(("log", message))

    @property
    def workpath(self):
        # the first thing the program reads once it is past the guard
        raise _StopAfterTheGuard()


def _run_job_program(state):
    code = job_code()
    program = code.split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]
    sas = _JobSas(state)
    exec(compile(program, "Ingest-Documents.sas", "exec"), {"SAS": sas})
    return sas


def test_the_job_program_returns_at_once_after_a_failed_reload():
    """Run, not read: with _rag_state at fail the program says why, as the
    milestone of a failed run, and writes nothing else - _rag_rc stays 1 and
    _rag_error keeps what the reload wrote - and it does not get as far as
    the two exports the lock read-back compares. It wrote nothing at all
    before, and the Prompt Builder showed such a run as finished
    (test_job_outcome.py)."""
    assert _run_job_program("fail").written == [
        ("log", "RAGINGEST Ingestion failed: " + _JobSas.ERROR)]


@pytest.mark.parametrize("state", ["run", " run ", "", None])
def test_the_job_program_goes_on_when_nothing_failed_before_it(state):
    """A session in which the variable is not set is a run: the guard stops
    only what the reload stopped."""
    with pytest.raises(_StopAfterTheGuard):
        _run_job_program(state)


# ---------------------------------------------------------------------------
# the About tab
# ---------------------------------------------------------------------------
def newest_changelog(path) -> str:
    """The newest entry of the changelog on a step's About tab."""
    def find(node):
        if isinstance(node, dict):
            if node.get("id") == "textChangelog":
                return node["text"]
            node = list(node.values())
        if isinstance(node, list):
            for value in node:
                found = find(value)
                if found:
                    return found
        return None
    step = json.loads(path.read_text(encoding="utf-8"))
    return find(json.loads(step["ui"])).split("\n* Version: ")[0]


@pytest.mark.parametrize("path", sorted(STEPS.glob("RAG - *.step")),
                         ids=lambda path: path.stem)
def test_the_about_tab_names_the_tables_a_step_loads_back(path):
    """Which step a site has is read on its About tab - so that is where a
    step says that it loads its saved tables back, and names them."""
    newest = newest_changelog(path)
    assert "(11OCT2026)" in newest.split("\n")[0]
    lines = newest.split("\n      - ")
    reloading = [line for line in lines if "saved but" in line]
    tables = set(RELOADING_STEPS.get(path.stem, ("", []))[1])
    assert len(reloading) == (1 if tables else 0)
    for line in reloading:
        assert {suffix for suffix in ("_LEDGER", "_EMBEDDED")
                if "<project>" + suffix in line} == tables
        assert "A saved file that cannot be loaded fails the step" in line
        assert ("before the run lock asks for its column types" in line) \
            == (path.stem in LOCKING_STEPS)
        # What ran in SAS is said by the step it ran in, with what it ran
        # on. Two steps loaded a table back in a measured run: the ledger in
        # RAG - List Documents, the embedded chunks in RAG - Embed Chunks.
        # The table was dropped from memory - the CAS server was not
        # restarted - and the line says that too. The other four steps
        # found their tables in memory, or did not run, and claim nothing.
        measured = MEASURED_RELOAD.get(path.stem)
        assert ("Measured" in line) == (measured is not None)
        if measured:
            assert line.endswith(measured)
            assert ("Measured on SAS Viya 2026.09 with pgvector, with the "
                    "saved %s dropped from memory and not with a restarted "
                    "CAS server: " % ("ledger" if "_LEDGER" in tables else "table")
                    ) in line
        else:
            assert line.endswith("A saved file that cannot be loaded fails the step")
    # the look that failed has a line of its own, in the same six steps
    unlisted = [line for line in lines
                if line.startswith("Where the files of the caslib cannot be listed")]
    assert len(unlisted) == (1 if tables else 0)
    for line in unlisted:
        assert "the run goes on and says so in a WARNING" in line
        assert "every document counts as new" in line


@pytest.mark.parametrize("path", sorted(STEPS.glob("RAG - *.step")),
                         ids=lambda path: path.stem)
def test_no_line_of_the_about_tab_says_that_it_did_not_run(path):
    """A line says what the step does, and what was measured where
    something was. What has not run in SAS is said in the changelog and in
    the guide, which are corrected after a live pass; a step file is not."""
    newest = newest_changelog(path)
    assert "ot run in SAS" not in newest
    for line in newest.split("\n      - "):
        # a line of the About tab ends without a full stop
        assert not line.endswith("."), line[-80:]
