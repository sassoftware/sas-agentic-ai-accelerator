"""The log parser: the code file and the custom step stay the same program, and the
parser turns the request blocks of every model kind into the rows of LLM_LOGS - the
twelve columns the table has always had and the columns that were added behind them.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import hashlib
import json
import math
import random
import re
import types
from datetime import datetime

import pandas as pd
import pytest

from log_parser_harness import (
    DECISION_ANSWERS,
    DECISION_QUESTIONS,
    DECISION_STATE,
    EMBEDDED_DOCUMENT,
    PROVIDER_ERROR,
    as_log,
    code_file_program,
    decision_block,
    decision_inputs,
    embedding_block,
    failed_block,
    get_block,
    in_file_order,
    lines_of,
    llm_block,
    llm_inputs,
    log_line,
    log_units,
    made_up_id,
    parse,
    python_block,
    request_block,
    retrieval_envelope,
    run_parser,
    split_at_scoring,
    startup_lines,
    step_definition,
    step_program,
)

# The first twelve columns of LLM_LOGS. Tables that sites append to and the LLM Usage
# Report hold exactly these names in this order, which is why they are spelled out here again.
LLM_LOGS_COLUMNS = [
    "timestamp", "model", "system_prompt", "user_prompt", "temperature", "top_p", "top_k", "max_tokens",
    "prompt_length", "output_length", "runtime", "response",
]
# The columns behind them, in the order the three programs that write and read the table agreed on
ADDED_COLUMNS = [
    "kind", "status_code", "failed", "error_text", "transaction_id", "request_id", "user_agent",
    "embedding_mode", "project", "input_hash", "answer", "answer_confidence", "options", "rag_collection", "rag_query_hash",
    "model_id", "caller_model_id", "caller_project_id", "caller_kind", "caller_name", "experiment_run_id", "rag_model_id",
    "match_method", "match_candidates",
]
ALL_COLUMNS = LLM_LOGS_COLUMNS + ADDED_COLUMNS
# The parser leaves these to the program that links the logs to the models
LINK_COLUMNS = ADDED_COLUMNS[ADDED_COLUMNS.index("model_id"):]
NUMERIC_COLUMNS = [
    "timestamp", "temperature", "top_p", "top_k", "max_tokens", "prompt_length", "output_length", "runtime",
    "status_code", "failed", "answer_confidence", "match_candidates",
]
TEXT_COLUMNS = [column for column in ALL_COLUMNS if column not in NUMERIC_COLUMNS]
# The ids of the controls of the first twelve columns: flows that use the step hold them
FIRST_COLUMN_CONTROLS = [
    "_log_timestamp", "_log_model", "_log_sys_prompt", "_log_usr_prompt", "_log_temp", "_log_top_p", "_log_top_k",
    "_log_max_tok", "_log_prompt_len", "_log_output_len", "_log_run_time", "_log_response",
]


def missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


def text_hash(text):
    """The hash the three programs know a text by: SHA-256 of the text with every run of white space made one
    blank. A character without UTF-8 bytes counts as a question mark, as it does in the programs."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8", errors="replace")).hexdigest()


def notes(run):
    return [message for kind, message in run.sas.messages if kind == "NOTE"]


# ---------------------------------------------------------------------------
# One program in two places
# ---------------------------------------------------------------------------

def test_code_file_and_step_carry_the_same_python():
    assert python_block(code_file_program()) == python_block(step_program())


def test_step_program_is_the_code_file_without_its_configuration():
    """The code file sets the options a user of the step picks in its UI, and removes
    them again at the end. Everything in between is the program of the step."""
    code_file = code_file_program()
    step = step_program()
    assert code_file.count(step) == 1
    before, after = code_file.split(step)
    assert before.rstrip().endswith("%let _log_save_table = 1;")
    assert after.strip().startswith("%symdel _log_path ") and after.strip().count("\n") == 0


def test_step_announces_the_columns_the_parser_writes(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block())))
    labels = run.namespace["labels"]

    step = step_definition()
    column_controls = step["flowMetadata"]["outputPorts"][0]["columnDelta"]["updateColumnArguments"]
    values = json.loads(step["ui"])["values"]

    assert column_controls[:12] == FIRST_COLUMN_CONTROLS
    assert len(set(column_controls)) == len(column_controls)
    assert [values[control]["value"] for control in column_controls] == ALL_COLUMNS
    assert {values[control]["value"]: values[control]["label"] for control in column_controls} == labels
    # A column the parser uploads as a number is announced as one
    numeric = [values[control]["value"] for control in column_controls if values[control].get("type") == "n"]
    assert numeric == [column for column in ALL_COLUMNS if column in NUMERIC_COLUMNS]
    assert all(values[control].get("length") == 8 for control in column_controls if values[control].get("type") == "n")


def test_every_announced_column_has_a_control_of_its_own():
    definition = json.loads(step_definition()["ui"])
    section = next(control for control in definition["pages"][0]["children"] if control["id"] == "_log_outputDefinition")
    column_controls = step_definition()["flowMetadata"]["outputPorts"][0]["columnDelta"]["updateColumnArguments"]

    assert [control["id"] for control in section["children"]] == column_controls
    assert {control["type"] for control in section["children"]} == {"newcolumn"}
    # The id of a control is the start of the macro variables SAS Studio hands the step
    assert all(re.fullmatch(r"_log_[a-z_]{1,13}", control) for control in column_controls)


# ---------------------------------------------------------------------------
# The table the parser describes
# ---------------------------------------------------------------------------

def _write(tmp_path, text, name="llms.log"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_columns_are_the_twelve_of_llm_logs_and_the_added_ones_behind_them(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(), decision_block(), embedding_block())))

    assert run.namespace["COLUMNS"][:12] == LLM_LOGS_COLUMNS
    assert run.namespace["COLUMNS"][12:] == ADDED_COLUMNS
    assert list(run.namespace["df"].columns) == ALL_COLUMNS
    assert sorted(run.namespace["labels"]) == sorted(ALL_COLUMNS)
    assert run.namespace["LINK_COLUMNS"] == LINK_COLUMNS


@pytest.mark.parametrize("text", [
    "",
    as_log(startup_lines("2026-01-05 08:00:00", "demo_llm")),
    as_log(startup_lines("2026-01-05 08:00:00", "demo_llm"), get_block("2026-01-05 08:30:00"), get_block("2026-01-05 08:30:01", "None")),
], ids=["zero bytes", "start-up lines only", "requests that score nothing"])
def test_log_without_requests_gives_an_empty_table_and_uploads_nothing(monkeypatch, tmp_path, text):
    run = run_parser(monkeypatch, _write(tmp_path, text))

    frame = run.namespace["df"]
    assert len(frame) == 0
    assert list(frame.columns) == ALL_COLUMNS
    assert run.uploads == []
    assert run.row_count == "0"
    # Nothing in these logs looks like a request, so nothing is said about them
    assert run.sas.messages == []


def test_rows_are_uploaded_once_under_the_staging_name_with_replace(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(), decision_block(), embedding_block())))

    assert run.row_count == "3"
    assert len(run.uploads) == 1
    upload = run.uploads[0]
    assert upload["casout"]["name"] == "_TEMP_LLM_LOGS"
    assert upload["casout"]["caslib"] == "CASUSER"
    # A CAS session that is used again can still hold the staging table of an earlier run
    assert upload["casout"]["replace"] is True
    assert [var["name"] for var in upload["importoptions"]["vars"]] == ALL_COLUMNS
    assert len(upload["frame"]) == 3
    # Requests that follow each other say nothing about themselves in the SAS log
    assert run.sas.messages == []


def test_every_column_is_uploaded_with_its_type(monkeypatch, tmp_path):
    """A log of embedding calls has no prompt and no response at all, and the columns of
    the link are empty in every log. The types are stated, so these columns are text
    columns whatever the log holds."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(embedding_block())))

    types = {var["name"]: var["type"] for var in run.uploads[0]["importoptions"]["vars"]}
    assert types == {column: ("varchar" if column in TEXT_COLUMNS else "double") for column in ALL_COLUMNS}
    formats = {var["name"]: var.get("format") for var in run.uploads[0]["importoptions"]["vars"]}
    assert formats["timestamp"] == "DATETIME20."
    assert [name for name, value in formats.items() if value] == ["timestamp"]


def test_row_count_stays_unset_when_the_parser_fails(monkeypatch, tmp_path):
    with pytest.raises(FileNotFoundError) as failure:
        run_parser(monkeypatch, tmp_path / "no-such-file.log")
    assert failure.value.sas.macro_variables["_log_row_count"] == "-1"


def test_timestamp_becomes_a_sas_datetime(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(timestamp="2026-01-05 09:15:00"))))

    seconds_since_1960 = (datetime(2026, 1, 5, 9, 15, 0) - datetime(1960, 1, 1)).total_seconds()
    assert run.namespace["df"].loc[0, "timestamp"] == seconds_since_1960


def test_the_columns_of_the_link_are_written_empty(monkeypatch, tmp_path):
    """Nothing in a log names the model in Model Manager or the prompt, the decision
    template or the RAG setup that sent a request: the parser writes these columns
    empty for every kind of request, and another program fills them."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(user_prompt="Context: " + retrieval_envelope()), decision_block(), embedding_block())))

    frame = run.uploads[0]["frame"]
    assert bool(frame[LINK_COLUMNS].isna().all().all())
    assert pd.api.types.is_numeric_dtype(frame["match_candidates"])


# ---------------------------------------------------------------------------
# The SAS code that writes the output table
#
# It only runs on a SAS Viya server. What is held here is its text: the order of
# its actions in both modes, in the code file and in the step, and what the DATA
# step that appends does by the rules SAS documents for it.
# ---------------------------------------------------------------------------

def _output_macro(program):
    return program[program.index("%macro _log_handle_output_table;"):program.index("%mend _log_handle_output_table;")]


def _append_lookup(program):
    """The CASL append mode runs first: it looks for the table the rows are appended to."""
    macro = _output_macro(program)
    start = macro.index("%if &_log_append_table. %then %do;")
    return macro[start:macro.index("%if &_log_target_scope. GE 1 %then %do;", start)]


def _append_step(program):
    """The statements that write the existing rows and the new ones to one table."""
    macro = _output_macro(program)
    start = macro.index("%if &_log_target_scope. GE 1 %then %do;")
    return macro[start:macro.index("libname _llmsrc clear;", start) + len("libname _llmsrc clear;")]


def _swap(program):
    """The CASL that puts the new table in the place of the existing one, in both modes."""
    macro = _output_macro(program)
    start = macro.index("proc cas;", macro.index("libname _llmsrc clear;"))
    return macro[start:macro.index("%else %if &_log_row_count. EQ 0 %then %do;")]


BOTH_PROGRAMS = pytest.mark.parametrize("program", [code_file_program, step_program], ids=["code file", "step"])


@BOTH_PROGRAMS
def test_appending_loads_a_saved_table_before_it_looks_for_the_table_to_append_to(program):
    """table.tableExists only sees the tables in memory. After a restart of the CAS
    server the table is on disk only, and a new table started from the rows of one log
    would be saved over the file that holds the rows of every run before."""
    lookup = _append_lookup(program())

    first_look, listing, load = (lookup.index(action) for action in ("table.tableExists result=re", "table.fileInfo", "table.loadTable"))
    look_again = lookup.index("table.tableExists result=re", load)
    decided = lookup.index("call symputx('_log_target_scope', '2');")
    assert first_look < listing < load < look_again < decided
    # The file keeps the case the table name was saved with, a CAS table name has none
    assert 'if savedEntry.Name EQ "&_log_target_table_name..sashdat" then savedFile = savedEntry.Name;' in lookup
    assert 'upcase(savedEntry.Name) EQ upcase("&_log_target_table_name..sashdat") then savedFile = savedEntry.Name;' in lookup
    # Loaded into the session: whether the table is promoted stays the choice of the user
    assert "promote" not in lookup[load:look_again].lower()


@BOTH_PROGRAMS
def test_what_appending_found_is_handed_on_in_two_macro_variables(program):
    program = program()
    lookup = " ".join(_append_lookup(program).split())

    # 2 a promoted table, 1 a table of the session, and a saved file only counts where no table was found
    assert ("if re.exists GE 2 then do; call symputx('_log_target_scope', '2'); end; "
            "else if re.exists EQ 1 then do; call symputx('_log_target_scope', '1'); end; "
            "else if savedFile NE '' then do; call symputx('_log_target_unloaded', '1'); end;") in lookup
    # Both start at 0 in every run - the step can run twice in one SAS session - and are deleted at the end
    before_the_macro = program[:program.index("%macro _log_handle_output_table;")]
    assert "%let _log_target_scope = 0;\n%let _log_target_unloaded = 0;\n" in before_the_macro
    assert "%let _log_fixed_rename = ;\n%let _log_fixed_assign = ;\n%let _log_fixed_drop = ;\n" in before_the_macro
    clean_up = program[program.index("* Clean up;"):]
    assert ("%symdel _log_clean_cas_session _casSessionUUID _log_row_count _log_target_scope _log_target_unloaded"
            " _log_fixed_rename _log_fixed_assign _log_fixed_drop _log_append_syserr;") in clean_up
    # Replace mode never asks: the lookup runs in append mode only
    macro = _output_macro(program)
    assert macro.index("%if &_log_append_table. %then %do;") < macro.index("call symputx('_log_target_scope'")
    assert macro.count("%if &_log_append_table. %then %do;") == 1


@BOTH_PROGRAMS
def test_saved_table_that_can_not_be_loaded_is_not_overwritten(program):
    program = program()
    swap = _swap(program)

    refused = swap[swap.index("%if &_log_target_unloaded. %then %do;"):swap.index("%else %do;")]
    assert "print (error)" in refused
    assert "could not be loaded, nothing was appended and the saved file was left as it is." in refused
    assert "tableWritten = False;" in refused
    # No table is built, dropped or renamed in that branch
    for action in ("table.copyTable", "table.dropTable", "table.alterTable", "table.promote"):
        assert action not in refused, action
        assert action in swap[swap.index("%else %do;"):], action
    # And nothing is saved without a written table
    save = swap[swap.index("%if &_log_save_table. %then %do;"):]
    assert save.index("if tableWritten then do;") < save.index("table.save")
    assert swap.count("table.save") == 1


@BOTH_PROGRAMS
def test_existing_rows_and_new_rows_become_one_table_beside_the_existing_one(program):
    """Appending builds a new table and swaps it in, as replacing does. The rows are not
    added to the existing table in place: a table with fewer columns would not take them."""
    program = program()
    lookup, step, swap = _append_lookup(program), _append_step(program), _swap(program)

    assert "table.append" not in program
    # What an earlier run left of its own table is dropped before the table of this run is built
    assert re.search(r"table\.dropTable /\s+casLib = \"&_log_target_table_lib\.\",\s+name = '_TEMP_LLM_LOGS_ALL',\s+quiet = True;", lookup)
    statements = [line.strip() for line in step.split("\n") if line.strip()]
    assert statements[0] == "%if &_log_target_scope. GE 1 %then %do;"
    assert 'libname _llmtgt cas caslib="&_log_target_table_lib.";' in statements
    assert "libname _llmsrc cas caslib='CASUSER';" in statements
    assert "data _llmtgt._TEMP_LLM_LOGS_ALL;" in statements
    # The existing table is read first, the rows of the log behind it
    read = 'set _llmtgt."&_log_target_table_name."n(&_log_fixed_rename. in=_log_existing_row) _llmsrc._TEMP_LLM_LOGS;'
    built = statements.index("data _llmtgt._TEMP_LLM_LOGS_ALL;")
    assert statements[built + 1].startswith("length timestamp 8 model varchar(*) ")
    assert statements[statements.index(read):] == [read, "&_log_fixed_assign.", "&_log_fixed_drop.", "run;", "%let _log_append_syserr = &syserr.;",
                                                   "libname _llmtgt clear;", "libname _llmsrc clear;"]
    assert built < statements.index(read)

    # The swap takes that table where there was a table to append to, and the rows of the log otherwise
    staged = " ".join(swap[swap.index("%else %do;"):swap.index("table.tableExists result=staged")].split())
    assert "%if &_log_target_scope. GE 1 %then %do; stagedTable = '_TEMP_LLM_LOGS_ALL';" in staged
    assert ('%else %do; stagedTable = "&_log_target_table_name._NEW"; table.copyTable / table = {casLib = \'CASUSER\', name = \'_TEMP_LLM_LOGS\'}, '
            'casOut = {casLib = "&_log_target_table_lib.", name = stagedTable, replace = True}; %end;') in staged
    # The existing table is only dropped once its replacement exists
    exists, drop, rename = (swap.index(text) for text in ("if staged.exists GE 1 and stagedComplete then do;", "table.dropTable", "table.alterTable"))
    assert exists < drop < rename
    # The existing table is dropped twice, and what a DATA step that did not finish left behind once
    assert len(re.findall(r'table\.dropTable /\s+casLib = "&_log_target_table_lib\.",\s+name = "&_log_target_table_name\.",', swap)) == 2
    assert swap.count("table.dropTable") == 3
    assert 'print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is.";' in swap[rename:]


@BOTH_PROGRAMS
def test_a_table_that_was_promoted_is_promoted_again(program):
    """The new table is a table of the session. A table that other sessions could see
    before the rows were appended has to be seen by them afterwards, whatever the
    option of this run says."""
    swap = _swap(program())

    promote = swap[swap.index("table.alterTable"):swap.index("tableWritten = True;")]
    assert "%if &_log_promote_table. or &_log_target_scope. EQ 2 %then %do;" in promote
    assert promote.count("table.promote") == 1


def _length_statement(program):
    """The LENGTH statement of the DATA step that appends, as (column, length) pairs."""
    step = _append_step(program)
    step = step[step.index("data _llmtgt._TEMP_LLM_LOGS_ALL;"):]
    statement = step[step.index("    length ") + len("    length "):]
    words = statement[:statement.index(";")].split()
    assert len(words) % 2 == 0
    return list(zip(words[0::2], words[1::2]))


def _handed_over_columns(program):
    """The columns the program hands over when the existing table holds them as CHAR: the list its DATA _NULL_ step compares the column names with."""
    step = _append_step(program)
    listed = step[step.index("lowcase(strip(Column)) in ("):]
    return re.findall(r"'(\w+)'", listed[:listed.index(") then do;")])


