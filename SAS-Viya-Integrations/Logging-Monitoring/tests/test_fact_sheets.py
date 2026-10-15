"""Load-Fact-Sheets.sas: a table is only replaced by a sheet that was read and that CAS held.

The program dropped each of its three tables and loaded the sheet behind it. A sheet that
was not in the folder, or that CAS did not take, left the site without the table it had.
Each sheet is now read and tried in CAS first, beside the table, and only a sheet CAS held
completely takes its place - the upload whose rows were counted, promoted under the name of
the table, as Load-Accelerator-Tables does it for the release table; the sheet is not loaded
a second time.

The SAS code can not run here. The tests read it as text: the statements and their order. The
tests at the end follow the two macros with macro_harness and run the CASL of their PROC CAS steps
against a CAS library that is made up (casl_harness): they say what the program does IF CAS
behaves as that model has it.
"""
import re
from pathlib import Path

import pytest

import casl_harness

FOLDER = Path(__file__).resolve().parents[1]
REPOSITORY = FOLDER.parents[1]

SHEETS = [
    ("llm_fact_sheet.csv", "_lfs_llm_fact_sheet", "LLM_FACT_SHEET", "LLM-Definitions"),
    ("embedding_fact_sheet.csv", "_lfs_embedding_fact_sheet", "EMBEDDING_FACT_SHEET", "Embedding-Definitions"),
    ("decision_fact_sheet.csv", "_lfs_decision_fact_sheet", "DECISION_FACT_SHEET", "Decision-Definitions"),
]


def program(name):
    return (FOLDER / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def without_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("*"))


def in_order(text, *markers):
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), [marker for _, marker in sorted(zip(positions, markers))]


def read_macro():
    code = without_comments(program("Load-Fact-Sheets.sas"))
    return code[code.index("%macro _lfs_read("):code.index("%mend _lfs_read;")]


def swap_macro():
    code = without_comments(program("Load-Fact-Sheets.sas"))
    return code[code.index("%macro _lfs_swap("):code.index("%mend _lfs_swap;")]


# ---------------------------------------------------------------------------
# Load-Fact-Sheets.sas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file, work, table, folder", SHEETS, ids=[sheet[2] for sheet in SHEETS])
def test_a_table_is_only_dropped_for_a_sheet_that_cas_held_completely(file, work, table, folder):
    code = without_comments(program("Load-Fact-Sheets.sas"))
    block = f"%_lfs_read({file}, {work}, {table});\n%_lfs_swap({table});\n"

    assert code.count(block) == 1
    # the table is named nowhere else: no statement outside the two macros drops, loads, promotes or saves it
    assert code.count(table) == 2 and code.count(work) == 2
    assert "casData=" not in code and "proc casUtil" not in code.replace(read_macro(), "")
    # the file the program reads is the fact sheet the repository holds for that kind of model
    assert (REPOSITORY / folder / file).is_file()


def test_the_three_sheets_are_read_one_after_the_other_and_none_stops_the_others():
    code = without_comments(program("Load-Fact-Sheets.sas"))
    calls = re.findall(r"^%_lfs_read\((\S+), (\w+), (\w+)\);$", code, flags=re.MULTILINE)

    assert calls == [sheet[:3] for sheet in SHEETS]
    macro = read_macro()
    # a sheet that is not loaded is a WARNING that names the file or the rows and the table that stays
    warnings = re.findall(r"%put (WARNING: [^;]*);", macro)
    # fix-content-22: one more, for a question CAS refused before the sheet is loaded
    assert len(warnings) == 5 and "ERROR" not in macro.upper().replace("NOWARN", "")
    for warning in warnings:
        assert warning.endswith("the table &_lfs_caslib..&_lfs_table. was left as it is.") or \
            warning.endswith("The table &_lfs_caslib..&_lfs_table. was left as it is.")
        assert not set(warning) & set("%'\"") and warning.count("(") == warning.count(")")
    # no ABORT and no statement that ends the program: the next sheet is read whatever became of this one
    assert not re.search(r"\babort\b|%abort|endsas", code, flags=re.IGNORECASE)
    assert macro.count("%return;") == 4


