"""The log parser: the code file and the custom step stay the same program, and the
parser turns the request blocks of every model kind into the rows of LLM_LOGS - the
twelve columns the table has always had and the columns that were added behind them.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import ast
import hashlib
import json
import math
import random
import re
import types
from datetime import datetime, timedelta

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


def ast_of_the_python():
    """The Python of the code file as a syntax tree."""
    import ast
    return ast.parse(python_block(code_file_program()))


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
    # Its configuration is one line per setting, each taken only where the macro variable is not set yet
    assert before.rstrip().endswith("%_log_default(_log_save_table, 1);\n%sysmacdelete _log_default;")
    assert re.findall(r"^%let (_log_\w+)", before, flags=re.MULTILINE) == []
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
    start = macro.index("%if &_log_table_mode. EQ APPEND %then %do;")
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

    # 2 a promoted table, 1 a table of the session, and a saved file only counts where no table was found. content-22:
    # what tableExists answered is kept as a number, -1 where CAS refused the question
    assert ("if targetExists GE 2 then do; call symputx('_log_target_scope', '2'); end; "
            "else if targetExists EQ 1 then do; call symputx('_log_target_scope', '1'); end; "
            "else if savedFile NE '' and targetExists EQ 0 then do; call symputx('_log_target_unloaded', '1'); end;") in lookup
    assert lookup.count("targetExists = -1; table.tableExists result=re status=reAsked /") == 2
    assert lookup.count("if reAsked.severity EQ 0 then targetExists = re.exists;") == 2 and lookup.count("re.exists") == 2
    # Both start at 0 in every run - the step can run twice in one SAS session - and are deleted at the end
    before_the_macro = program[:program.index("%macro _log_handle_output_table;")]
    assert "%let _log_target_scope = 0;\n%let _log_target_unloaded = 0;\n" in before_the_macro
    assert "%let _log_fixed_rename = ;\n%let _log_fixed_assign = ;\n%let _log_fixed_drop = ;\n" in before_the_macro
    clean_up = program[program.index("* Clean up;"):]
    deleted = next(line for line in clean_up.split("\n") if line.startswith("%symdel ") and "_log_target_scope" in line)
    for name in ("_log_clean_cas_session", "_casSessionUUID", "_log_row_count", "_log_target_scope", "_log_target_unloaded",
                 "_log_fixed_rename", "_log_fixed_assign", "_log_fixed_drop", "_log_append_syserr", "_log_table_mode",
                 "_log_target_rows", "_log_row_repeats", "_log_target_has_id", "_log_compare_syserr", "_log_target_has_failed",
                 "_log_kept_rows", "_log_took_rows", "_log_append_nothing"):
        assert f" {name}" in deleted, name
    # Replace mode never asks: the lookup runs in append mode only, and the setting itself is read in one place.
    # The mode is asked for four times: for the lookup, for the ERROR line of a saved table that could not be
    # loaded, for the one of an action CAS refused (content-22), and for the NOTE of a run that appended
    macro = _output_macro(program)
    assert macro.index("%if &_log_table_mode. EQ APPEND %then %do;") < macro.index("call symputx('_log_target_scope'")
    assert macro.count("%if &_log_table_mode. EQ APPEND %then %do;") == 4
    assert "_log_append_table. %then" not in program and "&_log_append_table." not in program


@BOTH_PROGRAMS
def test_saved_table_that_can_not_be_loaded_is_not_overwritten(program):
    program = program()
    swap = _swap(program)

    refused = swap[swap.index("%if &_log_target_unloaded. %then %do;"):swap.index("%else %if &_log_cas_refused. %then %do;")]
    # one line for a run that appends and one for a run that replaces, which counts the rows of the table first
    assert refused.count("print (error)") == 2
    assert "could not be loaded, nothing was appended and the saved file was left as it is." in refused
    assert "could not be loaded, nothing was replaced and the saved file was left as it is." in refused
    assert "tableWritten = False;" in refused
    # No table is built, dropped or renamed in that branch
    written = swap[swap.index("/* The new table is built beside the existing one"):]
    for action in ("table.copyTable", "table.dropTable", "table.alterTable", "table.promote"):
        assert action not in refused, action
        assert action in written, action
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
    assert statements[statements.index(read):][:5] == [read, "&_log_fixed_assign.", "&_log_fixed_drop.", "run;", "%let _log_append_syserr = &syserr.;"]
    # behind it the step that takes out the rows a row of the log takes the place of, only where there is such a row
    taken_out = statements.index("%if &_log_took_rows. GT 0 %then %do;")
    assert statements.index(read) < taken_out and statements[taken_out:] == [
        "%if &_log_took_rows. GT 0 %then %do;",
        "data _llmtgt._TEMP_LLM_LOGS_ALL _llmsrc._TEMP_LLM_LOGS_OUT(keep=transaction_id);",
        "merge _llmtgt._TEMP_LLM_LOGS_ALL _llmsrc._TEMP_LLM_LOGS_GONE(in=_log_gone_row);",
        "by transaction_id model timestamp;",
        "if _log_gone_row and failed EQ . then output _llmsrc._TEMP_LLM_LOGS_OUT;",
        "else output _llmtgt._TEMP_LLM_LOGS_ALL;",
        "run;",
        "%let _log_append_syserr = %sysfunc(max(&_log_append_syserr., &syserr.));",
        "%end;", "%end;", "libname _llmtgt clear;", "libname _llmsrc clear;"]
    # no table is built where every request of the log is in the table already
    assert statements[built - 1] == "%if &_log_append_nothing. EQ 0 %then %do;"
    assert built < statements.index(read)

    # The swap takes that table where there was a table to append to, and the rows of the log otherwise
    staged = " ".join(swap[swap.index("%else %do;"):swap.index("table.tableExists result=staged")].split())
    assert "%if &_log_target_scope. GE 1 %then %do; stagedTable = '_TEMP_LLM_LOGS_ALL';" in staged
    assert ('%else %do; stagedTable = "&_log_target_table_name._NEW"; table.copyTable / table = {casLib = \'CASUSER\', name = \'_TEMP_LLM_LOGS\'}, '
            'casOut = {casLib = "&_log_target_table_lib.", name = stagedTable, replace = True}; %end;') in staged
    # The existing table is only dropped once its replacement exists
    exists, drop, rename = (swap.index(text) for text in ("if stagedComplete then do;", "table.dropTable", "table.alterTable"))
    assert exists < drop < rename
    # The existing table is dropped twice, what a DATA step that did not finish left behind once, and a table the run
    # loaded from its saved file for an append that did not finish once (test_fix_content_21)
    assert len(re.findall(r'table\.dropTable /\s+casLib = "&_log_target_table_lib\.",\s+name = "&_log_target_table_name\.",', swap)) == 3
    assert swap.count("table.dropTable") == 4
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
    assert swap.index("if stagedComplete then do;") < swap.index("table.dropTable")


def test_replacing_writes_every_column(monkeypatch, tmp_path):
    """Replace mode copies the uploaded rows, and the upload holds every column whatever the log holds."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(embedding_block())))

    assert list(run.uploads[0]["frame"].columns) == ALL_COLUMNS
    swap = _swap(code_file_program())
    assert "table = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'}," in swap


# ---------------------------------------------------------------------------
# The setting _log_append_table has three states, and a run that appends writes a request once
#
# Measured on SAS Viya on 2026-10-13 with the program as it was: a run without the setting replaced a copy of a table
# that held 3,630 rows of earlier runs by the 1,024 requests of one log - in memory and in the saved file, with a
# completed job and no line that said so - and a second append of the same file left every request twice.
# ---------------------------------------------------------------------------

def _mode_of(program, value):
    """What the program makes of a value of _log_append_table: the statements between the comment of the mode and the
    lookup of the caslib are followed. They are %if statements in open code that do not nest, each with one %let, and
    their functions are given their documented results: %LENGTH is the length of the text, and %SUPERQ gives the text
    as text, so EQ compares two whole numbers as numbers and anything else as text."""
    start = program.index("* What the run does with a table that exists, by the setting _log_append_table")
    block = program[start:program.index("* Get the caslib name of the libname;")]
    lines = [line.strip() for line in block.split("\n") if line.strip() and not line.startswith("*")]
    assert lines[0] == "%let _log_table_mode = REFUSED;"
    mode, position = "REFUSED", 1

    def holds(condition):
        for part in condition.split(" and "):
            left, operator, right = part.split(" ")
            assert operator == "EQ", part
            left = left.replace("%length(%superq(_log_append_table))", str(len(value))).replace("%superq(_log_append_table)", value)
            left = left.replace("&_log_table_mode.", mode)
            whole = re.fullmatch(r"[+-]?\d+", left) and re.fullmatch(r"[+-]?\d+", right)
            if not (int(left) == int(right) if whole else left == right):
                return False
        return True

    refused = False
    while position < len(lines):
        match = re.fullmatch(r"%if (.+) %then %do;", lines[position])
        assert match, lines[position]
        body = lines[position + 1:lines.index("%end;", position)]
        if holds(match.group(1)):
            for statement in body:
                if statement.startswith("%let _log_table_mode = "):
                    mode = statement[len("%let _log_table_mode = "):-1]
                elif statement == "%let _log_ready = 0;":
                    refused = True
                else:
                    assert statement.startswith("%put ERROR: _log_append_table is %superq(_log_append_table), which is no value of this setting"), statement
        position = lines.index("%end;", position) + 1
    return mode, refused


@BOTH_PROGRAMS
@pytest.mark.parametrize("value, mode", [
    ("1", "APPEND"), ("0", "REPLACE"), ("", "START"),
    ("2", "REFUSED"), ("yes", "REFUSED"), ("01", "REFUSED"), ("10", "REFUSED"), ("-1", "REFUSED"), ("1.0", "REFUSED"),
    ("true", "REFUSED"), ("x", "REFUSED"),
], ids=["1", "0", "no value", "2", "yes", "01", "10", "-1", "1.0", "true", "one letter"])
def test_the_setting_has_three_states_and_any_other_value_refuses_the_run(program, value, mode):
    found, refused = _mode_of(program(), value)

    assert found == mode
    # a refusal is the one ERROR line and no log: the Python reads nothing where _log_ready is 0
    assert refused == (mode == "REFUSED")


@BOTH_PROGRAMS
def test_the_setting_is_read_as_text_and_nowhere_else(program):
    """A value is compared, never run: the program reads the setting through %SUPERQ in the statements that make the
    mode of it, and everything behind them goes by the mode."""
    program = program()
    code = _sas_code(program)

    assert "&_log_append_table" not in code
    start = code.index("* What the run does with a table that exists, by the setting _log_append_table")
    block = code[start:code.index("* Get the caslib name of the libname;")]
    assert code.count("%superq(_log_append_table)") == block.count("%superq(_log_append_table)") == 6
    # the ERROR line names the value, the two values the setting takes and what a run without one does
    message = next(line.strip() for line in block.split("\n") if line.strip().startswith("%put ERROR:"))
    assert message == ("%put ERROR: _log_append_table is %superq(_log_append_table), which is no value of this setting: 1 adds the "
                       "requests of the log to the output table, 0 replaces the table with them, and without a value the program only "
                       "starts a table that is not there yet. No log was read and nothing was written.;")
    # the code file gives the setting no value: its line only makes sure that the macro variable exists
    assert "%_log_default(_log_append_table, );\n" in code_file_program()
    assert "_log_append_table has no default value" in code_file_program()


def _count_macro(program):
    return program[program.index("%macro _log_count_target;"):program.index("%mend _log_count_target;")]


@BOTH_PROGRAMS
def test_a_run_without_the_setting_stops_at_a_table_that_holds_rows_in_front_of_the_log(program):
    """The rows are counted before PROC PYTHON runs. A table that holds rows sets _log_ready to 0: no log is read,
    _log_row_count stays -1 - the condition code of a failed job - and the output macro returns before it drops,
    copies or saves anything."""
    program = program()
    code = _sas_code(program)

    stop = code[code.index("* A run without a value of _log_append_table stops here"):code.index("* The number of requests found in the log.")]
    lines = [line.strip() for line in stop.split("\n")[1:] if line.strip()]
    assert lines == [
        "%if &_log_table_mode. EQ START and &_log_ready. EQ 1 %then %do;", "%_log_count_target;", "%end;",
        "%if &_log_table_mode. EQ START and &_log_target_rows. GT 0 %then %do;",
        "%put ERROR: The table &_log_target_table_lib..&_log_target_table_name. holds &_log_target_rows. rows and _log_append_table is "
        "not set, so nothing was read and nothing was written - not the table and not its saved file. Set it in front of the program: "
        "%nrstr(%let _log_append_table = 1;) adds the requests of the log to the table, %nrstr(%let _log_append_table = 0;) replaces "
        "the table with them.;",
        "%let _log_ready = 0;", "%end;",
        "%if &_log_table_mode. EQ START and &_log_target_rows. EQ -1 %then %do;",
        "%put ERROR: The saved table &_log_target_table_lib..&_log_target_table_name. could not be loaded and _log_append_table is not "
        "set, so nothing was read and nothing was written - the saved file was left as it is.;",
        "%let _log_ready = 0;", "%end;",
        "%if &_log_table_mode. EQ START and &_log_target_rows. EQ -2 %then %do;",
        "%put ERROR: The rows of the table &_log_target_table_lib..&_log_target_table_name. could not be counted and _log_append_table is "
        "not set, so nothing was read and nothing was written - not the table and not its saved file. Set it in front of the program: "
        "%nrstr(%let _log_append_table = 1;) adds the requests of the log to the table, %nrstr(%let _log_append_table = 0;) replaces "
        "the table with them.;",
        "%let _log_ready = 0;", "%end;",
        "%if &_log_table_mode. EQ START and &_log_target_rows. EQ -3 %then %do;",
        "%put ERROR: CAS refused an action that the count of the rows of the table &_log_target_table_lib..&_log_target_table_name. "
        "needs and _log_append_table is not set, so nothing was read and nothing was written - not the table and not its saved file.;",
        "%let _log_ready = 0;", "%end;"]
    # in front of the Python, behind the session and the caslib of the table
    assert program.index("* Get the caslib name of the libname;") < program.index("call symputx('_casSessionUUID', sessresults[1]);") \
        < program.index("%macro _log_count_target;") < program.index("%_log_count_target;") < program.index("proc python restart;")
    assert "%let _log_target_rows = 0;\n%macro _log_count_target;" in program
    # a table with no rows, and no table, are started: 0 is neither above nor below 0
    assert "%let _log_row_count = -1;" in code[code.index(stop) + len(stop) - 1:]
    macro = _output_macro(program)
    assert macro.index("%if &_log_ready. NE 1 %then %return;") < macro.index("%_log_count_target;") < macro.index("table.dropTable")