def _type_family(kind):
    return {"double": "numeric", "varchar": "varchar"}.get(kind, "character")


def _appended_columns(program, existing, uploaded):
    """The columns of the table append mode writes for an existing table and the uploaded
    rows, by the statements of the program and the rules of the DATA step.

    The program: a column that the existing table holds as CHAR and that the list of its
    DATA _NULL_ step names is read as _fx_<column>, assigned to <column> for the rows
    of the existing table, and dropped.
    The DATA step: a variable is created where the step names it first - in the LENGTH
    statement, then in the tables of the SET statement in their order - and has the type
    and the length it has there. A variable that is character in one place and numeric
    in another stops the step, and so does one that is CHAR in one place and VARCHAR in
    another (the RAG steps of this repository met that on SAS Viya, see Ingest-Documents.sas).

    A table is given as (column, type) pairs with the types double, varchar and char(n).
    Upper and lower case make no two names in SAS, so the names are compared in lower case."""
    existing = [(name.lower(), kind) for name, kind in existing]
    handed_over = [name for name, kind in existing if _type_family(kind) == "character" and name in _handed_over_columns(program)]
    read = [("_fx_" + name if name in handed_over else name, kind) for name, kind in existing]
    columns = {name: "double" if length == "8" else length.replace("(*)", "") for name, length in _length_statement(program)}
    for table in (read, uploaded):
        for name, kind in table:
            first = columns.setdefault(name, kind)
            if _type_family(first) != _type_family(kind):
                raise ValueError(f"Variable {name} has been defined as both {_type_family(first)} and {_type_family(kind)}.")
    for name in handed_over:
        assert name in columns, name
        del columns["_fx_" + name]
    return columns


def _uploaded_columns(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block())))
    return [(var["name"], var["type"]) for var in run.uploads[0]["importoptions"]["vars"]]


@BOTH_PROGRAMS
def test_the_length_statement_holds_the_columns_of_the_parser_with_their_types(monkeypatch, tmp_path, program):
    uploaded = _uploaded_columns(monkeypatch, tmp_path)

    lengths = _length_statement(program())
    assert [name for name, _ in lengths] == ALL_COLUMNS
    assert [(name, {"8": "double", "varchar(*)": "varchar"}[length]) for name, length in lengths] == uploaded


def test_appending_to_a_table_of_twelve_columns_adds_the_others(monkeypatch, tmp_path):
    """The table as the program wrote it before the columns were added: text as VARCHAR, numbers as DOUBLE."""
    uploaded = _uploaded_columns(monkeypatch, tmp_path)
    twelve = uploaded[:12]
    assert [name for name, _ in twelve] == LLM_LOGS_COLUMNS

    appended = _appended_columns(code_file_program(), twelve, uploaded)

    assert list(appended) == ALL_COLUMNS
    assert appended == dict(uploaded)


@pytest.mark.parametrize("extra", [
    ["kind", "options"],
    ["rag_query_hash", "kind", "failed"],
    ADDED_COLUMNS,
    list(reversed(ADDED_COLUMNS)),
], ids=["two of them", "three in another order", "all of them", "all of them backwards"])
def test_appending_to_a_table_with_some_of_the_added_columns_adds_the_rest(monkeypatch, tmp_path, extra):
    """A table that an earlier version of the parser or the link widened in part. The
    columns come out in the order of the parser wherever the table had them."""
    uploaded = _uploaded_columns(monkeypatch, tmp_path)
    existing = uploaded[:12] + [column for name in extra for column in uploaded if column[0] == name]

    appended = _appended_columns(code_file_program(), existing, uploaded)

    assert list(appended) == ALL_COLUMNS


def test_appending_keeps_a_column_the_parser_does_not_know(monkeypatch, tmp_path):
    uploaded = _uploaded_columns(monkeypatch, tmp_path)
    existing = uploaded[:5] + [("team", "char(20)")] + uploaded[5:12] + [("cost_center", "double")]

    appended = _appended_columns(code_file_program(), existing, uploaded)

    # Behind the columns of the parser, in the order the table had them, with their type and length:
    # a CHAR column the parser does not know is not handed over to anything
    assert list(appended) == ALL_COLUMNS + ["team", "cost_center"]
    assert (appended["team"], appended["cost_center"]) == ("char(20)", "double")


def test_appending_to_a_table_with_shorter_text_columns_cuts_nothing(monkeypatch, tmp_path):
    """A table whose text columns have a fixed length, as a DATA step or an import writes
    them. Read as they are, they would stop the DATA step beside the VARCHAR columns of
    the uploaded rows. The program reads each of them under another name and hands its
    values to the VARCHAR column, so the new rows are not cut to the length the table had."""
    uploaded = _uploaded_columns(monkeypatch, tmp_path)
    short = {"model": "char(10)", "system_prompt": "char(200)", "user_prompt": "char(200)", "response": "char(100)", "kind": "char(9)", "Options": "char(50)"}
    existing = [(name, short.get(name, kind)) for name, kind in uploaded[:12]] + [("kind", "char(9)"), ("Options", "char(50)")]

    appended = _appended_columns(code_file_program(), existing, uploaded)

    assert list(appended) == ALL_COLUMNS
    assert {name: appended[name] for name in TEXT_COLUMNS} == dict.fromkeys(TEXT_COLUMNS, "varchar")
    # Read as it is, the first of these columns would have ended the step
    with pytest.raises(ValueError, match="Variable model has been defined as both varchar and character"):
        _appended_columns(code_file_program().replace("'model' 'system_prompt'", "'system_prompt'"), existing, uploaded)


@BOTH_PROGRAMS
def test_every_text_column_of_the_parser_is_handed_over_from_a_char_column(program):
    """The existing table is asked for its columns, and the statements the DATA step is
    given are put together from the answer: a rename for the SET statement, the
    assignments for the rows of the existing table and a drop."""
    program = program()
    step = _append_step(program)

    assert _handed_over_columns(program) == TEXT_COLUMNS
    asked, saved, put_together, built = (step.index(text) for text in (
        "table.columnInfo result=targetColumns", "saveresult targetColumns dataout=work._log_target_columns;",
        "set work._log_target_columns end=lastColumn;", "data _llmtgt._TEMP_LLM_LOGS_ALL;"))
    assert asked < saved < put_together < built
    assert "if lowcase(strip(Type)) EQ 'char' and lowcase(strip(Column)) in (" in step
    for statement in ("fixedRename = catx(' ', fixedRename, cats(Column, '=_fx_', Column));",
                      "fixedAssign = catx(' ', fixedAssign, cats(Column, '=trimn(_fx_', Column, ');'));",
                      "fixedDrop = catx(' ', fixedDrop, cats('_fx_', Column));",
                      "call symputx('_log_fixed_rename', cats('rename=(', fixedRename, ')'));",
                      "call symputx('_log_fixed_assign', catx(' ', 'if _log_existing_row then do;', fixedAssign, 'end;'));",
                      "call symputx('_log_fixed_drop', catx(' ', 'drop', fixedDrop, ';'));"):
        assert statement in step, statement
    # Nothing is handed over where the table holds no such column: the three stay empty and the DATA step reads the table as it is
    assert "if lastColumn and fixedRename NE '' then do;" in step
    # The table of column names is a table of the SAS session and is deleted again, also the one of a run that did not get this far
    assert step.count("proc delete data=work._log_target_columns;") == 2
    assert step.index("proc delete data=work._log_target_columns;") < asked


def test_appending_to_a_table_whose_column_has_the_other_type_stops(monkeypatch, tmp_path):
    """The DATA step ends with an error, its table is not written, and the swap leaves the existing table as it is."""
    uploaded = _uploaded_columns(monkeypatch, tmp_path)
    existing = [(name, "char(8)" if name == "temperature" else kind) for name, kind in uploaded[:12]]

    with pytest.raises(ValueError, match="Variable temperature has been defined as both numeric and character"):
        _appended_columns(code_file_program(), existing, uploaded)
    swap = _swap(code_file_program())
    assert swap.index("if staged.exists GE 1 and stagedComplete then do;") < swap.index("table.dropTable")


def test_replacing_writes_every_column(monkeypatch, tmp_path):
    """Replace mode copies the uploaded rows, and the upload holds every column whatever the log holds."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(embedding_block())))

    assert list(run.uploads[0]["frame"].columns) == ALL_COLUMNS
    swap = _swap(code_file_program())
    assert "table = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'}," in swap


# ---------------------------------------------------------------------------
# What the step takes from the SAS session
#
# The code file starts a CAS session and assigns its librefs in its first lines.
# The step is the program without them, and its About tab says what it does in a
# session that has neither. Held here is the text of both: whoever changes the
# program changes the About tab with it.
# ---------------------------------------------------------------------------

def _about_tab():
    """The texts of the step's About tab by the id of their control, each on one line."""
    about = next(page for page in json.loads(step_definition()["ui"])["pages"] if page["id"] == "pageAbout")
    controls = list(about["children"])
    texts = {}
    while controls:
        control = controls.pop()
        controls += control.get("children", [])
        if control["type"] == "text":
            texts[control["id"]] = " ".join(control["text"].split())
    return texts


def test_code_file_brings_the_cas_session_and_the_librefs_the_step_takes_from_its_session():
    code_file = code_file_program()
    step = step_program()

    first_lines = code_file[:code_file.index(step)]
    for statement in ("cas mysess;", "libname public cas caslib='Public';", "libname casuser cas caslib='casuser';"):
        assert statement in first_lines, statement
    statements = [line.strip() for line in step.split("\n")]
    # The only librefs the step assigns are the two of the DATA step that appends, and it clears both
    assert [line for line in statements if line.startswith("libname ")] == [
        'libname _llmtgt cas caslib="&_log_target_table_lib.";', "libname _llmsrc cas caslib='CASUSER';",
        "libname _llmtgt clear;", "libname _llmsrc clear;"]
    # The only session the step ever starts is the one it also ends
    assert [line for line in statements if line.startswith("cas ")] == ["cas llmLogMySess;", "cas llmLogMySess terminate;"]


def _session_check(program):
    """The statements between the comment that announces the session check and the first PROC CAS."""
    start = program.index("* Check if a CAS session exists;")
    return program[start:program.index("proc cas;", start)]


@BOTH_PROGRAMS
def test_a_cas_session_is_started_only_where_there_is_none_and_ended_only_then(program):
    program = program()

    check = [line for line in _session_check(program).split("\n") if not line.startswith("*")]
    assert check[:3] == [
        "%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;",
        "    %let _log_clean_cas_session = 1;",
        "    cas llmLogMySess;",
    ]
    assert check[4:6] == ["%else %do;", "    %let _log_clean_cas_session = 0;"]
    # A name that is still there says nothing about the session: no macro variable decides
    assert "%symexist" not in _session_check(program)
    clean_up = program[program.index("* Clean up;"):]
    assert "%if &_log_clean_cas_session. %then %do;\n    cas llmLogMySess terminate;\n%end;" in clean_up


class SasSession:
    """What the session check can read of a SAS session: the CAS sessions that are
    connected, the session the option SESSREF names and the macro variables."""

    def __init__(self, connected, sessref, macro_variables):
        self.connected = {name.upper() for name in connected}
        self.sessref = sessref
        self.macro_variables = {name.upper() for name in macro_variables}


# The three states are the ones the job logs of SAS Viya Stable 2026.09 showed (11OCT2026).
# A job started without a CAS session, without _CASNAME_ and with the option at its default.
NO_CAS_SESSION = SasSession(connected=[], sessref="CASAUTO", macro_variables=[])
# After a run of the step with a session of its own: "cas llmLogMySess terminate;" had
# ended the session, "cas _all_ list;" listed none, and _CASNAME_, _SESSREF_ and the
# option SESSREF still named it.
OWN_SESSION_ENDED = SasSession(connected=[], sessref="LLMLOGMYSESS", macro_variables=["_CASNAME_", "_SESSREF_"])
# "cas mysess;" in front of the step
SESSION_OF_THE_USER = SasSession(connected=["MYSESS"], sessref="MYSESS", macro_variables=["_CASNAME_", "_SESSREF_"])


def _starts_a_session_of_its_own(program, session):
    """Whether the session check of the program starts "cas llmLogMySess;" in that SAS session.

    The condition is taken from the program and its functions are given their documented
    results: SESSFOUND is 1 for a session that is connected and 0 otherwise, GETOPTION
    returns the option, %SYMEXIST is 1 for a macro variable that exists."""
    lines = [line for line in _session_check(program).split("\n") if not line.startswith("*")]
    assert lines[0].startswith("%if ") and lines[0].endswith(" %then %do;"), lines[0]
    assert lines[1:7] == [
        "    %let _log_clean_cas_session = 1;", "    cas llmLogMySess;", "%end;",
        "%else %do;", "    %let _log_clean_cas_session = 0;", "%end;"]

    condition = lines[0][len("%if "):-len(" %then %do;")]
    condition = condition.replace("%sysfunc(getoption(sessref))", session.sessref)
    condition = re.sub(r"%sysfunc\(sessfound\((\w+)\)\)",
                       lambda found: str(int(found.group(1).upper() in session.connected)), condition)
    condition = re.sub(r"%symexist\((\w+)\)",
                       lambda exists: str(int(exists.group(1).upper() in session.macro_variables)), condition)
    left, operator, right = condition.split()
    return {"EQ": int(left) == int(right), "NE": int(left) != int(right)}[operator]


@BOTH_PROGRAMS
def test_a_session_without_a_cas_session_gets_one_that_is_ended_again(program):
    assert _starts_a_session_of_its_own(program(), NO_CAS_SESSION)


@BOTH_PROGRAMS
def test_the_name_an_ended_session_left_behind_is_not_taken_for_a_cas_session(program):
    """The step twice in one SAS session that has no CAS session. The first run ends its
    own session and the name stays behind. A second run that went by the name started no
    session, and its first CAS action ended with "ERROR: There is no server connection to
    execute the action 'session.sessionId'."."""
    assert _starts_a_session_of_its_own(program(), OWN_SESSION_ENDED)


@BOTH_PROGRAMS
def test_a_cas_session_of_the_user_is_used_and_never_ended(program):
    program = program()

    assert not _starts_a_session_of_its_own(program, SESSION_OF_THE_USER)
    statements = [line.strip() for line in program.split("\n")]
    # The one terminate names the step's own session and stands under the flag that only
    # the branch with "cas llmLogMySess;" sets
    assert [line for line in statements if line.startswith("cas ") and "terminate" in line] == ["cas llmLogMySess terminate;"]
    assert [line for line in statements if line.startswith("%let _log_clean_cas_session")] == [
        "%let _log_clean_cas_session = 1;", "%let _log_clean_cas_session = 0;"]
    # Neither the option nor a session reference is set by the program itself
    assert not [line for line in statements if line.lower().startswith("options ") or "sessref=" in line.lower()]


@BOTH_PROGRAMS
def test_the_caslib_is_read_from_the_libref_and_a_libref_that_is_not_assigned_stops_nothing(program):
    """The select finds no row for a libref the session does not have, which leaves the
    macro variable as it is: its value then reaches CAS as the name of the caslib."""
    program = program()

    lookup = program[program.index("* Get the caslib name of the libname;"):program.index("* Check if a CAS session exists;")]
    assert "select sysvalue into :_log_target_table_lib trimmed" in lookup
    assert "from dictionary.libnames" in lookup
    assert "where libname = upcase(\"&_log_target_table_lib.\") and upcase(sysname) = 'CASLIB';" in lookup
    assert "abort" not in lookup
    assert 'casLib = "&_log_target_table_lib.",' in program[program.index("%macro _log_handle_output_table;"):]


def test_about_tab_names_what_the_step_takes_from_the_session():
    prerequisites = _about_tab()["textPrereqs"]
    step = step_program()

    # Named the way the program spells them
    for name in ("llmLogMySess", "dictionary.libnames", "_TEMP_LLM_LOGS", "SSLCALISTLOC", "SAS_SERVICES_TOKEN"):
        assert name in prerequisites, name
        assert name in step, name
    for option in ("CASHOST", "CASPORT"):
        assert option in prerequisites, option
        assert f"SAS.sasfnc('getoption', '{option.lower()}')" in step, option
    assert "caslib CASUSER" in prerequisites and "caslib='CASUSER'," in step
    # The session check: the function and the option it asks about, and both outcomes
    assert "the SESSFOUND function whether the session that the SAS option SESSREF names is connected" in prerequisites
    assert "%sysfunc(sessfound(%sysfunc(getoption(sessref))))" in step
    assert "the step runs in that session and does not end it" in prerequisites
    assert "starts a CAS session of its own (llmLogMySess) and ends it as its last action" in prerequisites
    assert "the step does not go by the name" in prerequisites
    # The macro variable the step went by before decides nothing any more
    assert "_CASNAME_ exists" not in prerequisites and "%symexist(_CASNAME_)" not in step
    assert "the name of the libref is handed to CAS as the name of the caslib" in prerequisites


def test_about_tab_names_the_librefs_the_step_assigns_to_append():
    prerequisites = _about_tab()["textPrereqs"]
    step = _append_step(step_program())

    assert "Two librefs of its own when it appends to a table that exists" in prerequisites
    for name in ("_llmtgt", "_llmsrc", "_TEMP_LLM_LOGS_ALL", "work._log_target_columns"):
        assert name in prerequisites, name
        assert name in step, name
    assert "asks CAS for the columns of the existing table" in prerequisites and "deletes that table" in prerequisites
    assert "clears both librefs" in prerequisites
    # The table is read through the libref, which is where the limit of its name comes from
    assert '_llmtgt."&_log_target_table_name."n' in step
    assert "32 characters at most" in prerequisites


def test_about_tab_says_that_the_log_file_is_read_on_the_sas_server():
    about = _about_tab()["textAbout"]

    assert "located on the SAS Server and NOT in SAS Content" in about
    assert "has to be copied to the SAS Server before the step runs" in about
    # Any other location ends the step before it reads a line
    refused = step_program()[:step_program().index("* Ensure that the output table is in CAS;")]
    assert "if lowCase(locationType) ne 'sasserver' then do;" in refused
    assert "abort 10;" in refused