def test_a_sheet_is_read_and_tried_in_cas_before_its_table_is_touched():
    macro = read_macro()

    # nothing of an earlier run or of the sheet in front stands in for this sheet
    in_order(macro, "%let _lfs_ok = 0;", "delete &_lfs_work.;", 'filename _lfs_fs filesrvc folderPath="&_lfs_path." filename="&_lfs_file.";')
    # a file that is not there is not handed to PROC IMPORT, which would end with an error
    in_order(macro, "%let _lfs_there = %sysfunc(fexist(_lfs_fs));", "%if &_lfs_there. NE 1 %then %do;", "%return;", "proc import dataFile=_lfs_fs")
    assert "out=work.&_lfs_work." in macro and "guessingRows=max;" in macro and "getNames=yes;" in macro
    # its rows are counted, and a sheet without rows goes no further
    in_order(macro, "proc import dataFile=_lfs_fs", "%let _lfs_rows = %sysfunc(attrn(&_lfs_dsid., nlobs));", "%if &_lfs_rows. LE 0 %then %do;",
             'load data=work.&_lfs_work. casOut="&_lfs_table._NEW" replace;')
    # CAS is asked for a table of the name the sheet is loaded under, and the sheet is loaded only where it says 0
    assert macro.index("%if &_lfs_rows. LE 0 %then %do;") < macro.index("%let _lfs_taken = refused;")
    in_order(macro[macro.index("%let _lfs_taken = refused;"):], "%let _lfs_taken = refused;",
             'table.tableExists result = taken status = takenAsked / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW";',
             'call symputx("_lfs_taken", taken.exists, "L");', "%if &_lfs_taken. EQ refused %then %do;", "%return;",
             "%if &_lfs_taken. NE 0 %then %do;", 'load data=work.&_lfs_work. casOut="&_lfs_table._NEW" replace;')
    # it is loaded beside the table, under another name, counted there and dropped again
    in_order(macro, 'load data=work.&_lfs_work. casOut="&_lfs_table._NEW" replace;',
             'table.tableExists result = found status = foundAsked / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW";',
             'if upcase(entry.Name) == upcase("&_lfs_table._NEW") then staged = entry.Rows;',
             'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;',
             'call symputx("_lfs_staged", staged, "L");',
             "%if &_lfs_staged. EQ &_lfs_rows. %then %do;", "%let _lfs_ok = 1;")
    # the table itself is named nowhere in the macro: it only tries the sheet
    assert not re.search(r'name = "&_lfs_table\."', macro) and "promote" not in macro.lower() and "save " not in macro.lower()
    assert macro.count("%let _lfs_ok = 1;") == 1 and macro.count("%do") == macro.count("%end")
    assert macro.count("proc casUtil") == 1 and macro.count("proc cas;") == 2 and macro.count("proc import") == 1