@BOTH_PROGRAMS
def test_the_rows_of_the_table_are_counted_in_memory_or_from_its_saved_file(program):
    """The count writes nothing in the caslib of the table: a table in memory is asked for its rows, and the saved file
    of a table that is not in memory is loaded into CASUSER under a name of the program, counted and dropped again."""
    macro = " ".join(_count_macro(program()).split())

    assert macro.index('table.tableExists result=found status=foundAsked / casLib = "&_log_target_table_lib.", name = "&_log_target_table_name.";') \
        < macro.index("if foundAsked.severity NE 0 then targetRows = -3; else if found.exists GE 1 then do;") \
        < macro.index("table.fileInfo result=countedListing") < macro.index("table.loadTable")
    # content-22: a listing of the saved files that CAS refused is -3, not a caslib without the file
    assert ('table.fileInfo result=countedListing status=countedListed / casLib = "&_log_target_table_lib."; '
            "if countedListed.severity NE 0 then targetRows = -3; if countedListed.severity EQ 0 then do;") in macro
    assert ('table.tableInfo result=foundTables / casLib = "&_log_target_table_lib.", name = "&_log_target_table_name.", quiet = True; '
            "if exists(foundTables, 'TableInfo') then targetRows = foundTables.TableInfo[1].Rows;") in macro
    # the name of the file as append mode looks for it: the exact name, otherwise the same name in another case
    assert 'if countedEntry.Name EQ "&_log_target_table_name..sashdat" then countedFile = countedEntry.Name;' in macro
    assert 'upcase(countedEntry.Name) EQ upcase("&_log_target_table_name..sashdat") then countedFile = countedEntry.Name;' in macro
    # -1 until the loaded file was counted, so a file that could not be loaded is not taken for a table without rows
    loaded = macro[macro.index("if countedFile NE '' then do;"):]
    assert loaded.index("targetRows = -1;") < loaded.index("table.loadTable") < loaded.index("targetRows = countedTables.TableInfo[1].Rows;") \
        < loaded.index("table.dropTable")
    assert ("table.loadTable / casLib = \"&_log_target_table_lib.\", path = countedFile, "
            "casOut = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS_SAVED', replace = True};") in loaded
    assert "table.dropTable / casLib = 'CASUSER', name = '_TEMP_LLM_LOGS_SAVED', quiet = True;" in loaded
    assert macro.count("casOut") == 1 and macro.count("table.dropTable") == 1
    for action in ("table.promote", "table.save", "table.alterTable", "table.copyTable"):
        assert action not in macro, action
    assert macro.rstrip().endswith("call symputx('_log_target_rows', targetRows); run; quit;")
    # the macro is deleted with the other one
    assert "%sysmacdelete _log_handle_output_table;\n%sysmacdelete _log_count_target;\n" in program()


@BOTH_PROGRAMS
def test_a_run_that_replaces_says_how_many_rows_it_replaced(program):
    program = program()
    macro, swap = _output_macro(program), " ".join(_swap(program).split())

    # counted while the rows are still there: in front of the swap, for a run that replaces only
    counted = ("%if &_log_table_mode. EQ REPLACE %then %do;\n            %_log_count_target;\n            %if &_log_target_rows. EQ -1 %then %do;\n"
               "                %let _log_target_unloaded = 1;\n            %end;\n            %if &_log_target_rows. EQ -3 %then %do;\n"
               "                %let _log_cas_refused = 1;\n            %end;\n        %end;")
    assert macro.index("%if &_log_row_count. GT 0 %then %do;") < macro.index(counted) < macro.index("table.dropTable")
    assert program.count("%_log_count_target;") == 2
    # said behind the save, for a table that was written and held rows
    note = ('%if &_log_table_mode. EQ REPLACE and &_log_target_rows. GT 0 %then %do; if tableWritten then do; '
            'print (note) "The table &_log_target_table_lib..&_log_target_table_name. held &_log_target_rows. row(s). " || '
            '"They were replaced by the &_log_row_count. request(s) of the log, as _log_append_table is 0."; end; %end;')
    assert swap.index("table.save") < swap.index(note)


@BOTH_PROGRAMS
def test_a_run_that_appends_leaves_out_the_requests_the_table_holds_already(program):
    """Two DATA steps in front of the one that appends: what the rows of the table are known by - transaction ID, model
    and time - becomes a table of its own, and the rows of the log are merged with it. The rows that are kept take the
    place of the rows of the log, so the step that appends and the count behind it read what is appended. What the
    steps do with rows is followed in test_log_parser_output_table.py."""
    program = program()
    step = _append_step(program)
    statements = [line.strip() for line in step.split("\n") if line.strip()]

    start = statements.index("%if &_log_target_has_id. %then %do;")
    compared = statements.index("%let _log_compare_syserr = %sysfunc(max(&_log_compare_syserr., &syserr.));")
    assert statements[start:compared + 1] == [
        "%if &_log_target_has_id. %then %do;",
        "data _llmsrc._TEMP_LLM_LOGS_SEEN(keep=transaction_id model timestamp _log_seen_whole);",
        "length transaction_id varchar(*) model varchar(*);",
        "%if &_log_target_has_failed. %then %do;",
        'set _llmtgt."&_log_target_table_name."n(keep=transaction_id model timestamp failed rename=(transaction_id=_log_seen_id model=_log_seen_model));',
        "_log_seen_whole = (failed NE .);",
        "%end;",
        "%else %do;",
        'set _llmtgt."&_log_target_table_name."n(keep=transaction_id model timestamp rename=(transaction_id=_log_seen_id model=_log_seen_model));',
        "_log_seen_whole = 1;",
        "%end;",
        "transaction_id = trimn(_log_seen_id);",
        "model = trimn(_log_seen_model);",
        "if transaction_id NE '';",
        "run;",
        "%let _log_compare_syserr = &syserr.;",
        "data _llmsrc._TEMP_LLM_LOGS(drop=_log_seen_whole _log_id_whole) _llmsrc._TEMP_LLM_LOGS_GONE(keep=transaction_id model timestamp);",
        "merge _llmsrc._TEMP_LLM_LOGS(in=_log_new_row) _llmsrc._TEMP_LLM_LOGS_SEEN(in=_log_seen_row);",
        "by transaction_id model timestamp;",
        "retain _log_id_whole;",
        "if first.timestamp then _log_id_whole = 0;",
        "if _log_seen_row and _log_seen_whole then _log_id_whole = 1;",
        "if _log_new_row and not _log_seen_row then output _llmsrc._TEMP_LLM_LOGS;",
        "else if _log_new_row and last.timestamp and failed NE . and not _log_id_whole then do;",
        "output _llmsrc._TEMP_LLM_LOGS;",
        "output _llmsrc._TEMP_LLM_LOGS_GONE;",
        "end;",
        "run;",
        "%let _log_compare_syserr = %sysfunc(max(&_log_compare_syserr., &syserr.));"]
    # behind the librefs and in front of the step that appends
    assert statements.index("libname _llmsrc cas caslib='CASUSER';") < start < statements.index("data _llmtgt._TEMP_LLM_LOGS_ALL;")
    # what is left of the rows of the log is counted behind the comparison, and a run that has none left builds no table
    counted = " ".join(" ".join(statements[compared + 1:statements.index("%if &_log_append_nothing. EQ 0 %then %do;")]).split())
    assert ("call symputx('_log_kept_rows', keptRows); call symputx('_log_took_rows', tookRows); run; quit; "
            "/* Every request of the log is in the table already: no table is built, and the swap writes nothing */ "
            "%if &_log_kept_rows. EQ 0 %then %do; %if &_log_compare_syserr. EQ 0 or &_log_compare_syserr. EQ 4 %then %do; "
            "%let _log_append_nothing = 1; %end; %end; %end;") in counted
    assert "if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS' then keptRows = listedTable.Rows;" in counted
    assert "if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS_GONE' then tookRows = listedTable.Rows;" in counted
    # only where the table has the three columns a request is known by: a table of twelve columns holds nothing to
    # compare with, and neither does a table whose time is no number
    for column, kinds in (("transaction_id", "('char' 'varchar')"), ("model", "('char' 'varchar')"), ("timestamp", "('double')")):
        assert f"if lowcase(strip(Column)) EQ '{column}' and lowcase(strip(Type)) in {kinds} then knownBy + 1;" in step
    assert "if lastColumn and knownBy EQ 3 then call symputx('_log_target_has_id', '1');" in step
    assert step.count("call symputx('_log_target_has_id'") == 1 and step.count("knownBy + 1;") == 3
    # and a row of the table is only known as one without a response and without an error where failed is a number
    assert ("if lowcase(strip(Column)) EQ 'failed' and lowcase(strip(Type)) in ('double' 'int32' 'int64') "
            "then call symputx('_log_target_has_failed', '1');") in step
    before_the_macro = program[:program.index("%macro _log_handle_output_table;")]
    assert "%let _log_target_has_id = 0;\n%let _log_compare_syserr = 0;\n" in before_the_macro
    # rows that were not compared are not appended: the swap is refused with the line of an append that did not finish
    swap = " ".join(_swap(program).split())
    refused = "%if &_log_compare_syserr. NE 0 and &_log_compare_syserr. NE 4 %then %do; stagedComplete = False; %end;"
    assert swap.index("stagedRows + removedRows NE existingRows + uploadedRows then stagedComplete = False;") < swap.index(refused) \
        < swap.index("if stagedComplete then do;")
    # the table of the IDs is dropped with the rows of the log
    last = program[program.index("%else %if &_log_row_count. EQ 0 %then %do;"):program.index("%mend _log_handle_output_table;")]
    assert re.search(r"table\.dropTable /\s+casLib = 'CASUSER',\s+table = '_TEMP_LLM_LOGS_SEEN',\s+quiet = True;", last)


def test_a_run_that_appends_uploads_a_request_of_the_log_once(monkeypatch, tmp_path):
    """A file that was collected twice holds every request twice. Every run uploads the row of the first time, in the
    order of the file, and counts the others - a run that appends, a run that replaces and a run that starts a table.
    Measured on 2026-10-13 with version 2.8, which did so for a run that appends only: a run that replaced wrote
    2,048 rows from a file that held 1,024 requests twice."""
    first = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="First made-up question?")
    second = decision_block(timestamp="2026-01-05 09:10:00")
    third = embedding_block(timestamp="2026-01-05 09:20:00")
    log = _write(tmp_path, as_log(first, second, third, first, second, third))

    appended = run_parser(monkeypatch, log, table_mode="APPEND")
    assert appended.row_count == "3" and appended.sas.macro_variables["_log_row_repeats"] == "3"
    frame = appended.uploads[0]["frame"]
    assert list(frame["kind"]) == ["llm", "decision", "embedding"] and frame["transaction_id"].is_unique
    assert list(frame.index) == [0, 1, 2]

    for mode in ("REPLACE", "START"):
        other = run_parser(monkeypatch, log, table_mode=mode)
        assert other.row_count == "3" and other.sas.macro_variables["_log_row_repeats"] == "3"
        assert list(other.uploads[0]["frame"]["transaction_id"]) == list(frame["transaction_id"])
    # the request ID is no part of what a request is known by: the same request ID under two transactions is two requests
    again = llm_block(timestamp="2026-01-05 09:40:00", user_prompt="First made-up question?", request_id="a" * 32, transaction=made_up_id("one"))
    other = llm_block(timestamp="2026-01-05 09:50:00", user_prompt="First made-up question?", request_id="a" * 32, transaction=made_up_id("two"))
    run = run_parser(monkeypatch, _write(tmp_path, as_log(again, other), "same-request-id.log"), table_mode="APPEND")
    assert run.row_count == "2" and run.sas.macro_variables["_log_row_repeats"] == "0"


def test_a_request_without_a_transaction_id_is_never_taken_for_another_one(monkeypatch, tmp_path):
    unnamed = [[line for line in llm_block(timestamp=f"2026-01-05 09:{minute}:00", user_prompt="The same made-up question?",
                                           request_id="b" * 32)
                if " - transaction_" not in line and "Response Data:" not in line] for minute in (10, 20, 30)]
    log = _write(tmp_path, as_log(*unnamed, *unnamed))

    run = run_parser(monkeypatch, log, table_mode="APPEND")

    frame = run.uploads[0]["frame"]
    assert run.row_count == "6" and run.sas.macro_variables["_log_row_repeats"] == "0"
    assert bool(frame["transaction_id"].isna().all()) and set(frame["request_id"]) == {"b" * 32}