def test_about_tab_names_every_column_and_what_the_program_does_with_it(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block())))
    columns = _about_tab()["textColumns"]

    for column in ALL_COLUMNS:
        assert re.search(rf"\b{column}\b", columns), column
    assert f"The {len(ALL_COLUMNS)} columns of the step stand in front" in columns
    # The numbers and names the text gives are the ones of the program
    assert run.namespace["ERROR_TEXT_LENGTH"] == 500 and "cut after 500 characters" in columns
    assert run.namespace["MAX_TOKENS_ALIASES"] == ["max_completion_tokens", "max_length"]
    assert "read from the option max_completion_tokens and then from max_length when the request has no max_tokens" in columns
    assert "python-requests, browser, SAS or other" in columns
    assert "llm, embedding or decision" in columns
    assert "replaced by REDACTED" in columns and run.namespace["REDACTED"] == "REDACTED"
    assert "text columns as VARCHAR" in columns
    # A fixed length is handed over with TRIMN, which is what takes the trailing blanks
    assert "holds with a fixed length (CHAR) is read under another name" in columns and "without their trailing blanks" in columns
    assert "'=trimn(_fx_'" in step_program()
    assert "is promoted again" in columns


def test_about_tab_is_about_the_program_and_the_changelog_starts_with_this_version():
    texts = _about_tab()

    assert json.loads(step_definition()["ui"])["pages"][1]["children"][-1]["children"][0]["text"].startswith("* Version: 2.3 (")
    changelog = texts["textChangelog"]
    assert changelog.index("Version: 2.3") < changelog.index("Version: 2.2") < changelog.index("Version: 2.1") < changelog.index("Version: 2.0")
    # What a release has not run yet is said in the changelog of the release, not in the step
    for name, text in texts.items():
        assert "not run in sas" not in text.lower(), name


# ---------------------------------------------------------------------------
# One block per model kind
# ---------------------------------------------------------------------------

def test_llm_block(monkeypatch, tmp_path):
    system_prompt = "You are a concise assistant.\nAnswer in German."
    user_prompt = "Wie heißt die Hauptstadt von Österreich?\nSay \"only\" the city's name: {city}, please."
    block = llm_block(system_prompt=system_prompt, user_prompt=user_prompt,
                      response="Die Hauptstadt von Österreich\nist Wien.\n\nGern geschehen.")
    frame = parse(monkeypatch, tmp_path, as_log(block))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["timestamp"] == "2026-01-05 09:15:00"
    assert row["model"] == "demo_llm"
    assert row["system_prompt"] == system_prompt
    assert row["user_prompt"] == user_prompt
    assert (row["temperature"], row["top_p"], row["max_tokens"]) == ("0.2", "1", "40")
    assert missing(row["top_k"])
    assert (row["prompt_length"], row["output_length"], row["runtime"]) == (21, 9, 1.25)
    # The lines of a response are joined with a blank
    assert row["response"] == "Die Hauptstadt von Österreich ist Wien.  Gern geschehen."

    assert row["kind"] == "llm"
    assert (row["status_code"], row["failed"]) == (200, 0)
    assert row["transaction_id"] == made_up_id("2026-01-05 09:15:00", "demo_llm", llm_inputs(system_prompt, user_prompt))
    assert row["options"] == "{temperature:0.2,top_p:1,max_tokens:40}"
    # What only another kind of request has stays missing
    for column in ("error_text", "embedding_mode", "project", "input_hash", "answer", "answer_confidence", "rag_collection", "rag_query_hash"):
        assert missing(row[column]), column


def test_decision_block_keeps_questions_state_and_answers(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(decision_block(options="{temperature:0}")))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["model"] == "demo_decider"
    assert row["system_prompt"] == DECISION_QUESTIONS
    assert row["user_prompt"] == DECISION_STATE
    assert row["response"] == DECISION_ANSWERS
    # The answers are still the JSON the model returned, each answer with its confidence
    answers = json.loads(row["response"])
    assert answers["department"]["choice"] == "billing"
    assert answers["department"]["confidence"] == 0.9
    assert (row["prompt_length"], row["output_length"], row["runtime"]) == (120, 14, 0.4)
    assert row["temperature"] == "0"

    assert row["kind"] == "decision"
    # The headline the score code logs beside the answers: the answer to the first question and its confidence
    assert (row["answer"], row["answer_confidence"]) == ("billing", 0.9)
    assert (row["status_code"], row["failed"]) == (200, 0)
    for column in ("embedding_mode", "project", "input_hash", "rag_collection", "rag_query_hash"):
        assert missing(row[column]), column


@pytest.mark.parametrize("logged, answer, confidence", [
    ("billing (0.900)", "billing", 0.9),
    ("yes (0.750)", "yes", 0.75),
    ("7 (1.000)", "7", 1.0),
    ("not enough information (see notes) (0.207)", "not enough information (see notes)", 0.207),
    ("billing", "billing", None),
    (" (0.000)", None, 0.0),
], ids=["choice", "noul", "score", "an answer with brackets", "no confidence", "no answer"])
def test_headline_of_a_decision(monkeypatch, tmp_path, logged, answer, confidence):
    frame = parse(monkeypatch, tmp_path, as_log(decision_block(answer=logged)))

    assert (None if missing(frame.loc[0, "answer"]) else frame.loc[0, "answer"]) == answer
    assert (None if missing(frame.loc[0, "answer_confidence"]) else frame.loc[0, "answer_confidence"]) == confidence
    # The headline never takes the place of the answers
    assert frame.loc[0, "response"] == DECISION_ANSWERS


def test_embedding_block_keeps_tokens_and_runtime_but_not_the_document(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(embedding_block()))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["model"] == "demo_embedder"
    assert row["prompt_length"] == 17
    assert row["runtime"] == 0.08
    for column in ("system_prompt", "user_prompt", "response", "output_length", "answer", "answer_confidence", "rag_collection", "rag_query_hash"):
        assert missing(row[column]), column
    # Neither the text nor a part of it reaches any cell
    assert not any(isinstance(cell, str) and "travel expenses" in cell for cell in row)
    assert EMBEDDED_DOCUMENT not in frame.to_csv()

    assert row["kind"] == "embedding"
    assert (row["embedding_mode"], row["project"]) == ("document", "handbook")
    # The text is known by its hash only
    assert row["input_hash"] == text_hash(EMBEDDED_DOCUMENT)
    assert re.fullmatch(r"[0-9a-f]{64}", row["input_hash"])
    assert (row["status_code"], row["failed"]) == (200, 0)


@pytest.mark.parametrize("options, mode", [
    ("{Embedding_Mode:query}", "query"),
    ("{Embedding_Mode:document,API_KEY:REDACTED}", "document"),
    ('{"Embedding_Mode": "query"}', "query"),
    ("{}", None),
], ids=["query", "document", "options as JSON", "not sent"])
def test_embedding_mode_is_the_option_as_it_was_sent(monkeypatch, tmp_path, options, mode):
    """A call that sends no mode is embedded in the mode its model defaults to, which the log does not say."""
    frame = parse(monkeypatch, tmp_path, as_log(embedding_block(options=options)))

    assert (None if missing(frame.loc[0, "embedding_mode"]) else frame.loc[0, "embedding_mode"]) == mode


@pytest.mark.parametrize("inputs, kind", [
    ([("document", "made-up text")], "embedding"),
    ([("questions", "{}")], "decision"),
    ([("state", "made-up state"), ("options", "{}")], "decision"),
    ([("userPrompt", "made-up question")], "llm"),
    ([("systemPrompt", None), ("userPrompt", None), ("options", None)], "llm"),
    ([("text", "made-up text"), ("options", "{}")], None),
    ([], None),
], ids=["a document", "questions", "a state", "a user prompt", "prompts without a value", "other inputs", "no inputs"])
def test_kind_is_decided_by_the_names_of_the_inputs(monkeypatch, tmp_path, inputs, kind):
    frame = parse(monkeypatch, tmp_path, as_log(request_block("2026-01-05 09:00:00", "demo_model", inputs, [("run_time", 0.5)])))

    assert (None if missing(frame.loc[0, "kind"]) else frame.loc[0, "kind"]) == kind


# ---------------------------------------------------------------------------
# The hash of a text
# ---------------------------------------------------------------------------

def test_hash_of_a_text_is_the_one_the_other_programs_compute(monkeypatch, tmp_path):
    """SHA-256 in lower-case hex of the UTF-8 bytes of the text with every run of white
    space made one blank. The digests are written out: the programs that read the table
    compute the same ones."""
    run = run_parser(monkeypatch, _write(tmp_path, ""))
    hash_of = run.namespace["text_hash"]

    question = "How long does a travel refund take?"
    assert hash_of(question) == "e422a7ae4d49d80be7ae1d8c9b32f06398796c0f498f0e5f3c0e342edf7a9032"
    assert hash_of("  How long\tdoes a travel\r\nrefund   take?\n") == hash_of(question)
    assert hash_of("Wie lange dauert eine Erstattung für Österreich?") == "d2273a642c1c1b3462649ed6b72cb0c63cfb32ea3576868f57b82325817d4006"
    # Nothing else is made the same
    assert hash_of(question.lower()) != hash_of(question)


def test_text_that_has_no_utf8_bytes_is_hashed_as_the_other_programs_hash_it(monkeypatch, tmp_path):
    """A request body can hold half of a surrogate pair, which has no UTF-8 bytes. Get-All-Prompts.sas
    and the link program hash such a character as a question mark. The parser does the same, and the
    request does not stop the parsing of the file."""
    frame = parse(monkeypatch, tmp_path, as_log(embedding_block(document="caf\udce9 au lait"), llm_block()))

    assert len(frame) == 2
    assert frame.loc[0, "input_hash"] == text_hash("caf? au lait")


# ---------------------------------------------------------------------------
# The results of a retrieval in a prompt
# ---------------------------------------------------------------------------

def test_prompt_with_the_results_of_a_retrieval_names_its_collection_and_the_hash_of_its_question(monkeypatch, tmp_path):
    question = "How long does a travel refund take?"
    user_prompt = ("Answer from the context only.\n\nContext:\n" + retrieval_envelope(collection="staff_handbook_v1", query=question)
                   + "\n\nQuestion: " + question)
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_prompt=user_prompt)))

    assert frame.loc[0, "rag_collection"] == "staff_handbook_v1"
    assert frame.loc[0, "rag_query_hash"] == text_hash(question)
    # The prompt itself is kept as it was sent
    assert frame.loc[0, "user_prompt"] == user_prompt


def test_question_of_a_retrieval_has_the_hash_of_the_call_that_embedded_it(monkeypatch, tmp_path):
    """A retrieval embeds the question in query mode and hands its results to the prompt.
    Neither request names the other: the hash of the question is what the two rows share."""
    question = "How long does a travel refund take?"
    frame = parse(monkeypatch, tmp_path, as_log(
        embedding_block(timestamp="2026-01-05 09:14:58", document=f"  How long does a\ttravel refund take?\n",
                        options="{Embedding_Mode:query}", project="rag"),
        llm_block(timestamp="2026-01-05 09:15:00", user_prompt="Context: " + retrieval_envelope(query=question)),
        embedding_block(timestamp="2026-01-05 09:15:30", document="Another question?", options="{Embedding_Mode:query}", project="rag"),
    ))

    assert list(frame["kind"]) == ["embedding", "llm", "embedding"]
    assert frame.loc[0, "input_hash"] == frame.loc[1, "rag_query_hash"] == "e422a7ae4d49d80be7ae1d8c9b32f06398796c0f498f0e5f3c0e342edf7a9032"
    assert frame.loc[2, "input_hash"] != frame.loc[1, "rag_query_hash"]
    # The question is in the prompt, so it is in the table there and nowhere else
    assert missing(frame.loc[0, "user_prompt"]) and question in frame.loc[1, "user_prompt"]


@pytest.mark.parametrize("question", [
    'What does "refund" mean in chapter {7}?',
    "Wie lange dauert eine Erstattung\nfür Österreich?",
    'A question that quotes JSON: {"collection": "not_this_one", "query": "nor this"}',
    "",
], ids=["quotes and braces", "a line break and umlauts", "JSON in the question", "an empty question"])
def test_question_is_decoded_as_json_not_cut_out_of_the_text(monkeypatch, tmp_path, question):
    envelope = retrieval_envelope(collection="policies_v2", query=question, hits=3)
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_prompt=f"Context:\n{envelope}\nAnswer in one sentence.")))

    assert frame.loc[0, "rag_collection"] == "policies_v2"
    assert frame.loc[0, "rag_query_hash"] == text_hash(question)


@pytest.mark.parametrize("user_prompt", [
    '{"note": "an object that is no retrieval"} ' + retrieval_envelope(collection="policies_v2", query="made-up question"),
    'The braces of a template {city} and {"half": ' + " then " + retrieval_envelope(collection="policies_v2", query="made-up question"),
    json.dumps({"context": json.loads(retrieval_envelope(collection="policies_v2", query="made-up question")), "language": "en"}),
    json.dumps([{"role": "tool", "content": json.loads(retrieval_envelope(collection="policies_v2", query="made-up question"))}]) + " [1] and [2]",
], ids=["behind another object", "behind text with braces", "inside another object", "inside a list"])
def test_results_of_a_retrieval_are_found_wherever_they_stand_in_the_prompt(monkeypatch, tmp_path, user_prompt):
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_prompt=user_prompt)))

    assert frame.loc[0, "rag_collection"] == "policies_v2"
    assert frame.loc[0, "rag_query_hash"] == text_hash("made-up question")


@pytest.mark.parametrize("user_prompt", [
    'Which "collection" does the "query" of a retrieval name?',
    'Explain this: {"collection": "policies_v2", "hits": []}',
    'Explain this: {"collection": 7, "query": null}',
    'Explain this: {"collection": "", "query": "made-up question"}',
    "Context: " + retrieval_envelope()[:-25],
    'Explain this: {collection: "policies_v2", query: "made-up question"}',
], ids=["the two words", "no question", "no texts", "no collection", "cut off", "not JSON"])
def test_prompt_without_the_results_of_a_retrieval_names_no_collection(monkeypatch, tmp_path, user_prompt):
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_prompt=user_prompt)))

    assert missing(frame.loc[0, "rag_collection"]) and missing(frame.loc[0, "rag_query_hash"])
    assert frame.loc[0, "user_prompt"] == user_prompt


def test_only_the_user_prompt_of_an_llm_is_searched_for_the_results_of_a_retrieval(monkeypatch, tmp_path):
    envelope = retrieval_envelope()
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(system_prompt="Context: " + envelope, user_prompt="made-up question"),
        decision_block(state="Context: " + envelope),
        embedding_block(document=envelope),
    ))

    assert all(missing(value) for value in frame["rag_collection"])
    assert all(missing(value) for value in frame["rag_query_hash"])


# ---------------------------------------------------------------------------
# The options
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sent, kept", [
    ("{temperature:0.2,top_p:1,max_tokens:40}", "{temperature:0.2,top_p:1,max_tokens:40}"),
    ("{}", "{}"),
    ("{Embedding_Mode:query}", "{Embedding_Mode:query}"),
    ("{temperature:0.7,max_tokens:60,API_KEY:REDACTED}", "{temperature:0.7,max_tokens:60,API_KEY:REDACTED}"),
    ("{model:gpt-4o-mini-2024-07-18,reasoning_effort:medium,max_completion_tokens:4000}", "{model:gpt-4o-mini-2024-07-18,reasoning_effort:medium,max_completion_tokens:4000}"),
    ("{endpoint:https://models.example.com/v1/chat/completions,top_k:5}", "{endpoint:https://models.example.com/v1/chat/completions,top_k:5}"),
    ('{"temperature":0.3,"top_k":5}', '{"temperature":0.3,"top_k":5}'),
], ids=["an LLM", "none", "an embedding model", "redacted by the collection", "a model name", "an address", "JSON"])
def test_options_are_kept_as_they_were_sent(monkeypatch, tmp_path, sent, kept):
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(options=sent)))

    assert frame.loc[0, "options"] == kept


@pytest.mark.parametrize("sent, kept, secrets", [
    ("{temperature:0.7,API_KEY:sk-made-up-0123456789,max_tokens:60}", "{temperature:0.7,API_KEY:REDACTED,max_tokens:60}", ["sk-made-up"]),
    ("{API_KEY:AIzaMadeUp_0123456789-abcdef}", "{API_KEY:REDACTED}", ["AIzaMadeUp"]),
    ("{api_key:made-up-one,client_secret:made-up-two,Password:made-up-three}", "{api_key:REDACTED,client_secret:REDACTED,Password:REDACTED}", ["made-up"]),
    ("{hf_token:made-up-four,max_tokens:12}", "{hf_token:REDACTED,max_tokens:12}", ["made-up-four"]),
    ("{Authorization:Bearer made-up-five}", "{Authorization:REDACTED}", ["made-up-five", "Bearer"]),
    ("{API_KEY:made,up,six,temperature:0.2}", "{API_KEY:REDACTED,temperature:0.2}", ["made", "six"]),
    ("{deployment:A1b2C3d4E5f6G7h8I9j0K1l2,temperature:0.2}", "{deployment:REDACTED,temperature:0.2}", ["A1b2C3d4"]),
    ("{header:Bearer made-up-seven}", "{header:REDACTED}", ["made-up-seven"]),
    ("{route:sk-made-up-eight}", "{route:REDACTED}", ["made-up-eight"]),
    ("API_KEY:made-up-nine,temperature:0.2", "API_KEY:REDACTED,temperature:0.2", ["made-up-nine"]),
    ('{"temperature": 0.3, "API_KEY": "sk-made-up-0123456789", "max_tokens": 25}', '{"temperature": 0.3, "API_KEY": "REDACTED", "max_tokens": 25}', ["sk-made-up"]),
    ('{"headers": {"x-api-key": "made-up-ten"}, "stop": ["A1b2C3d4E5f6G7h8I9j0K1l2"]}', '{"headers": {"x-api-key": "REDACTED"}, "stop": ["REDACTED"]}', ["made-up-ten", "A1b2C3d4"]),
], ids=["the provider key", "a key of another shape", "other names of a secret", "a token", "an authorization", "a key with commas",
        "a value in the shape of a key", "a bearer value", "a value with the prefix of a key", "no braces", "JSON", "JSON inside JSON"])