def test_the_counted_upload_takes_the_place_of_the_table_and_nothing_loads_the_sheet_again():
    """Integrate-18 open item 5: the program tried each sheet beside its table, dropped that
    trial, and then dropped the table and loaded the sheet a second time - a failure of CAS
    between the drop and the second load left the site without that table, and the load that
    took its place was not the one whose rows were counted. The counted upload is now kept and
    promoted under the name of the table, the statements Load-Accelerator-Tables ran with on
    2026-10-12 for the release table; a refused promote loads the table again from its file."""
    read, swap = read_macro(), swap_macro()

    # the trial is dropped only where it does not hold every row
    in_order(read, "if staged != &_lfs_rows. then do;",
             'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;',
             'call symputx("_lfs_staged", staged, "L");')
    # nothing happens for a sheet that was not read completely
    in_order(swap, "%if &_lfs_ok. NE 1 %then %return;", "proc cas;")
    # the table is dropped, the counted upload promoted under its name and saved; no load of the sheet
    in_order(swap, 'table.dropTable status = dropped / caslib = "&_lfs_caslib." name = "&_lfs_table." quiet = true;',
             'table.promote status = promoted / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW"',
             'targetLib = "&_lfs_caslib." target = "&_lfs_table.";', "if promoted.severity == 0 then do;",
             'table.save status = stored / table = {caslib = "&_lfs_caslib.", name = "&_lfs_table."}',
             'caslib = "&_lfs_caslib." name = "&_lfs_table." replace = true;', 'if stored.severity == 0 then outcome = "loaded";',
             "else do;", 'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;',
             'table.loadTable status = restored / caslib = "&_lfs_caslib." path = "&_lfs_table..sashdat"',
             'casOut = {caslib = "&_lfs_caslib.", name = "&_lfs_table.", promote = true};',
             'call symputx("_lfs_outcome", outcome, "L");', "run; quit;")
    assert "load data=" not in swap and "work." not in swap and swap.count("proc cas;") == 1
    # every outcome but a saved table is a WARNING that says what the table holds; none is an ERROR or ends the program
    warnings = re.findall(r"%put (WARNING: [^;]*);", swap)
    # content-23: and the one of a step that halted before it said what became of the sheet
    assert len(warnings) == 5 and "ERROR" not in swap.upper()
    for warning in warnings:
        assert "&_lfs_caslib..&_lfs_table." in warning and not set(warning) & set("%'\"")
    # one test for a sheet that was not read, and one for each WARNING
    # content-23: and the line of a step that halted before it said what became of the sheet
    assert swap.count("%if") == swap.count("%put") + 1 and swap.count("%else %if") == 4


def test_an_error_for_one_sheet_does_not_put_a_batch_session_into_syntax_check_mode():
    """Behind an error a batch session checks syntax only and runs no further step - measured on
    2026-10-12 for the first DATA step behind a program that ended with an error condition. The
    sheets behind a sheet that CAS refused would not be read. The option that decides it is off
    while the program runs and is put back to what it was, as the ingestion job of a RAG setup
    does it while it publishes its run history."""
    code = without_comments(program("Load-Fact-Sheets.sas"))

    assert code.count("%let _lfs_syntaxcheck = %sysfunc(getoption(syntaxcheck));\noptions nosyntaxcheck;\n") == 1
    # the third OPTIONS statement is the one that sets SESSREF to a connected mySess (test_fix_content_23.py)
    assert code.count("options &_lfs_syntaxcheck.;") == 1 and len(re.findall(r"^\s*options\b", code, flags=re.MULTILINE)) == 3
    assert code.count("    options sessref=mySess;\n") == 1
    # switched off in front of the first step of the program and put back behind its last one
    in_order(code, "options nosyntaxcheck;", "cas mySess;", "%macro _lfs_read(", "%_lfs_read(llm_fact_sheet.csv",
             "delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet _lfs_decision_fact_sheet;", "cas mySess terminate;",
             "options &_lfs_syntaxcheck.;")
    # the condition code is left as the program found or made it: an error stays an error of the job
    assert not re.search(r"syscc", code, flags=re.IGNORECASE)


def test_the_macro_and_its_flag_are_gone_at_the_end():
    text = program("Load-Fact-Sheets.sas")
    tail = text[text.rindex("* Clean up;"):]

    assert "delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet _lfs_decision_fact_sheet;" in tail
    in_order(tail, "cas mySess terminate;", "%sysmacdelete _lfs_read;", "%sysmacdelete _lfs_swap;", "options &_lfs_syntaxcheck.;",
             "%symdel _lfs_ok _lfs_syntaxcheck / nowarn;", "%symdel _lfs_path _lfs_caslib _lfs_clean_cas_session;")
    # the header says what the program does with a table that exists, and who includes the file
    head = text.split("*/")[0]
    assert "A table that exists is only replaced by a sheet that was read" in head
    assert "The job definition Load-Accelerator-Tables includes this file from the" in head