def test_the_python_sets_the_repeats_in_front_of_the_upload_and_the_row_count_behind_it():
    tree = ast_of_the_python()
    guarded = tree.body[-1]
    statements = [ast.unparse(statement) for statement in guarded.body]

    counted = statements.index("put_number('_log_row_repeats', repeats)")
    assert statements[counted - 6:counted] == [
        "named = df['transaction_id'].notna() & (df['transaction_id'] != '')",
        "whole_first = df[named].assign(_log_part=df.loc[named, 'failed'].isna()).sort_values('_log_part', kind='stable')",
        "kept = whole_first.drop_duplicates(subset=['transaction_id', 'model', 'timestamp'], keep='first').index",
        "again = named & ~df.index.isin(kept)",
        "repeats = int(again.sum())",
        "df = df[~again].reset_index(drop=True)"]
    upload = next(index for index, statement in enumerate(statements) if "conn.upload_frame(" in statement)
    assert counted < upload < len(statements) - 1 and statements[-1] == "put_number('_log_row_count', len(df))"
    # in every state of the setting: the Python does not ask for the mode of the run
    assert "_log_table_mode" not in python_block(code_file_program())


@BOTH_PROGRAMS
def test_a_run_that_appends_says_its_numbers_in_one_note(program):
    """The requests of the log, the ones that were appended, the ones that took the place of a row and the ones that
    were left out, with the rows of the table before and after: read = appended + took the place + left out, and the
    table holds what it held and what was appended, with the rows that took the place of a row in the place of the
    rows that were taken out. The swap has checked the second of the two before the table was put in place."""
    program = program()
    swap = " ".join(_swap(program).split())

    onto = ('print (note) (string) readRows || " request(s) were read from the log. " || (string) (uploadedRows - &_log_took_rows.) || '
            '" were appended to the table &_log_target_table_lib..&_log_target_table_name. " || "and &_log_took_rows. took the place of " '
            '|| (string) removedRows || " row(s) of it whose failed was missing: the table held " || (string) existingRows || " row(s) '
            'and holds " || (string) stagedRows || " now. " || (string) (readRows - uploadedRows) || " were left out, as another row '
            'holds their request: " || (string) (&_log_row_count. - uploadedRows) || " a row of the table, &_log_row_repeats. a row of '
            'the log.";')
    started = ('print (note) (string) readRows || " request(s) were read from the log. &_log_row_count. were written to the table '
               '&_log_target_table_lib..&_log_target_table_name., " || "which was not there before. &_log_row_repeats. were left out, '
               'as another row of the log holds their request.";')
    notes = swap[swap.index("%if &_log_table_mode. EQ APPEND %then %do; if tableWritten then do;"):]
    assert notes.index("readRows = &_log_row_count. + &_log_row_repeats.;") < notes.index("%if &_log_target_scope. GE 1 %then %do; " + onto) \
        < notes.index("%else %do; " + started)
    # five NOTEs: the rows a run that replaces took away, the requests a run that replaces or starts a table left
    # out, a run that appends and writes nothing, and the two of a run that appends
    assert swap.count("print (note)") == 5 and swap.index("table.save") < swap.index("print (note)")
    # what the NOTE calls appended is what the swap counted in the rows of the log behind the merge
    assert swap.index("if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS' then uploadedRows = listedTable.Rows;") < swap.index(onto)
    # no text of a message is longer than the 262 bytes SAS writes a NOTE about its quotation marks for, with room for the names
    for message in (onto, started):
        assert max(len(text) for text in re.findall(r'"([^"]*)"', message)) < 200


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
    assert "call symputx('_log_ready', '0');" in refused and "abort" not in refused.lower().replace("an abort statement", "")


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

    assert json.loads(step_definition()["ui"])["pages"][1]["children"][-1]["children"][0]["text"].startswith("* Version: 2.10 (")
    changelog = texts["textChangelog"]
    assert (changelog.index("Version: 2.10") < changelog.index("Version: 2.9") < changelog.index("Version: 2.8") < changelog.index("Version: 2.7") < changelog.index("Version: 2.6") < changelog.index("Version: 2.5") < changelog.index("Version: 2.4") < changelog.index("Version: 2.3") < changelog.index("Version: 2.2")
            < changelog.index("Version: 2.1 (") < changelog.index("Version: 2.0"))
    # What a release has not run yet is said in the changelog of the release, not in the step
    for name, text in texts.items():
        assert "not run in sas" not in text.lower(), name


def test_about_tab_says_what_version_2_4_does(monkeypatch, tmp_path):
    """The sentences of the About tab that came with version 2.4, each beside the place of the program it is about."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block())))
    texts = _about_tab()
    about, columns = texts["textAbout"], texts["textColumns"]
    step = step_program()

    # Where the next part of a file begins
    assert run.namespace["STEP_BACK"] == 1
    assert "A line that is more than one second older than a request that is running or waiting starts the next part" in columns
    assert "A line that is older by one second can be a line of the same container that was written late: it ends no request" in columns
    assert "and for a request that was running where the lines of its container end" in columns
    assert "were running where the lines of their container end" in step
    # The two cases a response is not told apart from a request in, and what is counted as content that can not be read
    assert "with a Request Headers line behind it" in columns and "with no further line that starts like a log line behind it" in columns
    assert "a request body that is nested deeper than Python reads" in columns and "a request body that is nested deeper than Python reads" in step
    assert "Options that are nested deeper than Python decodes are read as names and values" in columns and "the NOTE does not count it" in columns
    assert "options that are nested deeper than Python decodes -" not in columns
    # How a run ends that wrote an ERROR line and no table
    assert "sets the condition code of the SAS session (SYSCC) to 1012 at its end" in about and "%let SYSCC = 1012;" in step
    for path in ("a log file that is not on the SAS Server", "an output table that is no CAS table", "a log that could not be parsed",
                 "a saved table that could not be loaded", "a new table that could not be created"):
        assert path in about, path
    assert "abort" not in step.lower().replace("an abort statement", "")
    assert "a quote shows as a backtick" in about and "('\"', '`')" in step


def test_about_tab_says_what_version_2_7_does(monkeypatch, tmp_path):
    """The sentences of the About tab that came with version 2.7, each beside the place of the program it is about,
    and the sentences of version 2.6 that the program no longer does."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block())))
    columns, step = _about_tab()["textColumns"], step_program()

    # the line of the collection script: no claim that no container writes one, and no rule by its time
    assert "No container writes a line that starts like it" not in columns and "No container writes" not in step
    assert "whose time of collection is older" not in columns and "match_marker.group(3) <" not in step
    assert "The script puts a backslash in front of every line of a container that starts like its own line" in columns
    assert "Behind the first line of the script the step takes that backslash off again" in columns
    assert run.namespace["ESCAPED_LINE"].pattern == "\\\\+#=# llm-log-collection " and "lines[position] = lines[position][1:]" in step
    assert "The time of collection decides nothing: every line of that shape ends a part" in columns
    assert "What stands in front of the first line of the script was collected without it" in columns and "marked = this >= marked_from" in step
    assert "is not told from a line of the script" in columns
    # nothing is decided silently, and every request whose failed is missing is counted once
    assert "so a NOTE counts the request lines that were read that way" in columns and "line(s) that read as a request line stand in the response" in step
    assert "every request whose failed is missing is counted once" in columns
    assert "is counted in the NOTE of those requests, and not in the last number." in step
    # what the end of a part leaves open
    assert "the requests since keep their request line only, and all of them are counted in the NOTE" in columns
    assert "Where a request since has ended, nothing is taken back" in columns
    assert "in a log that holds no line of a transaction, a request that had all its first lines and ended at that request line" in columns
    assert "The score code writes each of its lines once for a request" in columns and "if key in entry['_scored']:" in step
    assert run.namespace["SCORE_COLUMNS"] == ["prompt_length", "output_length", "runtime", "response", "answer", "answer_confidence"]
    assert "(token counts, run time, response, answer)" in columns
    assert "only behind the line of that transaction that follows the score code, or with a status of 400 and above" in columns
    assert "running[0]['scored'] or (read_number(message[len('Response: Status Code'):]) or 0) >= 400" in step


def test_readme_says_of_the_line_of_the_script_what_the_parser_does():
    """Review-19: the README said that no container writes a line that starts like the line of the script, and that a
    line of that shape with an older time of collection is text. It names the version of the step that holds this program."""
    import log_parser_harness
    readme = " ".join((log_parser_harness.CODE_FILE.parent / "README.md").read_text(encoding="utf-8").split())

    assert "No container writes a line that starts like it" not in readme
    assert "whose time of collection is older than the one of the line in front of it is text" not in readme
    assert "the script puts a backslash in front of every line of a container that starts like its own line" in readme
    assert "Behind the first line of the script the parser takes that backslash off again" in readme
    assert "so is what stands in front of the first line of the script in a file that is appended to" in readme
    assert "so a NOTE counts the request lines that were read that way" in readme
    assert "Every request whose `failed` is missing is counted once" in readme
    assert "versions 2.10 and 2.4 of the two steps" in readme and _about_tab()["textChangelog"].startswith("* Version: 2.10 (")
    assert "not run in sas" not in readme.lower()