def test_parser_redacts_every_secret_in_the_options_itself(monkeypatch, tmp_path, sent, kept, secrets):
    """The collection script replaces API_KEY before the log is written. A log that was
    collected another way still brings no key into the table."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(options=sent), embedding_block(options=sent))))

    frame = run.uploads[0]["frame"]
    assert list(frame["options"]) == [kept, kept]
    table = frame.to_csv()
    for secret in secrets:
        assert secret not in table, secret


def test_an_option_named_like_a_token_is_kept_when_it_is_a_number(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(options="{max_tokens:64,max_completion_tokens:300,max_new_tokens:20,token:made-up}")))

    assert frame.loc[0, "options"] == "{max_tokens:64,max_completion_tokens:300,max_new_tokens:20,token:REDACTED}"


@pytest.mark.parametrize("options, max_tokens", [
    ("{temperature:0.1,max_length:128}", 128),
    ("{max_length:128,max_tokens:64}", 64),
    ("{max_tokens:64,max_length:128}", 64),
    ("{max_completion_tokens:4000,reasoning_effort:low}", 4000),
    ("{max_length:128,max_completion_tokens:4000}", 4000),
    ('{"max_length": 256}', 256),
    ("{temperature:0.1}", None),
], ids=["max_length", "max_tokens first", "max_tokens last", "max_completion_tokens", "both other names", "JSON", "no limit"])
def test_other_names_of_the_token_limit_fill_max_tokens(monkeypatch, tmp_path, options, max_tokens):
    """max_tokens is the column of the limit of new tokens. A request that names the limit
    max_length or max_completion_tokens used to leave it empty."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(options=options))))

    value = run.uploads[0]["frame"].loc[0, "max_tokens"]
    assert (None if missing(value) else value) == max_tokens
    assert run.uploads[0]["frame"].loc[0, "options"] == options


def test_options_as_json_fill_the_option_columns(monkeypatch, tmp_path):
    """The score code reads the options as JSON first and as name:value pairs otherwise."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(options='{"temperature": 0.3, "top_p": 0.9, "top_k": 5, "max_tokens": 25}'),
        llm_block(timestamp="2026-01-05 09:16:00", options='{"temperature":"0.4","max_tokens":"30"}'),
        llm_block(timestamp="2026-01-05 09:17:00", options='{ "temperature" : 0.5 , \'max_tokens\' : 35 }'))))

    frame = run.uploads[0]["frame"]
    assert list(frame["temperature"]) == [0.3, 0.4, 0.5]
    assert list(frame["max_tokens"]) == [25, 30, 35]
    assert (frame.loc[0, "top_p"], frame.loc[0, "top_k"]) == (0.9, 5)


# ---------------------------------------------------------------------------
# The request headers
# ---------------------------------------------------------------------------

def test_request_id_is_the_header_of_the_request(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(request_id="0123456789abcdef0123456789abcdef"),
        embedding_block(headers=("x-request-id: fedcba9876543210fedcba9876543210",), request_id="aaaa"),
        decision_block()))

    assert frame.loc[0, "request_id"] == "0123456789abcdef0123456789abcdef"
    # Header names are read in any spelling of upper and lower case
    assert frame.loc[1, "request_id"] == "fedcba9876543210fedcba9876543210"
    assert re.fullmatch(r"[0-9a-f]{32}", frame.loc[2, "request_id"])


@pytest.mark.parametrize("header, kind", [
    ("python-requests/2.34.2", "python-requests"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36", "browser"),
    ("SAS/URL", "SAS"),
    ("sas-viya-cli/1.2", "SAS"),
    ("curl/8.5.0", "other"),
    ("Apache-HttpClient/5.4 (Java/21)", "other"),
    ("", None),
    (None, None),
], ids=["python-requests", "a browser", "SAS", "sas in lower case", "curl", "a Java client", "empty", "no header"])
def test_user_agent_is_kept_as_the_kind_of_client(monkeypatch, tmp_path, header, kind):
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_agent=header)))

    assert (None if missing(frame.loc[0, "user_agent"]) else frame.loc[0, "user_agent"]) == kind


def test_first_header_stands_on_the_request_headers_line(monkeypatch, tmp_path):
    block = llm_block(user_agent=None)
    assert " - Request Headers: Host: " in block[1]
    block[1] = log_line("2026-01-05 09:15:00", "api", "Request Headers: User-Agent: Mozilla/5.0 (X11; Linux x86_64)")
    frame = parse(monkeypatch, tmp_path, as_log(block))

    assert frame.loc[0, "user_agent"] == "browser"


def test_no_other_header_and_nothing_of_the_two_reaches_the_table(monkeypatch, tmp_path):
    """The headers carry addresses, the page a browser came from and, in a log that was
    not redacted, cookies and tokens. Two of them are read, one as an id and one as a class."""
    headers = ("X-Original-Forwarded-For: 198.51.100.23",
               "Referer: https://viya.example.com/SASJobExecution/?_program=/made-up/folder/secret-page",
               "Origin: https://viya.example.com",
               "Cookie: sessionid=made-up-cookie-value",
               "Authorization: Bearer made-up-bearer-token",
               "X-Csrf-Token: made-up-csrf-token")
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(headers=headers, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
        decision_block(headers=headers), embedding_block(headers=headers))))

    table = run.uploads[0]["frame"].to_csv()
    for text in ("192.0.2.17", "198.51.100.23", "viya.example.com", "secret-page", "made-up-cookie-value", "made-up-bearer-token",
                 "made-up-csrf-token", "Mozilla", "AppleWebKit", "2.32.5", "application/json"):
        assert text not in table, text
    assert list(run.uploads[0]["frame"]["user_agent"]) == ["browser", "python-requests", "python-requests"]


# ---------------------------------------------------------------------------
# Requests that failed
# ---------------------------------------------------------------------------

def test_request_that_failed_has_its_error_and_no_status(monkeypatch, tmp_path):
    """The score code logs what the provider answered and raises. The container logs the
    traceback and writes no 'Response:' line."""
    frame = parse(monkeypatch, tmp_path, as_log(
        failed_block("2026-01-05 09:00:00", "demo_decider", decision_inputs(options="{}")),
        llm_block(timestamp="2026-01-05 09:01:00")))

    assert len(frame) == 2
    failed, answered = frame.iloc[0], frame.iloc[1]
    assert (failed["kind"], failed["failed"]) == ("decision", 1)
    assert missing(failed["status_code"])
    assert failed["error_text"] == PROVIDER_ERROR
    # The request itself is all there, the answer is not
    assert (failed["system_prompt"], failed["user_prompt"], failed["options"]) == (DECISION_QUESTIONS, DECISION_STATE, "{}")
    assert failed["transaction_id"] == made_up_id("2026-01-05 09:00:00", "demo_decider", decision_inputs(options="{}"))
    for column in ("prompt_length", "output_length", "runtime", "response", "answer", "answer_confidence"):
        assert missing(failed[column]), column
    # The request behind it is not touched by any of it
    assert (answered["status_code"], answered["failed"]) == (200, 0)
    assert missing(answered["error_text"])
    assert (answered["prompt_length"], answered["response"]) == (21, "The capital of Austria is Vienna.")


def test_exception_the_score_code_did_not_log_is_taken_from_the_traceback(monkeypatch, tmp_path):
    """The line the container writes for an exception names no cause. The last line of
    the traceback behind it does."""
    frame = parse(monkeypatch, tmp_path, as_log(
        failed_block("2026-01-05 09:00:00", "demo_decider", decision_inputs(), error=None, exception="KeyError: 'API_KEY'")))

    assert frame.loc[0, "failed"] == 1
    assert frame.loc[0, "error_text"] == "An exception occurred within the scoreModel function of the score code: KeyError: 'API_KEY'"


def test_error_text_is_cut_and_holds_no_key(monkeypatch, tmp_path):
    error = ("HTTP 401: {\"error\": {\"message\": \"Incorrect API key provided: sk-made-up-A1b2C3d4E5f6G7h8. The header was"
             " Authorization: Bearer made-up-token-0123456789 and the key of the project is A1b2C3d4E5f6G7h8I9j0K1l2.\"}} "
             + "<p>made-up page</p>\t" * 60)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(failed_block("2026-01-05 09:00:00", "demo_llm", llm_inputs(), error=error))))

    text = run.uploads[0]["frame"].loc[0, "error_text"]
    assert len(text) == run.namespace["ERROR_TEXT_LENGTH"] == 500
    assert text.startswith('HTTP 401: {"error": {"message": "Incorrect API key provided: REDACTED The header was Authorization: REDACTED and the key of the project is REDACTED."}} <p>made-up page</p> <p>')
    for secret in ("sk-made-up", "made-up-token", "A1b2C3d4"):
        assert secret not in text, secret


def test_first_error_of_a_request_is_the_one_that_is_kept(monkeypatch, tmp_path):
    before, after = split_at_scoring(llm_block())
    errors = [log_line("2026-01-05 09:15:00", "scoreModel", "HTTP 429: {\"error\": \"rate limit\"}", level="ERROR"),
              log_line("2026-01-05 09:15:01", "scoreModel", "HTTP 503: {\"error\": \"overloaded\"}", level="ERROR")]
    frame = parse(monkeypatch, tmp_path, as_log(before, errors, after))

    # An error line makes the request a failed one even where a response was written
    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (200, 1)
    assert frame.loc[0, "error_text"] == 'HTTP 429: {"error": "rate limit"}'
    assert frame.loc[0, "response"] == "The capital of Austria is Vienna."


@pytest.mark.parametrize("status, failed", [(200, 0), (204, 0), (399, 0), (400, 1), (500, 1)])
def test_status_of_the_response_decides_whether_a_request_without_an_error_failed(monkeypatch, tmp_path, status, failed):
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(status=status)))

    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (status, failed)
    assert missing(frame.loc[0, "error_text"])


def test_numbers_of_the_added_columns_are_numbers_in_the_upload(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        decision_block(), failed_block("2026-01-05 09:30:00", "demo_decider", decision_inputs()))))

    frame = run.uploads[0]["frame"]
    assert list(frame["failed"]) == [0, 1]
    assert frame.loc[0, "status_code"] == 200 and missing(frame.loc[1, "status_code"])
    assert frame.loc[0, "answer_confidence"] == 0.9 and missing(frame.loc[1, "answer_confidence"])


# ---------------------------------------------------------------------------
# Redacted logs
# ---------------------------------------------------------------------------

def test_redacted_block_still_parses(monkeypatch, tmp_path):
    """The collection script replaces the provider key in the options and the values of
    the Cookie, Authorization and X-CSRF-TOKEN headers before it writes the file."""
    redacted_headers = ("Cookie: REDACTED", "Authorization: REDACTED", "X-Csrf-Token: REDACTED")
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(options="{temperature:0.7,max_tokens:60,API_KEY:REDACTED}", headers=redacted_headers),
        decision_block(options="{API_KEY:REDACTED}", headers=redacted_headers),
        embedding_block(options="{Embedding_Mode:query,API_KEY:REDACTED}", headers=redacted_headers),
    ))

    assert list(frame["model"]) == ["demo_llm", "demo_decider", "demo_embedder"]
    llm, decision, embedding = (frame.iloc[i] for i in range(3))
    assert llm["system_prompt"] == "You are a concise assistant."
    assert llm["user_prompt"] == "What is the capital of Austria?"
    assert (llm["temperature"], llm["max_tokens"]) == ("0.7", "60")
    assert decision["system_prompt"] == DECISION_QUESTIONS
    assert decision["user_prompt"] == DECISION_STATE
    assert embedding["prompt_length"] == 17
    assert list(frame["options"]) == ["{temperature:0.7,max_tokens:60,API_KEY:REDACTED}", "{API_KEY:REDACTED}", "{Embedding_Mode:query,API_KEY:REDACTED}"]
    assert embedding["embedding_mode"] == "query"


def test_key_of_an_unredacted_log_reaches_no_column(monkeypatch, tmp_path):
    made_up_key = "sk-made-up-0123456789"
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(options=f"{{temperature:0.7,API_KEY:{made_up_key}}}", headers=("Cookie: session=made-up-cookie",)),
        embedding_block(options=f"{{API_KEY:{made_up_key}}}"),
    ))

    assert frame.loc[0, "temperature"] == "0.7"
    table = frame.to_csv()
    assert made_up_key not in table
    assert "made-up-cookie" not in table
    assert list(frame["options"]) == ["{temperature:0.7,API_KEY:REDACTED}", "{API_KEY:REDACTED}"]


# ---------------------------------------------------------------------------
# Blocks of several kinds and containers in one file
# ---------------------------------------------------------------------------

def test_interleaved_blocks_of_all_kinds_keep_their_own_values(monkeypatch, tmp_path):
    """The collected file holds one container after the other, each with its start-up
    lines, its scoring requests and the requests that score nothing in between."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        startup_lines("2026-01-05 08:00:00", "demo_llm"),
        llm_block(timestamp="2026-01-05 09:00:00", prompt_length=10, output_length=1, run_time=1.5, response="first"),
        get_block("2026-01-05 09:00:30"),
        llm_block(timestamp="2026-01-05 09:01:00", model="demo_llm", options="{}", prompt_length=20, output_length=2, run_time=2.5, response="second"),
        startup_lines("2026-01-05 08:00:05", "demo_decider"),
        decision_block(timestamp="2026-01-05 09:02:00"),
        get_block("2026-01-05 09:02:30", "None"),
        startup_lines("2026-01-05 08:00:10", "demo_embedder"),
        embedding_block(timestamp="2026-01-05 09:03:00", tokens=5, run_time=0.01),
        embedding_block(timestamp="2026-01-05 09:03:01", tokens=6, run_time=0.02),
        llm_block(timestamp="2026-01-05 09:04:00", model="other_llm", prompt_length=30, output_length=3, run_time=3.5, response="third"),
    )))
    frame = run.namespace["parse_log_file"](run.sas.macro_variables["_log_path"])

    assert list(frame["timestamp"]) == [
        "2026-01-05 09:00:00", "2026-01-05 09:01:00", "2026-01-05 09:02:00",
        "2026-01-05 09:03:00", "2026-01-05 09:03:01", "2026-01-05 09:04:00",
    ]
    assert list(frame["model"]) == ["demo_llm", "demo_llm", "demo_decider", "demo_embedder", "demo_embedder", "other_llm"]
    assert list(frame["prompt_length"]) == [10, 20, 120, 5, 6, 30]
    assert list(frame["runtime"]) == [1.5, 2.5, 0.4, 0.01, 0.02, 3.5]
    assert [None if missing(value) else value for value in frame["output_length"]] == [1, 2, 14, None, None, 3]
    assert [None if missing(value) else value for value in frame["response"]] == ["first", "second", DECISION_ANSWERS, None, None, "third"]
    # The second LLM call set no option, so it must not show the options of the first
    assert frame.loc[0, "temperature"] == "0.2"
    assert missing(frame.loc[1, "temperature"])
    # The embedding calls follow a decision call and take none of its text
    assert missing(frame.loc[3, "system_prompt"]) and missing(frame.loc[3, "user_prompt"])

    assert list(frame["kind"]) == ["llm", "llm", "decision", "embedding", "embedding", "llm"]
    assert list(frame["status_code"]) == [200] * 6 and list(frame["failed"]) == [0] * 6
    assert frame["transaction_id"].nunique() == 6 and frame["request_id"].nunique() == 6
    assert [None if missing(value) else value for value in frame["answer"]] == [None, None, "billing", None, None, None]
    # The requests that score nothing are requests of a client too: its headers are not the headers of a scoring request
    assert list(frame["user_agent"]) == ["python-requests"] * 6
    assert run.sas.messages == []


def test_score_lines_between_two_blocks_belong_to_no_request(monkeypatch, tmp_path):
    """A container's part of the file can begin in the middle of a request, because the
    collection cuts each log at a point in time. Its leftover score lines follow the
    finished request of the container before."""
    leftover = [
        log_line("2026-01-05 09:05:00", "scoreModel", "tokens: 999"),
        log_line("2026-01-05 09:05:00", "scoreModel", "run_time: 99.5"),
        log_line("2026-01-05 09:05:00", "scoreModel", "response: an answer to a request that is not in this file"),
        log_line("2026-01-05 09:05:00", "scoreModel", "HTTP 500: an error of a request that is not in this file", level="ERROR"),
        log_line("2026-01-05 09:05:00", "api", "Response: Status Code 500"),
    ]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(leftover, llm_block(), leftover, embedding_block(), leftover)))
    frame = run.namespace["parse_log_file"](run.sas.macro_variables["_log_path"])

    assert len(frame) == 2
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "runtime"], frame.loc[0, "response"]) == (21, 1.25, "The capital of Austria is Vienna.")
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "runtime"]) == (17, 0.08)
    assert missing(frame.loc[1, "response"])
    assert list(frame["status_code"]) == [200, 200] and list(frame["failed"]) == [0, 0]
    assert all(missing(value) for value in frame["error_text"])
    assert run.sas.messages == []


def test_rest_of_a_request_at_the_start_of_the_file_belongs_to_no_request(monkeypatch, tmp_path):
    """The same cut with the lines of the transaction in it: they name a transaction that
    no request line of the file started."""
    cut_off = split_at_scoring(llm_block(timestamp="2026-01-05 08:59:00", user_prompt="a request whose first lines are not in this file",
                                         response="an answer to it", prompt_length=999, status=500))[1]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(cut_off, llm_block(), embedding_block())))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 2
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "response"], frame.loc[0, "status_code"]) == (21, "The capital of Austria is Vienna.", 200)
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "status_code"]) == (17, 200)
    assert "an answer to it" not in frame.to_csv()
    assert run.sas.messages == []


def test_request_that_is_still_running_takes_no_lines_of_the_next_container(monkeypatch, tmp_path):
    """The last request of a container can be cut off before its response was logged.
    The next container's part of the file starts earlier in time, which is what ends it:
    the leftover score lines there are not its numbers and not its answer."""
    running = split_at_scoring(llm_block(timestamp="2026-01-05 09:59:59", user_prompt="still running"))[0]
    leftover = [
        log_line("2026-01-05 09:00:00", "scoreModel", "prompt_length: 999"),
        log_line("2026-01-05 09:00:00", "scoreModel", "output_length: 99"),
        log_line("2026-01-05 09:00:00", "scoreModel", "run_time: 99.5"),
        log_line("2026-01-05 09:00:00", "scoreModel", "response: the answer of another container"),
        log_line("2026-01-05 09:00:00", "api", "Response: Status Code 200"),
    ]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(running, leftover, embedding_block(timestamp="2026-01-05 09:30:00"))))
    del run.sas.messages[:]
    frame = run.namespace["parse_log_file"](run.sas.macro_variables["_log_path"])

    assert len(frame) == 2
    assert frame.loc[0, "user_prompt"] == "still running"
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert (frame.loc[1, "model"], frame.loc[1, "prompt_length"]) == ("demo_embedder", 17)
    # The log holds no response for it and no error: the lines of its container end while it is running,
    # which does not say that it failed
    assert missing(frame.loc[0, "status_code"]) and missing(frame.loc[0, "failed"])
    assert missing(frame.loc[0, "error_text"])
    assert frame.loc[0, "kind"] == "llm" and not missing(frame.loc[0, "transaction_id"])
    assert frame.loc[1, "failed"] == 0
    assert _end_note(run) == 1


