"""Build-RAG-Cost-View.sas: the list of the run tables, and what the last line of a run says.

Two things were measured on SAS Viya on 2026-10-12:

- with two run tables - `%let _rcv_runs_tables = ZZ_VERIFY_R12S_RUNS,ZZ_VERIFY_R10_RUNS;`, the comma
  list the comment of the setting prescribes - the program failed: "ERROR: A character operand was
  found in the %EVAL function or %IF condition where a numeric operand is required. The condition
  was: ZZ_VERIFY_R10_RUNS", "ERROR: Argument 2 to macro function %SCAN is not a number." and
  "ERROR: The macro _RCV_STACK_RUNS will stop executing." The list stood in %scan as a reference
  with an ampersand, so its own comma ended the first argument;
- that run still ended with "NOTE: ZZ_VERIFY_R17_COST built in casuser." for a table that did not
  exist. And on 2026-10-12, in the pass before, the NOTE named RAG_RUN_COST whatever the output
  table was called.

The SAS code can not run here. Its text is read, and the two macros are followed with
macro_harness, which resolves the macro language the way the macro processor does: for a list of
one, two and many tables it gives the DATA step the macro generates, and for the list as it was
written before it gives the error that was measured.
"""
import re
from pathlib import Path

import pytest

from macro_harness import MacroError, Macros

PROGRAM = Path(__file__).resolve().parents[1] / "Build-RAG-Cost-View.sas"

# The two expressions the list stood in before, as they were written
SCAN_AS_IT_WAS = "%scan(&_rcv_runs_tables., &_rcv_i., %str(,))"


def text():
    return PROGRAM.read_text(encoding="utf-8").replace("\r\n", "\n")


def code():
    """The program without its comments."""
    return re.sub(r"/\*.*?\*/", "", text(), flags=re.DOTALL)


def macro(name):
    program = code()
    return program[program.index(f"%macro {name};"):program.index(f"%mend {name};")]


def stack(tables, caslib="Public"):
    """What the macro that stacks the run tables does for a value of the setting: (the DATA step it
    generates, the lines it writes to the log, the macro variables behind it)."""
    macros = Macros({"_rcv_runs_tables": tables, "_rcv_runs_caslib": caslib, "_rcv_failed": 0, "_rcv_stacked": 0, "syserr": 0})
    generated, log = macros.run(text(), "_rcv_stack_runs")
    return generated, log, macros


def step_for(names, caslib="Public"):
    """The DATA step that stacks these tables."""
    return (["data work._rcv_runs;", "length source_table $ 64;", "set"]
            + [f"{caslib}.{name} (in=_in{position})" for position, name in enumerate(names, 1)] + [";"]
            + [f'if _in{position} then source_table = "{name}";' for position, name in enumerate(names, 1)] + ["run;"])


# ---------------------------------------------------------------------------
# The list of the run tables
# ---------------------------------------------------------------------------

LISTS = [
    ("RAG_HR_RUNS", ["RAG_HR_RUNS"]),
    ("ZZ_VERIFY_R12S_RUNS,ZZ_VERIFY_R10_RUNS", ["ZZ_VERIFY_R12S_RUNS", "ZZ_VERIFY_R10_RUNS"]),
    ("A, B", ["A", "B"]),
    ("A ,B", ["A", "B"]),
    ("A , B , C", ["A", "B", "C"]),
    ("RAG_HR_RUNS,RAG_LEGAL_RUNS,RAG_IT_RUNS,RAG_SALES_RUNS,RAG_OPS_RUNS,RAG_QA_RUNS,RAG_RND_RUNS",
     ["RAG_HR_RUNS", "RAG_LEGAL_RUNS", "RAG_IT_RUNS", "RAG_SALES_RUNS", "RAG_OPS_RUNS", "RAG_QA_RUNS", "RAG_RND_RUNS"]),
    ("A,,B", ["A", "B"]),
    (",A,B,", ["A", "B"]),
    ("A B", ["A", "B"]),
]