def test_about_tab_says_of_the_condition_code_what_was_measured():
    """Version 2.5. The tab said that the condition code keeps a flow or a job from ending as
    completed. What a run with SYSCC 1012 did was then measured on SAS Viya (12OCT2026): the job
    failed, the first DATA step behind the program went into syntax check mode - in the code of a
    flow that is the end action of the flow - and a reset of SYSCC let later steps run. The tab
    says that, and the comment in front of the statement does."""
    about = _about_tab()["textAbout"]
    step = step_program()

    assert "does not end as completed" not in about
    for measured in (
            "Such a run behaves like any SAS error: the job the step runs in fails, and later DATA steps of the SAS session do not run until SYSCC is reset.",
            "Measured on SAS Viya Stable 2026.09 (12OCT2026) with Log-Parser-Code.sas, which holds the program of the step, in SAS Job Execution jobs",
            "the first DATA step behind the program went into syntax check mode (OBS=0) and did not run, while macro statements and PROC CAS behind it ran",
            "With %let syscc = 0; behind the program that DATA step ran and the job ended as completed.",
            "the first DATA step behind a step is the end action of the flow itself",
            "the log held _FLW_ACTION_START_ and neither _FLW_ACTION_TABLE_ nor _FLW_ACTION_END_"):
        assert measured in about, measured
    comment = [line for line in step.split("\n") if line.startswith("* A run that wrote an ERROR line and no table ends with the condition code")]
    assert len(comment) == 1
    assert "report it as failed and not as completed" not in comment[0] and "a schedule" not in comment[0]
    assert ("as a SAS error leaves it: SAS Job Execution reports the job as failed, and in the SAS session of a job the DATA steps behind the "
            "program do not run until the condition code is set back to 0 - in the code of a flow the first of them is the end action of the flow.") in comment[0]
    # the same comment stands in the code file, which holds the program of the step
    assert comment[0] in code_file_program()
    assert "* Version: 2.5 (12OCT2026) - The About tab says of a run that ends with the condition code 1012 what was measured" in (
        _about_tab()["textChangelog"])


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
    r"lines are mixed with theirs in the file\. The first lines of a request, the lines of the score code and the `Response:` line "
    r"name no request, so a line that the log does not decide was given to none of them: (\d+) request\(s\) can have missing "
    r"values, and failed is missing for (\d+) request\(s\)\. A request that was running or waiting where the lines of its "
    r"container end is counted in the NOTE of those requests, and not in the last number\.")


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
    r"of it: where the collection script wrote its line in front of the lines of the next container, or, in a file without "
    r"such lines, where the time of the lines goes back\. The log holds neither a response nor an error for them, and failed "
    r"is missing for them\.")


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
    # failed is missing for both, and both are counted where they belong: one was running and one was waiting where
    # the file ends - no row can be named for either, and the count is right all the same
    assert _overlap_counts(run.sas.messages) == (2, 2, 0) and _end_counts(run.sas.messages) == 2


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
    assert "`Request: POST` was found in 2 line(s)" in warning
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
    there is not called failed, and neither is the one whose first lines broke off at its
    request line - the two wrote their first lines at the same time, and the lines that
    would have shown it are not in this part. The NOTE counts both. Before, the first one
    was failed = 1 without a word, although the file holds its response further down.
    Nothing has ended since the first lines broke off, so the request since keeps its
    request line only and both are counted with the requests that can have missing values."""
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
    assert missing(frame.loc[0, "failed"]) and missing(frame.loc[1, "failed"])
    assert _end_counts(run.sas.messages) == 2 and _overlap_counts(run.sas.messages) == (2, 2, 0)


def test_request_whose_first_lines_broke_off_failed_where_the_request_behind_it_was_answered(monkeypatch, tmp_path):
    """What stays as it was: first lines that break off at the request line of a request that
    is answered are the first lines of a request that failed, and no NOTE is written for it.
    Only where the lines of the container end on the request behind it is nothing said."""
    broken_off = log_units(_numbered(1))[:2]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines_of(broken_off), llm_block(timestamp="2026-01-05 09:20:00", user_prompt="answered"))))
    frame = run.uploads[0]["frame"]
    assert list(frame["failed"]) == [1, 0] and run.sas.messages == []

    # The same first lines in front of a request the file ends on: nothing has ended since they broke off, so the
    # 'Request Data:' line behind them can be the line of either request - see the two files further down. The
    # request since keeps its request line only
    running = split_at_scoring(llm_block(timestamp="2026-01-05 09:20:00", user_prompt="still running"))[0]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines_of(broken_off), running), "ends.log"))
    frame = run.uploads[0]["frame"]
    assert missing(frame.loc[0, "failed"]) and missing(frame.loc[1, "failed"])
    assert _end_counts(run.sas.messages) == 2 and _overlap_counts(run.sas.messages) == (2, 2, 0)
    assert missing(frame.loc[1, "user_prompt"]) and missing(frame.loc[1, "transaction_id"]) and "still running" not in frame.to_csv()

    # And with an answered request in between: the first one failed, the last one is the only one the file ends on
    answered = llm_block(timestamp="2026-01-05 09:18:00", user_prompt="answered")
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines_of(broken_off), answered, running), "between.log"))
    frame = run.uploads[0]["frame"]
    assert list(frame["failed"])[:2] == [1, 0] and missing(frame.loc[2, "failed"]) and _end_note(run) == 1


@pytest.mark.parametrize("back", [2, 5, 30])
@pytest.mark.parametrize("late", range(1, 32))
def test_line_that_is_seconds_older_than_a_request_line_in_front_of_it_gives_no_value_away(monkeypatch, tmp_path, late, back):
    """The same two requests with one line that is older than the newest request line in
    front of it by more than a line of a container is written late. No container writes
    that and no collection puts it together: the part ends there, and what follows is read
    as the lines of the next one. Whatever the line is, no request gets a value of the
    other one, a request that was answered is not called failed, and a row that lacks a
    value is counted in a NOTE. A transaction whose later lines are younger than a request
    line is still running at that request line, also where its next line is older."""
    units = _two_that_overlap()
    moments = [datetime(2026, 1, 5, 9, 15, 0).timestamp() + position for position in range(len(units))]
    newest_request = max(position for position in range(late) if " - Request: POST" in units[position][1][0])
    moments[late] = moments[newest_request] - back
    lines = [line for moment, (_, unit) in zip(moments, units) for line in _stamped(unit, datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M:%S"))]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines)))

    frame = run.uploads[0]["frame"]
    # A request line that is older than the request in front of it starts the next part, which is read in its order
    order = (1, 2) if len(frame) == 2 else (1,)
    assert len(frame) == 2 or " - Request: POST" in units[late][1][0]
    lacking = 0
    for number, (_, row) in zip(order, frame.iterrows()):
        found = _numbers_in(row)
        assert set(found.values()) <= {number}, (number, found)
        assert missing(row["failed"]) or row["failed"] == 0, (number, row["failed"])
        lacking += 1 if missing(row["failed"]) or not ANSWERED_HAS <= set(found) else 0
    assert lacking == 0 or _counted(run.sas.messages) >= lacking


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


# What the parser writes for a response whose text holds a request line that is then not known: the response is missing
UNKNOWN_RESPONSE_NOTE = ("1 request(s) have no response: a line that reads as a request line stands in their response, in front of the "
                         "next line of their transaction, and nothing behind it in its part says whether it is a line of the response or "
                         "a request that came in while the response was written and that the end of the part cut off. It was read as a "
                         "line of the response.")
# What the parser writes where it read a request line in a response as a line of that response: the reading is not
# the only one, so it is counted
QUOTED_LINE_NOTE = ("%d line(s) that read as a request line stand in the response of another request, in front of the next line of "
                    "its transaction, and got no line of their own - no transaction, no error, no status and no response - while a "
                    "request that came later in their part got its transaction. They were read as lines of that response, and no row "
                    "was written for them. A request whose container wrote nothing but its first lines there reads the same.")
QUOTED_TIMES = ["2026-01-05 09:15:00", "2026-01-05 09:15:10", "2026-01-05 09:15:30", "2026-01-05 09:14:59", "2026-01-05 09:14:58",
                "2026-01-05 08:00:00", "2026-01-05 09:15:31", "2026-01-06 09:15:00"]
QUOTED_TIME_IDS = ["with the time of the request line", "with a time while the request ran", "with the time of the Response line",
                   "one second before the request line", "two seconds before the request line", "an hour before",
                   "one second after the Response line", "a day later"]


@pytest.mark.parametrize("headers", [True, False], ids=["with a Request Headers line behind it", "without one"])
@pytest.mark.parametrize("quoted_time", QUOTED_TIMES, ids=QUOTED_TIME_IDS)
def test_response_that_quotes_a_request_line_is_whole_where_a_later_request_got_its_transaction(monkeypatch, tmp_path, quoted_time, headers):
    """A response that holds a request line - with a 'Request Headers:' line behind it, or with no
    further line that starts like a log line up to the next line of the transaction - at the
    start of a line as a container writes them, with any time. The line gets no transaction,
    no error, no status and no response of its own, and a request that came later got its
    transaction, which a container gives a request at once: it is a line of the response. The
    response is whole, no row is written for the quoted line, and the request keeps its status
    - also where the quoted time is older or younger than the request. Up to release 2.3.0 of
    before this round one more row was written and the response ended in front of the quoted
    lines, and with an earlier or a later time the status of the request was missing."""
    quoted = [f"{quoted_time} - api - INFO - Request: POST /demo_llm"]
    if headers:
        quoted.append(f"{quoted_time} - api - INFO - Request Headers: Host: quoted.example.com")
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(finished="2026-01-05 09:15:30", response="Like this:\n" + "\n".join(quoted)),
        llm_block(timestamp="2026-01-07 10:00:00", user_prompt="the next request", response="the next answer"))))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 2
    assert frame.loc[0, "response"] == "Like this: " + " ".join(quoted)
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (21, 200, 0)
    assert (frame.loc[1, "user_prompt"], frame.loc[1, "response"], frame.loc[1, "status_code"], frame.loc[1, "failed"]) == (
        "the next request", "the next answer", 200, 0)
    # the reading is said, with the number of request lines it was made for (review-19: nothing is decided silently)
    assert run.sas.messages == [("NOTE", QUOTED_LINE_NOTE % 1)]


def test_a_request_that_wrote_only_its_first_lines_inside_a_response_reads_like_a_quoted_one(monkeypatch, tmp_path):
    """Review-19 B3: a request that came in while the response of another one was written, whose container wrote its
    request line, its headers and its request data and nothing more - it got no transaction, no error and no status -,
    and the container went on with a third request. Built that way and built as a response that quotes those three
    lines, the file is the same byte for byte, so the parser can not know which it is. It reads the lines as lines of
    the response, writes no row for them, and says in a NOTE how many request lines it read that way."""
    first = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="question A", response="answer A")
    response_line = next(index for index, line in enumerate(first) if " - scoreModel - INFO - response: " in line)
    head_only = lines_of(log_units(llm_block(timestamp="2026-01-05 09:00:00", user_prompt="the request that got no answer"))[:3])
    third = llm_block(timestamp="2026-01-05 09:01:00", user_prompt="question C", response="answer C")
    a_request = as_log(first[:response_line + 1], head_only, first[response_line + 1:], third)
    a_quote = as_log(llm_block(timestamp="2026-01-05 09:00:00", user_prompt="question A", response="answer A\n" + "\n".join(head_only)), third)
    assert a_request == a_quote

    run = run_parser(monkeypatch, _write(tmp_path, a_request))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["question A", "question C"]
    assert frame.loc[0, "response"] == " ".join(["answer A"] + [line.strip() for line in head_only])
    assert list(frame["status_code"]) == [200, 200] and list(frame["failed"]) == [0, 0]
    assert run.sas.messages == [("NOTE", QUOTED_LINE_NOTE % 1)]


@pytest.mark.parametrize("behind, quoted_as_text", [([], False), (["that was it"], False),
                                                    (["2026-01-05 09:15:00 - api - INFO - Request Headers: Host: quoted.example.com"], False),
                                                    (["2026-01-05 09:15:00 - scoreModel - INFO - prompt_length: 99999"], True),
                                                    (["that was it", "2026-01-05 09:15:00 - scoreModel - INFO - prompt_length: 99999"], True)],
                         ids=["nothing", "a line of text", "a Request Headers line", "a line that starts like a log line",
                              "text and a line that starts like a log line"])
def test_request_line_at_the_end_of_a_response_with_nothing_behind_it_leaves_the_response_unknown(monkeypatch, tmp_path, behind, quoted_as_text):
    """The case the lines do not tell apart: a request line in a response, at the end of the
    file - or of its part -, with nothing behind it that says whether it is a line of the
    response or a request that came in while the response was written and that the end of the
    file cut off before its transaction began. Two files with these lines, one of a model that
    quoted them and one of a container whose collection ended there, are the same line for
    line (test_the_two_files_that_need_different_answers_are_the_same_line_for_line). It is
    read as a line of the response and no row is written for it; the response is missing, and
    a NOTE counts it - the request keeps its status. A line that starts like a log line behind
    it and is no first line of a request makes it text, as before."""
    quoted = ["2026-01-05 09:15:00 - api - INFO - Request: POST /x"] + behind
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(response="Like this:\n" + "\n".join(quoted)))))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 1
    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"], frame.loc[0, "prompt_length"]) == (200, 0, 21)
    if quoted_as_text:
        assert frame.loc[0, "response"] == "Like this: " + " ".join(quoted)
        assert run.sas.messages == []
    else:
        assert missing(frame.loc[0, "response"])
        assert "that was it" not in frame.to_csv() and "/x" not in frame.to_csv()
        assert run.sas.messages == [("NOTE", UNKNOWN_RESPONSE_NOTE)]


def _quoted_or_cut_off():
    """The lines of a response whose text ends with a request line and its 'Request Headers:'
    line, and the rest of the request, up to the end of the file: what a model writes that
    quotes them, and what a container writes for a request that came in while the response was
    written, whose transaction would begin behind the end of the collection."""
    response = llm_block(response="The answer.")
    response_line = next(index for index, line in enumerate(response) if " - scoreModel - INFO - response: " in line)
    request = llm_block(model="demo_llm", user_prompt="a request that came in")
    return response[:response_line + 1], request[:8], response[response_line + 1:]


def test_the_two_files_that_need_different_answers_are_the_same_line_for_line(monkeypatch, tmp_path):
    """The proof that the case above is not decided by any rule: a container that got a request
    while it wrote the response of another one, collected before the transaction of the request
    began, writes the response line, the request line, its 'Request Headers:' and 'Request Data:'
    lines and the two 'Randomly generated' lines, and the rest of the first request. A model that
    quotes those lines in its response gives the same lines. Built both ways the two files are
    the same, byte for byte - and one needs a row for the request that came in and the response
    'The answer.', the other no such row and the response with the quoted lines. Neither row
    nor response is written then: the parser writes no value of the other reading."""
    head, request, tail = _quoted_or_cut_off()
    cut_off = as_log(head, request, tail)
    # The model answers with the lines of the request behind its first sentence
    quoted = as_log(llm_block(response="The answer.\n" + "\n".join(request)))
    assert cut_off == quoted

    run = run_parser(monkeypatch, _write(tmp_path, cut_off))
    frame = run.uploads[0]["frame"]
    assert len(frame) == 1
    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (200, 0) and missing(frame.loc[0, "response"])
    assert "a request that came in" not in frame.to_csv()
    assert run.sas.messages == [("NOTE", UNKNOWN_RESPONSE_NOTE)]


def test_request_that_came_in_during_a_response_and_got_its_transaction_stays_a_request(monkeypatch, tmp_path):
    """The other side of the rule: the same lines, and the transaction of the request that came
    in begins behind the end of the first request. It is a request, the response ends in front
    of its request line, and both requests have all their values."""
    head, request, tail = _quoted_or_cut_off()
    rest = llm_block(model="demo_llm", user_prompt="a request that came in", response="the second answer")[8:]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(head, request, tail, rest)))

    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["What is the capital of Austria?", "a request that came in"]
    assert (frame.loc[0, "response"], frame.loc[0, "status_code"], frame.loc[0, "failed"]) == ("The answer.", 200, 0)
    assert (frame.loc[1, "response"], frame.loc[1, "status_code"], frame.loc[1, "failed"]) == ("the second answer", 200, 0)


def test_a_quoted_request_line_that_scores_nothing_leaves_the_response_unknown(monkeypatch, tmp_path):
    """A request line for a GET - the health check of a container - gets no transaction whether it is a request
    or a line of the response: nothing decides it, also where later requests got their transactions."""
    quoted = ["2026-01-05 09:15:00 - api - INFO - Request: GET /openapi.json",
              "2026-01-05 09:15:00 - api - INFO - Request Headers: Host: quoted.example.com"]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(
        llm_block(response="Like this:\n" + "\n".join(quoted)),
        llm_block(timestamp="2026-01-05 09:16:00", user_prompt="the next request", response="the next answer"))))

    frame = run.uploads[0]["frame"]
    assert len(frame) == 2 and missing(frame.loc[0, "response"]) and (frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (200, 0)
    assert frame.loc[1, "response"] == "the next answer"
    assert run.sas.messages == [("NOTE", UNKNOWN_RESPONSE_NOTE)]


def test_a_response_data_line_of_another_transaction_ends_a_response(monkeypatch, tmp_path):
    """The 'Response Data:' line of a request whose 'Response:' line came earlier can be written while the
    response of the next request is: it names its transaction, and it is no text of that response."""
    first = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="question A", response="answer A")
    second = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="question B", response="answer B")
    response_data = first[-1]
    assert response_data.startswith("2026-01-05 09:00:00 - api - INFO - Response Data: ")
    response_line = next(index for index, line in enumerate(second) if " - scoreModel - INFO - response: " in line)
    run = run_parser(monkeypatch, _write(tmp_path, as_log(first[:-1], second[:response_line + 1], [response_data],
                                                          second[response_line + 1:])))

    frame = run.uploads[0]["frame"]
    assert list(frame["response"]) == ["answer A", "answer B"]
    assert list(frame["status_code"]) == [200, 200]


# ---------------------------------------------------------------------------
# The line the collection script writes in front of the lines of every container
# ---------------------------------------------------------------------------

def collected(pod, at="2026-01-05T10:00:00Z", container="scr"):
    return f"#=# llm-log-collection pod={pod} container={container} collected={at} #=#"


def _serial_block(serial, timestamp="2026-01-05 09:00:00"):
    return llm_block(timestamp=timestamp, system_prompt=f"System text {serial}.", user_prompt=f"Question number {serial}?",
                     prompt_length=1000 + serial, output_length=5000 + serial, run_time=100.5 + serial,
                     response=f"Answer number {serial} of the model.")


def _restamped(lines, stamp):
    return [stamp + line[19:] if is_log_line(line) else line for line in lines]


def is_log_line(line):
    return line[:4].isdigit() and line[4:5] == "-" and " - " in line


def test_a_part_that_ends_on_a_running_request_ends_it_at_the_line_of_the_collection_script(monkeypatch, tmp_path):
    """Review-16 and review-18: a part that ends on a running request, followed by a part whose lines are younger
    than that request line - the time does not go back, and before this release the score lines of the next part
    were taken for the lines of the request that was running. With the line of the collection script the request
    ends there, cut off: failed is missing, the NOTE counts it, and the request of the next part has its own values."""
    running = _serial_block(1, "2026-01-05 09:00:00")
    scoring = next(index for index, line in enumerate(running) if " - scoreModel - INFO - prompt_length: " in line)
    nxt = _restamped(_serial_block(2), "2026-01-05 09:00:30")
    text = as_log([collected("demo-llm-1")], running[:scoring + 1], [collected("demo-llm-2")], nxt)

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["Question number 1?", "Question number 2?"]
    assert frame.loc[0, "prompt_length"] == 1001 and all(missing(frame.loc[0, column]) for column in ("output_length", "runtime", "response",
                                                                                                       "status_code", "failed"))
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "output_length"], frame.loc[1, "response"], frame.loc[1, "failed"]) == (
        1002, 5002, "Answer number 2 of the model.", 0)
    assert _end_note(run) == 1
    # the same lines without the two lines of the collection script: the time does not go back, so the file is one part,
    # and the request that was cut off is a request whose container went on without answering it - failed = 1
    plain = run_parser(monkeypatch, _write(tmp_path, as_log(running[:scoring + 1], nxt), name="plain.log"))
    assert plain.uploads[0]["frame"].loc[0, "failed"] == 1


def test_nothing_that_was_read_crosses_the_line_of_the_collection_script(monkeypatch, tmp_path):
    """A part that begins with the rest of a transaction whose request line is in front of the collection is followed
    to its end and given to no request - also where a request of the part waits for its transaction."""
    earlier = _serial_block(1, "2026-01-05 09:00:00")
    transaction = next(index for index, line in enumerate(earlier) if "The data was successfully formatted" in line)
    waiting = lines_of(log_units(_serial_block(2, "2026-01-05 09:00:00"))[:5])   # the request line and its four first lines
    text = as_log([collected("demo-llm-1")], waiting,                                     # the first part: request 2 has its first lines
                  [collected("demo-llm-1", "2026-01-05T11:00:00Z")], earlier[transaction + 1:])   # the next one: the rest of request 1

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["Question number 2?"]
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "response", "status_code", "failed"))


@pytest.mark.parametrize("back", [2, 5, 30])
def test_a_line_that_is_seconds_older_inside_a_part_of_a_collection_ends_nothing(monkeypatch, tmp_path, back):
    """Review-18: two requests of one container that overlap, with lines 2 to 30 seconds older than the request line
    in front of them. Without the line of the collection script such a line is the first line of the next part
    (the time rule, which stays for those files). With it the part is one container, the order of the lines decides,
    and no row gets failed = 1 or a value of the other request."""
    a, b = log_units(_numbered(1)), log_units(_numbered(2))
    order = [(a, 0), (a, 1), (a, 2), (a, 3), (a, 4), (a, 5), (a, 6), (a, 7), (b, 0), (b, 1), (b, 2), (b, 3), (b, 4)] + \
            [(a, k) for k in range(8, 16)] + [(b, k) for k in range(5, 16)]
    stamps = []
    for position, _ in enumerate(order):
        moment = datetime(2026, 1, 5, 9, 0, 0) + timedelta(seconds=position)
        if position == 14:                       # a line of request 1 written late
            moment -= timedelta(seconds=back + 1)
        stamps.append(moment.strftime("%Y-%m-%d %H:%M:%S"))
    lines = [line for (units, k), stamp in zip(order, stamps) for line in _restamped(units[k], stamp)]

    run = run_parser(monkeypatch, _write(tmp_path, as_log([collected("demo-llm-1")], lines)))
    frame = run.uploads[0]["frame"]
    for row, serial in zip(frame.itertuples(), (1, 2)):
        assert row.failed != 1
        found = _numbers_in(frame.loc[row.Index])
        assert set(found.values()) <= {serial}, (serial, found)
    assert list(frame["failed"]) == [0, 0]


def _running(serial, timestamp):
    """A request whose container was collected while it ran: its lines up to its first line of the score code."""
    block = _serial_block(serial, timestamp)
    return block[:next(index for index, line in enumerate(block) if " - scoreModel - INFO - prompt_length: " in line) + 1]


@pytest.mark.parametrize("look_alike_time", ["2020-01-01T00:00:00Z", "2026-01-05T10:00:00Z", "2099-12-31T23:59:59Z"],
                         ids=["an earlier time", "the time of the run", "a later time"])
def test_a_line_of_the_script_behind_a_look_alike_still_ends_its_part(monkeypatch, tmp_path, look_alike_time):
    """Review-19 B1. Up to version 2.6 a line of the shape whose time of collection was older than the one of the line
    of the shape in front of it was text. So a line with a later time in a response - a model writes what a user
    asks it for - ended the part in the middle of the response, and every line of the script behind it was text: the
    containers behind it were read as one part. The time of collection decides nothing any more. The file here was
    written without the script of this release, which would have put a backslash in front of the look-alike (next
    test): the look-alike ends a part - which no code can know better, see the two files further down - and the line
    of the script behind it ends its part as it has to, whatever the time of the look-alike. Before, with the later
    time, the request of the second container had no token counts, no run time and no response."""
    look_alike = collected("made-up", look_alike_time)
    text = as_log([collected("demo-llm-1")], llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first", response="Here you go:\n" + look_alike + "\nline two"),
                  _running(2, "2026-01-05 09:00:10"), [collected("demo-llm-2")], _restamped(_serial_block(3), "2026-01-05 09:00:30"))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["first", "Question number 2?", "Question number 3?"]
    assert (frame.loc[2, "prompt_length"], frame.loc[2, "output_length"], frame.loc[2, "runtime"], frame.loc[2, "response"],
            frame.loc[2, "status_code"], frame.loc[2, "failed"]) == (1003, 5003, 103.5, "Answer number 3 of the model.", 200, 0)
    assert frame.loc[1, "prompt_length"] == 1002 and missing(frame.loc[1, "failed"])
    # the first request ends at the look-alike, as a request does that the lines of its container end on
    assert frame.loc[0, "response"] == "Here you go:" and missing(frame.loc[0, "status_code"]) and missing(frame.loc[0, "failed"])
    assert _end_note(run) == 2


def test_a_line_with_a_backslash_in_front_is_a_line_of_a_container(monkeypatch, tmp_path):
    """What the collection script of this release does with a line of a container that starts like its own line: one
    more backslash in front, also where the line had backslashes of its own. Behind the first line of the script the
    parser takes one backslash off again: the line is text, it ends no part, and the response holds it as the model
    wrote it. A file without a line of the script was not written by that script, and neither was what stands in
    front of the first one: there a backslash is part of the line."""
    look_alike = collected("made-up", "2099-12-31T23:59:59Z")
    written = ["Here you go:", "\\" + look_alike, "\\\\" + look_alike, "line two"]
    escaped = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first", response="\n".join(written))
    text = as_log([collected("demo-llm-1")], escaped, _running(2, "2026-01-05 09:00:10"),
                  [collected("demo-llm-2")], _restamped(_serial_block(3), "2026-01-05 09:00:30"))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert frame.loc[0, "response"] == "Here you go: " + look_alike + " \\" + look_alike + " line two"
    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (200, 0)
    assert missing(frame.loc[1, "failed"]) and frame.loc[2, "response"] == "Answer number 3 of the model." and _end_note(run) == 1
    # the same rows as for the same lines without the two look-alikes
    without = as_log([collected("demo-llm-1")], llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first", response="Here you go:\nline two"),
                     _running(2, "2026-01-05 09:00:10"), [collected("demo-llm-2")], _restamped(_serial_block(3), "2026-01-05 09:00:30"))
    plain = run_parser(monkeypatch, _write(tmp_path, without, name="without.log")).uploads[0]["frame"]
    assert frame.drop(columns="response").equals(plain.drop(columns="response")) and list(frame["response"])[1:] == list(plain["response"])[1:]

    # no line of the script in the file: nothing is taken off
    alone = run_parser(monkeypatch, _write(tmp_path, as_log(escaped), name="alone.log")).uploads[0]["frame"]
    assert alone.loc[0, "response"] == " ".join(written)
    # in front of the first line of the script: nothing is taken off either
    head = run_parser(monkeypatch, _write(tmp_path, as_log(escaped, [collected("demo-llm-2")], _serial_block(3)), name="head.log")).uploads[0]["frame"]
    assert head.loc[0, "response"] == " ".join(written) and head.loc[1, "response"] == "Answer number 3 of the model."


def _unmarked_head():
    """Lines as the script of release 2.2.0 collected them, without a line of its own: a container whose lines end on a
    running request, and the lines of the next container, which are an hour older."""
    return as_log(_serial_block(1, "2026-01-05 09:00:00"), _running(2, "2026-01-05 09:00:10"), _restamped(_serial_block(3), "2026-01-05 08:00:00"))


def test_lines_in_front_of_the_first_line_of_the_script_are_read_by_their_time(monkeypatch, tmp_path):
    """Review-19 B2: a file that is appended to holds, across the upgrade, the lines the script of release 2.2.0
    collected and behind them the parts this release collects. Version 2.6 read such a file without the time rule from
    its first line on: the parts of the head were not told apart, and the request of its second container had no token
    counts, no run time and no response. The head is read by the time of its lines now, as it is read alone, and it
    ends at the first line of the script."""
    alone = run_parser(monkeypatch, _write(tmp_path, _unmarked_head(), name="alone.log"))
    appended = run_parser(monkeypatch, _write(tmp_path, _unmarked_head() + as_log(
        [collected("demo-llm-1")], _restamped(_serial_block(4), "2026-01-05 09:30:00"), _running(5, "2026-01-05 09:31:00"),
        [collected("demo-llm-2")], _restamped(_serial_block(6), "2026-01-05 09:00:00"))))

    head, frame = alone.uploads[0]["frame"], appended.uploads[0]["frame"]
    assert list(head["failed"])[0::2] == [0, 0] and missing(head.loc[1, "failed"]) and _end_note(alone) == 1
    assert (head.loc[2, "prompt_length"], head.loc[2, "response"]) == (1003, "Answer number 3 of the model.")
    assert frame.iloc[:3].equals(head)
    # behind the first line of the script the parts end at its lines: request 6 is an hour older than request 5 and has its values
    assert list(frame["user_prompt"])[3:] == ["Question number 4?", "Question number 5?", "Question number 6?"]
    assert list(frame["response"])[3:] == ["Answer number 4 of the model.", None, "Answer number 6 of the model."]
    assert _end_note(appended) == 2


def test_a_running_request_of_the_head_ends_at_the_first_line_of_the_script(monkeypatch, tmp_path):
    """The head has no line of its own at its end: it ends where the script wrote its first line. A request that is
    running there is cut off, also where the lines behind that line are younger - the time rule would not see it."""
    text = as_log(_serial_block(1, "2026-01-05 09:00:00"), _running(2, "2026-01-05 09:00:10"),
                  [collected("demo-llm-1")], _restamped(_serial_block(3), "2026-01-05 09:00:30"))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert frame.loc[1, "prompt_length"] == 1002 and all(missing(frame.loc[1, column]) for column in ("output_length", "response", "failed"))
    assert (frame.loc[2, "prompt_length"], frame.loc[2, "output_length"], frame.loc[2, "failed"]) == (1003, 5003, 0)
    assert _end_note(run) == 1


def test_a_look_alike_in_lines_collected_without_the_script_is_two_files_in_one(monkeypatch, tmp_path):
    """The proof for what stays of B1 and B2, in lines that were collected without the script of this release: two files
    that are the same byte for byte and need different rows.

    One is a file of the script of release 2.2.0 in which a model answered with three lines, the second of them of
    the shape of the line of the script. It needs one row with the whole response and the status 200.
    The other is a file that is appended to: the collection of release 2.2.0 ended behind the first line of that
    response, and the first collection of this release wrote its line and what the container had written since - the
    second line of the response and the rest of the request. It needs a request that is cut off at the line of the
    script, and a response that does not hold that line.

    The parser reads the line as the line of the script: what a look-alike in such lines can do is end the response
    it stands in, leave its request without a status and with failed missing - counted in the NOTE - and have the
    lines behind it read as one part up to the next line of the shape, without the time rule."""
    look_alike = collected("demo-llm-1", "2026-01-05T10:00:00Z")
    a_response_of_three_lines = as_log(llm_block(response="Here you go:\n" + look_alike + "\nline two"))
    container = as_log(llm_block(response="Here you go:\nline two")).split("\n")
    ended_here = next(index for index, line in enumerate(container) if line.endswith(" - scoreModel - INFO - response: Here you go:"))
    an_appended_file = "\n".join(container[:ended_here + 1] + [look_alike] + container[ended_here + 1:])
    assert a_response_of_three_lines == an_appended_file

    run = run_parser(monkeypatch, _write(tmp_path, an_appended_file))
    frame = run.uploads[0]["frame"]
    assert len(frame) == 1 and frame.loc[0, "prompt_length"] == 21 and frame.loc[0, "response"] == "Here you go:"
    assert missing(frame.loc[0, "status_code"]) and missing(frame.loc[0, "failed"]) and _end_note(run) == 1


def test_a_file_without_the_lines_of_the_collection_script_is_read_as_before(monkeypatch, tmp_path):
    """The same requests with and without the lines: one part each, the rows the same."""
    text = as_log(_serial_block(1, "2026-01-05 09:00:00"), _serial_block(2, "2026-01-05 09:01:00"))
    with_lines = as_log([collected("demo-llm-1")], _serial_block(1, "2026-01-05 09:00:00"), _serial_block(2, "2026-01-05 09:01:00"))

    plain = run_parser(monkeypatch, _write(tmp_path, text)).uploads[0]["frame"]
    marked = run_parser(monkeypatch, _write(tmp_path, with_lines, name="marked.log")).uploads[0]["frame"]
    assert plain.equals(marked) and len(plain) == 2


def _stale_time_file():
    """The smallest file of the rule review-18 found (p_gen2 'parts', 10 of 600 files): a part whose requests could
    not be told apart for a while ends while nothing runs; the next part begins 50 seconds earlier with two requests
    whose first lines interleave, then a third request line. The time of the newest request that could not be told
    apart was kept from the first part, so a line of the second part read as older by more than a line is written
    late: a part began that is none, and the third request took the transaction of the first one of its part."""
    def units(serial):
        return log_units(_serial_block(serial))

    def at(unit, stamp):
        return [stamp + unit[0][19:]] + unit[1:]

    b, c, d, e, f = units(2), units(3), units(4), units(5), units(6)
    out = []
    first, second = "2026-01-05 09:00:", "2026-01-05 08:59:"
    out += [at(b[0], first + "05"), at(b[1], first + "05"), at(c[0], first + "07"), at(b[2], first + "07")]
    out += [at(c[k], first + "08") for k in range(1, 5)] + [at(b[k], first + "08") for k in range(3, 5)]
    out += [at(b[k], first + "10") for k in range(5, 16)] + [at(c[k], first + "20") for k in range(5, 16)]
    out += [at(d[0], second + "30"), at(d[1], second + "30"), at(e[0], second + "31"), at(d[2], second + "31"),
            at(e[1], second + "31"), at(e[2], second + "31")]
    out += [at(d[k], second + "32") for k in range(3, 5)] + [at(e[k], second + "32") for k in range(3, 5)]
    out += [at(f[0], second + "33")] + [at(d[k], second + "35") for k in range(5, 16)]
    out += [at(f[k], second + "40") for k in range(1, 16)] + [at(e[k], second + "45") for k in range(5, 16)]
    return "".join(line + "\n" for unit in out for line in unit)


def test_a_part_that_begins_while_nothing_runs_keeps_no_time_of_the_part_in_front_of_it(monkeypatch, tmp_path):
    """Before: the row of request 6 had the transaction id, the token counts, the run time and the response of
    request 4. Now no row has a value of another request; the requests that could not be told apart have missing
    values, which the NOTE counts."""
    run = run_parser(monkeypatch, _write(tmp_path, _stale_time_file()))
    frame = run.uploads[0]["frame"]

    assert list(frame["user_prompt"].fillna("-")).count("-") >= 0 and len(frame) == 5
    for row, serial in zip(frame.itertuples(), (2, 3, 4, 5, 6)):
        found = _numbers_in(frame.loc[row.Index])
        assert set(found.values()) <= {serial}, (serial, found)
        assert row.transaction_id is None or row.transaction_id == made_up_id(
            "2026-01-05 09:00:00", "demo_llm", llm_inputs(f"System text {serial}.", f"Question number {serial}?")), serial
    assert _overlap_counts(run.sas.messages)[0] == 5


# ---------------------------------------------------------------------------
# What the end of a part leaves open (review-19 and gate-19, B4 and B5)
# ---------------------------------------------------------------------------

def _first_lines(serial, model="demo_llm", units=5):
    """The request line of a request and its first lines: all four with units=5."""
    return lines_of(log_units(llm_block(timestamp="2026-01-05 09:00:00", model=model, system_prompt=f"System text {serial}.",
                                        user_prompt=f"Question number {serial}?"))[:units])


@pytest.mark.parametrize("models", [("demo_llm_0", "demo_llm_1"), ("demo_llm", "demo_llm")], ids=["two models", "one model"])
@pytest.mark.parametrize("ending", ["a line of the collection script", "the end of the file"])
def test_request_that_ended_at_the_request_line_of_a_request_the_lines_end_on_is_not_called_failed(monkeypatch, tmp_path, models, ending):
    """Review-19 B4. A file without a single line of a transaction - the lines of every container end before one - is
    read by the order of its lines, and a request is over at the next request line. Where the lines of the container
    end on that next request, no line shows that the container went on: both were running there. Before, the first
    of two such requests was failed = 1 - in 1 of 2,400 generated files with the lines of the collection script,
    against the guide's 'no cut-off request with failed = 1'."""
    behind = [[collected("demo-llm-2")], _first_lines(3, models[0])] if ending == "a line of the collection script" else []
    text = as_log([collected("demo-llm-1")], _first_lines(1, models[0]), _first_lines(2, models[1]), *behind)

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == [f"Question number {serial}?" for serial in range(1, len(frame) + 1)]
    assert all(missing(value) for value in frame["failed"])
    assert _end_note(run) == len(frame)