def test_request_to_another_model_is_the_next_container(monkeypatch, tmp_path):
    """A container serves one model. Where a request to another model follows, the part of
    the next container has begun - also when a client gave both requests the same
    transaction id and the second one is the later one."""
    shared = "99999999-8888-7777-6666-555555555555"
    running = split_at_scoring(llm_block(timestamp="2026-01-05 09:00:00", model="demo_llm", user_prompt="still running", transaction=shared))[0]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        running, llm_block(timestamp="2026-01-05 09:00:05", model="other_llm", response="the answer of the other model", transaction=shared))))

    frame = run.uploads[0]["frame"]
    assert list(frame["model"]) == ["demo_llm", "other_llm"]
    assert missing(frame.loc[0, "response"]) and frame.loc[0, "failed"] == 1
    assert (frame.loc[1, "response"], frame.loc[1, "status_code"], frame.loc[1, "failed"]) == ("the answer of the other model", 200, 0)
    assert run.sas.messages == []


def test_log_lines_quoted_in_a_prompt_or_a_response_are_text(monkeypatch, tmp_path):
    """Only a line that starts as a log line counts. The same words inside a prompt (one
    long 'Request Data:' line) or at the start of a response line are part of the text."""
    user_prompt = ("Explain these lines: 2026-01-05 09:00:00 - api - INFO - Request: POST /other_model"
                   " and 2026-01-05 09:00:00 - scoreModel - INFO - run_time: 77 and prompt_length: 88")
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(
        user_prompt=user_prompt,
        response="Request: POST /other_model starts a request.\nrun_time: 77 is the time it took.\nResponse: Status Code 500 ends it.")))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["model"] == "demo_llm"
    assert row["user_prompt"] == user_prompt
    assert (row["prompt_length"], row["runtime"]) == (21, 1.25)
    assert row["response"] == ("Request: POST /other_model starts a request. run_time: 77 is the time it took."
                               " Response: Status Code 500 ends it.")
    assert (row["status_code"], row["failed"]) == (200, 0)


# ---------------------------------------------------------------------------
# Requests of one container that overlap
#
# The lines of the score code and the 'Response:' line name no request. The lines
# of the transaction and the 'Response Data:' line do, and a request whose
# transaction a later line still names was running when the next one started.
# ---------------------------------------------------------------------------

OVERLAP_NOTE = re.compile(
    r"(\d+) request\(s\) were running at the same time as another request - of their container, or of another container whose "
    r"lines are mixed with theirs in the file\. The first lines of a request, the lines of the score code and the 'Response:' line "
    r"name no request, so a line that the log does not decide was given to none of them: (\d+) request\(s\) can have missing "
    r"values, and failed is missing for (\d+) request\(s\)\.")


def _overlap_counts(messages):
    """What the NOTE about requests that ran at the same time counts: those requests, the ones of them
    that can have missing values, and the ones whose failed is missing. None when there is no such NOTE."""
    found = [OVERLAP_NOTE.fullmatch(message) for kind, message in messages if kind == "NOTE" and "at the same time" in message]
    assert all(found), messages
    assert len(found) <= 1
    return tuple(int(number) for number in found[0].groups()) if found else None


def _overlap_note(run):
    assert [kind for kind, _ in run.sas.messages] == ["NOTE"], run.sas.messages
    return _overlap_counts(run.sas.messages)


END_NOTE = re.compile(
    r"(\d+) request\(s\) were running where the lines of their container end - at the end of the file, or at the end of a part "
    r"of it, where the time of the lines goes back\. The log holds neither a response nor an error for them, and failed is "
    r"missing for them\.")


def _end_counts(messages):
    """What the NOTE about requests the lines of their container end on counts. None when there is no such NOTE."""
    found = [END_NOTE.fullmatch(message) for kind, message in messages if kind == "NOTE" and "lines of their container end" in message]
    assert all(found), messages
    assert len(found) <= 1
    return int(found[0].group(1)) if found else None


def _end_note(run):
    assert [kind for kind, _ in run.sas.messages] == ["NOTE"], run.sas.messages
    return _end_counts(run.sas.messages)


def test_requests_that_overlap_in_one_container_stay_one_row_each(monkeypatch, tmp_path):
    """The second request starts before the first one was answered. What the score code
    logged while both were running is the line of neither. What it logged after the
    first one was answered is the line of the second."""
    first = llm_block(timestamp="2026-01-05 09:00:00", finished="2026-01-05 09:00:03", user_prompt="question A", response="answer A", prompt_length=11)
    second = llm_block(timestamp="2026-01-05 09:00:01", finished="2026-01-05 09:00:04", user_prompt="question B", response="answer B", prompt_length=22)
    (before_first, after_first), (before_second, after_second) = split_at_scoring(first), split_at_scoring(second)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(before_first, before_second, after_first, after_second)))

    frame = run.uploads[0]["frame"]
    assert list(frame["timestamp"].diff().dropna()) == [1]
    assert list(frame["user_prompt"]) == ["question A", "question B"]
    responses = [value for value in frame["response"] if not missing(value)]
    assert all(not ("answer A" in value and "answer B" in value) for value in responses)

    # Each request has what its own lines say: the inputs, the transaction, and the response by its 'Response Data:' line
    assert list(frame["transaction_id"]) == [made_up_id("2026-01-05 09:00:00", "demo_llm", llm_inputs(user_prompt="question A")),
                                              made_up_id("2026-01-05 09:00:01", "demo_llm", llm_inputs(user_prompt="question B"))]
    assert list(frame["status_code"]) == [200, 200] and list(frame["failed"]) == [0, 0]
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "output_length"], frame.loc[1, "runtime"], frame.loc[1, "response"]) == (22, 9, 1.25, "answer B")
    # Both ran at the same time, both can lack a value, and the log says of both whether they failed
    assert _overlap_note(run) == (2, 2, 0)


def test_score_lines_of_two_running_requests_are_the_lines_of_neither(monkeypatch, tmp_path):
    first = llm_block(timestamp="2026-01-05 09:00:00", finished="2026-01-05 09:00:03", user_prompt="question A", response="answer A", status=200)
    second = decision_block(timestamp="2026-01-05 09:00:01", finished="2026-01-05 09:00:03", model="demo_llm", status=500)
    (before_first, after_first), (before_second, after_second) = split_at_scoring(first), split_at_scoring(second)
    # The score lines of both, then the last four lines of each: scored, elapsed time, response, response data
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        before_first, before_second, after_first[:-4], after_second[:-4], after_first[-4:], after_second[-4:])))

    frame = run.uploads[0]["frame"]
    assert list(frame["kind"]) == ["llm", "decision"]
    assert list(frame["user_prompt"]) == ["question A", DECISION_STATE]
    for column in ("prompt_length", "output_length", "runtime", "response", "answer", "answer_confidence", "error_text"):
        assert all(missing(value) for value in frame[column]), column
    # The 'Response:' line stands in front of the 'Response Data:' line that names its transaction
    assert list(frame["status_code"]) == [200, 500] and list(frame["failed"]) == [0, 1]
    assert _overlap_note(run) == (2, 2, 0)


def test_response_lines_that_are_not_followed_by_their_response_data_are_the_lines_of_neither(monkeypatch, tmp_path):
    first = llm_block(timestamp="2026-01-05 09:00:00", finished="2026-01-05 09:00:03", user_prompt="question A", status=200)
    second = llm_block(timestamp="2026-01-05 09:00:01", finished="2026-01-05 09:00:03", user_prompt="question B", status=500)
    (before_first, after_first), (before_second, after_second) = split_at_scoring(first), split_at_scoring(second)
    last_lines = [line for pair in zip(after_first[-4:], after_second[-4:]) for line in pair]
    assert [" - Response: " in line for line in last_lines] == [False, False, False, False, True, True, False, False]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(before_first, before_second, last_lines)))

    frame = run.uploads[0]["frame"]
    # The second 'Response:' line stands in front of the 'Response Data:' line of the first request: its 500 is not taken for it
    assert all(missing(value) for value in frame["status_code"])
    assert all(missing(value) for value in frame["failed"])
    assert not any(missing(value) for value in frame["transaction_id"])
    assert _overlap_note(run) == (2, 2, 2)

    # A request behind them is read like any other
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        before_first, before_second, last_lines, llm_block(timestamp="2026-01-05 09:00:09", user_prompt="question C", status=201))))
    assert list(run.uploads[0]["frame"]["status_code"])[2] == 201
    assert _overlap_note(run) == (2, 2, 2)


def test_error_of_a_request_that_overlaps_is_given_to_it_by_its_transaction(monkeypatch, tmp_path):
    failed = failed_block("2026-01-05 09:00:00", "demo_llm", llm_inputs(user_prompt="question A"), finished="2026-01-05 09:00:02")
    answered = llm_block(timestamp="2026-01-05 09:00:01", finished="2026-01-05 09:00:03", user_prompt="question B", response="answer B")
    (before_failed, after_failed), (before_answered, after_answered) = split_at_scoring(failed), split_at_scoring(answered)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(before_failed, before_answered, after_failed, after_answered)))

    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["question A", "question B"]
    # The error line of the score code names no request. The two lines of the container name the transaction
    assert frame.loc[0, "failed"] == 1 and missing(frame.loc[0, "status_code"])
    assert frame.loc[0, "error_text"] == "An exception occurred within the scoreModel function of the score code: RuntimeError: Request failed: 401"
    # With that the first request has ended, and the lines behind it are the lines of the second
    assert (frame.loc[1, "status_code"], frame.loc[1, "failed"], frame.loc[1, "response"], frame.loc[1, "prompt_length"]) == (200, 0, "answer B", 21)
    assert missing(frame.loc[1, "error_text"])
    assert _overlap_note(run) == (2, 2, 0)


def test_requests_whose_first_lines_overlap_keep_their_request_line_only(monkeypatch, tmp_path):
    """The headers and the 'Request Data:' line name nothing either. A second line of the
    same kind before any transaction line says that two requests wrote their first lines
    at the same time, and no line says which is whose."""
    first = llm_block(timestamp="2026-01-05 09:00:00", finished="2026-01-05 09:00:02", user_prompt="question A", response="answer A")
    second = llm_block(timestamp="2026-01-05 09:00:00", finished="2026-01-05 09:00:03", system_prompt="Another system prompt.", user_prompt="question B", response="answer B")
    (before_first, after_first), (before_second, after_second) = split_at_scoring(first), split_at_scoring(second)
    data = next(index for index, line in enumerate(before_first) if " - Request Data: " in line)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        before_first[:1], before_second[:1], before_first[1:data], before_second[1:data], before_first[data:], before_second[data:],
        after_first, after_second,
        llm_block(timestamp="2026-01-05 09:00:09", user_prompt="question C", response="answer C"))))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 3
    assert list(frame["model"]) == ["demo_llm"] * 3
    for column in ALL_COLUMNS:
        if column not in ("timestamp", "model"):
            assert all(missing(value) for value in frame.loc[:1, column]), column
    table = frame.loc[:1].to_csv(header=False)
    assert "question" not in table and "answer" not in table and "prompt" not in table
    # The request behind them is read like any other
    assert (frame.loc[2, "user_prompt"], frame.loc[2, "response"], frame.loc[2, "status_code"], frame.loc[2, "failed"]) == ("question C", "answer C", 200, 0)
    assert _overlap_note(run) == (2, 2, 2)

    # The same where the file ends before the second transaction was logged: the one transaction that is there is not taken for either
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        before_first[:1], before_second[:1], before_first[1:data], before_second[1:data], before_first[data:data + 1], before_second[data:])))
    frame = run.uploads[0]["frame"]
    assert len(frame) == 2
    assert [column for column in ALL_COLUMNS if not frame[column].isna().all()] == ["timestamp", "model"]
    assert _overlap_note(run) == (2, 2, 2)


def test_request_that_is_cut_off_in_its_first_lines_ends_at_the_next_request(monkeypatch, tmp_path):
    """Its transaction is not known, so no later line can name it: the next request line ends it."""
    block = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="cut off behind its inputs")
    data = next(index for index, line in enumerate(block) if " - Request Data: " in line)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(block[:data + 1], llm_block(timestamp="2026-01-05 09:00:30"))))

    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["cut off behind its inputs", "What is the capital of Austria?"]
    assert frame.loc[0, "kind"] == "llm" and frame.loc[0, "failed"] == 1
    assert missing(frame.loc[0, "transaction_id"]) and missing(frame.loc[0, "status_code"]) and missing(frame.loc[0, "response"])
    assert (frame.loc[1, "response"], frame.loc[1, "status_code"], frame.loc[1, "failed"]) == ("The capital of Austria is Vienna.", 200, 0)
    assert run.sas.messages == []


def test_request_that_scores_nothing_does_not_hand_its_headers_to_a_scoring_request(monkeypatch, tmp_path):
    block = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="cut off behind its request line")
    frame = parse(monkeypatch, tmp_path, as_log(block[:1], get_block("2026-01-05 09:00:10"), llm_block(timestamp="2026-01-05 09:00:30")))

    assert len(frame) == 2
    # The headers behind the GET are the headers of the GET, whose client was curl
    assert missing(frame.loc[0, "user_agent"]) and missing(frame.loc[0, "request_id"])
    assert frame.loc[1, "user_agent"] == "python-requests"


# ---------------------------------------------------------------------------
# Lines that are not what the parser expects
# ---------------------------------------------------------------------------

def test_request_without_a_response_line_ends_at_the_next_request(monkeypatch, tmp_path):
    failed = split_at_scoring(llm_block(timestamp="2026-01-05 09:00:00", user_prompt="a call that failed"))[0] + [
        log_line("2026-01-05 09:00:02", "scoreModel", "HTTP 429: {\"error\": \"rate limit\"}", level="ERROR"),
    ]
    frame = parse(monkeypatch, tmp_path, as_log(failed, llm_block(timestamp="2026-01-05 09:01:00")))

    assert len(frame) == 2
    assert frame.loc[0, "user_prompt"] == "a call that failed"
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "response"]) == (21, "The capital of Austria is Vienna.")
    assert (frame.loc[0, "failed"], frame.loc[0, "error_text"]) == (1, 'HTTP 429: {"error": "rate limit"}')
    assert (frame.loc[1, "failed"], frame.loc[1, "status_code"]) == (0, 200)


def test_block_without_the_lines_of_a_transaction_is_read_by_its_order(monkeypatch, tmp_path):
    """A log whose lines name no transaction leaves the order of the lines to go by, which
    is all the parser went by before."""
    def without_transaction(block):
        return [line for line in block if " - transaction_" not in line]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        without_transaction(llm_block()), without_transaction(decision_block())[:-1], without_transaction(embedding_block()))))

    frame = run.uploads[0]["frame"]
    assert list(frame["kind"]) == ["llm", "decision", "embedding"]
    assert list(frame["prompt_length"]) == [21, 120, 17]
    assert list(frame["status_code"]) == [200, 200, 200] and list(frame["failed"]) == [0, 0, 0]
    # The 'Response Data:' line is then the only one that names the transaction; the second block was given none
    assert not missing(frame.loc[0, "transaction_id"]) and missing(frame.loc[1, "transaction_id"])
    assert run.sas.messages == []


@pytest.mark.parametrize("prefix", [
    "2026-01-05T09:15:00.123456789Z ",
    "[pod/demo-llm-5d8f7c9b6-abcde/demo-llm] ",
], ids=["kubectl logs --timestamps", "kubectl logs --prefix"])
def test_requests_behind_a_line_prefix_are_reported_with_a_warning(monkeypatch, tmp_path, prefix):
    """A log pipeline that puts something in front of every line leaves nothing the
    parser can read. That is not a log without requests, and the SAS log has to say so."""
    block = llm_block() + decision_block()
    run = run_parser(monkeypatch, _write(tmp_path, as_log([prefix + line for line in block])))

    assert run.row_count == "0"
    assert run.uploads == []
    assert [kind for kind, _ in run.sas.messages] == ["WARNING"]
    warning = run.sas.messages[0][1]
    assert "'Request: POST' was found in 2 line(s)" in warning
    assert "<timestamp> - <logger> - <level> - " in warning