@pytest.mark.parametrize("tables, names", LISTS, ids=[tables for tables, _ in LISTS])
def test_one_two_and_many_tables_give_the_step_that_stacks_them(tables, names):
    generated, log, macros = stack(tables)

    assert generated == step_for(names)
    assert log == [] and macros["_rcv_stacked"] == "1" and macros["_rcv_failed"] == "0"
    # each table once, under the library of the setting
    assert stack(tables, "casuser")[0] == step_for(names, "casuser")


def test_the_list_as_it_was_written_fails_the_way_it_was_measured():
    """The harness is held against the measurement: the expression of before gives the name of
    one table, and for two tables the error SAS wrote, with the second table as the operand."""
    one = Macros({"_rcv_runs_tables": "RAG_HR_RUNS", "_rcv_i": 1})
    assert one.resolve(SCAN_AS_IT_WAS) == "RAG_HR_RUNS"
    assert Macros({"_rcv_runs_tables": "RAG_HR_RUNS", "_rcv_i": 2}).resolve(SCAN_AS_IT_WAS) == ""

    two = Macros({"_rcv_runs_tables": "ZZ_VERIFY_R12S_RUNS,ZZ_VERIFY_R10_RUNS", "_rcv_i": 1})
    with pytest.raises(MacroError) as measured:
        two.resolve(SCAN_AS_IT_WAS)
    assert str(measured.value) == "Argument 2 to macro function %SCAN is not a number."
    assert measured.value.operand == "ZZ_VERIFY_R10_RUNS"
    with pytest.raises(MacroError):
        Macros({"_rcv_runs_tables": "A, B", "_rcv_i": 1}).resolve(SCAN_AS_IT_WAS)

    # the expressions of the program give the names of that list
    now = re.findall(r"%scan\([^;]*\)(?=;)", macro("_rcv_stack_runs"))
    assert now == ["%scan(%superq(_rcv_runs_tables), &_rcv_i., %str( ,))"] * 2
    for position, name in enumerate(["ZZ_VERIFY_R12S_RUNS", "ZZ_VERIFY_R10_RUNS", ""], 1):
        two.symbols["_rcv_i"] = str(position)
        assert two.resolve(now[0]) == name


def test_the_list_is_handed_to_the_macro_functions_as_one_text():
    """No statement of the program resolves the list with an ampersand: in an argument of a macro
    function or of a macro call a comma of the list ends the argument."""
    program = code()
    stacking = macro("_rcv_stack_runs")

    assert "&_rcv_runs_tables" not in program
    assert program.count("%superq(_rcv_runs_tables)") == stacking.count("%superq(_rcv_runs_tables)") == 4
    assert "%let _rcv_n = %sysfunc(countw(%superq(_rcv_runs_tables), %str( ,)));" in stacking
    # the names are counted and then taken by their position: no name stands in a condition
    assert "%do %while" not in program and stacking.count("%do _rcv_i = 1 %to &_rcv_n.;") == 2
    assert re.findall(r"%if (.*?) %then", stacking) == ["%length(%superq(_rcv_runs_tables)) GT 0", "&_rcv_n. LT 1", "&syserr. LE 4"]
    assert "%local _rcv_n _rcv_i _rcv_t;" in stacking
    # the comment of the setting says how a list is written
    assert "comma-separated, with or\n   without blanks around the commas" in text()


@pytest.mark.parametrize("tables", ["", ",", " , ,"], ids=["empty", "a comma", "commas and blanks"])
def test_a_list_without_a_table_is_one_error_line_and_no_step(tables):
    generated, log, macros = stack(tables)

    assert generated == []
    assert log == ["ERROR: Build-RAG-Cost-View: the setting _rcv_runs_tables names no run table, no run was read."]
    assert macros["_rcv_failed"] == "1" and macros["_rcv_stacked"] == "0"