def test_request_that_ended_at_the_request_line_of_an_answered_request_failed_as_before(monkeypatch, tmp_path):
    """The other side: in a log that names no transaction, a request without a response whose container answered the
    next request did fail."""
    answered = [line for line in _serial_block(2, "2026-01-05 09:01:00") if " - transaction_" not in line]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(_first_lines(1), answered)))

    frame = run.uploads[0]["frame"]
    assert list(frame["failed"]) == [1, 0] and frame.loc[1, "response"] == "Answer number 2 of the model." and run.sas.messages == []


def test_requests_that_overlap_and_are_cut_off_are_counted_with_the_requests_the_lines_end_on(monkeypatch, tmp_path):
    """Gate-19: two requests of a container that are both running where its lines end. They ran at the same time, and
    up to version 2.6 the NOTE of the requests that ran at the same time counted them under 'failed is missing' while
    the NOTE of the requests the lines end on did not count them. Every request with failed missing is counted once
    now, in the NOTE of its reason."""
    first, second = log_units(_serial_block(1)), log_units(_serial_block(2))
    text = as_log([collected("demo-llm-1")], lines_of(first[:8]), lines_of(second[:8]), lines_of(first[8:10]),
                  [collected("demo-llm-2")], _serial_block(3))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert [missing(value) for value in frame["failed"]] == [True, True, False]
    # two ran at the same time, the two lines of the score code were given to neither, and no response is missing but
    # the ones the end of the part cut off
    assert _overlap_counts(run.sas.messages) == (2, 2, 0) and _end_counts(run.sas.messages) == 2