def test_no_warning_while_requests_are_read(monkeypatch, tmp_path):
    """The words can stand in a line that is no log line - a response that quotes them."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(response="A request starts like this:\nRequest: POST /other_model"))))

    assert run.row_count == "1"
    assert run.sas.messages == []


def test_byte_that_is_not_utf8_does_not_cost_the_file(monkeypatch, tmp_path):
    text = as_log(llm_block(response="Ein Café in Wien."), embedding_block())
    # The same word as a Latin-1 writer leaves it behind: one byte that is no UTF-8
    damaged = text.encode("utf-8").replace("Café".encode("utf-8"), b"Caf\xe9")
    assert damaged != text.encode("utf-8")
    path = tmp_path / "llms.log"
    path.write_bytes(damaged)

    run = run_parser(monkeypatch, path)

    assert run.row_count == "2"
    frame = run.uploads[0]["frame"]
    assert frame.loc[0, "response"] == "Ein Caf� in Wien."
    assert (frame.loc[0, "prompt_length"], frame.loc[1, "prompt_length"]) == (21, 17)


def test_unreadable_request_data_keeps_the_row_and_its_numbers(monkeypatch, tmp_path):
    block = llm_block()
    data_line = next(index for index, line in enumerate(block) if " - Request Data: " in line)
    block[data_line] = block[data_line][:-25]   # the line was cut off
    frame = parse(monkeypatch, tmp_path, as_log(block))

    assert len(frame) == 1
    assert missing(frame.loc[0, "system_prompt"]) and missing(frame.loc[0, "user_prompt"])
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "output_length"], frame.loc[0, "runtime"]) == (21, 9, 1.25)
    # What the inputs would have said is missing, what the other lines say is there
    assert missing(frame.loc[0, "kind"]) and missing(frame.loc[0, "options"])
    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"], frame.loc[0, "user_agent"]) == (200, 0, "python-requests")


def test_score_value_that_is_no_number_is_missing(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(prompt_length="None", output_length="None", run_time="2e-05")))

    assert missing(frame.loc[0, "prompt_length"]) and missing(frame.loc[0, "output_length"])
    assert frame.loc[0, "runtime"] == 2e-05


def test_inputs_that_are_no_text_do_not_stop_the_parser(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(
        request_block("2026-01-05 09:00:00", "demo_llm",
                      [("systemPrompt", None), ("userPrompt", 42), ("options", None)],
                      [("prompt_length", 3), ("output_length", 1), ("run_time", 0.5), ("response", "ok")]),
        request_block("2026-01-05 09:00:01", "demo_embedder",
                      [("document", 42), ("options", 7), ("project", ["made-up", "list"])],
                      [("tokens", 1), ("run_time", 0.5)]),
        request_block("2026-01-05 09:00:02", "demo_llm",
                      [(["a", "list"], "as a name"), ("userPrompt", {"collection": "x", "query": "y"}), ("options", {"max_length": 5})],
                      [("run_time", 0.5)])))

    assert missing(frame.loc[0, "system_prompt"])
    assert frame.loc[0, "user_prompt"] == "42"
    assert frame.loc[0, "response"] == "ok"
    assert frame.loc[0, "kind"] == "llm" and missing(frame.loc[0, "options"])
    assert (frame.loc[1, "kind"], frame.loc[1, "input_hash"], frame.loc[1, "options"]) == ("embedding", text_hash("42"), "7")
    assert frame.loc[2, "kind"] == "llm" and frame.loc[2, "runtime"] == 0.5


def test_text_columns_stay_text_after_the_parser_prepared_the_upload(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(user_prompt="Context: " + retrieval_envelope()), decision_block(), embedding_block(),
        failed_block("2026-01-05 09:30:00", "demo_llm", llm_inputs()))))

    frame = run.uploads[0]["frame"]
    for column in ALL_COLUMNS:
        if column in TEXT_COLUMNS:
            assert all(missing(value) or isinstance(value, str) for value in frame[column]), column
        else:
            assert pd.api.types.is_numeric_dtype(frame[column]), column
    # Every column the parser fills holds a value in at least one of these four rows
    assert [column for column in ALL_COLUMNS if frame[column].isna().all()] == ["top_k"] + LINK_COLUMNS


# ---------------------------------------------------------------------------
# What the log decides and what it does not
#
# Only the lines of a transaction and the 'Response Data:' line name their request.
# The tests below mix the lines of requests whose every value says which request it
# is of, and hold three things: no row has a value of another request, failed is
# what the request did or missing, and where a row lacks a value the NOTE says so.
# ---------------------------------------------------------------------------

def _numbered(number, failed=False, model="demo_llm", **more):
    """A request whose every value carries its number."""
    inputs = llm_inputs(f"SYS{number} system prompt", f"USR{number} user", "{temperature:0.%d,max_tokens:%d}" % (number, 100 + number))
    transaction = f"0000000{number}-aaaa-bbbb-cccc-00000000000{number}"
    if failed:
        return failed_block("2026-01-05 09:15:00", model, inputs, error=f"HTTP 40{number}: err{number}", transaction=transaction,
                            request_id=f"req{number}", **more)
    return request_block("2026-01-05 09:15:00", model, inputs,
                         [("prompt_length", 10 + number), ("output_length", 20 + number), ("run_time", number + 0.5),
                          ("response", f"RESP{number} answer")],
                         transaction=transaction, request_id=f"req{number}", status=200 + number, **more)


def _numbers_in(row):
    """The requests the values of a row are of, as {column: number of the request}."""
    found = {}
    for column, prefix in (("system_prompt", "SYS"), ("user_prompt", "USR"), ("response", "RESP"), ("request_id", "req")):
        if isinstance(row[column], str):
            found[column] = int(row[column][len(prefix)]) if row[column].startswith(prefix) else -1
    if isinstance(row["transaction_id"], str):
        found["transaction_id"] = int(row["transaction_id"][7])
    for column, base in (("prompt_length", 10), ("output_length", 20), ("status_code", 200), ("max_tokens", 100)):
        if not missing(row[column]):
            found[column] = int(float(row[column])) - base
    if not missing(row["runtime"]):
        found["runtime"] = int(row["runtime"])
    if not missing(row["temperature"]):
        found["temperature"] = round(float(row["temperature"]) * 10)
    if isinstance(row["error_text"], str) and row["error_text"].startswith("HTTP 40"):
        found["error_text"] = int(row["error_text"][7])
    return found


ANSWERED_HAS = {"system_prompt", "user_prompt", "response", "request_id", "transaction_id", "prompt_length", "output_length",
                "status_code", "max_tokens", "runtime", "temperature"}
FAILED_HAS = {"system_prompt", "user_prompt", "request_id", "transaction_id", "max_tokens", "temperature", "error_text"}


def _hold_what_the_log_decides(frame, messages, requests, failed):
    """requests: the number of the request of each row; failed: {number: whether it failed}.
    Returns the number of rows that lack a value their request has in the log."""
    assert len(frame) == len(requests)
    lacking = 0
    for number, (_, row) in zip(requests, frame.iterrows()):
        found = _numbers_in(row)
        assert set(found.values()) <= {number}, (number, found)
        assert missing(row["failed"]) or row["failed"] == int(failed[number]), (number, row["failed"])
        if missing(row["failed"]) or not (FAILED_HAS if failed[number] else ANSWERED_HAS) <= set(found):
            lacking += 1
    counts = _overlap_counts(messages)
    if lacking:
        assert counts is not None, "values are missing and no NOTE says so"
        assert counts[1] >= lacking or counts[0] >= lacking
        assert counts[2] == sum(1 for value in frame["failed"] if missing(value))
    assert [kind for kind, _ in messages if kind != "NOTE"] == []
    return lacking


def test_first_lines_of_a_second_request_in_front_of_the_first_transaction_leave_both_open(monkeypatch, tmp_path):
    """Two requests arrive, and the container writes the first five lines of both before it
    starts the first transaction. No line says which request that transaction is of: the
    rows keep what their first lines say and nothing of the transactions."""
    first, second = log_units(_numbered(1)), log_units(_numbered(2))
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        lines_of(first[:5]), lines_of(second[:5]), lines_of(first[5:]), lines_of(second[5:]))))

    frame = run.uploads[0]["frame"]
    assert list(frame["system_prompt"]) == ["SYS1 system prompt", "SYS2 system prompt"]
    assert list(frame["user_prompt"]) == ["USR1 user", "USR2 user"]
    assert list(frame["request_id"]) == ["req1", "req2"]
    assert list(frame["temperature"]) == [0.1, 0.2] and list(frame["kind"]) == ["llm", "llm"]
    for column in ("transaction_id", "prompt_length", "output_length", "runtime", "response", "status_code", "failed", "error_text"):
        assert all(missing(value) for value in frame[column]), column
    assert _overlap_note(run) == (2, 2, 2)


@pytest.mark.parametrize("first_failed, second_failed", [(False, True), (True, False), (True, True)],
                         ids=["the second failed", "the first failed", "both failed"])
def test_neither_of_two_requests_that_wait_for_a_transaction_is_called_failed(monkeypatch, tmp_path, first_failed, second_failed):
    first, second = log_units(_numbered(1, first_failed)), log_units(_numbered(2, second_failed))
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        lines_of(first[:5]), lines_of(second[:5]), lines_of(first[5:]), lines_of(second[5:]))))

    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["USR1 user", "USR2 user"]
    for column in ("transaction_id", "status_code", "failed", "error_text", "response", "prompt_length"):
        assert all(missing(value) for value in frame[column]), column
    assert _overlap_note(run) == (2, 2, 2)


def test_request_that_has_not_written_all_its_first_lines_is_not_given_the_transaction(monkeypatch, tmp_path):
    """The first request has its five first lines, the second one three of them when the
    transaction starts: a request writes its two 'Randomly generated' lines in front of its
    transaction, so the transaction is the one of the first request, and both rows are whole."""
    first, second = log_units(_numbered(1)), log_units(_numbered(2))
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        lines_of(first[:5]), lines_of(second[:3]), lines_of(first[5:]), lines_of(second[3:]))))

    frame = run.uploads[0]["frame"]
    assert list(frame["transaction_id"]) == ["00000001-aaaa-bbbb-cccc-000000000001", "00000002-aaaa-bbbb-cccc-000000000002"]
    assert list(frame["response"]) == ["RESP1 answer", "RESP2 answer"]
    assert list(frame["prompt_length"]) == [11, 12] and list(frame["status_code"]) == [201, 202] and list(frame["failed"]) == [0, 0]
    # Nothing is missing, and the SAS log still says that the two were there at the same time
    assert _overlap_note(run) == (2, 0, 0)


def test_requests_that_queue_for_one_worker_are_open_until_each_has_had_a_transaction(monkeypatch, tmp_path):
    blocks = [log_units(_numbered(number)) for number in (1, 2, 3)]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        *(lines_of(block[:5]) for block in blocks), *(lines_of(block[5:]) for block in blocks), _numbered(4))))

    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["USR1 user", "USR2 user", "USR3 user", "USR4 user"]
    for column in ("transaction_id", "status_code", "failed", "response"):
        assert all(missing(value) for value in frame.loc[:2, column]), column
    # Three requests, three transactions: the request behind them is read like any other
    assert (frame.loc[3, "transaction_id"], frame.loc[3, "response"], frame.loc[3, "status_code"], frame.loc[3, "failed"]) == \
        ("00000004-aaaa-bbbb-cccc-000000000004", "RESP4 answer", 204, 0)
    assert _overlap_note(run) == (3, 3, 3)


@pytest.mark.parametrize("first_failed", [False, True], ids=["first answered", "first failed"])
@pytest.mark.parametrize("second_failed", [False, True], ids=["second answered", "second failed"])
@pytest.mark.parametrize("together", [3, 5, 8], ids=["request, headers, data", "the five first lines", "with its transaction"])
def test_first_lines_of_a_second_request_at_every_place_between_the_lines_of_the_first(monkeypatch, tmp_path, together, first_failed, second_failed):
    """The first lines of a second request stand at every place between two lines of the first
    one, the rest of the second request behind the first. Whatever the place, no row gets a
    value of the other request and no request is called failed that was answered."""
    first, second = log_units(_numbered(1, first_failed)), log_units(_numbered(2, second_failed))
    run = run_parser(monkeypatch, _write(tmp_path, ""))
    parse_file = run.namespace["parse_log_file"]
    whole = 0
    for place in range(1, len(first) + 1):
        lines = lines_of(first[:place]) + lines_of(second[:together]) + lines_of(first[place:]) + lines_of(second[together:])
        del run.sas.messages[:]
        frame = parse_file(str(_write(tmp_path, as_log(lines))))
        lacking = _hold_what_the_log_decides(frame, run.sas.messages, [1, 2], {1: first_failed, 2: second_failed})
        whole += 1 if lacking == 0 else 0
    # Behind the last line of the first request the second one is a request of its own
    assert whole >= 1
    assert lacking == 0 and run.sas.messages == []


@pytest.mark.parametrize("together", [8, 5, 3, 1], ids=["first lines and transaction together", "the five first lines together",
                                                         "request, headers, data together", "every line on its own"])
@pytest.mark.parametrize("models", [1, 3], ids=["one container", "a container each"])
def test_requests_whose_lines_mix_in_any_order_keep_what_is_theirs(monkeypatch, tmp_path, together, models):
    """Two or three requests whose lines mix by chance - of one container, or of a container
    each as an export of a log store in the order of time holds them. Every request keeps
    the order of its own lines, and every log line has a time of its own."""
    run = run_parser(monkeypatch, _write(tmp_path, ""))
    parse_file = run.namespace["parse_log_file"]
    chance = random.Random(15 + together + models)
    open_logs = 0
    for _ in range(40):
        count = chance.choice([2, 2, 3])
        failed = {number: chance.random() < 0.25 for number in range(1, count + 1)}
        waiting = []
        for number in range(1, count + 1):
            units = log_units(_numbered(number, failed[number], model="demo_llm" if models == 1 else f"demo_llm_{number}"))
            waiting.append([lines_of(units[:together])] + units[together:])
        mixed, order = [], []
        while any(waiting):
            number = chance.choice([index for index, units in enumerate(waiting) if units])
            if " - Request: POST" in waiting[number][0][0]:
                order.append(number + 1)
            mixed.extend(waiting[number].pop(0))
        lines, _ = in_file_order(mixed)
        del run.sas.messages[:]
        frame = parse_file(str(_write(tmp_path, as_log(lines))))
        open_logs += 1 if _hold_what_the_log_decides(frame, run.sas.messages, order, failed) else 0
    # These logs do hold requests the log does not tell apart
    assert open_logs > 0


def test_containers_whose_lines_are_mixed_in_one_file_are_not_called_failed(monkeypatch, tmp_path):
    """An export of a log store in the order of time: the request of one container starts
    while the request of another one is running. Before, the request to another model ended
    the first one - a request that was answered became failed = 1 without a word."""
    one = llm_block(timestamp="2026-01-05 09:00:00", finished="2026-01-05 09:00:03", model="model_x", user_prompt="question X", response="answer X")
    other = llm_block(timestamp="2026-01-05 09:00:01", finished="2026-01-05 09:00:04", model="model_y", user_prompt="question Y", response="answer Y")
    (before_one, after_one), (before_other, after_other) = split_at_scoring(one), split_at_scoring(other)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(before_one, before_other, after_one, after_other)))

    frame = run.uploads[0]["frame"]
    assert list(frame["model"]) == ["model_x", "model_y"]
    assert list(frame["status_code"]) == [200, 200] and list(frame["failed"]) == [0, 0]
    # The score lines of the first were written while both were running: they are the lines of neither
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "response"]) == (21, "answer Y")
    assert _overlap_note(run) == (2, 2, 0)


def test_request_that_was_cut_off_is_over_where_the_file_goes_on_later_than_its_last_line(monkeypatch, tmp_path):
    """The collection script appends one collection behind the other. A request that was
    running when a collection was made is cut off, and the next collection holds its rest at
    the start of the part of its container. In between stand the parts of other containers:
    a line there that is younger than the last line of the transaction says that the request
    did not run on through them."""
    cut_off, rest = split_at_scoring(llm_block(timestamp="2026-01-05 09:59:50", finished="2026-01-05 10:00:20", model="model_x",
                                               user_prompt="still running", response="its answer"))
    between = [llm_block(timestamp="2026-01-05 10:05:00", model="model_y", response="answer Y1"),
               llm_block(timestamp="2026-01-05 10:06:00", model="model_y", response="answer Y2")]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(cut_off, *between, rest, llm_block(timestamp="2026-01-05 10:30:00", model="model_x"))))

    frame = run.uploads[0]["frame"]
    assert list(frame["model"]) == ["model_x", "model_y", "model_y", "model_x"]
    assert list(frame["response"])[1:] == ["answer Y1", "answer Y2", "The capital of Austria is Vienna."]
    # The lines of its container ended while it was running, which does not say that it failed
    assert missing(frame.loc[0, "failed"]) and list(frame["failed"])[1:] == [0, 0, 0]
    # Its rest further down is the rest of a request that ended: it is added to no row
    assert missing(frame.loc[0, "response"]) and missing(frame.loc[0, "status_code"])
    assert "its answer" not in frame.to_csv()
    assert _end_note(run) == 1


# ---------------------------------------------------------------------------
# Where the time goes back
#
# kubectl logs writes the lines of one container, and the collection script appends
# container after container and collection after collection. Where a part ends the
# time goes back, by as much as the collections reach back. Inside a part it can go
# back by a second: a line is stamped when it is made and written a moment later.
# ---------------------------------------------------------------------------

def _stamped(unit, time):
    """A log line, with the lines behind it that carry no time, under another time."""
    return [time + unit[0][19:]] + unit[1:]


def _counted(messages):
    """The requests the two NOTEs count that say what the log does not decide."""
    assert all(kind == "NOTE" for kind, _ in messages), messages
    return (_overlap_counts(messages) or (0,))[0] + (_end_counts(messages) or 0)


def _two_that_overlap():
    """Two requests of one container, as (request, lines of one log line) in file order: the eight first lines
    of the first, the eight of the second, the rest of the first, the rest of the second."""
    first, second = log_units(_numbered(1)), log_units(_numbered(2))
    return [(1, unit) for unit in first[:8]] + [(2, unit) for unit in second[:8]] + [(1, unit) for unit in first[8:]] + [(2, unit) for unit in second[8:]]


@pytest.mark.parametrize("late", range(1, 32))
def test_line_that_is_a_second_older_than_a_request_line_in_front_of_it_ends_no_request(monkeypatch, tmp_path, late):
    """Two requests of one container overlap, every log line a second later than the one
    before - and one line carries the second before the newest request line in front of it,
    as a line that was made in one second and written in the next. Before, that line was
    taken for the first line of the next container: everything running was dropped, and a
    request that was answered became failed = 1, at two places without a NOTE."""
    units = _two_that_overlap()
    moments = [datetime(2026, 1, 5, 9, 15, 0).timestamp() + position for position in range(len(units))]
    newest_request = max(position for position in range(late) if " - Request: POST" in units[position][1][0])
    moments[late] = moments[newest_request] - 1
    lines = [line for moment, (_, unit) in zip(moments, units) for line in _stamped(unit, datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M:%S"))]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines)))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 2
    lacking = 0
    for number, (_, row) in zip((1, 2), frame.iterrows()):
        found = _numbers_in(row)
        # No value of the other request, and both were answered: failed is 0 or the log does not decide it
        assert set(found.values()) <= {number}, (number, found)
        assert missing(row["failed"]) or row["failed"] == 0, (number, row["failed"])
        lacking += 1 if missing(row["failed"]) or not ANSWERED_HAS <= set(found) else 0
    assert lacking == 0 or _counted(run.sas.messages) >= lacking


def test_line_that_names_its_transaction_is_read_wherever_it_stands(monkeypatch, tmp_path):
    """Two requests one behind the other, no overlap: the 'Response Data:' line of the first
    is written behind the request line of the second and carries the second before it.
    The line names its transaction, so nothing is in doubt. Before, the second request lost
    every value and became failed = 1 without a NOTE."""
    first, second = log_units(_numbered(1)), log_units(_numbered(2))
    lines = lines_of([_stamped(unit, "2026-01-05 09:15:01") for unit in first[:8]]
                     + [_stamped(unit, "2026-01-05 09:15:03") for unit in first[8:-1]]
                     + [_stamped(second[0], "2026-01-05 09:15:04"), _stamped(first[-1], "2026-01-05 09:15:03")]
                     + [_stamped(unit, "2026-01-05 09:15:04") for unit in second[1:]])
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines)))

    frame = run.uploads[0]["frame"]
    assert _hold_what_the_log_decides(frame, run.sas.messages, [1, 2], {1: False, 2: False}) == 0
    assert list(frame["status_code"]) == [201, 202] and list(frame["failed"]) == [0, 0]
    assert _numbers_in(frame.loc[1])["prompt_length"] == 2


def test_response_line_that_was_written_late_is_given_to_no_request(monkeypatch, tmp_path):
    """The same two requests, and the 'Response:' line of the first is late as well. It
    names no request: where a part of the file begins in the middle of a request, such a
    line is the line of a request of the next container. The first request keeps what was
    read, its status and failed are missing and the NOTE counts it; the second has all its
    values. Before, both became failed = 1 without a NOTE."""
    first, second = log_units(_numbered(1)), log_units(_numbered(2))
    lines = lines_of([_stamped(unit, "2026-01-05 09:15:01") for unit in first[:8]]
                     + [_stamped(unit, "2026-01-05 09:15:03") for unit in first[8:-2]]
                     + [_stamped(second[0], "2026-01-05 09:15:04")] + [_stamped(unit, "2026-01-05 09:15:03") for unit in first[-2:]]
                     + [_stamped(unit, "2026-01-05 09:15:04") for unit in second[1:]])
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines)))

    frame = run.uploads[0]["frame"]
    assert (frame.loc[0, "response"], frame.loc[0, "prompt_length"]) == ("RESP1 answer", 11)
    assert missing(frame.loc[0, "status_code"]) and missing(frame.loc[0, "failed"])
    assert ANSWERED_HAS <= set(_numbers_in(frame.loc[1])) and set(_numbers_in(frame.loc[1]).values()) == {2}
    assert (frame.loc[1, "status_code"], frame.loc[1, "failed"]) == (202, 0)
    assert _overlap_note(run) == (2, 2, 1)


@pytest.mark.parametrize("seconds, part_ends", [(1, False), (2, True), (3600, True)], ids=["one second", "two seconds", "an hour"])
def test_next_part_of_the_file_begins_where_the_time_goes_back_by_more_than_a_second(monkeypatch, tmp_path, seconds, part_ends):
    """A request is running, and the score lines that follow are older than its request
    line. By more than a second they are the lines of the next container, which was cut
    off in the middle of a request: the request ended with its part and the lines are the
    lines of no request. By one second the log does not say which it is: the lines are
    given to no request either, and the request goes on."""
    running, rest = split_at_scoring(llm_block(timestamp="2026-01-05 10:00:00", finished="2026-01-05 10:00:05", user_prompt="still running", response="its own answer"))
    older = (datetime(2026, 1, 5, 10, 0, 0) - pd.Timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    leftover = [log_line(older, "scoreModel", "prompt_length: 999"), log_line(older, "scoreModel", "response: the answer of another request")]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(running, leftover, rest)))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 1 and frame.loc[0, "user_prompt"] == "still running"
    assert "the answer of another request" not in frame.to_csv() and "999" not in frame.to_csv()
    if part_ends:
        # Its own rest further down is the rest of a request that ended: nothing of it is added
        assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "response", "status_code", "failed"))
        assert _end_note(run) == 1
    else:
        assert (frame.loc[0, "prompt_length"], frame.loc[0, "response"], frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (21, "its own answer", 200, 0)
        assert _overlap_note(run) == (1, 1, 0)


def test_request_the_file_ends_on_is_not_called_failed(monkeypatch, tmp_path):
    """The log was collected while the last request was running. It holds neither a
    response nor an error for it, which does not say that it failed. A request that did
    log an error before the file ends failed."""
    answered = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="answered")
    running = split_at_scoring(llm_block(timestamp="2026-01-05 09:01:00", user_prompt="still running"))[0]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(answered, running)))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["answered", "still running"]
    assert frame.loc[0, "failed"] == 0 and missing(frame.loc[1, "failed"]) and missing(frame.loc[1, "status_code"])
    assert _end_note(run) == 1

    # Only its request line and its first lines: it is waiting for its transaction where the file ends
    run = run_parser(monkeypatch, _write(tmp_path, as_log(answered, running[:-3]), "waiting.log"))
    assert missing(run.uploads[0]["frame"].loc[1, "failed"]) and _end_note(run) == 1

    failing = failed_block("2026-01-05 09:01:00", "demo_llm", llm_inputs(user_prompt="failing"))
    run = run_parser(monkeypatch, _write(tmp_path, as_log(answered, failing[:-1]), "failing.log"))
    frame = run.uploads[0]["frame"]
    assert frame.loc[1, "failed"] == 1 and not missing(frame.loc[1, "error_text"])
    assert run.sas.messages == []


def test_request_without_a_response_whose_container_went_on_failed(monkeypatch, tmp_path):
    """What stays as it was: a request that has no response and no error where the next
    request of its container begins did fail, and no NOTE is written for it."""
    running = split_at_scoring(llm_block(timestamp="2026-01-05 09:00:00", user_prompt="never answered"))[0]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(running, llm_block(timestamp="2026-01-05 09:05:00", user_prompt="answered"))))

    frame = run.uploads[0]["frame"]
    assert list(frame["failed"]) == [1, 0] and missing(frame.loc[0, "status_code"])
    assert run.sas.messages == []


def test_transaction_that_goes_on_behind_the_end_of_its_part_takes_no_line_and_gives_none_away(monkeypatch, tmp_path):
    """The lines of two containers in a file that is no container after the other: the
    request of the first is running, the lines of the second are hours older, and the rest
    of the first follows in the middle of the second. The first request ended with its
    part. Its transaction is still named further down, so it is followed: its score lines
    are not taken for the lines of the one request that is running then. Before, the second
    request got the token counts, the run time and the response of the first, without a NOTE."""
    first = log_units(_numbered(1, model="model_x"))
    second = log_units(_numbered(2, model="model_y"))
    lines = lines_of([_stamped(unit, "2026-01-05 12:49:50") for unit in first[:8]]
                     + [_stamped(unit, "2026-01-05 07:30:45") for unit in second[:8]]
                     + [_stamped(unit, "2026-01-05 12:49:56") for unit in first[8:]]
                     + [_stamped(unit, "2026-01-05 07:30:47") for unit in second[8:]])
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines)))

    frame = run.uploads[0]["frame"]
    assert list(frame["model"]) == ["model_x", "model_y"]
    assert set(_numbers_in(frame.loc[0]).values()) == {1} and set(_numbers_in(frame.loc[1]).values()) == {2}
    # The first: what its first lines and its transaction say, and nothing of what stands behind the end of its part
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "output_length", "runtime", "response", "status_code", "failed"))
    assert not missing(frame.loc[0, "transaction_id"])
    # The second: its own values, read once the transaction of the first had ended
    assert ANSWERED_HAS <= set(_numbers_in(frame.loc[1])) and (frame.loc[1, "status_code"], frame.loc[1, "failed"]) == (202, 0)
    # Both are counted: the second ran while the transaction of the first was still named, the first ended with its part
    assert _overlap_counts(run.sas.messages) == (1, 1, 0) and _end_counts(run.sas.messages) == 1


def test_requests_whose_first_lines_stand_behind_older_lines_are_not_both_called_failed(monkeypatch, tmp_path):
    """Two requests whose first lines are cut in two, the second halves four seconds older
    than the request line in front of them - no container writes that, the file was put
    together by hand. The part ends where the time goes back: the request that was waiting
    there is not called failed, and the NOTE counts it. The first one had lost its first
    lines to the request line of the second, which is a request that broke off."""
    first, second = log_units(_numbered(1)), log_units(_numbered(2))
    lines = lines_of([_stamped(unit, "2026-01-05 07:30:45") for unit in first[:2]]
                     + [_stamped(unit, "2026-01-05 07:30:49") for unit in second[:2]]
                     + [_stamped(unit, "2026-01-05 07:30:45") for unit in first[2:8]]
                     + [_stamped(unit, "2026-01-05 07:30:47") for unit in first[8:]]
                     + [_stamped(unit, "2026-01-05 07:30:49") for unit in second[2:8]]
                     + [_stamped(unit, "2026-01-05 07:30:50") for unit in second[8:]])
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines)))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 2
    for number, (_, row) in zip((1, 2), frame.iterrows()):
        assert set(_numbers_in(row).values()) <= {number}
    assert frame.loc[0, "failed"] == 1 and missing(frame.loc[1, "failed"])
    assert _end_note(run) == 1


def test_seconds_that_go_back_by_one_between_requests_that_overlap_change_no_value(monkeypatch, tmp_path):
    """Two or three requests of one container whose lines mix by chance, each line stamped
    up to a second before its place in the file and never before the line of its own
    request in front of it. What holds for the same lines with rising seconds holds here."""
    run = run_parser(monkeypatch, _write(tmp_path, ""))
    parse_file = run.namespace["parse_log_file"]
    chance = random.Random(16)
    went_back = 0
    for _ in range(60):
        count = chance.choice([2, 2, 3])
        failed = {number: chance.random() < 0.25 for number in range(1, count + 1)}
        waiting = [log_units(_numbered(number, failed[number])) for number in range(1, count + 1)]
        lines, order, stamps = [], [], []
        clock, newest = 0.0, {}
        while any(waiting):
            number = chance.choice([index for index, units in enumerate(waiting) if units])
            unit = waiting[number].pop(0)
            clock += chance.choice([0.0, 0.1, 0.3, 0.6, 1.0, 1.4])
            second = max(int(clock - chance.choice([0.0, 0.0, 0.2, 0.5, 0.9])), newest.get(number, 0))
            newest[number] = second
            stamps.append(second)
            if " - Request: POST" in unit[0]:
                order.append(number + 1)
            lines.extend(_stamped(unit, (datetime(2026, 1, 5, 9, 0, 0) + pd.Timedelta(seconds=second)).strftime("%Y-%m-%d %H:%M:%S")))
        assert all(before - after <= 1 for before, after in zip(stamps, stamps[1:]))
        went_back += sum(1 for before, after in zip(stamps, stamps[1:]) if after < before)
        del run.sas.messages[:]
        frame = parse_file(str(_write(tmp_path, as_log(lines))))
        assert len(frame) == count
        lacking = 0
        for number, (_, row) in zip(order, frame.iterrows()):
            found = _numbers_in(row)
            assert set(found.values()) <= {number}, (number, found)
            assert missing(row["failed"]) or row["failed"] == int(failed[number]), (number, row["failed"])
            lacking += 1 if missing(row["failed"]) or not (FAILED_HAS if failed[number] else ANSWERED_HAS) <= set(found) else 0
        assert lacking == 0 or _counted(run.sas.messages) >= lacking
    assert went_back > 20


# ---------------------------------------------------------------------------
# A response that holds lines of a log
# ---------------------------------------------------------------------------

def test_lines_of_a_response_that_start_like_log_lines_are_its_text(monkeypatch, tmp_path):
    """A model that was asked about a log answers with lines of one. They stand between the
    'response:' line and the next line of the transaction, where a container writes nothing
    else for the request: they are text, the request keeps its 'Response:' line and no
    request is made of them."""
    quoted = ["2026-01-05 08:00:00 - api - INFO - Request: POST /phantom_model",
              "2026-01-05 08:00:00 - api - INFO - Request Data: {'inputs': [{'name': 'userPrompt', 'value': 'not a request'}]}",
              "2026-01-05 08:00:00 - scoreModel - INFO - prompt_length: 99999",
              "2026-01-05 08:00:00 - api - INFO - Response: Status Code 500",
              "2026-01-05 08:00:00 is when it happened."]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(response="The log holds these lines:\n" + "\n".join(quoted) + "\nThat is all."),
        llm_block(timestamp="2026-01-05 09:16:00", user_prompt="the next request", response="the next answer"))))

    frame = run.uploads[0]["frame"]
    assert list(frame["model"]) == ["demo_llm", "demo_llm"]
    assert frame.loc[0, "response"] == "The log holds these lines: " + " ".join(quoted) + " That is all."
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (21, 200, 0)
    assert (frame.loc[1, "user_prompt"], frame.loc[1, "response"], frame.loc[1, "status_code"]) == ("the next request", "the next answer", 200)
    assert run.sas.messages == []


def test_request_that_starts_inside_a_response_is_a_request(monkeypatch, tmp_path):
    """A request line that is followed by its 'Request Headers:' line is the line of a
    request, also between the 'response:' line of another request and the next line of
    its transaction."""
    first = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="question A", response="answer A")
    second = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="question B", response="answer B")
    response_line = next(index for index, line in enumerate(first) if " - scoreModel - INFO - response: " in line)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(first[:response_line + 1], second[:8], first[response_line + 1:], second[8:])))

    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["question A", "question B"]
    assert (frame.loc[0, "response"], frame.loc[0, "status_code"]) == ("answer A", 200)
    assert (frame.loc[1, "response"], frame.loc[1, "status_code"], frame.loc[1, "failed"]) == ("answer B", 200, 0)
    assert _overlap_note(run) == (2, 0, 0)


@pytest.mark.parametrize("quoted_time, status", [
    ("2026-01-05 09:15:00", 200), ("2026-01-05 09:15:10", 200), ("2026-01-05 09:15:30", 200), ("2026-01-05 09:14:59", 200),
    ("2026-01-05 09:14:58", None), ("2026-01-05 08:00:00", None), ("2026-01-05 09:15:31", None), ("2026-01-06 09:15:00", None)],
    ids=["with the time of the request line", "with a time while the request ran", "with the time of the Response line",
         "one second before the request line", "two seconds before the request line", "an hour before",
         "one second after the Response line", "a day later"])
def test_response_that_quotes_a_request_line_and_its_headers_line_reads_as_a_request(monkeypatch, tmp_path, quoted_time, status):
    """What the lines do not tell apart: a response that holds a request line with a 'Request
    Headers:' line behind it, each at the start of a line as a container writes them. One
    more row is written, and the response ends in front of the quoted lines. The request
    keeps its 'Response:' line where the quoted lines carry a time from one second before
    its request line to the time of its 'Response:' line. Quoted with an earlier or a later
    time, the lines of the request and the quoted ones are not in the order of one
    container: its status and failed stay missing, which a NOTE counts - it is never
    called failed for it."""
    quoted = [f"{quoted_time} - api - INFO - Request: POST /demo_llm",
              f"{quoted_time} - api - INFO - Request Headers: Host: quoted.example.com"]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(finished="2026-01-05 09:15:30", response="Like this:\n" + "\n".join(quoted)),
        llm_block(timestamp="2026-01-07 10:00:00", user_prompt="the next request", response="the next answer"))))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 3
    assert (frame.loc[0, "response"], frame.loc[0, "prompt_length"]) == ("Like this:", 21)
    if status is None:
        assert missing(frame.loc[0, "status_code"]) and missing(frame.loc[0, "failed"])
    else:
        assert (frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (status, 0)
    assert missing(frame.loc[1, "user_prompt"]) and missing(frame.loc[1, "status_code"]) and missing(frame.loc[1, "failed"])
    assert (frame.loc[2, "response"], frame.loc[2, "status_code"], frame.loc[2, "failed"]) == ("the next answer", 200, 0)
    # Both rows are counted, by one NOTE or by the other
    counted = (_overlap_counts(run.sas.messages) or (0,))[0] + (_end_counts(run.sas.messages) or 0)
    assert counted == 2 and all(kind == "NOTE" for kind, _ in run.sas.messages)


@pytest.mark.parametrize("behind, rows", [([], 2), (["that was it"], 2),
                                          (["2026-01-05 09:15:00 - scoreModel - INFO - prompt_length: 99999"], 1),
                                          (["that was it", "2026-01-05 09:15:00 - scoreModel - INFO - prompt_length: 99999"], 1)],
                         ids=["nothing", "a line of text", "a line that starts like a log line", "text and a line that starts like a log line"])
def test_request_line_that_is_the_last_log_line_of_a_response_reads_as_a_request(monkeypatch, tmp_path, behind, rows):
    """The other case the lines do not tell apart: a response that holds a request line and
    behind it, up to the next line of the transaction, no further line that starts like a
    log line - lines of text can follow. One more row is written and the response ends in
    front of the request line. With one more line that starts like a log line behind it,
    the request line is text of the response."""
    quoted = ["2026-01-05 09:15:00 - api - INFO - Request: POST /x"] + behind
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(response="Like this:\n" + "\n".join(quoted)))))

    frame = run.uploads[0]["frame"]
    assert len(frame) == rows
    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"], frame.loc[0, "prompt_length"]) == (200, 0, 21)
    if rows == 2:
        assert frame.loc[0, "response"] == "Like this:"
        assert frame.loc[1, "model"] == "x" and all(missing(frame.loc[1, column]) for column in ("user_prompt", "status_code", "failed"))
        assert "that was it" not in frame.to_csv()
        assert _overlap_note(run) == (2, 0, 1)
    else:
        assert frame.loc[0, "response"] == "Like this: " + " ".join(quoted)
        assert run.sas.messages == []


# ---------------------------------------------------------------------------
# Content that can not be read
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("options", ["[" * 100000, '{"a":' * 100000, "{a:" * 100000 + "1" + "}" * 100000],
                         ids=["brackets", "JSON objects", "lists of options"])
def test_options_that_are_nested_deeper_than_python_decodes_do_not_stop_the_file(monkeypatch, tmp_path, options):
    """json.loads gives up on them with a RecursionError, which is no ValueError."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(options=options), llm_block(timestamp="2026-01-05 09:16:00", options="{temperature:0.4}"))))

    frame = run.uploads[0]["frame"]
    assert run.row_count == "2"
    assert (frame.loc[0, "kind"], frame.loc[0, "user_prompt"], frame.loc[0, "response"], frame.loc[0, "status_code"]) == \
        ("llm", "What is the capital of Austria?", "The capital of Austria is Vienna.", 200)
    assert frame.loc[0, "options"] == options
    assert (frame.loc[1, "temperature"], frame.loc[1, "options"]) == (0.4, "{temperature:0.4}")