def test_a_step_that_ended_with_an_error_is_not_stacked():
    """The return code of the DATA step is read behind it: 0 and the 4 of a warning are a step that ran."""
    for syserr, stacked in ((0, "1"), (4, "1"), (1012, "0"), (3000, "0")):
        macros = Macros({"_rcv_runs_tables": "A,B", "_rcv_runs_caslib": "Public", "_rcv_failed": 0, "_rcv_stacked": 0, "syserr": syserr})
        macros.run(text(), "_rcv_stack_runs")
        assert macros["_rcv_stacked"] == stacked, syserr
    stacking = macro("_rcv_stack_runs")
    assert stacking.index("run;") < stacking.index("%if &syserr. LE 4 %then %let _rcv_stacked = 1;")
    assert code().index("%let _rcv_stacked = 0;") < code().index("%macro _rcv_stack_runs;")


# ---------------------------------------------------------------------------
# Leaving at the first refusal
# ---------------------------------------------------------------------------

def price(stacked, failed, syserr=0, fails_at=None):
    """What the macro that prices the runs does: (the statements it generates, the lines it writes, the macro
    variables behind it). fails_at is the number of the step (1 to 5) whose return code is 1012."""
    macros = Macros({"_rcv_stacked": stacked, "_rcv_failed": failed, "_rcv_priced": 0, "syserr": syserr, "_rcv_has_enrich": 1,
                     "_rcv_out_caslib": "casuser", "_rcv_out_table": "ZZ_VERIFY_COST", "_rcv_fact_caslib": "Public"})
    steps = []

    def stand_in(line, table):
        # each step ends with RUN or QUIT: the return code the next check reads is the one of that step
        if line in ("run;", "quit;"):
            steps.append(line)
            table.symbols["syserr"] = "1012" if len(steps) == fails_at else "0"

    generated, log = macros.run(text(), "_rcv_price", stand_in)
    return generated, log, macros


CLOSING = ("ERROR: Build-RAG-Cost-View: ZZ_VERIFY_COST was not built, and a table of that name in casuser was left as it is: the run tables "
           "of the setting _rcv_runs_tables could not be stacked. The lines above say why.")
STEP_FAILED = ("ERROR: Build-RAG-Cost-View: ZZ_VERIFY_COST was not built, and a table of that name in casuser was left as it is: a step "
               "that prices the runs ended with an error. The lines above say why.")


def test_a_list_without_a_table_is_its_one_error_line_and_nothing_runs_behind_it():
    """Measured on 2026-10-12 with an empty list: behind the ERROR line of the program SAS wrote
    'File WORK._RCV_RUNS.DATA does not exist.', 'File WORK._RCV_COST.DATA does not exist.' and two
    WARNINGs. Now no step that reads a table of WORK is generated behind it, and no second ERROR line."""
    _, stacking_log, stacked = stack("")
    generated, log, macros = price(stacked["_rcv_stacked"], stacked["_rcv_failed"])

    assert stacking_log == ["ERROR: Build-RAG-Cost-View: the setting _rcv_runs_tables names no run table, no run was read."]
    assert generated == [] and log == []
    assert macros["_rcv_failed"] == "1" and macros["_rcv_priced"] == "0"


def test_run_tables_that_were_not_stacked_are_one_error_line_and_nothing_runs_behind_them():
    """Measured on 2026-10-12 with a table that is not there: behind SAS's own ERROR line for the table
    'File WORK._RCV_COST.DATA does not exist.' and three WARNINGs, then the closing ERROR. Now the closing
    ERROR line follows SAS's line, and nothing else."""
    generated, log, macros = price(0, 0)

    assert generated == [] and log == [CLOSING]
    assert macros["_rcv_failed"] == "1" and macros["_rcv_priced"] == "0"


def test_run_tables_that_were_stacked_are_priced():
    generated, log, macros = price(1, 0)

    assert log == [] and macros["_rcv_priced"] == "1" and macros["_rcv_failed"] == "0"
    assert generated[0] == "data _null_;" and "create table work._rcv_cost as" in generated
    assert generated[-3:] == ["data work._rcv_cost;", "set work._rcv_cost(drop = enrich_cost_total);", "run;"]


