"""The class 'an action CAS refused, or a PROC CAS step that halted, taken as an answer', closed with one rule in every
program that is not frozen (content-23).

Round 22 measured on SAS Viya: a PROC CAS step that halts - 'ERROR: There is no server connection to execute the
action table.caslibInfo.', 'ERROR: Execution halted' - runs none of its statements behind the action, so no CALL
SYMPUTX of it runs. Get-All-Prompts.sas then read the %LET in front of the step, 0, as a run table that is not in
memory. The rule now, for CASL:

- every macro variable that a PROC CAS step sets with CALL SYMPUTX is set by a %LET in front of the step - the last
  statement in front of it that sets the variable - to the value of a question CAS did not answer, and only the step
  moves it. The first test takes every step and every variable from the programs and the steps that are no RAG
  steps, and fails for one that is not decided in NOT_ASKED;
- the tests behind it let a made-up CAS library halt at every action of those steps, one after the other, and follow
  what the programs do then. Up to this change each of them read the halt as an answer.

And for the Python (swat) of the same programs: an action whose answer the Python reads goes through read_action,
which takes an answer of any severity other than 0 for no answer, as the CASL does; the test takes every swat call
of every Python block from the programs.

SAS and CAS can not run here: the tests say what the programs do IF a PROC CAS step stops at an action and runs
nothing behind it, as the made-up libraries have it.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import ast
import copy
import json
import re
import textwrap
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

import casl_harness
import link_logs_harness as link
from log_parser_harness import code_file_program as parser_program, step_definition as parser_step
from log_parser_table_harness import FAILED, Library, Macros, table_of
from test_content_22 import LIB, TABLE, World, request
from test_fix_content_22 import FROZEN, INTEGRATIONS, Asking, RefusingCAS, _follow_part, _programs

GAP_FILE = INTEGRATIONS / "Logging-Monitoring" / "Get-All-Prompts.sas"
LINK_FILE = INTEGRATIONS / "Logging-Monitoring" / "Link-Logs-To-Models.sas"
GAP_STEP = INTEGRATIONS / "Custom-Steps" / "LLM - Get All Prompts.step"


# ---------------------------------------------------------------------------
# The class, taken from the code
# ---------------------------------------------------------------------------

STEP = re.compile(r"^[ \t]*proc cas\b[^;]*;(.*?)^[ \t]*(?:run;\s*)?quit;", flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
SETS = re.compile(r"call symputx\(\s*['\"]([^'\"]+)['\"]", flags=re.IGNORECASE)

# The value of each macro variable a PROC CAS step sets, in front of the step: what the program does with a question CAS
# did not answer. -1, refused and unknown are the values the step itself sets for a refusal; the flags of a failed run
# and of a refused action are 1; _lat_missing names every table the look asks for; the session id is empty. The three
# of the parser at 0 are read only where the step set _log_cas_refused to 0, which it starts at 1.
NOT_ASKED = {
    "_rcv_built": "-1",
    "_gap_runs_exists": "-1", "_gap_ledger_exists": "-1",
    "_casSessionUUID": "",
    "_lnk_log_scope": "-1", "_lnk_log_saved": "-1", "_lnk_log_exists": "-1", "_lnk_catalog_exists": "-1", "_lnk_runs_exists": "-1",
    "_lnk_failed": "1",
    "_lfs_taken": "refused", "_lfs_staged": "-1", "_lfs_outcome": "unknown",
    "_log_target_rows": "-3", "_log_cas_refused": "1", "_log_target_scope": "0", "_log_target_unloaded": "0", "_log_target_loaded": "0",
    "_log_kept_rows": "-1", "_log_took_rows": "-1", "_log_failed": "1",
    "_lat_taken": "refused", "_lat_outcome": "unknown", "_lat_staged": "-1", "_lat_helper": "unknown",
    "_lat_missing": "&releaseTable. LLM_FACT_SHEET EMBEDDING_FACT_SHEET DECISION_FACT_SHEET",
}


def _without_comments(text):
    """The program without its comments: /* */ and the statements that start with an asterisk, also over several lines."""
    text = re.sub(r"/\*.*?\*/", "", text.replace("\r\n", "\n"), flags=re.DOTALL)
    return re.sub(r"(?m)^[ \t]*\*[^;]*;", "", text)


def _last_set(code, variable, before):
    """The last statement in front of `before` that sets the macro variable: ('let', value), ('symputx', '') or
    ('local', ''), None for none."""
    name = re.escape(variable)
    found = []
    for kind, pattern in (("let", rf"%let\s+{name}\s*=([^;]*);"), ("symputx", rf"symputx\(\s*['\"]{name}['\"]"),
                          ("local", rf"%local\b[^;]*\b{name}\b")):
        for match in re.finditer(pattern, code[:before], flags=re.IGNORECASE):
            found.append((match.start(), kind, match.group(1).strip() if kind == "let" else ""))
    return max(found)[1:] if found else None


def _members():
    """{(program, variable): [(the line of the step, what sets the variable last in front of it)]} for every macro
    variable every PROC CAS step of every program sets. The flag the macro _lnk_ensure_loaded sets is the third
    argument of each of its calls."""
    found = {}
    for name, text in _programs().items():
        code = _without_comments(text)
        for step in STEP.finditer(code):
            line = code[:step.start()].count("\n") + 1
            for variable in dict.fromkeys(SETS.findall(step.group(0))):
                if variable == "&_lnk_flag.":
                    for call in re.finditer(r"%_lnk_ensure_loaded\([^;]*,\s*(\w+)\);", code):
                        found.setdefault((name, call.group(1)), []).append((line, _last_set(code, call.group(1), call.start())))
                    continue
                found.setdefault((name, variable), []).append((line, _last_set(code, variable, step.start())))
    return found


def test_every_macro_variable_a_proc_cas_step_sets_starts_at_the_value_of_a_question_cas_did_not_answer():
    members = _members()

    for (name, variable), steps in members.items():
        assert variable in NOT_ASKED, f"{name}: {variable} is set by a PROC CAS step and not decided here"
        for line, last in steps:
            assert last == ("let", NOT_ASKED[variable]), (name, variable, line, last)
    # the programs the class can live in: every program that is not frozen and the program of every step that is no
    # RAG step. A new step or a new variable fails here until it is decided
    assert sorted({name for name, _ in members}) == [
        "Custom-Steps/LLM - Get All Prompts.step", "Custom-Steps/LLM - Log Parser.step", "Logging-Monitoring/Build-RAG-Cost-View.sas",
        "Logging-Monitoring/Get-All-Prompts.sas", "Logging-Monitoring/Link-Logs-To-Models.sas", "Logging-Monitoring/Load-Fact-Sheets.sas",
        "Logging-Monitoring/Log-Parser-Code.sas", "Setup/Load-Accelerator-Tables.sas"]
    assert {variable for _, variable in members} == set(NOT_ASKED)
    counted = Counter()
    for (name, _), steps in members.items():
        counted[name] += len(steps)
    assert counted == {"Custom-Steps/LLM - Get All Prompts.step": 2, "Logging-Monitoring/Get-All-Prompts.sas": 2,
                       "Logging-Monitoring/Build-RAG-Cost-View.sas": 1, "Logging-Monitoring/Link-Logs-To-Models.sas": 8,
                       "Logging-Monitoring/Load-Fact-Sheets.sas": 3, "Custom-Steps/LLM - Log Parser.step": 11,
                       "Logging-Monitoring/Log-Parser-Code.sas": 11, "Setup/Load-Accelerator-Tables.sas": 5}
    # the frozen programs are the ones the rules name, and no other program is left out
    assert set(FROZEN) == {"RAG-Ingestion/Ingest-Documents.sas", "RAG-Ingestion/Test-Retrieval.sas", "Prompt-Optimization/Optimize-Prompt-DSPy.sas"}
    every = {path.relative_to(INTEGRATIONS).as_posix() for path in INTEGRATIONS.rglob("*.sas") if "/tests/" not in path.as_posix()}
    assert every - set(FROZEN) == {name for name in _programs() if name.endswith(".sas")}


def test_the_values_are_the_ones_the_programs_read_as_no_answer():
    """Each value of NOT_ASKED is the one the program goes by where CAS did not answer: the line it writes for it."""
    programs = _programs()
    gap = programs["Logging-Monitoring/Get-All-Prompts.sas"]
    assert "%if &_gap_runs_exists. LT 0 %then %do;\n            %put NOTE: The CAS library &_gap_rs_caslib. of the run table" in gap
    link_code = programs["Logging-Monitoring/Link-Logs-To-Models.sas"]
    assert ("%if &_lnk_log_scope. EQ -1 or &_lnk_log_exists. EQ -1 or &_lnk_catalog_exists. EQ -1 or &_lnk_runs_exists. EQ -1 or "
            "&_lnk_log_saved. EQ -1 %then %do;") in link_code
    assert "%if &_lnk_row_count. LT 0 or &_lnk_failed. EQ 1 %then %do;" in link_code
    parser = programs["Logging-Monitoring/Log-Parser-Code.sas"]
    assert "%if &_log_table_mode. EQ START and &_log_target_rows. EQ -3 %then %do;" in parser
    assert "%if &_log_target_rows. EQ -3 %then %do;\n                %let _log_cas_refused = 1;" in parser
    assert "%else %if &_log_cas_refused. %then %do;" in parser and "%if &_log_row_count. LT 0 or &_log_failed. EQ 1 %then %do;" in parser
    sheets = programs["Logging-Monitoring/Load-Fact-Sheets.sas"]
    assert "%if &_lfs_taken. EQ refused %then %do;" in sheets and "%else %if &_lfs_outcome. NE loaded %then" in sheets
    loader = programs["Setup/Load-Accelerator-Tables.sas"]
    assert "%if &_lat_taken. EQ refused %then %do;" in loader and "%if %length(&_lat_missing.) GT 0 %then %do;" in loader
    assert "nothing said what became of the upload of ACCELERATOR_RELEASES.csv" in loader
    cost = programs["Logging-Monitoring/Build-RAG-Cost-View.sas"]
    assert "-1 stands for a table that CAS did not say is there or did not count" in cost


# ---------------------------------------------------------------------------
# Made-up CAS libraries that halt
# ---------------------------------------------------------------------------

class Halted(Exception):
    """A PROC CAS step stops at the action: nothing behind it in the step runs."""


def _halt_lines(action):
    return [("ERROR", f"There is no server connection to execute the action '{action}'."), ("ERROR", "Execution halted")]


class HaltingLibrary(Library):
    """The made-up library of log_parser_table_harness, which halts at the call that carries the parameter zzHalt - the
    action a test marked in a copy of the program -, and with ended=True at every call behind it as well, as a CAS
    session that ended. A halt sets no condition code: round 22 measured a pull with halted steps that ended with 4."""

    def __init__(self, ended=False, **more):
        super().__init__(**more)
        self.ended, self.gone, self.halted = ended, False, []

    def __call__(self, action, parameters):
        if parameters.pop("zzhalt", None) or self.gone:
            self.halted.append(action)
            self.gone = self.ended
            raise Halted(action)
        return super().__call__(action, parameters)

    def run(self, casl):
        try:
            return super().run(casl)
        except Halted as halted:
            self.lines.extend(_halt_lines(halted.args[0]))
            return dict(self.symbols)


class HaltingAsking(Asking):
    """Asking of test_fix_content_22 (it answers table.caslibInfo), which halts at every call from the n-th on."""

    def __init__(self, halt_from=1, **more):
        super().__init__(**more)
        self.halt_from, self.count, self.halted = halt_from, 0, []

    def __call__(self, action, parameters):
        self.count += 1
        if self.count >= self.halt_from:
            self.halted.append(action)
            raise Halted(action)
        return super().__call__(action, parameters)

    def run(self, casl):
        try:
            return super().run(casl)
        except Halted as halted:
            self.lines.extend(_halt_lines(halted.args[0]))
            return dict(self.symbols)


class HaltingCAS(RefusingCAS):
    """casl_harness.FakeCAS (as RefusingCAS), which halts at every call from the n-th on. Each step returns only what it set."""

    def __init__(self, halt_from=1, **more):
        super().__init__(**more)
        self.halt_from, self.count, self.halted = halt_from, 0, []

    def __call__(self, action, parameters):
        self.count += 1
        if self.count >= self.halt_from:
            self.halted.append(action)
            raise Halted(action)
        return super().__call__(action, parameters)

    def run(self, casl):
        self.symbols = {}
        try:
            return super().run(casl)
        except Halted:
            return dict(self.symbols)


# ---------------------------------------------------------------------------
# Get-All-Prompts.sas: the setup whose step halted (the live defect of round 22)
# ---------------------------------------------------------------------------

NOT_SAID = ("NOTE: The CAS library Public of the run table RAG_HR_RUNS is not available in this session, or CAS did not say whether it, "
            "the table or its ledger table is there, so its runs are not in RAG_RUNS.")


def _gap_part(program):
    """The statements of _gap_stack_rag_runs from the %LET in front of the step to the branch that reads a run table in
    memory, which is a line of its own here."""
    start = re.search(r"^        %let _gap_runs_exists = ", program, flags=re.MULTILINE).start()
    stop = program.index("        %else %do;\n            libname _gaprn cas")
    return program[start:stop] + "%else %do;\n%put READ THE RUN TABLE;\n%end;\n"


def _gap_follow(library, program):
    macros = Macros({"_gap_rs_caslib": LIB, "_gap_rs_table": "RAG_HR_RUNS", "_gap_rs_ledger": "RAG_HR_LEDGER", "_gap_out_rag_runs": "RAG_RUNS"})
    state = {"cas": None}

    def on_text(line, found):
        if not line or line.startswith("*"):
            return
        if line.startswith(casl_harness.CALLED):
            found.symbols["zz_called"] = line
            return
        if line == "proc cas;":
            state["cas"] = [line]
            return
        assert state["cas"] is not None, "a line of SAS code this test does not follow: " + line
        # the listing of the CAS libraries takes no parameters, which the harness reads behind a slash
        state["cas"].append(re.sub(r"(table\.caslibInfo result=\w+ status=\w+);", r"\1 /;", line))
        if line in ("quit;", "run; quit;"):
            for variable, value in library.run("\n".join(state["cas"])).items():
                found.symbols[variable.lower()] = value
            state["cas"] = None

    put = macros.run(casl_harness.in_blocks("%macro _zz_part;\n" + _gap_part(program) + "\n%mend _zz_part;\n", "_zz_part"), "_zz_part", on_text)[1]
    return put, macros


def _gap_programs():
    return {"code file": GAP_FILE.read_text(encoding="utf-8").replace("\r\n", "\n"),
            "step": json.loads(GAP_STEP.read_bytes().decode("utf-8"))["templates"]["SAS"].replace("\r\n", "\n")}


@pytest.mark.parametrize("where", ["code file", "step"])
@pytest.mark.parametrize("halt_from", [1, 2, 3], ids=["the listing of the CAS libraries", "the run table", "the ledger"])
def test_a_setup_whose_step_halted_is_left_out_with_the_note_that_cas_did_not_say(where, halt_from):
    """Round 22, live: the step halted at its first action and the setup was 'not in memory' - its run table of 5 rows
    was in memory. With the run table and the ledger in memory, a halt at each of the three actions leaves it out with
    the NOTE that names CAS, and neither table is read."""
    library = HaltingAsking(halt_from=halt_from)
    for name in ("RAG_HR_RUNS", "RAG_HR_LEDGER"):
        library.put(LIB, name, table_of([("run_id", "varchar")], [{"run_id": "run-1"}]))

    put, macros = _gap_follow(library, _gap_programs()[where])

    assert library.halted and put == [NOT_SAID]
    assert macros["_gap_runs_exists"] == "-1" and "zz_called" not in macros.symbols


@pytest.mark.parametrize("where", ["code file", "step"])
def test_a_setup_cas_answered_for_is_read_as_before(where):
    library = HaltingAsking(halt_from=99)
    library.put(LIB, "RAG_HR_LEDGER", table_of([("run_id", "varchar")], [{"run_id": "run-1"}]))
    put, macros = _gap_follow(library, _gap_programs()[where])
    assert put == [] and macros["zz_called"] == casl_harness.CALLED + "_gap_runs_of_ledger;"

    library.put(LIB, "RAG_HR_RUNS", table_of([("run_id", "varchar")], [{"run_id": "run-1"}]))
    put, macros = _gap_follow(library, _gap_programs()[where])
    assert put == ["READ THE RUN TABLE"]


def test_the_about_tab_of_the_step_says_which_setups_are_left_out_as_the_code_decides():
    """review 22: the About tab of 2.5 said that a setup is left out where CAS refused to say whether its CAS library, its
    run table or its ledger is there - untrue for a refused question for the ledger where the run table is in memory,
    and for a refused listing of the CAS libraries for CASUSER. Both are read (test_fix_content_22.py)."""
    ui = json.loads(json.loads(GAP_STEP.read_bytes().decode("utf-8"))["ui"])
    texts = {}

    def walk(children):
        for child in children:
            if child.get("type") == "text":
                texts[child["id"]] = child["text"]
            walk(child.get("children", []))
    walk(ui["pages"])

    assert "refused to say whether its CAS library, its run table or its ledger is there" not in json.dumps(texts)
    assert ("A setup with neither table in memory is left out with a NOTE, and so is a setup whose CAS library is not available in the "
            "session, a setup where CAS refused the question for its run table, the question for its ledger where the run table is not "
            "in memory or - for a CAS library other than CASUSER - the listing of the CAS libraries, and a setup whose PROC CAS step "
            "halted before it set what it found; no table of a setup is loaded or changed.") in texts["textAbout"]
    assert texts["textChangelog"].startswith("* Version: 2.6 (15OCT2026)\n      - A setup whose PROC CAS step halted before it set what it found")
    assert "\n* Version: 2.5 (14OCT2026)\n      - A setup where CAS refused the question for its run table, the question for its ledger where the run table is not in memory" in texts["textChangelog"]
    assert json.loads(json.loads(GAP_STEP.read_bytes().decode("utf-8"))["localUi"]) == ui


# ---------------------------------------------------------------------------
# Link-Logs-To-Models.sas
# ---------------------------------------------------------------------------

LINK_NOT_SAID = ("CAS did not say whether a table the program reads is in memory or did not list the saved files of its CAS library, so "
                 "it is not known whether that table is there or whether the log table has a saved file. Nothing was linked and the "
                 "tables were left as they are.")


def _link_head(library, save_table):
    """The statements of the link from the question what the log table is to the decision whether the links are made,
    with the %LET statements of the three flags in front of the calls of _lnk_ensure_loaded."""
    program = LINK_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    macros = Macros({"_lnk_save_table": save_table, "_lnk_log_caslib": LIB, "_lnk_log_table": TABLE, "_lnk_catalog_caslib": LIB,
                     "_lnk_catalog_table": "MODEL_CATALOG", "_lnk_runs_caslib": LIB, "_lnk_runs_table": "RAG_RUNS"})
    put = []
    state = {"cas": None}

    def on_text(line, found):
        if not line or line.startswith("*"):
            return
        if line == "proc cas;":
            state["cas"] = [line]
            return
        assert state["cas"] is not None, "a line of SAS code this test does not follow: " + line
        state["cas"].append(line)
        if line in ("quit;", "run; quit;"):
            for variable, value in library.run("\n".join(state["cas"])).items():
                found.symbols[variable.lower()] = value
            state["cas"] = None

    def run(text, name):
        put.extend(macros.run(casl_harness.in_blocks(text, name), name, on_text)[1])

    def open_code(start, stop):
        return "%macro _open_code;\n" + program[program.index(start):program.index(stop)] + "%mend _open_code;\n"

    run(open_code("%let _lnk_log_scope = ", "* Sets the macro variable named in _lnk_flag"), "_open_code")
    run(open_code("%let _lnk_log_exists = ", "%_lnk_ensure_loaded("), "_open_code")
    for name, flag in ((TABLE, "_lnk_log_exists"), ("MODEL_CATALOG", "_lnk_catalog_exists"), ("RAG_RUNS", "_lnk_runs_exists")):
        macros.symbols.update({"_lnk_lib": LIB, "_lnk_name": name, "_lnk_flag": flag})
        run(program, "_lnk_ensure_loaded")
    run(open_code("%let _lnk_ready = 1;", "* The number of rows of the log table."), "_open_code")
    return [line[len("ERROR: "):] if line.startswith("ERROR: ") else line for line in put], macros


class HaltingLink(Asking):
    """Asking, which halts the n-th call of the run - and, as a session that ended, every call behind it."""

    def __init__(self, halt_from, **more):
        super().__init__(**more)
        self.halt_from, self.count, self.halted = halt_from, 0, []

    def __call__(self, action, parameters):
        self.count += 1
        if self.count >= self.halt_from:
            self.halted.append(action)
            raise Halted(action)
        return super().__call__(action, parameters)

    def run(self, casl):
        try:
            return super().run(casl)
        except Halted as halted:
            self.lines.extend(_halt_lines(halted.args[0]))
            return dict(self.symbols)


def _three_tables(library, saved_only=()):
    for name in (TABLE, "MODEL_CATALOG", "RAG_RUNS"):
        table = table_of([("name", "varchar")], [{"name": name}])
        library.put_file(LIB, name + ".sashdat", table)
        if name not in saved_only:
            library.put(LIB, name, table)
    return library


def _calls_of_the_head(saved_only, save_table):
    counter = _three_tables(HaltingLink(halt_from=999), saved_only)
    put, macros = _link_head(counter, save_table)
    assert put == [] and macros["_lnk_ready"] == "1"
    return counter.count


@pytest.mark.parametrize("saved_only, save_table", [((), "1"), ((), "0"), ((TABLE,), "1"), (("MODEL_CATALOG", "RAG_RUNS"), "0")],
                         ids=["in memory", "in memory, the saved file looked for", "the log table saved only", "two saved only"])
def test_a_step_of_the_link_that_halted_links_nothing(saved_only, save_table):
    """A halt at each action in front of the link - the question what the log table is, the listing of its saved file,
    the questions and listings of _lnk_ensure_loaded and the load behind them: the run links nothing and writes the
    line that CAS did not say. Up to this change the flags stayed at 0, which said 'not found' for a table in memory."""
    calls = _calls_of_the_head(saved_only, save_table)
    assert calls >= 4
    for halt_from in range(1, calls + 1):
        library = _three_tables(HaltingLink(halt_from=halt_from), saved_only)
        put, macros = _link_head(library, save_table)

        assert library.halted and put == [LINK_NOT_SAID] and macros["_lnk_ready"] == "0", (halt_from, library.halted, put)


def _output_part():
    program = LINK_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    macro = program[program.index("%macro _lnk_handle_output_table;"):program.index("%mend _lnk_handle_output_table;")]
    return macro[macro.index("%if &_lnk_row_count. GT 0 and &_lnk_unchanged. NE 1 %then %do;"):macro.index("%else %if &_lnk_row_count. EQ 0 %then %do;")]


def _put_in_place(library):
    library.put(LIB, TABLE, table_of([("name", "varchar")], [{"name": "before"}]))
    library.put(LIB, TABLE + "_NEW", table_of([("name", "varchar")], [{"name": "linked"}]), scope="session")
    return _follow_part(library, _output_part(), {"_lnk_row_count": "5", "_lnk_unchanged": "0", "_lnk_promote_table": "1",
                                                  "_lnk_save_table": "1", "_lnk_log_scope": "2", "_lnk_log_saved": "0", "_lnk_failed": "0"})


def test_a_new_table_whose_step_halted_is_a_failed_run():
    """The step that puts the new table in place, halted at each of its actions: _lnk_failed is 1 and the run ends with
    SYSCC 1012. Up to this change it stayed 0 - a run that had not put its table in place ended as completed."""
    counter = HaltingLink(halt_from=999)
    put, macros = _put_in_place(counter)
    assert macros["_lnk_failed"] == "0" and counter.count == 6
    for halt_from in range(1, counter.count + 1):
        library = HaltingLink(halt_from=halt_from)
        put, macros = _put_in_place(library)
        assert library.halted and macros["_lnk_failed"] == "1", halt_from


def test_a_new_table_cas_refused_to_say_is_there_is_named_as_such():
    """gate 22: where CAS refused table.tableExists for <table>_NEW the program wrote that the new table could not be
    created - it had been created. The line says what is known now."""
    library = Asking(refuse_asks={(TABLE + "_NEW", 1)})
    put, macros = _put_in_place(library)
    assert ("ERROR", f"CAS refused to say whether the new table {LIB}.{TABLE}_NEW is there, so it did not take the place of the table "
                     f"{LIB}.{TABLE}, which was left as it is.") in library.lines
    assert not [line for line in library.lines if "could not be created" in line[1]]
    assert macros["_lnk_failed"] == "1" and library.get(LIB, TABLE)["rows"] == [{"name": "before"}]

    # a new table that CAS says is not there could not be created
    library = Asking()
    library.put(LIB, TABLE, table_of([("name", "varchar")], [{"name": "before"}]))
    put, macros = _follow_part(library, _output_part(), {"_lnk_row_count": "5", "_lnk_unchanged": "0", "_lnk_promote_table": "1",
                                                         "_lnk_save_table": "1", "_lnk_log_scope": "2", "_lnk_log_saved": "0", "_lnk_failed": "0"})
    assert ("ERROR", f"The new table could not be created, the table {LIB}.{TABLE} was left as it is.") in library.lines
    assert macros["_lnk_failed"] == "1"


PROMOTE_NOT_SAID = (f"WARNING: The table {LIB}.{TABLE} was not in memory. It was loaded from its saved file, and CAS did not say whether it "
                    "was promoted then: where it was not, it is a table of this CAS session and gone when the session ends. The saved file "
                    "was left as it is.")


@pytest.mark.parametrize("halt_from", [1, 2], ids=["the promote", "the question behind it"])
def test_a_loaded_table_whose_promote_step_halted_is_said_to_be_unknown(halt_from):
    """Up to this change _lnk_log_scope stayed 0 behind such a step, and the WARNING said that the table could not be
    promoted."""
    library = HaltingLink(halt_from=halt_from)
    library.put(LIB, TABLE, table_of([("name", "varchar")], [{"name": "loaded"}]), scope="session")
    program = LINK_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    macro = program[program.index("%macro _lnk_handle_output_table;"):program.index("%mend _lnk_handle_output_table;")]
    outer = macro.index("%if (&_lnk_unchanged. EQ 1 or &_lnk_row_count. EQ 0)")
    start = macro.index("%if &_lnk_promote_table. %then %do;", outer)
    end = macro.rindex("%end;", start, macro.index("\n    proc cas;\n        table.dropTable", start))
    put, macros = _follow_part(library, macro[start:end], {"_lnk_promote_table": "1", "_lnk_log_scope": "0"})

    assert library.halted and put == [PROMOTE_NOT_SAID] and macros["_lnk_log_scope"] == "-1"


# ---------------------------------------------------------------------------
# Log-Parser-Code.sas: every action of every step that sets a macro variable, halted
# ---------------------------------------------------------------------------

ACTION = re.compile(r"\btable\.(\w+)\b[^;]*?/")


def _halts(program):
    """The actions of the steps of the count and of the output macro that set a macro variable, as matches in the
    program: the place of the slash behind each, where a test marks it."""
    found = []
    for macro in ("_log_count_target", "_log_handle_output_table"):
        start, end = program.index(f"%macro {macro};"), program.index(f"%mend {macro};")
        for step in STEP.finditer(program, start, end):
            if "call symputx(" in step.group(0):
                found += list(ACTION.finditer(program, step.start(), step.end()))
    return found


def _marked(program, action):
    return program[:action.end()] + " zzHalt = 1," + program[action.end():]


def test_the_class_holds_every_action_of_the_steps_of_the_parser_that_set_a_macro_variable():
    names = [action.group(1) for action in _halts(parser_program())]
    assert names == ["tableExists", "tableInfo", "fileInfo", "loadTable", "tableInfo", "dropTable",
                     "tableExists", "fileInfo", "loadTable", "tableExists", "dropTable", "columnInfo", "tableInfo",
                     "promote", "tableInfo", "tableInfo", "copyTable", "tableExists", "dropTable", "dropTable", "alterTable", "alterTable",
                     "promote", "dropTable", "dropTable", "save"]


def _scenarios():
    """(setting, saved_only, the requests of the run, the DATA steps that do not run): a table of 3 requests in memory or
    as its saved file, a run that adds one request, a run that appends the 3 it holds, and a run whose DATA step of the
    append does not run - the new table is then not put in place."""
    for setting in (None, "0", "1"):
        for saved_only in (False, True):
            yield setting, saved_only, (7,), ()
    yield "1", True, (1, 2, 3), ()
    yield "1", False, (1, 2, 3), ()
    yield "1", True, (7,), ("_TEMP_LLM_LOGS_ALL",)


@pytest.mark.parametrize("ended", [False, True], ids=["the one step halts", "the session ended"])
def test_a_step_of_the_parser_that_halted_is_never_an_answer(monkeypatch, tmp_path, ended):
    """Each action of the class halts in turn, in every state of the setting, at a table in memory and at a table that
    is only its saved file: once the one step alone, once as a session that ended - every call behind the halt halts
    too. Every run that reaches the action ends as a failed job, and a halt in front of the step that writes the table
    leaves the table and its saved file as they were. Up to this change a halt left the values of a table that is not
    there - a run that appends replaced the table by the rows of the log - and a halted step that wrote the table left
    the run a completed job."""
    text = parser_program()
    halts = _halts(text)
    final = text.index("        %let _log_failed = 1;\n        proc cas;")
    reached = set()
    for number, action in enumerate(halts):
        marked = _marked(text, action)
        for setting, saved_only, requests, fail_steps in _scenarios():
            w = World(monkeypatch, tmp_path, text, HaltingLibrary(ended=ended))
            first = w.run("1", request(1), request(2), request(3))
            assert first.errors == [] and not w.library.halted
            w.library.fail_steps = set(fail_steps)
            if saved_only:
                w.only_saved()
            before = w.state()
            run = w.run(setting, *[request(n) for n in requests], program=marked)
            if not w.library.halted:
                continue
            reached.add(number)
            assert run.syscc == FAILED, (number, action.group(1), setting, saved_only, requests, run.errors)
            if action.start() < final:
                after = w.state()
                if before["exists"]:
                    assert after == before, (number, action.group(1), setting, saved_only, requests)
                else:
                    # a table that was only its saved file: the file is as it was, and where the halted step had loaded it
                    # before it halted, it is a table of the session that holds what the file holds - nothing was promoted
                    assert after["file"] == before["file"] and after["exists"] in (0, 1), (number, action.group(1), setting, requests)
                    assert after["rows"] in (None, before["file"]), (number, action.group(1), setting, requests)
                assert not [note for note in run.notes if "were written to the table" in note or "were appended to the table" in note]
    assert reached == set(range(len(halts))), sorted(set(range(len(halts))) - reached)


def test_the_parser_says_cas_refused_or_did_not_run_the_action(monkeypatch, tmp_path):
    """The line of a run that appends where the step that asks whether the table is there halted."""
    text = parser_program()
    asked = next(action for action in _halts(text) if action.group(1) == "tableExists" and action.start() > text.index("%macro _log_handle_output_table;"))
    w = World(monkeypatch, tmp_path, text, HaltingLibrary())
    w.run("1", request(1), request(2), request(3))
    before = w.state()
    run = w.run("1", request(7), program=_marked(text, asked))

    assert run.errors == ["There is no server connection to execute the action 'table.tableExists'.", "Execution halted",
                          "CAS refused or did not run an action that the run needs for the table Public.LLM_LOGS, nothing was appended and "
                          "neither the table nor its saved file was written."]
    assert w.state() == before and run.syscc == FAILED and run.macros["_log_cas_refused"] == "1"


def test_the_step_of_the_parser_says_it_in_its_about_tab_and_its_changelog():
    ui = json.loads(parser_step()["ui"])
    texts = {}

    def walk(children):
        for child in children:
            if child.get("type") == "text":
                texts[child["id"]] = child["text"]
            walk(child.get("children", []))
    walk(ui["pages"])

    assert texts["textChangelog"].startswith("* Version: 2.11 (15OCT2026)\n     - A PROC CAS step that halts")
    assert ("an action that the step goes by and that CAS refused or did not run - the question whether the output table is there, the "
            "listing of the saved files, of the columns of the table or of the tables of CASUSER -, a new table that could not be created "
            "and a PROC CAS step that halted while it wrote the table.") in texts["textAbout"]


# ---------------------------------------------------------------------------
# Load-Fact-Sheets.sas
# ---------------------------------------------------------------------------

def _sheets():
    import test_fact_sheets
    return test_fact_sheets


SHEET_NOT_SAID = ("WARNING: CAS did not say whether there is a table Public.LLM_FACT_SHEET_NEW, the name the sheet is loaded under first. "
                  "The file llm_fact_sheet.csv was not loaded into CAS, the table Public.LLM_FACT_SHEET was left as it is.")
SHEET_NOT_COUNTED = ("WARNING: The 30 rows of the file llm_fact_sheet.csv could not be loaded into the CAS library Public - CAS held -1 of "
                     "them, -1 stands for a table that CAS did not say is there or did not count. The table Public.LLM_FACT_SHEET was left "
                     "as it is.")
SWAP_NOT_SAID = "WARNING: Nothing said what became of the sheet - messages above -, the table Public.LLM_FACT_SHEET may not hold it."


@pytest.mark.parametrize("halt_from, line", [(1, SHEET_NOT_SAID), (2, SHEET_NOT_COUNTED), (3, SHEET_NOT_COUNTED)],
                         ids=["the question for the name", "the question for the upload", "the count of the upload"])
def test_a_sheet_whose_step_halted_is_not_loaded(halt_from, line):
    """Up to this change a halt of the first question read as 'tableExists said unknown' - an answer CAS never gave."""
    sheets = _sheets()
    cas = HaltingCAS(halt_from=halt_from)
    cas.table("Public", "LLM_FACT_SHEET", sheets.COLUMNS, rows=28, of="the table of before")
    generated, log, asked, macros = sheets.follow("_lfs_read", cas, status="200", there=1, rows=30, loads=30)

    assert cas.halted and log == [line] and macros["_lfs_ok"] == "0"
    assert sheets.in_memory(cas)["of"] == "the table of before"


def test_a_swap_whose_step_halted_says_that_nothing_said_what_became_of_the_sheet():
    """Up to this change the swap wrote nothing at all behind a halted step - the line of a sheet that took the place of
    the table. The step is halted at each of its actions in turn."""
    sheets = _sheets()
    counter = HaltingCAS(halt_from=999)
    counter.table("Public", "LLM_FACT_SHEET", sheets.COLUMNS, rows=28, of="the table of before")
    counter.file("Public", "LLM_FACT_SHEET.sashdat", sheets.COLUMNS, rows=28, of="the table of before")
    counter.table("Public", "LLM_FACT_SHEET_NEW", sheets.COLUMNS, rows=30, of="the sheet", scope="session")
    assert sheets.swapped(counter) == [] and counter.count == 3
    for halt_from in range(1, counter.count + 1):
        cas = HaltingCAS(halt_from=halt_from)
        cas.table("Public", "LLM_FACT_SHEET", sheets.COLUMNS, rows=28, of="the table of before")
        cas.file("Public", "LLM_FACT_SHEET.sashdat", sheets.COLUMNS, rows=28, of="the table of before")
        cas.table("Public", "LLM_FACT_SHEET_NEW", sheets.COLUMNS, rows=30, of="the sheet", scope="session")
        assert sheets.swapped(cas) == [SWAP_NOT_SAID], halt_from


# ---------------------------------------------------------------------------
# The Python (swat) of the same programs
# ---------------------------------------------------------------------------

PYTHON = re.compile(r"^[ \t]*submit;[ \t]*\n(.*?)^[ \t]*endsubmit;", flags=re.MULTILINE | re.DOTALL)
SWAT_CALLS = ("retrieve", "run_action", "read_action", "upload_frame")


def _python_blocks():
    """{program: [the source of each Python block]} for every program of _programs() that holds one."""
    found = {}
    for name, text in _programs().items():
        for block in PYTHON.findall(text.replace("\r\n", "\n")):
            found.setdefault(name, []).append(textwrap.dedent(block))
    return found


def _called(node):
    function = node.func
    return function.attr if isinstance(function, ast.Attribute) else function.id if isinstance(function, ast.Name) else ""


def test_every_swat_action_whose_answer_a_program_reads_takes_any_severity_but_0_for_no_answer():
    """The class for the Python, taken from every Python block of every program and step that is not frozen: an action
    whose answer is read goes through read_action. conn.retrieve and run_action are called only where nothing of the
    answer is read but its severity; run_action, which takes a warning for an answer, runs the actions that write, and
    what they wrote is read behind them."""
    reads, writes = Counter(), Counter()
    for name, blocks in _python_blocks().items():
        for source in blocks:
            tree = ast.parse(source)
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            # a swat action reached as an attribute of the connection (conn.table.fetch(...)) is no way of this class
            assert not re.search(r"\bconn\.(table|simple|dataStep|builtins|session)\.\w+\(", source), name
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _called(node) not in SWAT_CALLS:
                    continue
                if isinstance(node.func, ast.Name) and _called(node) in ("run_action", "read_action"):
                    action = node.args[1].value
                elif _called(node) == "retrieve":
                    if not isinstance(node.args[0], ast.Constant):
                        # the call of the two functions themselves, which their callers name the action to
                        outer = node
                        while not isinstance(outer, ast.FunctionDef):
                            outer = parents[outer]
                        assert outer.name in ("run_action", "read_action"), (name, ast.unparse(node))
                        continue
                    action = node.args[0].value
                else:
                    continue
                parent = parents[node]
                if _called(node) == "read_action":
                    reads[(name, action)] += 1
                    continue
                writes[(name, action)] += 1
                # the answer of a write is not read: the call is a statement, or its severity alone is asked for
                if isinstance(parent, ast.Assign):
                    target = parent.targets[0].id
                    uses = [use for use in ast.walk(tree) if isinstance(use, ast.Name) and use.id == target and use is not parent.targets[0]]
                    assert all(isinstance(parents[use], ast.Call) and ast.unparse(parents[use]).startswith(f"getattr({target}, 'severity'")
                               for use in uses), (name, action)
                else:
                    assert isinstance(parent, ast.Expr), (name, action, ast.unparse(parent)[:80])
    assert reads == Counter({("Logging-Monitoring/Link-Logs-To-Models.sas", "table.columnInfo"): 1,
                             ("Logging-Monitoring/Link-Logs-To-Models.sas", "table.tableInfo"): 1,
                             ("Logging-Monitoring/Link-Logs-To-Models.sas", "table.fetch"): 1,
                             ("Logging-Monitoring/Link-Logs-To-Models.sas", "simple.numRows"): 1})
    assert writes == Counter({("Logging-Monitoring/Link-Logs-To-Models.sas", "dataStep.runCode"): 2,
                              ("Logging-Monitoring/Link-Logs-To-Models.sas", "table.dropTable"): 2,
                              ("Logging-Monitoring/Link-Logs-To-Models.sas", "table.alterTable"): 2})
    # the programs whose Python the class was looked for in
    assert sorted(_python_blocks()) == ["Custom-Steps/LLM - Get All Prompts.step", "Custom-Steps/LLM - Log Parser.step",
                                        "Logging-Monitoring/Get-All-Prompts.sas", "Logging-Monitoring/Link-Logs-To-Models.sas",
                                        "Logging-Monitoring/Log-Parser-Code.sas", "SAS-Code-LLM-Calls/Track-Prompt-Experiments.sas",
                                        "Setup/Start-Here.sas"]


class _Answer(dict):
    def __init__(self, severity=0, **values):
        super().__init__(**values)
        self.severity, self.reason, self.status = severity, "", ""


@pytest.mark.parametrize("severity, taken", [(0, True), (1, False), (2, False)], ids=["normal", "a warning", "an error"])
def test_read_action_takes_only_an_answer_of_severity_0(monkeypatch, severity, taken):
    functions = link.functions(monkeypatch)

    class Connection:
        def retrieve(self, action, **parameters):
            return _Answer(severity, numrows=5)

    if taken:
        assert functions["row_count"](Connection(), "Public", "LLM_LOGS") == 5
    else:
        with pytest.raises(RuntimeError, match=f"The answer of CAS to the action simple.numRows has the severity {severity}, and only 0 is taken"):
            functions["row_count"](Connection(), "Public", "LLM_LOGS")
        assert functions["label_of"](Connection(), "Public", "LLM_LOGS") is None


def test_a_label_cas_did_not_answer_for_is_said_to_be_lost(monkeypatch):
    """review 22: a refused table.tableInfo was read as a table without a label, and the new table lost the label of the
    log table without a line. The links are made, and a NOTE says that the label could not be read."""
    from test_link_logs import showcase_tables

    def refused_info(self, caslib, name):
        return _Answer(1, TableInfo=pd.DataFrame({"Name": [name.upper()], "Label": ["what a warning came with"]}))

    monkeypatch.setattr(link.FakeCAS, "_table_tableInfo", refused_info)
    run = link.run_program(monkeypatch, showcase_tables(), table_labels={("Public", "LLM_LOGS"): "Extracted LLM Log Data"})

    assert run.sas.macro_variables["_lnk_row_count"] == "6" and run.sas.lines("ERROR") == []
    assert "The label of the table Public.LLM_LOGS could not be read, so the linked table has none." in run.sas.lines("NOTE")
    assert run.new_table_label == ""


@pytest.mark.parametrize("action", ["table.columnInfo", "table.fetch", "simple.numRows"])
def test_a_read_cas_answered_with_a_warning_links_nothing(monkeypatch, action):
    """Up to this change an answer of severity 1 was read as what the table holds."""
    from test_link_logs import showcase_tables

    method = "_" + action.replace(".", "_")
    answered = getattr(link.FakeCAS, method)

    def warned(self, *arguments, **parameters):
        result = answered(self, *arguments, **parameters)
        result.severity = 1
        return result

    monkeypatch.setattr(link.FakeCAS, method, warned)
    with pytest.raises(RuntimeError, match=f"The answer of CAS to the action {re.escape(action)} has the severity 1"):
        link.run_program(monkeypatch, showcase_tables())


# ---------------------------------------------------------------------------
# What the READMEs quote
# ---------------------------------------------------------------------------

def test_the_readmes_quote_the_lines_as_the_programs_write_them():
    readme = " ".join((INTEGRATIONS / "Logging-Monitoring" / "README.md").read_text(encoding="utf-8").split())
    setup = " ".join((INTEGRATIONS / "Setup" / "README.md").read_text(encoding="utf-8").split())

    assert "`" + NOT_SAID.replace("Public", "<caslib>", 1).replace("RAG_HR_RUNS", "<table>").replace("RAG_RUNS.", "<RAG_RUNS>.") + "`" in readme
    assert "`ERROR: " + LINK_NOT_SAID + "`" in readme
    assert ("`ERROR: CAS refused to say whether the new table <caslib>.<table>_NEW is there, so it did not take the place of the table "
            "<caslib>.<table>, which was left as it is.`") in readme
    assert "`" + SWAP_NOT_SAID.replace("Public.LLM_FACT_SHEET", "<caslib>.<table>") + "`" in readme
    for old in ("CAS refused to say whether a table the program reads", "CAS refused an action that the", "CAS refused to say whether it took"):
        assert old not in readme, old
    assert "or CAS does not say whether it is," in setup and "refuses to say" not in setup