def test_request_whose_content_raises_anything_keeps_its_row_with_what_was_read(monkeypatch, tmp_path):
    """Whatever the content of one request does to the code that reads it, the file is read
    to its end. Here the hash of one embedded text can not be taken."""
    path = _write(tmp_path, as_log(
        embedding_block(document="a text whose hash can not be taken", tokens=5),
        embedding_block(timestamp="2026-01-05 09:26:00", document="another text", tokens=6)))
    run = run_parser(monkeypatch, path)
    hash_of = run.namespace["text_hash"]

    def breaks_on_one_text(text):
        if "can not be taken" in text:
            raise OverflowError("made up")
        return hash_of(text)

    run.namespace["text_hash"] = breaks_on_one_text
    del run.sas.messages[:]
    frame = run.namespace["parse_log_file"](str(path))

    assert len(frame) == 2
    assert missing(frame.loc[0, "input_hash"]) and frame.loc[1, "input_hash"] == text_hash("another text")
    # The inputs that could be read are there, and so is everything the other lines say
    assert (frame.loc[0, "kind"], frame.loc[0, "embedding_mode"], frame.loc[0, "project"]) == ("embedding", "document", "handbook")
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (5, 200, 0)
    assert notes(run) == ["1 request(s) hold a value that could not be read, for example a 'Request Data:' line that was cut off or "
                          "options that are nested deeper than Python decodes. The rows were kept with what was read."]