@pytest.mark.parametrize("fails_at", [1, 2, 3, 4, 5, 6])
def test_a_step_that_prices_the_runs_and_ends_with_an_error_is_the_last_one(fails_at):
    """A step behind the stacking that ends with an error - in a SAS Studio session, where an error does not
    put SAS into syntax check mode - is the last step: one ERROR line, and no cost table is handed to CAS.
    The steps are counted by their RUN or QUIT statement; the first one is the look at the columns."""
    generated, log, macros = price(1, 0, fails_at=fails_at)

    if fails_at == 1:
        # the look at the columns of the stacked table is no step that prices them
        assert log == [] and macros["_rcv_priced"] == "1"
        return
    assert log == [STEP_FAILED] and macros["_rcv_priced"] == "0" and macros["_rcv_failed"] == "1"
    assert sum(line in ("run;", "quit;") for line in generated) == fails_at


def test_no_table_of_work_is_left_from_an_earlier_run():
    program = code()
    cleared = "proc datasets lib=work nolist nowarn;\n    delete _rcv_runs _rcv_runs_n _rcv_cost _rcv_facts _rcv_llm;\nrun; quit;\n"

    assert program.count(cleared) == 2
    assert program.index(cleared) < program.index("%macro _rcv_stack_runs;")
    assert program.index("%_rcv_stack_runs;") < program.index("%macro _rcv_price;") < program.index("%_rcv_price;")
    assert program.index("%_rcv_price;") < program.index("%macro _rcv_publish;")


# ---------------------------------------------------------------------------
# The output table and the last line of the run
# ---------------------------------------------------------------------------

PUBLISH = ['proc casUtil incaslib="casuser" outcaslib="casuser";', 'droptable casdata="ZZ_VERIFY_COST" quiet;',
           'load data=work._rcv_cost casout="ZZ_VERIFY_COST";', 'promote casdata="ZZ_VERIFY_COST" casout="ZZ_VERIFY_COST";',
           'save casdata="ZZ_VERIFY_COST" casout="ZZ_VERIFY_COST" replace;', "run; quit;"]


def publish(stacked=1, cost_table=(15, 30), in_cas=15, unpriced=(0, 0)):
    """What the macro that publishes does: (the statements it generates, the lines it writes to the
    log, the macro variables behind it). cost_table is (rows, columns) of WORK._RCV_COST or None
    for a table that is not there, in_cas the rows CAS holds under the name of the output table
    behind the statements that load it, -1 for a table that is not there."""
    functions = {
        "exist": lambda args: int(cost_table is not None and args == ["work._rcv_cost"]),
        "open": lambda args: 7 if cost_table is not None else 0,
        "attrn": lambda args: {"nlobs": cost_table[0], "nvars": cost_table[1]}[args[1]] if args[0] == "7" else ".",
        "close": lambda args: 0,
    }
    macros = Macros({"_rcv_priced": stacked, "_rcv_out_caslib": "casuser", "_rcv_out_table": "ZZ_VERIFY_COST", "_rcv_failed": 0}, functions)

    def stand_in(line, table):
        # PROC CAS hands the rows it found to the macro variable, PROC SQL the two counts
        if line.startswith('call symputx("_rcv_built", built'):
            table.symbols["_rcv_built"] = str(in_cas)
        elif line.startswith("select count(distinct embed_model) into :_rcv_unpriced trimmed"):
            table.symbols["_rcv_unpriced"] = str(unpriced[0])
        elif line.startswith("select count(distinct enrich_model) into :_rcv_unpriced_llm trimmed"):
            table.symbols["_rcv_unpriced_llm"] = str(unpriced[1])

    generated, log = macros.run(text(), "_rcv_publish", stand_in)
    return generated, log, macros