# ---------------------------------------------------------------------------
# Followed: the folder of the sheets
# ---------------------------------------------------------------------------

SHEET = ("llm_fact_sheet.csv", "_lfs_llm_fact_sheet", "LLM_FACT_SHEET")
FOLDER_OF_SHEETS = "/Site, Root (2026)/Data"
NOT_FOUND = ("WARNING: The file llm_fact_sheet.csv was not found in the folder " + FOLDER_OF_SHEETS
             + ", the table Public.LLM_FACT_SHEET was left as it is.")
COLUMNS = ["model", "provider", "input_cost", "output_cost"]


def follow(name, cas=None, status="200", there=1, rows=30, loads=None, **more):
    """Follows a macro of the program for one sheet. Returns (the SAS code it generates, the lines it writes, the
    folders it asked for, the macro variables at its end). `loads` is the number of rows PROC CASUTIL loads as a
    table of the session where the macro gets to its LOAD statement."""
    symbols = {"_lfs_file": SHEET[0], "_lfs_work": SHEET[1], "_lfs_table": SHEET[2], "_lfs_path": FOLDER_OF_SHEETS, "_lfs_caslib": "Public",
               "_lfs_ok": "0", "SYS_PROCHTTP_STATUS_CODE": "an answer of an earlier request"}
    symbols.update(more)
    asked = []

    def called(text, macros):
        asked.append(text)
        macros.symbols["sys_prochttp_status_code"] = status

    functions = {"fexist": lambda arguments: there, "exist": lambda arguments: 1, "open": lambda arguments: 7,
                 "attrn": lambda arguments: rows, "close": lambda arguments: 0}
    def on_line(line, macros):
        load = re.fullmatch(r'load data=work\.\w+ casOut="(\w+)" replace;', line)
        if load and loads is not None:
            cas.table(macros["_lfs_caslib"], load.group(1), COLUMNS, rows=loads, of="the sheet", scope="session")

    generated, log, macros = casl_harness.follow(program("Load-Fact-Sheets.sas"), name, symbols, cas, functions, called, on_line=on_line)
    return [line for line in generated if not line.startswith(casl_harness.CALLED)], log, asked, macros


def test_a_folder_that_is_not_there_is_the_warning_and_no_statement_that_fails():
    """live-19, defect 6, for this program: a FILENAME statement of the access method FILESRVC wrote two ERROR
    lines for a folder that is not there (measured on 2026-10-13 for the statement of the job that includes this
    program). The Folders service is asked first, and the statement is left out where it answers 404."""
    generated, log, asked, macros = follow("_lfs_read", status="404", there=0)

    assert asked == ["_lfs_ask_folder();"]
    assert not [line for line in generated if "filesrvc" in line] and log == [NOT_FOUND] and macros["_lfs_ok"] == "0"
    # nothing of the sheet is read or loaded
    assert generated == ["proc datasets lib=work noList noWarn;", "delete _lfs_llm_fact_sheet;", "run; quit;"]


@pytest.mark.parametrize("status", ["200", "401", "403", "500", ""], ids=["there", "401", "403", "500", "no answer"])
def test_a_folder_without_the_file_is_the_same_warning(status):
    """Only the answer 404 says that the folder is not there: every other answer assigns the fileref as before."""
    generated, log, asked, macros = follow("_lfs_read", status=status, there=0)

    assert asked == ["_lfs_ask_folder();"]
    assert [line for line in generated if "filename" in line] == [
        'filename _lfs_fs filesrvc folderPath="' + FOLDER_OF_SHEETS + '" filename="llm_fact_sheet.csv";', "filename _lfs_fs clear;"]
    assert log == [NOT_FOUND] and "proc import dataFile=_lfs_fs" not in generated