def _interleaved(*units):
    return lines_of(list(units))


def test_first_lines_behind_a_break_are_given_to_no_request_where_nothing_has_ended_since(monkeypatch, tmp_path):
    """The one class of rows with a value of another request that the generators of the review gave in files with
    the lines of the collection script (13 of 42,000 files of five seeds): the first lines of two requests interleave -
    request line and headers of A, request line and headers of B, the 'Request Data:' line of A - and the lines of
    the container end before B's own 'Request Data:' line, which would have shown it. B had A's prompt, and in some
    files A's transaction. Where a request's first lines broke off and no request since has a status or an error,
    the requests since keep their request line only, and all of them are counted."""
    a, b = log_units(_serial_block(1)), log_units(_serial_block(2))
    text = as_log([collected("demo-llm-1")], _interleaved(a[0], a[1], b[0], b[1], a[2], a[3], a[4], a[5]),
                  [collected("demo-llm-2")], _serial_block(3))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert len(frame) == 3 and "Question number 1?" not in frame.to_csv()
    assert all(missing(frame.loc[1, column]) for column in ALL_COLUMNS if column not in ("timestamp", "model"))
    assert all(missing(value) for value in frame.loc[:1, "failed"]) and frame.loc[2, "failed"] == 0
    assert _overlap_counts(run.sas.messages) == (2, 2, 0) and _end_counts(run.sas.messages) == 2


def test_requests_that_can_not_be_told_apart_are_counted_where_the_lines_end_on_them(monkeypatch, tmp_path):
    """The count of the requests the lines of their container end on, where no row can be marked: the first lines of
    request 1 and request 2 interleave, a first line that no request is waiting for shows it, and both keep their
    request line only. One transaction starts - it is the transaction of one of the two, the log does not say which.
    Then two more requests come in, the first of them with its request line only, and the lines end. All four were
    running or waiting there: failed is missing for the four, and the NOTE of those requests counts four."""
    one, two, three, four = (log_units(_serial_block(serial)) for serial in (1, 2, 3, 4))
    part = _interleaved(*one[:4], two[0], one[4], one[5], two[1], one[6], one[7], two[2], three[0], four[0], four[1], three[1])
    text = as_log([collected("demo-llm-1")], part, [collected("demo-llm-2")], _serial_block(5))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert len(frame) == 5 and [missing(value) for value in frame["failed"]] == [True, True, True, True, False]
    assert _end_counts(run.sas.messages) == 4 and _overlap_counts(run.sas.messages)[2] == 0
    for serial in (1, 2, 3, 4):
        assert f"Question number {serial}?" not in frame.to_csv()


def test_first_lines_that_broke_off_in_front_of_a_request_that_scores_nothing_failed_as_before(monkeypatch, tmp_path):
    """No scoring request came in since the first lines broke off - only the health check of the container: there is
    no request since that could hold a line of the one that broke off, and nothing is counted."""
    broken_off = log_units(_numbered(1))[:2]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(lines_of(broken_off), get_block("2026-01-05 09:20:00"))))

    frame = run.uploads[0]["frame"]
    assert list(frame["failed"]) == [1] and run.sas.messages == []


def _three_requests(owner_of_p, owner_of_q):
    """Three requests of a model in one container. p and q are what a container writes for a request behind its
    headers - the 'Request Data:' line with the prompt, the two 'Randomly generated' lines, the transaction, the
    lines of the score code and the response; the request line and the headers of the three stand in front of
    them in the same places whoever p and q belong to."""
    def units(request_id, prompt, transaction):
        return log_units(llm_block(timestamp="2026-01-05 09:00:00", user_prompt=prompt, response="the answer to " + prompt,
                                   request_id=request_id, transaction=transaction))
    rest = {"p": ("the prompt p", "00000000-aaaa-bbbb-cccc-00000000000p"), "q": ("the prompt q", "00000000-aaaa-bbbb-cccc-00000000000q"),
            None: ("a prompt the file does not hold", "00000000-aaaa-bbbb-cccc-00000000000x")}
    owned = {owner_of_p: "p", owner_of_q: "q"}
    requests = [units(f"request{number}", *rest[owned.get(number)]) for number in (1, 2, 3)]
    p, q = requests[owner_of_p - 1][2:], requests[owner_of_q - 1][2:]
    return lines_of(requests[0][:2] + requests[1][:2] + p + requests[2][:2] + q)


def test_first_lines_that_broke_off_in_front_of_requests_that_ended_are_two_files_in_one(monkeypatch, tmp_path):
    """The proof for what stays of that class: two files that are the same byte for byte and need different rows.

    In the first, request 1 wrote its request line and its headers and nothing more; request 2 and request 3 were
    answered, with the prompts p and q. In the second, request 1 went on behind the headers of request 2 and was
    answered with the prompt p, request 2 went on behind the headers of request 3 and was answered with q, and the
    lines end on request 3. Nothing but its place ties a 'Request Data:' line, the transaction or a line of the
    score code to a request line.

    The parser reads the first: where a request since the break has ended, nothing is taken back - a part with one
    request that broke off would otherwise lose every request behind it."""
    broke_off, went_on = _three_requests(2, 3), _three_requests(1, 2)
    assert as_log(broke_off) == as_log(went_on)

    run = run_parser(monkeypatch, _write(tmp_path, as_log(broke_off)))
    frame = run.uploads[0]["frame"]
    assert list(frame["request_id"]) == ["request1", "request2", "request3"]
    assert [None if missing(value) else value for value in frame["user_prompt"]] == [None, "the prompt p", "the prompt q"]
    assert list(frame["failed"]) == [1, 0, 0] and run.sas.messages == []