def test_a_table_that_was_built_is_a_note_with_its_name():
    generated, log, macros = publish()

    assert generated[:6] == PUBLISH
    assert log == ["NOTE: ZZ_VERIFY_COST built in casuser."] and macros["_rcv_failed"] == "0"
    # an empty run table gives an empty cost table, which is a table that was built
    assert publish(cost_table=(0, 30), in_cas=0)[1] == ["NOTE: ZZ_VERIFY_COST built in casuser."]


def test_a_run_that_did_not_stack_its_tables_leaves_the_output_table_and_says_error():
    """The run that was measured: the macro stopped, and the steps behind it made a cost table without a column."""
    for case in (dict(stacked=0, cost_table=(0, 0)), dict(stacked=0, cost_table=None), dict(stacked=0),
                 dict(stacked=1, cost_table=None), dict(stacked=1, cost_table=(0, 0))):
        generated, log, macros = publish(**case)

        assert generated == [], case            # no statement that drops, loads, promotes or saves
        assert log == ["ERROR: Build-RAG-Cost-View: ZZ_VERIFY_COST was not built, and a table of that name in casuser was left as it is: "
                       "this run has no cost table to put in its place. The lines above say why."], case
        assert macros["_rcv_failed"] == "1" and not [line for line in log if line.startswith("NOTE")], case


@pytest.mark.parametrize("in_cas", [-1, 0, 14, 400], ids=["not there", "empty", "a row short", "the table of an earlier run"])
def test_a_table_that_cas_does_not_hold_with_the_rows_of_this_run_is_an_error(in_cas):
    generated, log, macros = publish(in_cas=in_cas)

    assert generated[:6] == PUBLISH
    assert log == [f"ERROR: Build-RAG-Cost-View: ZZ_VERIFY_COST was not built in casuser: this run has 15 rows for it and the CAS library "
                   f"holds {in_cas}, -1 stands for a table that CAS did not say is there or did not count. The lines above say why."]
    assert macros["_rcv_failed"] == "1"


def test_models_without_a_price_are_warnings_behind_the_note():
    _, log, macros = publish(unpriced=(2, 1))

    assert log[0] == "NOTE: ZZ_VERIFY_COST built in casuser."
    assert log[1] == "WARNING: 2 embedding model(s) have no price in EMBEDDING_FACT_SHEET"
    assert [line for line in log if line.startswith("WARNING: ")][1] == "WARNING: 1 enrichment LLM(s) have no price in LLM_FACT_SHEET"
    assert len(log) == 7 and macros["_rcv_failed"] == "0"
    assert len(publish(unpriced=(0, 3))[1]) == 5 and len(publish(unpriced=(1, 0))[1]) == 3


def test_the_closing_note_of_the_cost_view_names_the_table_it_wrote():
    program = code()

    assert program.count("%put NOTE: &_rcv_out_table. built in &_rcv_out_caslib..;") == 1
    assert "RAG_RUN_COST built in" not in program
    # the name the program comes with stands in one statement only, the one that sets the output table
    named = [line.strip() for line in program.split("\n") if "RAG_RUN_COST" in line]
    assert len(named) == 1 and "_rcv_out_table" in named[0]
    # the NOTE stands behind the statements that publish the table and behind the look at what CAS holds
    publishing = macro("_rcv_publish")
    positions = [publishing.index(marker) for marker in (
        "%if &_rcv_vars. GT 0 %then %do;", 'droptable casdata="&_rcv_out_table." quiet;', 'load data=work._rcv_cost casout="&_rcv_out_table.";',
        'save casdata="&_rcv_out_table." casout="&_rcv_out_table." replace;',
        'table.tableExists result = found status = foundAsked / caslib = "&_rcv_out_caslib." name = "&_rcv_out_table.";',
        'if upcase(entry.Name) == upcase("&_rcv_out_table.") then built = entry.Rows;', 'call symputx("_rcv_built", built, "L");',
        "%if &_rcv_vars. GT 0 and &_rcv_built. EQ &_rcv_rows. %then %do;", "%put NOTE: &_rcv_out_table. built in &_rcv_out_caslib..;",
        "%else %if &_rcv_vars. GT 0 %then %do;", "%else %do;")]
    assert positions == sorted(positions)
    # the output table is dropped in one statement, which stands behind the condition
    assert program.count("droptable") == 1 and program.count("proc casUtil") == 1
    assert "%if &_rcv_priced. EQ 1 and %sysfunc(exist(work._rcv_cost)) %then %do;" in publishing
    # and the macro is called only behind a run that priced its runs
    assert "%if &_rcv_priced. EQ 1 %then %do;\n    %_rcv_publish;\n%end;\n" in program
    assert re.findall(r"^\s*%_rcv_publish;", program, flags=re.MULTILINE) == ["    %_rcv_publish;"]
    assert "%let _rcv_vars = %sysfunc(attrn(&_rcv_dsid., nvars));" in publishing and "%let _rcv_rows = %sysfunc(attrn(&_rcv_dsid., nlobs));" in publishing