def test_a_sheet_that_is_there_is_read_and_counted_as_before():
    cas = casl_harness.FakeCAS()
    generated, log, asked, macros = follow("_lfs_read", cas, status="200", there=1, rows=30, loads=30)

    assert log == [] and macros["_lfs_ok"] == "1" and cas.errors == []
    in_order("\n".join(generated), "filename _lfs_fs filesrvc folderPath=", "proc import dataFile=_lfs_fs", "filename _lfs_fs clear;",
             'table.tableExists result = taken status = takenAsked / caslib = "Public" name = "LLM_FACT_SHEET_NEW";',
             'load data=work._lfs_llm_fact_sheet casOut="LLM_FACT_SHEET_NEW" replace;',
             'table.tableExists result = found status = foundAsked / caslib = "Public" name = "LLM_FACT_SHEET_NEW";')
    assert cas.exists("Public", "LLM_FACT_SHEET_NEW") == 1
    # CAS was asked for a table of that name before the sheet was loaded under it
    assert cas.calls[0] == ("table.tableExists", {"caslib": "Public", "name": "LLM_FACT_SHEET_NEW"})


TAKEN = ("WARNING: CAS did not answer that there is no table Public.LLM_FACT_SHEET_NEW - tableExists said {0}, and 0 is no table. The sheet "
         "is loaded under that name first, as a table of this session, and this program drops and replaces no table it did not load: where "
         "a table of that name is there, rename or drop it and run this program again. The file llm_fact_sheet.csv was not loaded into "
         "CAS, the table Public.LLM_FACT_SHEET was left as it is.")


@pytest.mark.parametrize("scope, answer", [("global", 2), ("session", 1)], ids=["a table of the site", "a table of the session"])
def test_a_table_that_has_the_name_the_sheet_is_loaded_under_is_not_touched(scope, answer):
    """gate-20. The sheet is loaded as LLM_FACT_SHEET_NEW and that table is dropped again or promoted. A site can
    have a table of that name. What CAS does with a table of the session that has the name of a global one was
    measured once, on 2026-10-13 behind this change: the session that held both got 1 from tableExists, and its
    first dropTable took the table of the session and left the global one. The program does not rest on that one
    run. It asks before it loads, and where a table of the name is there it loads nothing under it and drops
    nothing of it. Measured in the same pass from a copy of the program with three test table names and a global
    table <table>_NEW: the WARNING with `tableExists said 2`, and both tables with the rows and the time of before."""
    cas = casl_harness.FakeCAS()
    cas.table("Public", "LLM_FACT_SHEET", COLUMNS, rows=28, of="the table of before")
    cas.table("Public", "LLM_FACT_SHEET_NEW", COLUMNS, rows=7, of="a table of the site", scope=scope)
    generated, log, asked, macros = follow("_lfs_read", cas, status="200", there=1, rows=30, loads=30)

    assert log == [TAKEN.format(answer)] and macros["_lfs_ok"] == "0"
    # nothing was loaded and nothing was dropped: the one action is the question
    assert [action for action, _ in cas.calls] == ["table.tableExists"] and not [line for line in generated if "casUtil" in line or "load data=" in line]
    held = cas.promoted if scope == "global" else cas.session
    assert held[cas.key("Public", "LLM_FACT_SHEET_NEW")] == {"columns": COLUMNS, "rows": 7, "of": "a table of the site"}
    assert in_memory(cas) == {"columns": COLUMNS, "rows": 28, "of": "the table of before"}
    # and the swap does nothing for a sheet that was not loaded
    assert swapped(cas, _lfs_ok=macros["_lfs_ok"]) == [] and len(cas.calls) == 1


