"""The class 'an action CAS refused is no answer', closed in every program that is not frozen (fix-content-22).

Round 22 found the class closed only part of the way: table.fileInfo carried a status in every program, and every
action of the two macros of the parser did, but table.tableExists was read as `<result>.exists` without asking whether
CAS had refused it - in the link program, the loader, the fact sheets, the cost view and Get-All-Prompts.sas -, and so
were a few listings (table.tableInfo, table.columnInfo, table.caslibInfo): a refused count of rows was 0 rows, a refused
listing of columns a table without them. Every such action carries status= now, and the statement right behind it reads
the severity.

- The first test takes every action whose answer a program reads from the programs and the steps themselves and holds
  the rule: status= and a look at the severity before anything else. The few that read no status are named, each with
  the reason it needs none.
- The other tests refuse each of those actions in turn, in the made-up CAS libraries of log_parser_table_harness and
  casl_harness, and follow what the program does then. Up to this change each of them stopped the harness at the
  statement that read the answer of the refused action, or read a refused listing as 0 rows.

SAS and CAS can not run here: the tests say what the programs do IF CAS answers a refused action with a severity
other than 0, as the made-up libraries have it.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

import casl_harness
from log_parser_table_harness import Library, Macros, table_of
from test_content_22 import LIB, LINK_FILE, LINK_REFUSED, TABLE, _follow_link

INTEGRATIONS = Path(__file__).resolve().parents[2]
FROZEN = ("RAG-Ingestion/Ingest-Documents.sas", "RAG-Ingestion/Test-Retrieval.sas", "Prompt-Optimization/Optimize-Prompt-DSPy.sas")


# ---------------------------------------------------------------------------
# The class, taken from the code
# ---------------------------------------------------------------------------

READ = re.compile(r"\b(\w+\.\w+)\s+result\s*=\s*(\w+)\s*(?:status\s*=\s*(\w+)\s*)?(?:/[^;]*)?;")


def _code(text):
    """A program without its comments: the /* */ ones and the statements that start with an asterisk."""
    text = re.sub(r"/\*.*?\*/", "", text.replace("\r\n", "\n"), flags=re.DOTALL)
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("*"))


def _programs():
    """Every program of SAS-Viya-Integrations that is not frozen, and the program of every step that is no RAG step."""
    found = {}
    for path in sorted(INTEGRATIONS.rglob("*.sas")):
        relative = path.relative_to(INTEGRATIONS).as_posix()
        if relative not in FROZEN and "/tests/" not in relative:
            found[relative] = path.read_text(encoding="utf-8")
    for path in sorted((INTEGRATIONS / "Custom-Steps").glob("*.step")):
        if not path.name.startswith("RAG - "):
            found["Custom-Steps/" + path.name] = json.loads(path.read_bytes().decode("utf-8"))["templates"]["SAS"]
    return found


def _reads():
    """{program: [(action, result, status, the statement behind it)]} for every action whose answer the program reads."""
    found = {}
    for name, text in _programs().items():
        code = _code(text)
        for match in READ.finditer(code):
            behind = " ".join(code[match.end():code.index(";", match.end())].split())
            found.setdefault(name, []).append((match.group(1), match.group(2), match.group(3), behind))
    return found


# The actions that read no status, and why they need none: the id of the session is no answer about a table, and the
# four listings of the parser are followed with a refusal each in test_content_22.py (the class of its two macros)
NO_STATUS = {
    "Logging-Monitoring/Link-Logs-To-Models.sas": ["session.sessionId"],
    "Logging-Monitoring/Log-Parser-Code.sas": ["session.sessionId", "table.tableInfo", "table.tableInfo", "table.tableInfo", "table.tableInfo"],
    "Custom-Steps/LLM - Log Parser.step": ["session.sessionId", "table.tableInfo", "table.tableInfo", "table.tableInfo", "table.tableInfo"],
}


def test_every_action_whose_answer_a_program_reads_takes_a_refusal_for_no_answer():
    reads = _reads()

    for name, actions in reads.items():
        for action, result, status, behind in actions:
            if status is None:
                continue
            assert re.match(rf"if {status}\.severity (NE|!=|EQ|==) 0 then ", behind), (name, action, result, behind)
    assert {name: [action for action, _, status, _ in actions if status is None] for name, actions in reads.items()
            if any(status is None for _, _, status, _ in actions)} == NO_STATUS
    # the class as it stands: a new action that reads an answer fails this test until it is decided how its refusal reads
    assert {name: Counter(action for action, _, status, _ in actions if status) for name, actions in reads.items()} == {
        "Custom-Steps/LLM - Get All Prompts.step": Counter({"table.tableExists": 2, "table.caslibInfo": 1}),
        "Custom-Steps/LLM - Log Parser.step": Counter({"table.tableExists": 4, "table.fileInfo": 2, "table.columnInfo": 1, "table.tableInfo": 1}),
        "Logging-Monitoring/Build-RAG-Cost-View.sas": Counter({"table.tableExists": 1, "table.tableInfo": 1}),
        "Logging-Monitoring/Get-All-Prompts.sas": Counter({"table.tableExists": 2, "table.caslibInfo": 1}),
        "Logging-Monitoring/Link-Logs-To-Models.sas": Counter({"table.tableExists": 5, "table.fileInfo": 2}),
        "Logging-Monitoring/Load-Fact-Sheets.sas": Counter({"table.tableExists": 3, "table.tableInfo": 1}),
        "Logging-Monitoring/Log-Parser-Code.sas": Counter({"table.tableExists": 4, "table.fileInfo": 2, "table.columnInfo": 1, "table.tableInfo": 1}),
        "Setup/Load-Accelerator-Tables.sas": Counter({"table.tableExists": 5, "table.fileInfo": 1, "table.tableInfo": 1, "table.columnInfo": 1,
                                                      "table.loadTable": 1}),
    }


def test_the_step_runs_the_core_of_get_all_prompts_as_the_code_file_has_it():
    """The step LLM - Get All Prompts holds the lines between the two gap core comments of the code file, with the
    line ends of its own program."""
    start = "* gap core - from here to the line that ends the core the custom step LLM - Get All Prompts runs the same lines;"
    end = "* end of the gap core;"
    programs = _programs()
    code = programs["Logging-Monitoring/Get-All-Prompts.sas"].replace("\r\n", "\n")
    step = programs["Custom-Steps/LLM - Get All Prompts.step"].replace("\r\n", "\n")

    assert code[code.index(start):code.index(end)] == step[step.index(start):step.index(end)]


# ---------------------------------------------------------------------------
# A made-up CAS library that refuses one question
# ---------------------------------------------------------------------------

class Asking(Library):
    """The made-up library of log_parser_table_harness, which refuses table.tableExists for the (TABLE, n) pairs in
    `refuse_asks` - the n-th question for that table - or for every question of a table in `refuse_tables`. It lists
    `caslibs` as the CAS libraries of the session (table.caslibInfo), or refuses that listing."""

    def __init__(self, refuse_asks=(), refuse_tables=(), caslibs=("Public", "CASUSER"), refuse_listing=False, **more):
        super().__init__(**more)
        self.refuse_asks, self.refuse_tables, self.asked = set(refuse_asks), set(refuse_tables), Counter()
        self.caslibs, self.refuse_listing = list(caslibs), refuse_listing

    def __call__(self, action, parameters):
        if action == "table.caslibInfo":
            self.calls.append((action, parameters))
            if self.refuse_listing:
                return self._end(action, error=f"The action {action} was refused.")
            return self._end(action, {"CASLibInfo": [SimpleNamespace(Name=caslib) for caslib in self.caslibs]})
        if action == "table.tableExists":
            name = str(parameters.get("name", "")).upper()
            self.asked[name] += 1
            if name in self.refuse_tables or (name, self.asked[name]) in self.refuse_asks:
                self.calls.append((action, parameters))
                return self._end(action, error=f"The action {action} was refused.")
        return super().__call__(action, parameters)


def _three_tables(saved_only=(), **refusals):
    library = Asking(**refusals)
    for name in (TABLE, "MODEL_CATALOG", "RAG_RUNS"):
        table = table_of([("name", "varchar")], [{"name": name}])
        library.put_file(LIB, name + ".sashdat", table)
        if name not in saved_only:
            library.put(LIB, name, table)
    return library


FLAGS = {TABLE: "_lnk_log_exists", "MODEL_CATALOG": "_lnk_catalog_exists", "RAG_RUNS": "_lnk_runs_exists"}
NAMES = pytest.mark.parametrize("name", list(FLAGS), ids=["log table", "catalog", "ingestion runs"])


# ---------------------------------------------------------------------------
# Link-Logs-To-Models.sas: the questions in front of the link
# ---------------------------------------------------------------------------

@NAMES
def test_the_link_takes_no_answer_for_a_table_in_memory_where_cas_refused_the_question(name):
    """A table in memory whose question CAS refused is not taken for a table that is not there: nothing is linked."""
    put, macros = _follow_link(_three_tables(refuse_tables={name}))

    assert put == [LINK_REFUSED] and macros["_lnk_ready"] == "0" and macros[FLAGS[name]] == "-1"


def test_the_link_takes_no_answer_for_what_the_log_table_is_where_cas_refused_the_question():
    """The first question - what the log table is, which decides whether it is promoted again - refused alone."""
    library = _three_tables(refuse_asks={(TABLE, 1)})
    put, macros = _follow_link(library)

    assert put == [LINK_REFUSED] and macros["_lnk_ready"] == "0"
    assert macros["_lnk_log_scope"] == "-1" and macros["_lnk_log_exists"] == "1"


@NAMES
def test_the_link_takes_no_answer_for_a_table_it_loaded_where_cas_refused_the_question_behind_the_load(name):
    """A table that is only its saved file is loaded and then asked for again: a refusal there is no answer either."""
    nth = 3 if name == TABLE else 2
    put, macros = _follow_link(_three_tables(saved_only=(name,), refuse_asks={(name, nth)}))

    assert put == [LINK_REFUSED] and macros["_lnk_ready"] == "0" and macros[FLAGS[name]] == "-1"


@NAMES
def test_the_link_goes_on_where_cas_answers(name):
    put, macros = _follow_link(_three_tables(saved_only=(name,)))

    assert put == [] and macros["_lnk_ready"] == "1" and macros[FLAGS[name]] == "1" and macros["_lnk_log_scope"] in ("0", "2")


# ---------------------------------------------------------------------------
# Link-Logs-To-Models.sas: the questions behind the link
# ---------------------------------------------------------------------------

def _output_macro():
    program = LINK_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    return program[program.index("%macro _lnk_handle_output_table;"):program.index("%mend _lnk_handle_output_table;")]


def _follow_part(library, part, symbols):
    """A part of the output macro as a macro of its own, with its PROC CAS steps run against the made-up library."""
    macros = Macros(dict(symbols, _lnk_log_caslib=LIB, _lnk_log_table=TABLE))
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

    text = "%macro _zz_part;\n" + part + "\n%mend _zz_part;\n"
    put = macros.run(casl_harness.in_blocks(text, "_zz_part"), "_zz_part", on_text)[1]
    return put, macros


def _linked(refuse_asks=()):
    """The log table promoted and the new table of a run that linked 5 rows beside it."""
    library = Asking(refuse_asks=refuse_asks)
    library.put(LIB, TABLE, table_of([("name", "varchar")], [{"name": "before"}]))
    library.put(LIB, TABLE + "_NEW", table_of([("name", "varchar")], [{"name": "linked"}]), scope="session")
    macro = _output_macro()
    part = macro[macro.index("%if &_lnk_row_count. GT 0 and &_lnk_unchanged. NE 1 %then %do;"):macro.index("%else %if &_lnk_row_count. EQ 0 %then %do;")]
    put, macros = _follow_part(library, part, {"_lnk_row_count": "5", "_lnk_unchanged": "0", "_lnk_promote_table": "1", "_lnk_save_table": "1",
                                               "_lnk_log_scope": "2", "_lnk_log_saved": "0", "_lnk_failed": "0"})
    return library, put, macros


def test_a_new_table_that_cas_did_not_say_is_there_takes_the_place_of_nothing():
    library, put, macros = _linked(refuse_asks={(TABLE + "_NEW", 1)})

    assert ("ERROR", f"The new table could not be created, the table {LIB}.{TABLE} was left as it is.") in library.lines
    assert macros["_lnk_failed"] == "1"
    assert library.get(LIB, TABLE)["rows"] == [{"name": "before"}] and library.exists(LIB, TABLE) == 2
    assert not [action for action, _ in library.calls if action in ("table.dropTable", "table.alterTable", "table.save")]


def test_a_new_table_that_cas_says_is_there_takes_the_place_of_the_log_table():
    library, put, macros = _linked()

    assert macros["_lnk_failed"] == "0" and library.get(LIB, TABLE)["rows"] == [{"name": "linked"}] and library.exists(LIB, TABLE) == 2


PROMOTE_UNANSWERED = (f"WARNING: The table {LIB}.{TABLE} was not in memory. It was loaded from its saved file, and CAS refused to say "
                      "whether it was promoted then: where it was not, it is a table of this CAS session and gone when the session ends. "
                      "The saved file was left as it is.")


@pytest.mark.parametrize("refused", [True, False], ids=["refused", "answered"])
def test_a_loaded_table_whose_promotion_cas_did_not_answer_is_said_to_be_unknown(refused):
    """A log table loaded from its saved file and not linked is promoted, and asked for again: a refusal of that question
    is neither the NOTE of a promoted table nor the WARNING of one that could not be promoted."""
    library = Asking(refuse_asks={(TABLE, 1)} if refused else ())
    library.put(LIB, TABLE, table_of([("name", "varchar")], [{"name": "loaded"}]), scope="session")
    macro = _output_macro()
    outer = macro.index("%if (&_lnk_unchanged. EQ 1 or &_lnk_row_count. EQ 0)")
    start = macro.index("%if &_lnk_promote_table. %then %do;", outer)
    end = macro.rindex("%end;", start, macro.index("\n    proc cas;\n        table.dropTable", start))
    put, macros = _follow_part(library, macro[start:end], {"_lnk_promote_table": "1", "_lnk_log_scope": "0"})

    if refused:
        assert put == [PROMOTE_UNANSWERED] and macros["_lnk_log_scope"] == "-1"
    else:
        assert put == [f"NOTE: The table {LIB}.{TABLE} was not in memory. It was loaded from its saved file and promoted as it was "
                       "loaded, the saved file was left as it is."]
    assert library.exists(LIB, TABLE) == 2


# ---------------------------------------------------------------------------
# casl_harness: a made-up CAS library that refuses one question or listing
# ---------------------------------------------------------------------------

class RefusingCAS(casl_harness.FakeCAS):
    """casl_harness.FakeCAS, which refuses tableExists, tableInfo, columnInfo and caslibInfo for the (action, TABLE, n)
    entries of `asks` - n the n-th call for that table, None every call - and answers caslibInfo with `caslibs`."""

    def __init__(self, asks=(), caslibs=("Public", "CASUSER"), **more):
        super().__init__(**more)
        self.asks, self.caslibs, self.counted = set(asks), list(caslibs), Counter()

    def __call__(self, action, parameters):
        if action in ("table.tableExists", "table.tableInfo", "table.columnInfo", "table.caslibInfo"):
            name = str(parameters["table"]["name"] if action == "table.columnInfo" else parameters.get("name", "")).upper()
            self.counted[(action, name)] += 1
            if (action, name, None) in self.asks or (action, name, self.counted[(action, name)]) in self.asks:
                self.calls.append((action, parameters))
                return self._end(action, error="the action was refused")
            if action == "table.caslibInfo":
                self.calls.append((action, parameters))
                return self._end(action, {"CASLibInfo": [SimpleNamespace(Name=caslib) for caslib in self.caslibs]})
        return super().__call__(action, parameters)

    def run(self, casl):
        """FakeCAS.run, for a step that also lists the CAS libraries (an action without parameters) and lists the names
        of a DO OVER loop in braces, as the look at the end of Load-Accelerator-Tables does."""
        casl = re.sub(r"(table\.caslibInfo result=\w+ status=\w+);", r"\1 / ;", casl)
        listed = re.search(r"do (\w+) over \{(.*?)\};", casl, flags=re.DOTALL)
        names = []
        if listed:
            names = re.findall(r'"([^"]*)"', listed.group(2))
            casl = casl.replace(listed.group(0), f"do {listed.group(1)} over zzNames;")

        def symputx(name, value, scope=""):
            # CALL SYMPUTX leaves out the blanks in front of and behind the value
            self.symbols[name] = str(value).strip()

        namespace = {"_cas": self, "_symputx": symputx, "_rows": list, "exists": lambda result, name: hasattr(result, name),
                     "upcase": lambda text: str(text).upper(), "index": lambda text, part: str(text).find(str(part)) + 1, "zzNames": names}
        exec(compile(casl_harness.as_python(casl), "CASL", "exec"), namespace)
        return dict(self.symbols)


# ---------------------------------------------------------------------------
# Build-RAG-Cost-View.sas: what CAS holds under the name of the output table
# ---------------------------------------------------------------------------

def _built(cas):
    program = (INTEGRATIONS / "Logging-Monitoring" / "Build-RAG-Cost-View.sas").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = program.index("proc cas;\n            built = -1;")
    step = program[start:program.index("quit;", start) + len("quit;")]
    step = step.replace("&_rcv_out_caslib.", "casuser").replace("&_rcv_out_table.", "RAG_RUN_COST")
    return cas.run(_code(step))["_rcv_built"]


@pytest.mark.parametrize("asks, built", [((), "15"), ({("table.tableExists", "RAG_RUN_COST", None)}, "-1"),
                                         ({("table.tableInfo", "", None)}, "-1")],
                         ids=["answered", "the question refused", "the listing refused"])
def test_the_cost_view_counts_no_rows_where_cas_refused_the_question_or_the_listing(asks, built):
    """Up to this change a refused listing counted 0 rows - a table that CAS holds without a row - and a refused
    question stopped the harness at the statement that read its answer."""
    cas = RefusingCAS(asks=asks)
    cas.table("casuser", "RAG_RUN_COST", ["run_id"], rows=15)

    assert _built(cas) == built


# ---------------------------------------------------------------------------
# Load-Fact-Sheets.sas
# ---------------------------------------------------------------------------

def _sheets():
    import test_fact_sheets
    return test_fact_sheets


def test_the_sheet_is_not_loaded_where_cas_refused_the_question_for_the_name_it_is_loaded_under():
    sheets = _sheets()
    cas = RefusingCAS(asks={("table.tableExists", "LLM_FACT_SHEET_NEW", 1)})
    cas.table("Public", "LLM_FACT_SHEET", sheets.COLUMNS, rows=28, of="the table of before")
    generated, log, asked, macros = sheets.follow("_lfs_read", cas, status="200", there=1, rows=30, loads=30)

    assert log == ["WARNING: CAS refused to say whether there is a table Public.LLM_FACT_SHEET_NEW, the name the sheet is loaded under "
                   "first. The file llm_fact_sheet.csv was not loaded into CAS, the table Public.LLM_FACT_SHEET was left as it is."]
    assert macros["_lfs_ok"] == "0" and not [line for line in generated if "load data=" in line]
    assert cas.session == {} and sheets.in_memory(cas)["of"] == "the table of before"


@pytest.mark.parametrize("asks", [{("table.tableExists", "LLM_FACT_SHEET_NEW", 2)}, {("table.tableInfo", "", None)}],
                         ids=["the question refused", "the listing refused"])
def test_a_sheet_whose_count_cas_refused_takes_the_place_of_nothing(asks):
    """Up to this change a refused listing counted 0 rows, and the WARNING said that CAS held 0 of them."""
    sheets = _sheets()
    cas = RefusingCAS(asks=asks)
    cas.table("Public", "LLM_FACT_SHEET", sheets.COLUMNS, rows=28, of="the table of before")
    generated, log, asked, macros = sheets.follow("_lfs_read", cas, status="200", there=1, rows=30, loads=30)

    assert log == ["WARNING: The 30 rows of the file llm_fact_sheet.csv could not be loaded into the CAS library Public - CAS held -1 of "
                   "them, -1 stands for a table that CAS did not say is there or did not count. The table Public.LLM_FACT_SHEET was left "
                   "as it is."]
    assert macros["_lfs_ok"] == "0" and cas.session == {} and sheets.in_memory(cas)["of"] == "the table of before"


def test_nothing_is_loaded_over_a_table_cas_did_not_answer_for_behind_a_refused_promote():
    """Behind a refused promote the program asks whether a table of the name is in memory before it loads the saved
    file: where CAS refuses that question, nothing is loaded, and the line is the one of a table not loaded again."""
    sheets = _sheets()
    cas = RefusingCAS(asks={("table.tableExists", "LLM_FACT_SHEET", 1)}, refuse={("table.promote", "LLM_FACT_SHEET")})
    cas.table("Public", "LLM_FACT_SHEET", sheets.COLUMNS, rows=28, of="the table of before")
    cas.file("Public", "LLM_FACT_SHEET.sashdat", sheets.COLUMNS, rows=28, of="the table of before")
    cas.table("Public", "LLM_FACT_SHEET_NEW", sheets.COLUMNS, rows=30, of="the sheet", scope="session")

    assert sheets.swapped(cas) == [sheets.GONE]
    assert not [action for action, _ in cas.calls if action == "table.loadTable"]
    assert cas.session == {} and cas.files[("public", "LLM_FACT_SHEET.sashdat")]["of"] == "the table of before"


# ---------------------------------------------------------------------------
# Get-All-Prompts.sas: the run table and the ledger table of a RAG setup
# ---------------------------------------------------------------------------

def _looked(library, caslib="Public", ledger="RAG_HR_LEDGER"):
    """The PROC CAS step that looks for the run table and the ledger table of a setup, run against the made-up library:
    (_gap_runs_exists, _gap_ledger_exists)."""
    program = (INTEGRATIONS / "Logging-Monitoring" / "Get-All-Prompts.sas").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = program.index("proc cas;\n            runsExist = -1;")
    step = program[start:program.index("quit;", start) + len("quit;")]
    opened = "%if %length(&_gap_rs_ledger.) GT 0 %then %do;"
    closed = step.index("%end;", step.index(opened))
    step = step[:step.index(opened)] + (step[step.index(opened) + len(opened):closed] if ledger else "") + step[closed + len("%end;"):]
    step = step.replace("&_gap_rs_caslib.", caslib).replace("&_gap_rs_table.", "RAG_HR_RUNS").replace("&_gap_rs_ledger.", ledger)
    # the listing of the CAS libraries takes no parameters, which the harness reads behind a slash
    step = re.sub(r"(table\.caslibInfo result=\w+ status=\w+);", r"\1 /;", _code(step))
    symbols = library.run(step)
    return symbols["_gap_runs_exists"], symbols["_gap_ledger_exists"]


def _ledger_only(**refusals):
    library = Asking(**refusals)
    library.put(LIB, "RAG_HR_LEDGER", table_of([("run_id", "varchar")], [{"run_id": "run-1"}]))
    return library


@pytest.mark.parametrize("refusals", [{"refuse_tables": {"RAG_HR_RUNS"}}, {"refuse_tables": {"RAG_HR_LEDGER"}}, {"refuse_listing": True}],
                         ids=["the run table", "the ledger table", "the CAS libraries"])
def test_a_setup_whose_tables_cas_did_not_answer_for_is_the_note_of_a_library_not_available(refusals):
    """-1, the NOTE that names the library as not available or the question as refused - not 0, a run table that
    is not in memory, which reads the ledger, and not a ledger that is not there."""
    assert _looked(_ledger_only(**refusals))[0] == "-1"


def test_a_setup_whose_tables_cas_answered_for_is_read_as_before():
    library = _ledger_only()
    assert _looked(library) == ("0", "2")
    library.put(LIB, "RAG_HR_RUNS", table_of([("run_id", "varchar")], [{"run_id": "run-1"}]))
    assert _looked(library) == ("2", "2")
    # a run table in memory is read whatever became of the question for the ledger
    refused = _ledger_only(refuse_tables={"RAG_HR_LEDGER"})
    refused.put(LIB, "RAG_HR_RUNS", table_of([("run_id", "varchar")], [{"run_id": "run-1"}]))
    assert _looked(refused) == ("2", "-1")
    # the CAS library of the session that is always there needs no listing
    assert _looked(Asking(refuse_listing=True), caslib="CASUSER", ledger="") == ("0", "0")


def test_the_note_names_both_causes():
    program = (INTEGRATIONS / "Logging-Monitoring" / "Get-All-Prompts.sas").read_text(encoding="utf-8")
    assert ("%put NOTE: The CAS library &_gap_rs_caslib. of the run table &_gap_rs_table. is not available in this session, or CAS refused "
            "to say whether it, the table or its ledger table is there, so its runs are not in &_gap_out_rag_runs..;") in program