def test_a_run_with_an_error_line_ends_with_the_condition_code_of_a_failed_job():
    """As the other programs of the folder end: the statement that sets the condition code is the
    last one in front of the macro variables that are deleted."""
    program = code()
    block = "%if &_rcv_failed. EQ 1 %then %do;\n    %let SYSCC = 1012;\n%end;\n"

    assert program.count(block) == 1 and len(re.findall(r"syscc\s*=", program, flags=re.IGNORECASE)) == 1
    behind = [statement.strip() for statement in program.split(block)[1].split(";") if statement.strip()]
    assert len(behind) == 1 and behind[0].startswith("%symdel ") and behind[0].endswith("/ nowarn")
    assert {"_rcv_failed", "_rcv_stacked"} <= set(behind[0].split())
    assert program.index("cas _rcvSess terminate;") < program.index(block)
    # every ERROR line of the program sets what that statement reads, and nothing else does
    errors = re.findall(r"%put (ERROR: [^;]*);\s*(\S[^;]*;)", program)
    # the list without a table, the run tables that were not stacked, the five steps that price the runs, the two of the publish
    assert len(errors) == 9 and all(following == "%let _rcv_failed = 1;" for _, following in errors)
    assert re.findall(r"%let _rcv_failed\s*=\s*(\d);", program) == ["0"] + ["1"] * 9
    for message, _ in errors:
        assert message.startswith("ERROR: Build-RAG-Cost-View: ") and message.endswith(".")
        assert not set(message) & set("%'\"") and message.count("(") == message.count(")"), message
    assert not re.search(r"\babort\b|%abort|endsas", program, flags=re.IGNORECASE)
    # the three macros are gone at the end
    assert all(f"%sysmacdelete {name};" in program for name in ("_rcv_stack_runs", "_rcv_price", "_rcv_publish"))


def test_the_harness_reads_arguments_as_the_macro_processor_does():
    """The harness itself, on the two things the tests above rest on: where an argument ends, and what a word is."""
    macros = Macros({"list": "A , B,C", "one": "A", "n": 2})

    assert macros.resolve("%sysfunc(countw(%superq(list), %str( ,)))") == "3"
    assert macros.resolve("%sysfunc(countw(%superq(one), %str( ,)))") == "1"
    assert [macros.resolve(f"%scan(%superq(list), {position}, %str( ,))") for position in (1, 2, 3, 4)] == ["A", "B", "C", ""]
    assert macros.resolve("%scan(%superq(list), &n., %str(,))").strip() == "B"
    assert macros.resolve("%length(%superq(list))") == "7" and macros.resolve("%length(&one.)") == "1"
    # a reference with an ampersand: the comma of the value ends the argument
    with pytest.raises(MacroError):
        macros.resolve("%scan(&list., 1, %str(,))")
    with pytest.raises(AssertionError):
        macros.resolve("%sysfunc(tranwrd(&one., A, B))")
    with pytest.raises(AssertionError):
        macros.run("%macro m;\n%goto done;\n%mend m;", "m")