def test_a_session_without_an_answer_loads_nothing_either():
    """Where the step that asks did not run - no CAS session -, the macro variable keeps the text it was given,
    and that is no 0."""
    read = read_macro()

    in_order(read, "%let _lfs_taken = refused;", "proc cas;", 'call symputx("_lfs_taken", taken.exists, "L");', "%if &_lfs_taken. NE 0 %then %do;")
    assert "_lfs_taken" in read[read.index("%local "):read.index(";", read.index("%local "))]
    # an upload that does not hold every row is dropped behind the question, so it is the upload of this run
    in_order(read[read.index("%if &_lfs_taken. NE 0 %then %do;"):], "%if &_lfs_taken. NE 0 %then %do;", "%return;", "proc casUtil",
             "if staged != &_lfs_rows. then do;", 'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;')
    assert read.count("_NEW\" quiet = true;") == 1


# ---------------------------------------------------------------------------
# Followed: what became of the table, and the sentence that says it
# ---------------------------------------------------------------------------

BEFORE = {"columns": COLUMNS, "rows": 28, "of": "the table of before"}
COULD_NOT = "WARNING: The sheet could not take the place of the table Public.LLM_FACT_SHEET - messages above"
KEPT = COULD_NOT + ". The table of that name that is in memory was left as it is."
RESTORED = COULD_NOT + ". The table was loaded again from the file it was saved to before."
GONE = (COULD_NOT + " - and the table was not loaded again from the file it was saved to before: load that file, or run this "
        "program again.")
NOT_SAVED = ("WARNING: The table Public.LLM_FACT_SHEET holds the sheet and is promoted, but it could not be saved - messages above. "
             "Its saved file is the one of before.")


def library(refuse=(), saved=True):
    """The table of before, global and saved, and the upload of the sheet whose rows were counted."""
    cas = casl_harness.FakeCAS(refuse=refuse)
    cas.table("Public", "LLM_FACT_SHEET", COLUMNS, rows=28, of="the table of before")
    if saved:
        cas.file("Public", "LLM_FACT_SHEET.sashdat", COLUMNS, rows=28, of="the table of before")
    cas.table("Public", "LLM_FACT_SHEET_NEW", COLUMNS, rows=30, of="the sheet", scope="session")
    return cas


def swapped(cas, **more):
    generated, log, asked, macros = follow("_lfs_swap", cas, **dict({"_lfs_ok": "1"}, **more))
    return log


def in_memory(cas):
    return cas.promoted.get(cas.key("Public", "LLM_FACT_SHEET"))


def test_the_sheet_takes_the_place_of_the_table():
    cas = library()

    assert swapped(cas) == [] and cas.errors == [] and cas.session == {}
    assert in_memory(cas) == {"columns": COLUMNS, "rows": 30, "of": "the sheet"}
    assert cas.files[("public", "LLM_FACT_SHEET.sashdat")]["of"] == "the sheet"


def test_a_table_that_could_not_be_dropped_is_said_to_be_left():
    """gate-19. Where the drop of the table is refused, the promote is refused as well - the name is taken - and
    so was the load from the saved file. The program then said that the table could not be loaded again from its
    file, while the table of before was in memory. It asks whether a table of that name is in memory, loads
    nothing over it and says that it was left."""
    cas = library(refuse={("table.dropTable", "LLM_FACT_SHEET")})

    assert swapped(cas) == [KEPT]
    assert in_memory(cas) == BEFORE and cas.session == {} and cas.files[("public", "LLM_FACT_SHEET.sashdat")]["of"] == "the table of before"
    assert not [action for action, _ in cas.calls if action == "table.loadTable"]
    # the errors of the log are the two CAS wrote: none for a load that had to be refused
    assert cas.errors == ["ERROR: table.dropTable: the table LLM_FACT_SHEET could not be dropped",
                          "ERROR: table.promote: the table LLM_FACT_SHEET_NEW could not be promoted as LLM_FACT_SHEET"]


def test_a_refused_promote_loads_the_table_again_from_its_file():
    cas = library(refuse={("table.promote", "LLM_FACT_SHEET")})

    assert swapped(cas) == [RESTORED]
    assert in_memory(cas) == BEFORE and cas.session == {}
    assert [parameters["path"] for action, parameters in cas.calls if action == "table.loadTable"] == ["LLM_FACT_SHEET.sashdat"]