def test_a_second_line_of_a_kind_from_the_score_code_leaves_the_request_none_of_them(monkeypatch, tmp_path):
    """The largest class of the rows with a value of another request in files WITHOUT the lines of the collection
    script (85 of the 90 files of the guide): a request whose transaction no later line names - its lines end
    before its closing line - is over at the next request line, and the lines its score code still writes are read
    as the lines of the one transaction that is running then. The score code writes each of its lines once for a
    request (one statement each in the 53 score codes of the repository), so a second line of a kind says that two
    requests are writing: the request keeps no value of the score code and is counted. Before, the row of request 2
    had the token count, the run time and the response of request 1."""
    a, b = log_units(_serial_block(1)), log_units(_serial_block(2))
    # request 1 up to its transaction, request 2 up to its own, then the lines of the two score codes mixed; the
    # closing lines of request 1 are not in the file
    text = as_log(_interleaved(*a[:8], *b[:8], b[8], a[8], a[9], b[9], b[10], a[10], b[11], a[11], *b[12:]))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["Question number 1?", "Question number 2?"]
    assert all(missing(frame.loc[1, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert (frame.loc[1, "status_code"], frame.loc[1, "failed"]) == (200, 0)
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert _overlap_note(run) == (1, 1, 0)

    # the lines of one request, each kind once, are read as before
    alone = run_parser(monkeypatch, _write(tmp_path, as_log(_serial_block(2)), name="alone.log"))
    assert alone.uploads[0]["frame"].loc[0, "prompt_length"] == 1002 and alone.sas.messages == []


def test_score_lines_behind_a_request_without_a_closing_line_are_two_files_in_one(monkeypatch, tmp_path):
    """The proof for what stays of that class, in a file without the lines of the collection script: two files that are
    the same byte for byte and need different rows. Request 1 has its transaction and no later line names it; request
    2 has its transaction; four lines of the score code follow, each kind once, and the file ends.

    In the first file the container of request 1 was collected while request 1 ran, the lines of the next container
    follow, and the four lines are of request 2. In the second file both requests are of one container, and the four
    lines are of request 1, which was still running. The lines of the score code name no request.

    The parser reads the first. With the line of the collection script in front of each container the two files
    differ, and both are read right."""
    a, b = log_units(_serial_block(1)), log_units(_serial_block(2))
    score_lines = lines_of(a[8:12])
    two_containers = as_log(lines_of(a[:8]), lines_of(b[:8]), score_lines)
    one_container = as_log(lines_of(a[:8] + b[:8]), score_lines)
    assert two_containers == one_container and "prompt_length: 1001" in two_containers

    frame = run_parser(monkeypatch, _write(tmp_path, two_containers)).uploads[0]["frame"]
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "response"]) == (1001, "Answer number 1 of the model.")
    assert missing(frame.loc[0, "prompt_length"])

    # with the lines of the script: one container - the four lines are given to neither of two running requests;
    # two containers - they are the lines of the one request of the second
    marked_one = as_log([collected("demo-llm-1")], lines_of(a[:8] + b[:8]), score_lines)
    frame = run_parser(monkeypatch, _write(tmp_path, marked_one, name="one.log")).uploads[0]["frame"]
    assert missing(frame.loc[0, "prompt_length"]) and missing(frame.loc[1, "prompt_length"])
    marked_two = as_log([collected("demo-llm-1")], lines_of(a[:8]), [collected("demo-llm-2")], lines_of(b[:8]), score_lines)
    frame = run_parser(monkeypatch, _write(tmp_path, marked_two, name="two.log")).uploads[0]["frame"]
    assert missing(frame.loc[0, "prompt_length"]) and frame.loc[1, "prompt_length"] == 1001


def test_a_line_of_the_score_code_in_a_response_is_two_files_in_one(monkeypatch, tmp_path):
    """The same class where the line of the request that is taken as over stands in the response of the request that
    is running (9 of 12,000 generated files without the lines of the script): request 1 has its transaction and no
    later line names it, request 2 wrote the first line of its response, and a line that starts like the response
    line of a score code follows. It is the response line of request 1, which was still running - or the second
    line of the response of request 2, whose model quoted such a line. The two files are the same byte for byte.
    The parser reads the second, as it reads every line of a response that starts like a log line."""
    a, b = log_units(_serial_block(1)), log_units(_serial_block(2))
    line_of_request_1 = a[11][0]
    assert " - scoreModel - INFO - response: Answer number 1 of the model." in line_of_request_1
    still_running = as_log(lines_of(a[:8] + b[:12]), [line_of_request_1], lines_of(b[12:]))
    quoting = llm_block(timestamp="2026-01-05 09:00:00", system_prompt="System text 2.", user_prompt="Question number 2?", prompt_length=1002,
                        output_length=5002, run_time=102.5, response="Answer number 2 of the model.\n" + line_of_request_1)
    a_quote = as_log(lines_of(a[:8]), quoting)
    assert still_running == a_quote

    frame = run_parser(monkeypatch, _write(tmp_path, a_quote)).uploads[0]["frame"]
    assert frame.loc[1, "response"] == "Answer number 2 of the model. " + line_of_request_1
    assert (frame.loc[1, "status_code"], frame.loc[1, "failed"]) == (200, 0) and missing(frame.loc[0, "response"])


def test_response_line_of_a_request_that_is_taken_as_over_ends_no_other_request(monkeypatch, tmp_path):
    """1 of 12,000 generated files without the lines of the script: request 1 has the line of its transaction that
    follows the score code, and no later line names its transaction, so it is over at the request line of request 3.
    Its 'Response:' line comes behind that, and its 'Response Data:' line is not in the file. Request 2 is the one
    transaction that is running then, and before this round the line ended it: its response line, which came later,
    was read as the line of request 3. A container writes the 'Response:' line behind the line of the transaction
    that follows the score code, and request 2 has no such line yet: the 'Response:' line is not its line."""
    a, b, c = log_units(_serial_block(1)), log_units(_serial_block(2)), log_units(_serial_block(3))
    # request 1 up to the line behind its score code, request 2 up to two lines of its score code; request 3 comes in;
    # the 'Response:' line of request 1; the transaction of request 3 starts; the rest of request 2
    text = as_log(lines_of(a[:8] + b[:8] + a[8:14] + b[8:10] + c[:5]), a[14], c[5], lines_of(b[10:12] + [b[12]] + c[6:8]))

    run = run_parser(monkeypatch, _write(tmp_path, text))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["Question number 1?", "Question number 2?", "Question number 3?"]
    assert all(missing(frame.loc[2, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert missing(frame.loc[1, "status_code"])
    for row, serial in zip(frame.itertuples(), (1, 2, 3)):
        assert row.response is None or row.response == f"Answer number {serial} of the model."

    # behind the line that follows its score code the line is the line of the one transaction that is running, as before
    answered = _serial_block(4)
    without_its_last_line = run_parser(monkeypatch, _write(tmp_path, as_log(answered[:-1]), name="answered.log"))
    assert (without_its_last_line.uploads[0]["frame"].loc[0, "status_code"], without_its_last_line.uploads[0]["frame"].loc[0, "failed"]) == (200, 0)
    # and so is the status of an error, which a container can write without a line of the score code
    refused = _serial_block(5)[:8] + ["2026-01-05 09:00:00 - api - INFO - Response: Status Code 422"]
    frame = run_parser(monkeypatch, _write(tmp_path, as_log(refused), name="refused.log")).uploads[0]["frame"]
    assert (frame.loc[0, "status_code"], frame.loc[0, "failed"]) == (422, 1)


# ---------------------------------------------------------------------------
# What `failed` can not say in a file without the lines of the collection script (review-20 and gate-20)
#
# test_log_parser_generated.py reads its generated files without those lines and puts every `failed` that is not what
# the part of its request says to one of four classes. The three tests below hold, for each class, two files that are
# the same byte for byte and need different rows.
# ---------------------------------------------------------------------------

def _at(units, *times):
    """The lines of units, the log line of each under the time that stands at its place."""
    assert len(units) == len(times)
    return [line for unit, time in zip(units, times) for line in _stamped(unit, "2026-01-05 " + time)]


@pytest.mark.parametrize("begins", ["09:00:30", "09:00:12"],
                         ids=["behind the last line in front of it", "ten seconds before the last line in front of it"])
def test_a_request_without_a_closing_line_is_two_files_in_one(monkeypatch, tmp_path, begins):
    """The largest class of wrong `failed` values in files WITHOUT the lines of the collection script: failed = 1 for a
    request the lines of its container end on. Request 1 has its transaction and two lines of its score code, and
    neither a 'Response:' line nor an error. The lines of request 2 follow, which is answered.

    These bytes are more than one file. In the first, the container of request 1 was collected while request 1 ran,
    and the lines of the next container follow: nothing is known of request 1, `failed` is missing. In the second,
    one container wrote all of it: request 1 ended without a line - its worker was gone - and the container went on
    with request 2: request 1 failed. A request that ends without a response writes no line, and no line says where
    the lines of a container end.

    The parser reads the second wherever the time does not go back by more than a second against a request that is
    running or waiting. That is so in both files here: in 'ten seconds before', the first line of request 2 is older
    than the line in front of it and younger than the request line of request 1. Such a line does not end a part,
    because a file whose containers are mixed holds such lines as well, with the container going on behind them
    (test_transaction_that_goes_on_behind_the_end_of_its_part_takes_no_line_and_gives_none_away).

    With the line of the script in front of each container, request 1 is cut off there: `failed` is missing, and
    the NOTE counts it."""
    a = log_units(_serial_block(1))
    first = _at(a[:10], "09:00:00", "09:00:00", "09:00:00", "09:00:00", "09:00:00", "09:00:01", "09:00:01", "09:00:01", "09:00:20", "09:00:22")
    second = _restamped(_serial_block(2), "2026-01-05 " + begins)
    two_containers = as_log(first, second)
    one_container = as_log(first + second)
    assert two_containers == one_container
    assert " - api - INFO - Response: " not in as_log(first) and " - ERROR - " not in two_containers

    run = run_parser(monkeypatch, _write(tmp_path, one_container))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["Question number 1?", "Question number 2?"]
    assert list(frame["failed"]) == [1, 0] and missing(frame.loc[0, "status_code"])
    assert (frame.loc[0, "prompt_length"], frame.loc[1, "prompt_length"]) == (1001, 1002)
    # for the parser nothing is open in these lines: it writes no NOTE
    assert run.sas.messages == []

    marked = run_parser(monkeypatch, _write(tmp_path, as_log([collected("demo-llm-1")], first, [collected("demo-llm-2")], second), name="marked.log"))
    frame = marked.uploads[0]["frame"]
    assert missing(frame.loc[0, "failed"]) and frame.loc[1, "failed"] == 0 and _end_note(marked) == 1


@pytest.mark.parametrize("scored", [True, False], ids=["the request behind it has the line that follows its score code", "the request behind it has not"])
def test_a_response_line_without_its_response_data_line_is_two_files_in_one(monkeypatch, tmp_path, scored):
    """Two more classes of wrong `failed` values in those files, both of one line: a 'Response: Status Code' line names
    no request - the 'Response Data:' line behind it does -, and the lines end between the two.

    Request 1 has the line of its transaction that follows the score code, and no later line names it. Request 2 comes
    in. Then the 'Elapsed time' line and the 'Response:' line of a request, and the lines end.

    In the first file the two lines are of request 1: it was answered, and the lines of the container end on request
    2. In the second they are not: request 1 ended without a line, and the two lines are of request 2 - or, where
    request 2 has not got as far as the line that follows its score code, the rest of a request of the next
    container, whose lines begin in the middle of that request. The files are the same byte for byte.

    The parser reads the second. In the first file that is failed = 1 for a request that was answered, and - where
    request 2 has the line that follows its score code - failed = 0 for a request the lines end on."""
    a, b, c = (log_units(_serial_block(serial)) for serial in (1, 2, 3))
    assert "successfully scored using the scoreModel function" in a[12][0] and " - api - INFO - Elapsed time for " in a[13][0]
    assert " - api - INFO - Response: Status Code 200" in a[14][0] and " - api - INFO - Response Data: " in a[15][0]
    in_front = lines_of(a[:13] + (b[:13] if scored else b[:8]))
    of_request_1 = as_log(in_front, lines_of(a[13:15]))
    of_another = as_log(in_front, lines_of((b if scored else c)[13:15]))
    assert of_request_1 == of_another

    run = run_parser(monkeypatch, _write(tmp_path, of_request_1))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["Question number 1?", "Question number 2?"]
    assert frame.loc[0, "failed"] == 1 and missing(frame.loc[0, "status_code"]) and frame.loc[0, "response"] == "Answer number 1 of the model."
    if scored:
        assert (frame.loc[1, "status_code"], frame.loc[1, "failed"]) == (200, 0) and run.sas.messages == []
    else:
        # the line is given to no request: request 2 is the one that is running, and it is counted
        assert missing(frame.loc[1, "status_code"]) and missing(frame.loc[1, "failed"])
        assert _overlap_note(run) == (1, 1, 1)


def test_an_error_line_of_the_score_code_behind_a_request_without_a_closing_line_is_two_files_in_one(monkeypatch, tmp_path):
    """The fourth class: the line the score code writes when the provider refuses a request names no request either.
    It is the class of test_score_lines_behind_a_request_without_a_closing_line_are_two_files_in_one, with a line
    that sets `failed`. Request 1 has its transaction and no later line names it; request 2 has its transaction; the
    ERROR line of a score code follows, and the file ends.

    In the first file the container of request 1 was collected while request 1 ran, the lines of the next container
    follow, and the line is of request 2, which failed. In the second file both requests are of one container and
    the line is of request 1, which was still running; the lines end on request 2, of which nothing is known.

    The parser reads the first: the text of the error and failed = 1 are in the row of request 2."""
    def failing(serial):
        return log_units(failed_block("2026-01-05 09:00:00", "demo_llm", llm_inputs(f"System text {serial}.", f"Question number {serial}?")))

    a, b = failing(1), failing(2)
    assert " - scoreModel - ERROR - HTTP 401" in a[8][0] and " - transaction_" in a[9][0]
    of_request_2 = as_log(lines_of(a[:8]), lines_of(b[:8]), b[8])
    of_request_1 = as_log(lines_of(a[:8] + b[:8]), a[8])
    assert of_request_2 == of_request_1

    run = run_parser(monkeypatch, _write(tmp_path, of_request_1))
    frame = run.uploads[0]["frame"]
    assert list(frame["user_prompt"]) == ["Question number 1?", "Question number 2?"]
    assert frame.loc[1, "failed"] == 1 and frame.loc[1, "error_text"].startswith("HTTP 401") and missing(frame.loc[0, "error_text"])
    # with the lines of the script: one container - the line is given to neither of two running requests; two - it
    # is the line of the one request of the second
    one = run_parser(monkeypatch, _write(tmp_path, as_log([collected("demo-llm-1")], lines_of(a[:8] + b[:8]), a[8]), name="one.log"))
    assert all(missing(value) for value in one.uploads[0]["frame"]["error_text"]) and all(missing(value) for value in one.uploads[0]["frame"]["failed"])
    two = run_parser(monkeypatch, _write(tmp_path, as_log([collected("demo-llm-1")], lines_of(a[:8]), [collected("demo-llm-2")], lines_of(b[:8]), b[8]),
                                         name="two.log"))
    frame = two.uploads[0]["frame"]
    assert missing(frame.loc[0, "failed"]) and frame.loc[1, "failed"] == 1 and frame.loc[1, "error_text"].startswith("HTTP 401")


# ---------------------------------------------------------------------------
# Content that can not be read
# ---------------------------------------------------------------------------

# The NOTE names what it counts. Options that are nested deeper than Python decodes are not
# among it: they are read as names and values and leave nothing unread
UNREADABLE_NOTE = ("1 request(s) hold a value that could not be read, for example a `Request Data:` line that was cut off or "
                   "a request body that is nested deeper than Python reads. The rows were kept with what was read.")

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
    # They are read as names and values, the way options that are no JSON are: nothing of the request
    # is left unread, and the NOTE that counts requests with a value that could not be read is not written
    assert run.sas.messages == []


def test_request_body_that_is_nested_deeper_than_python_reads_is_counted_in_a_note(monkeypatch, tmp_path):
    """The 'Request Data:' line holds the request body as a Python literal. One that is nested
    deeper than Python reads leaves the inputs of its request unread: the row is kept with
    what the other lines say, the file is read to its end and the NOTE counts the request."""
    block = llm_block()
    data_line = next(index for index, line in enumerate(block) if " - Request Data: " in line)
    start = block[data_line].index("Request Data: ") + len("Request Data: ")
    block[data_line] = block[data_line][:start] + "{'inputs': " + "[" * 100000 + "]" * 100000 + "}"
    run = run_parser(monkeypatch, _write(tmp_path, as_log(block, llm_block(timestamp="2026-01-05 09:16:00"))))

    frame = run.uploads[0]["frame"]
    assert run.row_count == "2"
    assert missing(frame.loc[0, "user_prompt"]) and missing(frame.loc[0, "kind"])
    assert (frame.loc[0, "response"], frame.loc[0, "status_code"], frame.loc[0, "failed"]) == ("The capital of Austria is Vienna.", 200, 0)
    assert frame.loc[1, "user_prompt"] == "What is the capital of Austria?"
    assert notes(run) == [UNREADABLE_NOTE]


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
    assert notes(run) == [UNREADABLE_NOTE]


def test_request_data_that_was_cut_off_is_counted_in_a_note(monkeypatch, tmp_path):
    block = llm_block()
    data_line = next(index for index, line in enumerate(block) if " - Request Data: " in line)
    block[data_line] = block[data_line][:-25]
    run = run_parser(monkeypatch, _write(tmp_path, as_log(block, llm_block(timestamp="2026-01-05 09:16:00"))))

    assert run.row_count == "2"
    assert notes(run) == [UNREADABLE_NOTE]


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

    assert ("%put ERROR: The table &_log_target_table_lib..&_log_target_table_name. is not a CAS table. For this step to run "
            "the target table needs to be in CAS, no log was read.;") in program
    assert "&_log_target_table." not in program


def _sas_code(program):
    """A program without its Python."""
    return program[:program.index("submit;")] + program[program.index("endsubmit;"):]


@BOTH_PROGRAMS
def test_refusal_ends_the_run_with_its_one_line_and_without_an_abort(program):
    """With a table that is no CAS table the parser wrote its ERROR lines and stopped its DATA step with
    ABORT. SAS went on in syntax check mode, PROC PYTHON did not run, and the program wrote a further
    'ERROR: The log could not be parsed' about a log it had not read. Both refusals now set
    _log_ready to 0: the Python reads nothing and the macro of the output table returns."""
    program = program()
    code = _sas_code(program)

    statements = [statement.strip().lower() for statement in code.split(";")]
    assert not [statement for statement in statements if statement.split(" ")[0] == "abort"]
    checks = code[code.index("* Check if the file is located on the SAS Server"):code.index("* Get the caslib name of the libname;")]
    assert checks.index("%let _log_ready = 1;") < checks.index("data _null_;")
    # Each refusal is one ERROR line and sets the flag: the file, the engine, and a value of _log_append_table
    # that is none of its values
    assert checks.count("ERROR:") == 3 and checks.count("putLog 'ERROR:") == 1 and checks.count("%put ERROR:") == 2
    assert "call symputx('_log_ready', '0');" in checks
    engine = checks[checks.index("%if &_log_target_table_engine. NE CAS %then %do;"):]
    assert [line.strip() for line in engine.split("\n")[1:4]] == [
        "%put ERROR: The table &_log_target_table_lib..&_log_target_table_name. is not a CAS table. For this step to run the target "
        "table needs to be in CAS, no log was read.;",
        "%let _log_ready = 0;", "%end;"]

    # The macro returns before it can say that a log could not be parsed, and before it drops a table of the upload
    macro = _output_macro(program)
    returns = macro.index("%if &_log_ready. NE 1 %then %return;")
    assert returns < macro.index("%if &_log_row_count. GT 0 %then %do;")
    assert returns < macro.index("%put ERROR: The log could not be parsed") and returns < macro.index("table.dropTable")
    assert " _log_ready" in next(line for line in code.split("\n") if line.startswith("%symdel ") and "_log_row_count" in line)


def test_python_of_a_run_that_was_refused_reads_no_log(monkeypatch, tmp_path):
    """_log_ready is 0: the file is not opened - this one does not exist -, nothing is uploaded and nothing is
    said. _log_row_count stays what the SAS program set it to."""
    run = run_parser(monkeypatch, tmp_path / "not there.log", ready="0")

    assert run.row_count == "-1"
    assert run.uploads == [] and run.sas.messages == []
    assert "df" not in run.namespace

    # The same file with a run that was not refused is the failed parse the last ERROR line is for
    with pytest.raises(FileNotFoundError):
        run_parser(monkeypatch, tmp_path / "not there.log")


def test_every_macro_variable_the_code_file_reads_is_one_it_sets():
    program = code_file_program()
    program = program[:program.index("submit;")] + program[program.index("endsubmit;"):]

    read = set(name.lower() for name in re.findall(r"&+(_log_\w+)", program))
    assigned = set(name.lower() for name in re.findall(r"%let (_log_\w+)", program))
    assigned |= set(name.lower() for name in re.findall(r"^%_log_default\((_log_\w+),", program, flags=re.MULTILINE))
    assigned |= set(name.lower() for name in re.findall(r"call symputx\('(_log_\w+)'", program))
    # the two names of the macro that sets a default are its parameters
    assert read - assigned == {"_log_name", "_log_value"}
    assert "_log_row_count" not in read - assigned and "put_number('_log_row_count', len(df))" in code_file_program()


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
    assert "    run;\n                    %let _log_append_syserr = &syserr.;\n" in step
    assert "%let _log_append_syserr = 0;\n" in program[:program.index("%macro _log_handle_output_table;")]
    # The rows: of the existing table and of the new one from the listing of their caslib, of the log from CASUSER
    assert 'table.tableInfo result=targetTables / casLib = "&_log_target_table_lib.";' in swap
    assert ('if upcase(listedTable.Name) EQ upcase("&_log_target_table_name.") then do; existingRows = listedTable.Rows; '
            "existingLabel = listedTable.Label; end;") in swap
    assert "if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS_ALL' then stagedRows = listedTable.Rows;" in swap
    assert "table.tableInfo result=uploadTables / casLib = 'CASUSER';" in swap
    assert "if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS' then uploadedRows = listedTable.Rows;" in swap
    refused = ("if &_log_append_syserr. GT 4 or existingRows LT 0 or uploadedRows LT 0 or stagedRows + removedRows NE existingRows + uploadedRows "
               "then stagedComplete = False;")
    assert refused in swap
    # The rows that were taken out for rows of the log are counted where the step that takes them out ran, and there
    # are at least as many of them as rows of the log that took their place
    assert ("%if &_log_took_rows. GT 0 %then %do; if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS_OUT' then removedRows = listedTable.Rows; "
            "%end;") in swap
    assert refused + " if removedRows LT &_log_took_rows. then stagedComplete = False;" in swap
    # A table that could not be counted is no reason to go on: the three counts start at -1
    assert "existingRows = -1; uploadedRows = -1; stagedRows = -1; removedRows = 0;" in swap
    # Only in append mode: a table that is copied has no DATA step to ask
    assert swap.index("%if &_log_target_scope. GE 1 %then %do; stagedTable = '_TEMP_LLM_LOGS_ALL';") < swap.index(refused) < swap.index("%else %do; stagedTable =")
    assert swap.index("stagedComplete = True;") < swap.index(refused) < swap.index("if stagedComplete then do;") < swap.index("table.dropTable")
    # content-22: the new table takes the place of the existing one only where CAS said that it is there
    assert ("table.tableExists result=staged status=stagedAsked / casLib = \"&_log_target_table_lib.\", name = stagedTable; "
            "/* A new table that CAS did not say is there takes the place of nothing, also where it refused the question */ "
            "if stagedAsked.severity NE 0 then stagedComplete = False; else if staged.exists LT 1 then stagedComplete = False; "
            "if stagedComplete then do;") in swap


@BOTH_PROGRAMS
def test_append_that_did_not_finish_leaves_the_table_with_one_error_line(program):
    swap = " ".join(_swap(program()).split())

    otherwise = swap[swap.index("tableWritten = True; end; else do;"):swap.index("%if &_log_save_table. %then %do;")]
    assert otherwise.count("print (error)") == 2 and otherwise.count("tableWritten = False;") == 1
    in_append_mode = otherwise[otherwise.index("%if &_log_target_scope. GE 1 %then %do;"):otherwise.index("%else %do;")]
    assert ('print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is. '
            'The DATA step that appends ended with the return code &_log_append_syserr. and wrote " || (string) stagedRows || " rows, '
            'the table held " || (string) existingRows || " and the log " || (string) uploadedRows || ", and " || (string) removedRows '
            '|| " rows of the table were taken out for &_log_took_rows. rows of the log (-1 stands for a table that was not found). " '
            '|| "The DATA steps that leave out the requests the table holds already ended with the return code '
            '&_log_compare_syserr..";') in in_append_mode
    # What the step left behind is dropped, the existing table is not named in that branch
    assert "table.dropTable / casLib = \"&_log_target_table_lib.\", name = '_TEMP_LLM_LOGS_ALL', quiet = True;" in in_append_mode
    # the existing table is named only where this run loaded it from its saved file for the append (test_fix_content_21)
    loaded = otherwise[otherwise.index("%if &_log_target_loaded. %then %do;"):]
    loaded = loaded[:loaded.index("%end;") + len("%end;")]
    assert 'name = "&_log_target_table_name."' not in otherwise.replace(loaded, "")
    # No message goes through %put with a semicolon in it
    assert ";" not in re.sub(r'" \|\| (\(string\) \w+ \|\| )?"', "", in_append_mode[in_append_mode.index('"The new table'):in_append_mode.index('.";')])


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
    assert read < drop < rename < swap.index(label) < swap.index("table.promote", swap.index(label))
    # The label of a table that is started is the one the upload gives it
    assert "label='Extracted LLM Log Data'," in program()