def test_request_data_that_was_cut_off_is_counted_in_a_note(monkeypatch, tmp_path):
    block = llm_block()
    data_line = next(index for index, line in enumerate(block) if " - Request Data: " in line)
    block[data_line] = block[data_line][:-25]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(block, llm_block(timestamp="2026-01-05 09:16:00"))))

    assert run.row_count == "2"
    assert len(notes(run)) == 1 and notes(run)[0].startswith("1 request(s) hold a value that could not be read, for example a 'Request Data:' line")


@pytest.mark.parametrize("inputs, columns", [
    (llm_inputs(system_prompt="half \ud83d of a pair", user_prompt="tell me \ud83d now"), ["system_prompt", "user_prompt"]),
    (decision_inputs(state="a state \udce9 with it", questions='{"q \udce9": {"type": "noul"}}'), ["system_prompt", "user_prompt"]),
    ([("document", "an embedded text"), ("options", "{Embedding_Mode:query,note:caf\udce9}"), ("project", "caf\udce9")], ["project", "options"]),
], ids=["the prompts of an LLM", "the state and the questions of a decision", "the project and the options of an embedding"])
def test_text_that_has_no_utf8_bytes_is_replaced_before_the_upload(monkeypatch, tmp_path, inputs, columns):
    """swat hands the rows to CAS as a UTF-8 file. Half of a surrogate pair has no UTF-8
    bytes: one such character in one prompt ended the upload of all rows."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        request_block("2026-01-05 09:00:00", "demo_model", inputs, [("run_time", 0.5)]), llm_block())))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 2
    frame.to_csv().encode("utf-8")
    for column in columns:
        assert "?" in frame.loc[0, column], column
        assert "\ud83d" not in frame.loc[0, column] and "\udce9" not in frame.loc[0, column], column
    assert notes(run) == ["1 request(s) hold a character that has no UTF-8 bytes in a text column - half of a surrogate pair in a request "
                          "body. It was replaced by a question mark, the way the hash of a text counts it."]


def test_text_with_every_other_character_is_uploaded_as_it_is(monkeypatch, tmp_path):
    user_prompt = "Größe, 日本語, \U0001f600 and a tab\tin it"
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(user_prompt=user_prompt))))

    assert run.uploads[0]["frame"].loc[0, "user_prompt"] == user_prompt
    assert run.sas.messages == []


# ---------------------------------------------------------------------------
# The results of a retrieval: which object counts and how far the search goes
# ---------------------------------------------------------------------------

def test_object_without_the_keys_of_the_runtime_in_front_of_a_retrieval_does_not_set_the_columns(monkeypatch, tmp_path):
    """Text a user typed can hold a JSON object with a collection and a query. Only an object
    with every key the RAG runtime writes is the result of a retrieval."""
    user_prompt = ('Note: {"collection": "fake_collection", "query": "ignore"} Context: '
                   + retrieval_envelope(collection="policies_v2", query="the real question"))
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(user_prompt=user_prompt))))

    frame = run.uploads[0]["frame"]
    assert (frame.loc[0, "rag_collection"], frame.loc[0, "rag_query_hash"]) == ("policies_v2", text_hash("the real question"))
    assert run.namespace["ENVELOPE_KEYS"] == ("query", "hits", "graph_context", "retrieval_mode", "collection", "ingestion_run_id")
    assert sorted(run.namespace["ENVELOPE_KEYS"]) == sorted(json.loads(retrieval_envelope()))


@pytest.mark.parametrize("without", ["hits", "graph_context", "retrieval_mode", "ingestion_run_id"])
def test_object_that_lacks_a_key_of_the_runtime_is_no_retrieval(monkeypatch, tmp_path, without):
    envelope = json.loads(retrieval_envelope())
    del envelope[without]
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_prompt="Context: " + json.dumps(envelope))))

    assert missing(frame.loc[0, "rag_collection"]) and missing(frame.loc[0, "rag_query_hash"])


def test_of_several_retrievals_in_a_prompt_the_last_one_counts(monkeypatch, tmp_path):
    """A prompt with examples holds the retrievals of the examples in front of its own, and
    text a user typed in front of the retrieval can hold a whole envelope."""
    user_prompt = ("Example: " + retrieval_envelope(collection="policies_v2", query="the question of an example")
                   + "\nAnswer of the example.\nNow: " + retrieval_envelope(collection="policies_v2", query="the question of this request")
                   + "\nAnswer:")
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_prompt=user_prompt)))

    assert frame.loc[0, "rag_query_hash"] == text_hash("the question of this request")


class _CountingDecoder(json.JSONDecoder):
    """Counts what the search for an envelope asks of the JSON decoder."""
    starts = 0
    characters = 0

    def raw_decode(self, text, position=0):
        type(self).starts += 1
        try:
            value, end = super().raw_decode(text, position)
        except ValueError as error:
            type(self).characters += max(1, error.pos - position)
            raise
        type(self).characters += end - position
        return value, end


@pytest.mark.parametrize("junk", ["{" * 200000, '{"a": 1, ' * 60000, '{"a": [1, ' * 20000, '{"a":' * 100000, '{"a": 1} ' * 50000],
                         ids=["opening braces", "objects that are not closed", "lists that are not closed", "nested objects", "small objects"])
def test_search_for_a_retrieval_is_bounded(monkeypatch, tmp_path, junk):
    """A prompt can be made up to keep the search busy: from every brace the decoder runs on
    until the text stops being JSON. The search tries a limited number of braces and counts
    the characters it decoded."""
    run = run_parser(monkeypatch, _write(tmp_path, ""))
    find, starts, characters = (run.namespace[name] for name in ("find_retrieval_envelope", "ENVELOPE_STARTS", "ENVELOPE_CHARACTERS"))
    assert (starts, characters) == (500, 2000000)
    envelope = retrieval_envelope(collection="policies_v2", query="made-up question")
    _CountingDecoder.starts = _CountingDecoder.characters = 0
    run.namespace["json"] = types.SimpleNamespace(JSONDecoder=_CountingDecoder, loads=json.loads, dumps=json.dumps)

    # Behind the retrieval the junk changes nothing, in front of it the retrieval is not reached
    assert find(envelope + " " + junk)["collection"] == "policies_v2"
    assert find(junk + " " + envelope) is None
    assert _CountingDecoder.starts <= 2 * starts
    assert _CountingDecoder.characters <= 2 * (characters + len(junk) + len(envelope))


def test_prompt_with_few_braces_in_front_of_its_retrieval_is_searched_to_its_end(monkeypatch, tmp_path):
    user_prompt = ("{city} and {name} " * 100 + '{"note": "an object"} ' * 100
                   + retrieval_envelope(collection="policies_v2", query="made-up question"))
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(user_prompt=user_prompt)))

    assert frame.loc[0, "rag_collection"] == "policies_v2"


# ---------------------------------------------------------------------------
# Secrets the options can hold beyond a key under its name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sent, kept, secrets", [
    ("{my_pass:hunter2,max_tokens:40}", "{my_pass:REDACTED,max_tokens:40}", ["hunter2"]),
    ("{pw:hunter2}", "{pw:REDACTED}", ["hunter2"]),
    ("{userPass:hunter2,PW:hunter3,temperature:0.2}", "{userPass:REDACTED,PW:REDACTED,temperature:0.2}", ["hunter"]),
    ("{passphrase:made-up words}", "{passphrase:REDACTED}", ["made-up words"]),
    ("{base_url:https://user:hunter2pass@models.example.com/v1,top_k:5}", "{base_url:https://REDACTED@models.example.com/v1,top_k:5}", ["hunter2pass", "user:"]),
    ('{"base_url": "postgresql://rag:hunter2pass@db.example.com:5432/rag", "top_p": 0.9}',
     '{"base_url": "postgresql://REDACTED@db.example.com:5432/rag", "top_p": 0.9}', ["hunter2pass"]),
    ('{"headers": "Authorization: Bearer abc.def.ghi"}', '{"headers": "Authorization: REDACTED"}', ["abc.def.ghi", "Bearer"]),
    ("{headers:X-Api-Key: made-up-one,temperature:0.2}", "{headers:X-Api-Key: REDACTED,temperature:0.2}", ["made-up-one"]),
    ('{"note": "send it as bearer abc.def.ghi please", "max_tokens": 12}', '{"note": "send it as REDACTED please", "max_tokens": 12}', ["abc.def.ghi"]),
    ("{temperature:0.2,client:{id:1,secret:s3cr3tvalue}}", "{temperature:0.2,client:{id:1,secret:REDACTED}}", ["s3cr3tvalue"]),
    ("{client:{secret:s3cr3tvalue,id:1},temperature:0.2}", "{client:{secret:REDACTED,id:1},temperature:0.2}", ["s3cr3tvalue"]),
    ("{a:{b:{API_KEY:made,up},c:[1,2]},max_tokens:7}", "{a:{b:{API_KEY:REDACTED},c:[1,2]},max_tokens:7}", ["made", "up}"]),
], ids=["pass as a word of the name", "pw", "camel case and capitals", "passphrase", "the user and password of an address",
        "an address in JSON", "a header in a JSON value", "a header in a value", "a bearer token in a sentence",
        "the last name of a nested list", "the first name of a nested list", "a nested key with a comma"])
def test_parser_redacts_the_secrets_inside_the_options(monkeypatch, tmp_path, sent, kept, secrets):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(options=sent))))

    frame = run.uploads[0]["frame"]
    assert frame.loc[0, "options"] == kept
    for secret in secrets:
        assert secret not in frame.to_csv(), secret
    # What is kept reads as options the way what was sent did: the columns of the options are the same
    read = run.namespace["read_options"]
    for column in ("temperature", "top_p", "top_k", "max_tokens"):
        assert read(kept).get(column) == read(sent).get(column), column
    assert kept.count("{") == kept.count("}") and kept.count("[") == kept.count("]")


@pytest.mark.parametrize("sent", [
    "{bypass:true,compass:north,stopwords:and or,passes:3}",
    "{endpoint:https://models.example.com/v1/chat/completions,mailto:someone@example.com}",
    '{"stop": ["}", "]"], "temperature": 0.3}',
    "{response_format:{type:json_object},temperature:0.1}",
], ids=["words that hold pass and pw", "an address without credentials", "brackets as values", "a nested list without a secret"])
def test_options_without_a_secret_are_kept_as_they_were_sent(monkeypatch, tmp_path, sent):
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(options=sent)))

    assert frame.loc[0, "options"] == sent


# ---------------------------------------------------------------------------
# The SAS code around the Python: messages, sessions, the result of the append
# ---------------------------------------------------------------------------

@BOTH_PROGRAMS
def test_message_for_a_table_that_is_not_in_cas_names_it_by_what_both_programs_set(program):
    """The code file sets the library and the name of the table, the step is handed them by
    its output table control. &_log_target_table. is set by that control only."""
    program = program()

    assert 'putlog "ERROR: The table &_log_target_table_lib..&_log_target_table_name. is not a CAS table.";' in program
    assert "&_log_target_table." not in program


def test_every_macro_variable_the_code_file_reads_is_one_it_sets():
    program = code_file_program()
    program = program[:program.index("submit;")] + program[program.index("endsubmit;"):]

    read = set(name.lower() for name in re.findall(r"&+(_log_\w+)", program))
    assigned = set(name.lower() for name in re.findall(r"%let (_log_\w+)", program))
    assigned |= set(name.lower() for name in re.findall(r"call symputx\('(_log_\w+)'", program))
    assert read - assigned == set()
    assert "_log_row_count" not in read - assigned and "SAS.symput('_log_row_count'" in code_file_program()


def test_code_file_starts_its_cas_session_only_where_none_is_connected():
    """Run behind Get-All-Prompts.sas or behind itself in one SAS session, a second
    'cas mysess;' gives a WARNING that SAS Job Execution reports as the error of the job."""
    code_file = code_file_program()
    first_lines = [line.strip() for line in code_file[:code_file.index("libname public cas caslib='Public';")].split("\n")]

    assert first_lines[-4:-1] == ["%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;", "cas mysess;", "%end;"]
    # The session it started stays, as the librefs that are bound to it do: nothing ends it
    assert "mysess terminate" not in code_file.lower().replace("llmlogmysess", "")


@BOTH_PROGRAMS
def test_append_asks_the_data_step_for_its_result_before_the_existing_table_is_dropped(program):
    """The DATA step that appends can end with an error, and its table is then no table to
    put in the place of the existing one. Its return code and its rows are looked at first."""
    program = program()
    step, swap = _append_step(program), " ".join(_swap(program).split())

    # The return code is taken right behind the step, and it starts at 0 in every run
    assert "    run;\n                %let _log_append_syserr = &syserr.;\n" in step
    assert "%let _log_append_syserr = 0;\n" in program[:program.index("%macro _log_handle_output_table;")]
    # The rows: of the existing table and of the new one from the listing of their caslib, of the log from CASUSER
    assert 'table.tableInfo result=targetTables / casLib = "&_log_target_table_lib.";' in swap
    assert ('if upcase(listedTable.Name) EQ upcase("&_log_target_table_name.") then do; existingRows = listedTable.Rows; '
            "existingLabel = listedTable.Label; end;") in swap
    assert "if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS_ALL' then stagedRows = listedTable.Rows;" in swap
    assert "table.tableInfo result=uploadTables / casLib = 'CASUSER';" in swap
    assert "if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS' then uploadedRows = listedTable.Rows;" in swap
    refused = ("if &_log_append_syserr. GT 4 or existingRows LT 0 or uploadedRows LT 0 or stagedRows NE existingRows + uploadedRows "
               "then stagedComplete = False;")
    assert refused in swap
    # A table that could not be counted is no reason to go on: the three counts start at -1
    assert "existingRows = -1; uploadedRows = -1; stagedRows = -1;" in swap
    # Only in append mode: a table that is copied has no DATA step to ask
    assert swap.index("%if &_log_target_scope. GE 1 %then %do; stagedTable = '_TEMP_LLM_LOGS_ALL';") < swap.index(refused) < swap.index("%else %do; stagedTable =")
    assert swap.index("stagedComplete = True;") < swap.index(refused) < swap.index("if staged.exists GE 1 and stagedComplete then do;") < swap.index("table.dropTable")


@BOTH_PROGRAMS
def test_append_that_did_not_finish_leaves_the_table_with_one_error_line(program):
    swap = " ".join(_swap(program()).split())

    otherwise = swap[swap.index("tableWritten = True; end; else do;"):swap.index("%if &_log_save_table. %then %do;")]
    assert otherwise.count("print (error)") == 2 and otherwise.count("tableWritten = False;") == 1
    in_append_mode = otherwise[otherwise.index("%if &_log_target_scope. GE 1 %then %do;"):otherwise.index("%else %do;")]
    assert ('print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is. '
            'The DATA step that appends ended with the return code &_log_append_syserr. and wrote " || (string) stagedRows || " rows, '
            'the table held " || (string) existingRows || " and the log " || (string) uploadedRows || " (-1 stands for a table that was '
            'not found).";') in in_append_mode
    # What the step left behind is dropped, the existing table is not named in that branch
    assert "table.dropTable / casLib = \"&_log_target_table_lib.\", name = '_TEMP_LLM_LOGS_ALL', quiet = True;" in in_append_mode
    assert 'name = "&_log_target_table_name."' not in otherwise
    # No message goes through %put with a semicolon in it
    assert ";" not in re.sub(r'" \|\| \(string\) \w+ \|\| "', "", in_append_mode[in_append_mode.index('"The new table'):in_append_mode.index('.";')])


@BOTH_PROGRAMS
def test_appended_table_keeps_the_label_of_the_table_it_replaces(program):
    """The DATA step writes a table without a label, so a table lost 'Extracted LLM Log
    Data' with the first append. The label of the existing table is read before it is
    dropped and set on the new one - the label of the parser where it had none."""
    swap = " ".join(_swap(program()).split())

    read, drop, rename = (swap.index(text) for text in ("existingLabel = listedTable.Label;", "table.dropTable", "rename ="))
    label = ("%if &_log_target_scope. GE 1 %then %do; "
             "/* A DATA step writes a table without a label. The new table gets the label the existing one had, and the label of a "
             "table this program starts where it had none */ "
             "if existingLabel EQ '' then existingLabel = 'Extracted LLM Log Data'; "
             'table.alterTable / casLib = "&_log_target_table_lib.", name = "&_log_target_table_name.", label = existingLabel; %end;')
    assert read < drop < rename < swap.index(label) < swap.index("table.promote")
    # The label of a table that is started is the one the upload gives it
    assert "label='Extracted LLM Log Data'," in program()