@pytest.mark.parametrize("saved", [True, False], ids=["the load is refused", "there is no saved file"])
def test_a_refused_promote_and_a_load_that_fails_leave_no_table_and_say_so(saved):
    cas = library(refuse={("table.promote", "LLM_FACT_SHEET")} | ({("table.loadTable", "LLM_FACT_SHEET")} if saved else set()), saved=saved)

    assert swapped(cas) == [GONE]
    assert in_memory(cas) is None and cas.session == {}
    assert ("public", "LLM_FACT_SHEET.sashdat") in cas.files if saved else cas.files == {}


def test_a_table_that_is_promoted_and_not_saved_says_so():
    cas = library(refuse={("table.save", "LLM_FACT_SHEET")})

    assert swapped(cas) == [NOT_SAVED]
    assert in_memory(cas)["of"] == "the sheet" and cas.files[("public", "LLM_FACT_SHEET.sashdat")]["of"] == "the table of before"


def test_each_outcome_has_one_sentence_and_the_sentences_differ():
    """Five ways the swap can end, one line of the log for each but the one that worked - and for a sheet that
    was not read completely nothing at all."""
    assert len({KEPT, RESTORED, GONE, NOT_SAVED}) == 4
    cas = library()
    assert swapped(cas, _lfs_ok="0") == [] and cas.calls == [] and in_memory(cas) == BEFORE
    swap = swap_macro()
    assert re.findall(r"%if &_lfs_outcome\. EQ (\w+) %then", swap) == ["kept", "restored", "replace", "save"]
    in_order(swap, 'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;',
             'table.tableExists result = still status = stillAsked / caslib = "&_lfs_caslib." name = "&_lfs_table.";',
             'if still.exists != 0 then outcome = "kept";', "table.loadTable status = restored /")


def test_the_readme_says_of_the_program_what_it_does():
    """The README of the folder said that the table is 'dropped, loaded, promoted and saved as before': since the
    counted upload takes the place of the table, no statement loads the sheet a second time. It says that now, and
    what becomes of a folder that is not there and of a promote that is refused."""
    readme = " ".join(program("README.md").split())
    code = without_comments(program("Load-Fact-Sheets.sas"))

    assert "dropped, loaded, promoted and saved as before" not in readme
    assert ("only a sheet that CAS held completely takes the place of the table: the table is dropped, and the upload whose rows were "
            "counted is promoted under its name and saved - the sheet is not loaded a second time.") in readme
    assert code.count("load data=") == 1 and code.count("table.promote ") == 1 and code.count("table.loadTable ") == 1
    assert ("A file that is not in the folder, a folder that is not there, a file without rows and a sheet that CAS did not take leave "
            "the table of that sheet as it is, with a WARNING, and the other sheets are loaded.") in readme
    assert ("Where the promote is refused, a table of that name that is in memory - the table could not be dropped - is left as it is, "
            "and where none is, the table is loaded again from the file it was saved to before; each of these outcomes is a WARNING of "
            "its own.") in readme
    # and of the name the sheet is loaded under
    assert ("A table that has the name `<table>_NEW` before a sheet is loaded is no table of the program: CAS is asked first "
            "(`table.tableExists`), and where it does not answer that there is none, the sheet is not loaded and neither table is "
            "touched, with a WARNING that says to rename or drop that table.") in readme
    assert code.count('table.tableExists result = taken status = takenAsked / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW";') == 1
    # what has not run in SAS is said in the changelog and in the guide, not here
    paragraph = readme[readme.index("*Load-Fact-Sheets.sas* reads the three fact sheet files"):readme.index("*Build-RAG-Cost-View.sas* stacks")]
    assert not re.search(r"not (been )?run|untested|not measured", paragraph, flags=re.IGNORECASE)
