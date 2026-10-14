"""What a run of the log parser does to its output table and to the saved file of that table, for the three
states of _log_append_table: the program is followed from the setting to its last statement, against a CAS
library that is made up (log_parser_table_harness says what that library and its DATA steps do, and which of
their rules were measured).

The cases are the ones a pass on a SAS Viya system left open on 2026-10-13, with the program of version 2.8 of
the step: a run that replaces and can not load the saved file still replaced table and file, a request that
was cut off kept its row when a later log held it whole, a file that holds requests twice gave doubled rows in
a run that replaces, and an append that added nothing wrote and saved the table again.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import copy
import json
import random
import re

import pytest

from log_parser_harness import CODE_FILE, as_log, code_file_program, llm_block, split_at_scoring, step_definition, step_program
from log_parser_table_harness import FAILED, Library, run_program, table_of

LIB, TABLE = "Public", "LLM_LOGS"
PROGRAMS = pytest.mark.parametrize("program", [code_file_program, step_program], ids=["code file", "step"])
STATES = pytest.mark.parametrize("setting", [None, "1", "0"], ids=["no value", "1", "0"])

APPENDED = re.compile(
    r"(\d+) request\(s\) were read from the log\. (\d+) were appended to the table Public\.LLM_LOGS and (\d+) took the place of (\d+) "
    r"row\(s\) of it whose failed was missing: the table held (\d+) row\(s\) and holds (\d+) now\. (\d+) were left out, as another row "
    r"holds their request: (\d+) a row of the table, (\d+) a row of the log\.")
STARTED = ("%d request(s) were read from the log. %d were written to the table Public.LLM_LOGS, which was not there before. %d were left "
           "out, as another row of the log holds their request.")
NOTHING = ("%d request(s) were read from the log and none of them was written: the table Public.LLM_LOGS keeps its rows, and neither the "
           "table nor its saved file was written. All were left out, as another row holds their request: %d a row of the table, %d a row of "
           "the log.")
REPLACED = "The table Public.LLM_LOGS held %d row(s). They were replaced by the %d request(s) of the log, as _log_append_table is 0."
LEFT_OUT = ("%d request(s) were read from the log. %d were written to the table Public.LLM_LOGS and %d were left out, as another row of "
            "the log holds their request.")
UNLOADED = {"1": "The saved table Public.LLM_LOGS could not be loaded, nothing was appended and the saved file was left as it is.",
            "0": "The saved table Public.LLM_LOGS could not be loaded, nothing was replaced and the saved file was left as it is.",
            None: "The saved table Public.LLM_LOGS could not be loaded and _log_append_table is not set, so nothing was read and nothing was "
                  "written - the saved file was left as it is."}
NOT_SET = ("The table Public.LLM_LOGS holds %d rows and _log_append_table is not set, so nothing was read and nothing was written - not the "
           "table and not its saved file. Set it in front of the program: %%let _log_append_table = 1; adds the requests of the log to the "
           "table, %%let _log_append_table = 0; replaces the table with them.")


def request(number, **more):
    """A made-up request, whole: its lines hold its response."""
    return llm_block(timestamp=f"2026-03-01 10:{number:02d}:00", user_prompt=f"Made-up question {number}?", **more)


def cut(number, **more):
    """The same request as the lines of a collection hold it that ended while it ran: no response and no error."""
    return split_at_scoring(request(number, **more))[0]


def without_id(number, **more):
    """A request whose lines name no transaction: the lines of its transaction and the Response Data line are gone."""
    return [line for line in request(number, **more) if " - transaction_" not in line and "Response Data:" not in line]


def collection(pod, *blocks):
    """The lines of one container as the collection script of the guide writes them: its line, then the lines."""
    return [f"#=# llm-log-collection pod={pod} container=scr collected=2026-03-01T11:00:00Z #=#"] + [line for block in blocks for line in block]


class World:
    """One CAS library and the runs of one program against it."""

    def __init__(self, monkeypatch, tmp_path, program):
        self.monkeypatch, self.tmp_path, self.program = monkeypatch, tmp_path, program()
        self.library = Library()
        self.files = 0

    def log(self, *blocks):
        self.files += 1
        path = self.tmp_path / f"llms-{self.files}.log"
        path.write_text(as_log(*blocks), encoding="utf-8", newline="")
        return path

    def run(self, setting, *blocks, table=TABLE, **more):
        self.calls = len(self.library.calls)
        return run_program(self.program, self.library, self.monkeypatch, self.log(*blocks), setting=setting, caslib=LIB, table=table, **more)

    def written(self):
        """The actions of the last run that write: every action but the ones that look, and but the ones on the
        tables of the program itself."""
        own = ("_TEMP_LLM_LOGS", "_TEMP_LLM_LOGS_ALL", "_TEMP_LLM_LOGS_SEEN", "_TEMP_LLM_LOGS_GONE", "_TEMP_LLM_LOGS_OUT", "_TEMP_LLM_LOGS_SAVED")
        found = []
        for action, parameters in self.library.calls[self.calls:]:
            named = parameters.get("name") or parameters.get("table") or (parameters.get("casout") or {}).get("name")
            if action in ("table.tableExists", "table.tableInfo", "table.fileInfo", "table.columnInfo") or (action == "table.dropTable" and named in own):
                continue
            if action == "table.loadTable" and parameters["casout"]["name"] == "_TEMP_LLM_LOGS_SAVED":
                continue
            found.append(action)
        return found

    def state(self):
        return self.library.state(LIB, TABLE)

    def rows(self):
        return self.library.get(LIB, TABLE)["rows"]

    def only_saved(self):
        """The table as a restart of the CAS server leaves it: its saved file, and nothing in memory."""
        self.library.promoted.pop(self.library.key(LIB, TABLE), None)
        self.library.session.pop(self.library.key(LIB, TABLE), None)


@pytest.fixture
def world(monkeypatch, tmp_path):
    return lambda program=code_file_program: World(monkeypatch, tmp_path, program)


def table_notes(run):
    """The NOTEs of the SAS code about the table. The Python writes NOTEs of its own about the requests."""
    return [note for note in run.notes if "request(s) were read from the log" in note or note.startswith("The table ")]


def whole(row):
    return row["failed"] is not None


def facts(rows):
    """What the tests compare of rows: the transaction ID and whether the row holds a response or an error."""
    return sorted((row["transaction_id"], whole(row)) for row in rows)


def nothing_left(library):
    """No table of the program is left in CASUSER or in the library of the table, and no table of the SAS session."""
    return library.tables_named("_TEMP_LLM_LOGS") == [] and library.work == {}


# ---------------------------------------------------------------------------
# What was measured with version 2.8 on 2026-10-13 holds for the program as it is
# ---------------------------------------------------------------------------

@PROGRAMS
@STATES
def test_every_state_of_the_setting_starts_a_table_that_is_not_there(world, program, setting):
    w = world(program)

    run = w.run(setting, request(1), request(2), request(3))

    state = w.state()
    assert run.errors == [] and run.syscc == 0
    assert state["exists"] == 2 and len(state["rows"]) == 3 and facts(state["file"]) == facts(state["rows"])
    assert len(state["columns"]) == 36 and state["label"] == "Extracted LLM Log Data"
    assert table_notes(run) == ([STARTED % (3, 3, 0)] if setting == "1" else [])
    assert nothing_left(w.library)


@PROGRAMS
@pytest.mark.parametrize("in_memory", [True, False], ids=["in memory", "only its saved file"])
def test_a_run_without_the_setting_stops_at_rows_and_writes_nothing(world, program, in_memory):
    w = world(program)
    w.run("0", request(1), request(2))
    if not in_memory:
        w.only_saved()
    before = w.state()

    run = w.run(None, request(3))

    assert run.errors == [NOT_SET % 2] and run.syscc == FAILED and run.notes == []
    assert run.uploaded is None and run.macros["_log_row_count"] == "-1"
    assert w.state() == before and nothing_left(w.library) and w.written() == []


@PROGRAMS
def test_a_run_without_the_setting_writes_to_a_table_without_rows(world, program):
    w = world(program)
    w.run("0", request(1))
    w.library.get(LIB, TABLE)["rows"].clear()

    run = w.run(None, request(2), request(3))

    assert run.errors == [] and run.syscc == 0 and len(w.rows()) == 2


@PROGRAMS
@pytest.mark.parametrize("value", ["2", "yes", "01", "TRUE", "-1", "00"])
def test_a_value_that_is_none_of_the_setting_writes_nothing(world, program, value):
    w = world(program)
    w.run("0", request(1))
    before = w.state()

    run = w.run(value, request(2))

    assert len(run.errors) == 1 and run.errors[0].startswith(f"_log_append_table is {value}, which is no value of this setting")
    assert run.syscc == FAILED and run.uploaded is None and w.state() == before


@PROGRAMS
def test_appending_adds_the_requests_the_table_does_not_hold(world, program):
    w = world(program)
    w.run("1", request(1), request(2))

    run = w.run("1", request(2), request(3), request(4))

    assert run.errors == [] and run.syscc == 0
    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("3", "2", "0", "0", "2", "4", "1", "1", "0")
    state = w.state()
    assert len(state["rows"]) == 4 and len({row["transaction_id"] for row in state["rows"]}) == 4
    assert state["exists"] == 2 and facts(state["file"]) == facts(state["rows"]) and state["label"] == "Extracted LLM Log Data"
    assert nothing_left(w.library)


@PROGRAMS
def test_appending_to_a_table_of_twelve_columns_keeps_its_rows_and_adds_every_request(world, program):
    """A table of release 2.2: no transaction_id, so nothing a request of the log can be compared with."""
    w = world(program)
    w.run("0", request(1), request(2))
    written = w.library.get(LIB, TABLE)
    twelve = written["columns"][:12]
    old = table_of(twelve, [{name: row[name] for name, _ in twelve} for row in written["rows"]], "Extracted LLM Log Data")
    w.library.put(LIB, TABLE, old)
    w.library.put_file(LIB, TABLE + ".sashdat", old)

    run = w.run("1", request(1), request(2))

    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("2", "2", "0", "0", "2", "4", "0", "0", "0")
    state = w.state()
    assert [name for name, _ in state["columns"]] == [name for name, _ in written["columns"]]
    assert [{name: row[name] for name, _ in twelve} for row in state["rows"][:2]] == old["rows"]
    assert [row["transaction_id"] for row in state["rows"][:2]] == ["", ""] and all(row["transaction_id"] for row in state["rows"][2:])


@PROGRAMS
def test_an_append_whose_data_steps_do_not_run_leaves_the_table(world, program):
    """Measured: a table whose name has 33 characters is not read through a libref. Every DATA step of the append
    ends with 1012, and the run with one ERROR line of the program."""
    w = world(program)
    name = "LLM_LOGS_WITH_A_NAME_OF_33_CHARS_"
    assert len(name) == 33
    w.run("1", request(1), table=name)
    before = w.library.state(LIB, name)

    run = w.run("1", request(2), table=name)

    assert run.errors[-1] == (
        f"The new table could not be created, the table Public.{name} was left as it is. The DATA step that appends ended with the return "
        "code 1012 and wrote -1 rows, the table held 1 and the log 1, and 0 rows of the table were taken out for -1 rows of the log (-1 "
        "stands for a table that was not found). The DATA steps that leave out the requests the table holds already ended with the return "
        "code 1012.")
    assert run.syscc == FAILED and w.library.state(LIB, name) == before and nothing_left(w.library)


# ---------------------------------------------------------------------------
# C1 - a run that ends with an ERROR line has left the table and its saved file as they were
# ---------------------------------------------------------------------------

@PROGRAMS
@STATES
def test_a_saved_table_that_can_not_be_loaded_is_left_as_it_is_in_every_state(world, program, setting):
    """The table is only its saved file and the file can not be loaded. Measured on 2026-10-13 with version 2.8 and
    the setting 0, in a copy of the program whose count asked for a file that was not there: the run ended with the
    ERROR lines of the action and SYSCC 1012 - and it had started a new table from the rows of the log and saved it
    over the file."""
    w = world(program)
    w.run("0", request(1), request(2))
    w.only_saved()
    w.library.unloadable.add(TABLE + ".sashdat")
    before = w.state()
    assert before["exists"] == 0 and len(before["file"]) == 2

    run = w.run(setting, request(3))

    assert w.state() == before
    # the one action that was tried is the load of a run that appends, which loads the table it appends to
    assert w.written() == (["table.loadTable"] if setting == "1" else [])
    assert run.syscc == FAILED
    # one ERROR line of the program, behind the ones of the action that did not load the file
    assert [error for error in run.errors if "could not be loaded" in error] == [UNLOADED[setting]] and run.errors[-1] == UNLOADED[setting]
    assert table_notes(run) == [] and nothing_left(w.library)
    # the run with no value stops in front of the log, the two others have read it
    assert (run.uploaded is None) == (setting is None)


@PROGRAMS
def test_a_run_that_replaces_counts_before_it_writes(program):
    """The order in the macro: the count, what it found, and only then the PROC CAS step that drops, renames and saves."""
    macro = program()[program().index("%macro _log_handle_output_table;"):program().index("%mend _log_handle_output_table;")]

    counted = ("%if &_log_table_mode. EQ REPLACE %then %do;\n            %_log_count_target;\n            %if &_log_target_rows. LT 0 %then %do;\n"
               "                %let _log_target_unloaded = 1;\n            %end;\n        %end;")
    assert macro.index(counted) < macro.index("table.dropTable") and macro.count("%let _log_target_unloaded = 1;") == 1
    refused = macro[macro.index("%if &_log_target_unloaded. %then %do;"):macro.index("%else %if &_log_append_nothing. %then %do;")]
    said = [line.strip() for line in refused.split("\n") if "print (error)" in line or line.strip().startswith("%")]
    table = "&_log_target_table_lib..&_log_target_table_name."
    assert said == ["%if &_log_target_unloaded. %then %do;", "%if &_log_table_mode. EQ APPEND %then %do;",
                    f'print (error) "The saved table {table} could not be loaded, nothing was appended and the saved file was left as it is.";',
                    "%end;", "%else %do;",
                    f'print (error) "The saved table {table} could not be loaded, nothing was replaced and the saved file was left as it is.";',
                    "%end;", "%end;"]
    for action in ("table.copyTable", "table.dropTable", "table.alterTable", "table.promote", "table.save"):
        assert action not in refused, action


@PROGRAMS
def test_a_run_that_replaces_a_table_that_is_only_its_saved_file_says_what_it_replaced(world, program):
    w = world(program)
    w.run("0", request(1), request(2))
    w.only_saved()

    run = w.run("0", request(3))

    assert run.errors == [] and run.syscc == 0 and table_notes(run) == [REPLACED % (2, 1)]
    assert len(w.rows()) == 1 and len(w.state()["file"]) == 1


@PROGRAMS
def test_a_new_table_that_can_not_be_built_leaves_table_and_file(world, program):
    w = world(program)
    w.run("0", request(1), request(2))
    before = w.state()
    w.library.refuse.add("table.copyTable")

    run = w.run("0", request(3))

    assert run.errors[-1] == "The new table could not be created, the table Public.LLM_LOGS was left as it is."
    assert run.syscc == FAILED and w.state() == before and table_notes(run) == [] and w.written() == ["table.copyTable"]


# ---------------------------------------------------------------------------
# C2 - a row that holds neither a response nor an error gives way to one that does
# ---------------------------------------------------------------------------

@PROGRAMS
def test_a_request_that_was_cut_off_is_replaced_when_a_later_log_holds_it_whole(world, program):
    """Measured with version 2.8: the row of the request kept its missing status and its missing failed."""
    w = world(program)
    first = w.run("1", request(1), cut(2))
    assert facts(w.rows()) == sorted([(w.rows()[0]["transaction_id"], True), (w.rows()[1]["transaction_id"], False)])
    cut_row = next(row for row in w.rows() if not whole(row))
    assert (cut_row["status_code"], cut_row["failed"], cut_row["output_length"], cut_row["response"]) == (None, None, None, "")
    assert table_notes(first) == [STARTED % (2, 2, 0)]

    run = w.run("1", request(1), request(2))

    assert run.errors == [] and run.syscc == 0
    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("2", "0", "1", "1", "2", "2", "1", "1", "0")
    rows = w.rows()
    assert len(rows) == 2 and all(whole(row) for row in rows)
    again = next(row for row in rows if row["transaction_id"] == cut_row["transaction_id"])
    assert (again["status_code"], again["failed"], again["output_length"], again["response"]) == (200, 0, 9, "The capital of Austria is Vienna.")
    assert facts(w.state()["file"]) == facts(rows) and w.state()["exists"] == 2 and nothing_left(w.library)


@PROGRAMS
@pytest.mark.parametrize("in_table, in_log", [("whole", "cut"), ("whole", "whole"), ("cut", "cut")])
def test_a_row_of_the_table_stays_unless_it_is_incomplete_and_the_log_holds_it_whole(world, program, in_table, in_log):
    w = world(program)
    made = {"whole": request, "cut": cut}
    w.run("1", request(1), made[in_table](2))
    before = w.state()

    run = w.run("1", request(1), made[in_log](2))

    assert run.errors == [] and table_notes(run) == [NOTHING % (2, 2, 0)]
    assert w.state() == before


@pytest.mark.parametrize("mode", ["APPEND", "REPLACE", "START"])
def test_of_a_request_the_log_holds_cut_off_and_whole_the_whole_row_is_written(monkeypatch, tmp_path, mode):
    """One file, two collections of one container: the first ended while request 2 ran, the second holds it whole."""
    from log_parser_harness import run_parser
    log = tmp_path / "llms.log"
    log.write_text(as_log(collection("pod-a", request(1), cut(2)), collection("pod-a", request(1), request(2), request(3))), encoding="utf-8", newline="")

    run = run_parser(monkeypatch, log, table_mode=mode)

    frame = run.uploads[0]["frame"]
    assert run.row_count == "3" and run.sas.macro_variables["_log_row_repeats"] == "2"
    assert frame["transaction_id"].is_unique and list(frame["failed"]) == [0, 0, 0] and list(frame["status_code"]) == [200, 200, 200]
    assert list(frame.index) == [0, 1, 2]
    # the other order: the whole row is the first, and the row of the collection that cut the request off is left out
    other = tmp_path / "other.log"
    other.write_text(as_log(collection("pod-a", request(1), request(2)), collection("pod-a", request(1), cut(2))), encoding="utf-8", newline="")
    frame = run_parser(monkeypatch, other, table_mode=mode).uploads[0]["frame"]
    assert len(frame) == 2 and list(frame["failed"]) == [0, 0]
    # cut off in both: the row of the first time
    both = tmp_path / "both.log"
    both.write_text(as_log(collection("pod-a", request(1), cut(2)), collection("pod-b", cut(2))), encoding="utf-8", newline="")
    run = run_parser(monkeypatch, both, table_mode=mode)
    assert run.row_count == "2" and run.sas.macro_variables["_log_row_repeats"] == "1"
    assert [value != value for value in run.uploads[0]["frame"]["failed"]] == [False, True]


@PROGRAMS
def test_a_table_without_a_numeric_column_failed_has_no_row_that_is_replaced(world, program):
    w = world(program)
    w.run("1", request(1), cut(2))
    table = w.library.get(LIB, TABLE)
    table["columns"] = [column for column in table["columns"] if column[0] != "failed"]
    for row in table["rows"]:
        del row["failed"]
    kept = copy.deepcopy(table["rows"])

    run = w.run("1", request(1), request(2))

    assert table_notes(run) == [NOTHING % (2, 2, 0)] and run.macros["_log_target_has_failed"] == "0"
    assert w.rows() == kept


@PROGRAMS
def test_every_incomplete_row_of_a_request_gives_way_to_the_one_whole_row(world, program):
    """A table that holds the cut-off request twice, as a table written before a request was appended once can."""
    w = world(program)
    w.run("1", request(1), cut(2))
    table = w.library.get(LIB, TABLE)
    table["rows"].append(copy.deepcopy(next(row for row in table["rows"] if not whole(row))))
    assert len(table["rows"]) == 3

    run = w.run("1", request(2), request(3))

    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("2", "1", "1", "2", "3", "3", "0", "0", "0")
    assert len(w.rows()) == 3 and all(whole(row) for row in w.rows()) and len({row["transaction_id"] for row in w.rows()}) == 3


@PROGRAMS
def test_a_table_that_holds_a_request_whole_and_cut_off_keeps_both_rows(world, program):
    """A complete row of the table is never replaced, and where the table holds one, its incomplete rows stay as well."""
    w = world(program)
    w.run("1", request(1), cut(2))
    table = w.library.get(LIB, TABLE)
    done = copy.deepcopy(next(row for row in table["rows"] if not whole(row)))
    done.update(failed=1, error_text="made up")
    table["rows"].append(done)
    before = w.state()

    run = w.run("1", request(2))

    assert table_notes(run) == [NOTHING % (1, 1, 0)] and w.state()["rows"] == before["rows"]


@PROGRAMS
def test_a_table_that_holds_its_ids_with_a_fixed_length_is_compared_and_replaced_the_same(world, program):
    w = world(program)
    w.run("1", request(1), cut(2))
    table = w.library.get(LIB, TABLE)
    table["columns"] = [(name, "char(64)" if name in ("transaction_id", "model") else kind) for name, kind in table["columns"]]
    for row in table["rows"]:
        row["transaction_id"], row["model"] = row["transaction_id"].ljust(64), row["model"].ljust(64)

    run = w.run("1", request(1), request(2), request(3))

    assert run.errors == []
    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("3", "1", "1", "1", "2", "3", "1", "1", "0")
    assert all(whole(row) for row in w.rows()) and {row["model"] for row in w.rows()} == {"demo_llm"}
    assert dict(w.library.get(LIB, TABLE)["columns"])["transaction_id"] == "varchar"


@PROGRAMS
def test_what_the_other_rows_hold_stays_when_a_row_is_replaced(world, program):
    """What the link wrote into the rows of the table, and a column the parser does not know."""
    w = world(program)
    w.run("1", request(1), cut(2))
    table = w.library.get(LIB, TABLE)
    table["columns"].append(("team", "char(20)"))
    for row in table["rows"]:
        row.update(caller_name="made-up caller", match_method="template", team="made up")

    w.run("1", request(2), request(3))

    by_state = {row["user_prompt"]: row for row in w.rows()}
    assert [name for name, _ in w.library.get(LIB, TABLE)["columns"]][-1] == "team" and len(by_state) == 3
    assert (by_state["Made-up question 1?"]["caller_name"], by_state["Made-up question 1?"]["team"]) == ("made-up caller", "made up")
    # the row that took the place of the cut-off one is the row of the log: the link fills it at its next run
    assert (by_state["Made-up question 2?"]["caller_name"], by_state["Made-up question 2?"]["match_method"], by_state["Made-up question 2?"]["team"]) == ("", "", "")


def _expected(table, log):
    """What a run that appends has to do, written down without the program: table and log as lists of
    (request, whole). Returns the rows of the table afterwards and the numbers of the NOTE."""
    kept, repeats = [], 0
    for number in dict.fromkeys(number for number, _ in log):
        versions = [is_whole for other, is_whole in log if other == number]
        kept.append((number, True in versions))
        repeats += len(versions) - 1
    rows, appended, took, taken_out, in_table = list(table), 0, 0, 0, 0
    for number, is_whole in kept:
        held = [state for other, state in table if other == number]
        if not held:
            rows.append((number, is_whole))
            appended += 1
        elif is_whole and True not in held:
            rows = [row for row in rows if row[0] != number] + [(number, True)]
            took, taken_out = took + 1, taken_out + len(held)
        else:
            in_table += 1
    return sorted(rows), (len(log), appended, took, taken_out, len(table), len(rows), in_table + repeats, in_table, repeats)


@pytest.mark.parametrize("seed", range(12))
def test_the_numbers_of_an_append_add_up_and_the_table_holds_what_they_say(world, seed):
    """Tables and logs put together at random from requests that are whole, cut off, there twice or not there."""
    chosen = random.Random(seed)
    numbers = list(range(1, 9))
    in_table = [(number, chosen.random() < 0.5) for number in numbers if chosen.random() < 0.6]
    in_log = [(number, chosen.random() < 0.6) for number in numbers if chosen.random() < 0.7]
    in_log += [(number, chosen.random() < 0.5) for number, _ in in_log if chosen.random() < 0.3]
    chosen.shuffle(in_log)
    if not in_table or not in_log:
        in_table, in_log = [(1, False)], [(1, True), (2, True)]
    w = world()

    def as_parts(requests):
        # a request that is cut off is the last one of the lines of its container
        return [collection(f"pod-{position}", (request if is_whole else cut)(number)) for position, (number, is_whole) in enumerate(requests)]

    def held_now():
        # the request of a row by the number in its made-up question
        return sorted((int(row["user_prompt"].split()[2].rstrip("?")), whole(row)) for row in w.rows())

    w.run("1", *as_parts(in_table))
    assert held_now() == sorted(in_table)
    before = w.state()
    rows, note = _expected(in_table, in_log)

    run = w.run("1", *as_parts(in_log))

    assert run.errors == [] and run.syscc == 0
    read, appended, took, taken_out, held, holds, left_out, left_table, left_log = note
    if appended + took == 0:
        assert table_notes(run) == [NOTHING % (read, left_table, left_log)] and w.state() == before
    else:
        assert tuple(int(value) for value in APPENDED.fullmatch(table_notes(run)[0]).groups()) == note
        assert read == appended + took + left_out and left_out == left_table + left_log and holds == held + appended + took - taken_out
    assert held_now() == rows
    assert facts(w.state()["file"]) == facts(w.rows()) and nothing_left(w.library)


# ---------------------------------------------------------------------------
# What a request is known by: its transaction ID, its model and the time of its request line
#
# The container gives a request its transaction ID unless the client sends one, and a client can send the same
# one with every request. The parser reads the next 'Score request received' line of an ID as another request;
# the guard of version 2.8 went by the ID alone and took such requests for one.
# ---------------------------------------------------------------------------

GIVEN = "99999999-8888-7777-6666-555555555555"


@PROGRAMS
@STATES
def test_requests_a_client_gave_one_transaction_id_stay_the_requests_they_are(world, program, setting):
    """One ID for a call to each of two models and for a later call to the first one: three requests."""
    w = world(program)
    calls = [request(1, transaction=GIVEN), request(2, model="other_llm", transaction=GIVEN), request(3, transaction=GIVEN)]

    run = w.run(setting, *calls)

    assert run.errors == [] and len(w.rows()) == 3 and {row["transaction_id"] for row in w.rows()} == {GIVEN}
    assert sorted(row["model"] for row in w.rows()) == ["demo_llm", "demo_llm", "other_llm"]
    assert table_notes(run) == ([STARTED % (3, 3, 0)] if setting == "1" else [])


@PROGRAMS
def test_a_request_of_the_table_is_another_one_than_a_request_of_the_log_with_its_id(world, program):
    w = world(program)
    w.run("1", request(1, transaction=GIVEN), cut(2, transaction=GIVEN))

    # the same ID again: for the other model at the time of request 1, and for the model of request 1 a minute later.
    # Neither is in the table, and neither takes the place of the row that was cut off
    run = w.run("1", request(1, model="other_llm", transaction=GIVEN), request(3, transaction=GIVEN), request(1, transaction=GIVEN))

    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("3", "2", "0", "0", "2", "4", "1", "1", "0")
    assert sorted((row["model"], row["user_prompt"], whole(row)) for row in w.rows()) == [
        ("demo_llm", "Made-up question 1?", True), ("demo_llm", "Made-up question 2?", False), ("demo_llm", "Made-up question 3?", True),
        ("other_llm", "Made-up question 1?", True)]
    # and the request that was cut off is replaced by its own lines, whole
    run = w.run("1", request(2, transaction=GIVEN))
    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("1", "0", "1", "1", "4", "4", "0", "0", "0")
    assert all(whole(row) for row in w.rows()) and len(w.rows()) == 4


@PROGRAMS
@pytest.mark.parametrize("change", ["no model", "a time that is text"])
def test_a_table_that_lacks_what_a_request_is_known_by_is_compared_with_nothing(world, program, change):
    """As a table without a transaction_id: every request of the log is appended."""
    w = world(program)
    w.run("1", request(1))
    table = w.library.get(LIB, TABLE)
    if change == "no model":
        table["columns"] = [column for column in table["columns"] if column[0] != "model"]
        for row in table["rows"]:
            del row["model"]

        run = w.run("1", request(1))

        assert run.macros["_log_target_has_id"] == "0" and APPENDED.fullmatch(table_notes(run)[0]).groups() == ("1", "1", "0", "0", "1", "2", "0", "0", "0")
    else:
        table["columns"] = [(name, "char(20)" if name == "timestamp" else kind) for name, kind in table["columns"]]
        before = w.state()

        run = w.run("1", request(1))

        # no comparison, and then the DATA step that appends stops at a column that is a number on one side and text on the other
        assert run.macros["_log_target_has_id"] == "0" and run.syscc == FAILED and w.state() == before
        assert run.errors[-1].startswith("The new table could not be created, the table Public.LLM_LOGS was left as it is.")


# ---------------------------------------------------------------------------
# C3 - a request the log holds more than once is written once in every state
# ---------------------------------------------------------------------------

@PROGRAMS
@STATES
def test_a_file_that_holds_its_requests_twice_gives_each_request_one_row(world, program, setting):
    """Measured with version 2.8 and the setting 0: 2,048 rows from a file that held 1,024 requests twice."""
    w = world(program)
    blocks = [request(1), request(2), request(3)]

    run = w.run(setting, *blocks, *blocks)

    assert run.errors == [] and len(w.rows()) == 3 and len({row["transaction_id"] for row in w.rows()}) == 3
    assert table_notes(run) == [(STARTED if setting == "1" else LEFT_OUT) % (6, 3, 3)]


@PROGRAMS
def test_a_run_that_replaces_says_the_rows_it_replaced_and_the_requests_it_left_out(world, program):
    w = world(program)
    w.run("0", request(1), request(2), request(3), request(4))

    run = w.run("0", request(5), request(6), request(5))

    assert table_notes(run) == [REPLACED % (4, 2), LEFT_OUT % (3, 2, 1)] and len(w.rows()) == 2
    # a file that holds no request twice has nothing to say about it
    assert table_notes(w.run("0", request(7))) == [REPLACED % (2, 1)]


@PROGRAMS
def test_a_file_that_holds_its_requests_twice_onto_a_table_that_holds_them(world, program):
    w = world(program)
    w.run("1", request(1), request(2))

    run = w.run("1", request(1), request(2), request(3), request(1), request(2), request(3))

    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("6", "1", "0", "0", "2", "3", "5", "2", "3")


# ---------------------------------------------------------------------------
# C4 - a request without a transaction ID: no rule decides it, so it is appended as before
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("request_id", [None, "c" * 32], ids=["no X-Request-Id of its own", "the same X-Request-Id"])
def test_two_requests_without_an_id_and_one_request_read_twice_are_the_same_table_and_the_same_lines(world, request_id):
    """A request the container gave no transaction - it wrote its first lines and nothing that names one - has no
    column that is its own. Its row is made of the second it came in, its model, its inputs and its request headers,
    and the X-Request-Id is set outside the container: a client that repeats a request and a proxy that sends it on
    to a second container repeat it.

    File A holds such a request of one container. File B holds the request another container got in the same
    second with the same body and the same headers. The two files are the same line for line. A run that appends B
    onto the table that holds the row of A has to add a row - it is another request -, and a run that appends A a
    second time has to add none. Both runs get the same table and the same lines, so no rule of the program can
    give both the right answer. The program appends: it never takes a request without an ID for another one."""
    more = {} if request_id is None else {"request_id": request_id}
    file_a = as_log(without_id(1, **more))
    file_b = as_log(without_id(1, **more))
    assert file_a == file_b

    first = world()
    first.run("1", without_id(1, **more))
    second = world()
    second.run("1", without_id(1, **more))
    assert first.state() == second.state() and first.rows()[0]["transaction_id"] == ""
    # all 36 columns of the row are the same for the two requests: nothing in the table tells them apart
    assert len(first.rows()[0]) == 36

    again = first.run("1", without_id(1, **more))       # the same file once more
    other = second.run("1", without_id(1, **more))      # the file of the other container
    assert table_notes(again) == table_notes(other) and APPENDED.fullmatch(table_notes(again)[0]).groups() == ("1", "1", "0", "0", "1", "2", "0", "0", "0")
    assert first.state() == second.state() and len(first.rows()) == 2 and first.rows()[0] == first.rows()[1]


@PROGRAMS
def test_a_request_without_an_id_is_appended_every_time_and_takes_the_place_of_no_row(world, program):
    """Measured with version 2.8: a second append of a file added its three requests without an ID again."""
    w = world(program)
    w.run("1", request(1), without_id(2), without_id(3))

    run = w.run("1", request(1), without_id(2), without_id(3), without_id(2))

    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("4", "3", "0", "0", "3", "6", "1", "1", "0")
    assert sorted(row["transaction_id"] != "" for row in w.rows()) == [False] * 5 + [True]


# ---------------------------------------------------------------------------
# C5 - a run that appends nothing writes neither the table nor its saved file
# ---------------------------------------------------------------------------

@PROGRAMS
def test_an_append_that_adds_nothing_writes_neither_the_table_nor_its_file(world, program):
    """Measured with version 2.8: the table held the same rows afterwards and was written and saved again."""
    w = world(program)
    w.run("1", request(1), request(2))
    table, saved = w.library.get(LIB, TABLE), w.library.file_of(LIB, TABLE + ".sashdat")

    run = w.run("1", request(1), request(2), request(2))

    assert run.errors == [] and run.syscc == 0 and table_notes(run) == [NOTHING % (3, 2, 1)]
    # the very table and the very file: nothing was dropped, built, renamed, promoted or saved
    assert w.library.get(LIB, TABLE) is table and w.library.file_of(LIB, TABLE + ".sashdat") is saved
    assert w.written() == [] and run.macros["_log_append_nothing"] == "1" and nothing_left(w.library)


@PROGRAMS
@pytest.mark.parametrize("promote, exists", [("1", 2), ("0", 1)], ids=["promote", "do not promote"])
def test_a_table_that_is_only_its_saved_file_is_loaded_when_nothing_is_appended(world, program, promote, exists):
    """The table is loaded from its file first, as in every run that appends, and promoted where the run was asked
    to promote. Its file is not written."""
    w = world(program)
    w.run("1", request(1), request(2))
    w.only_saved()
    saved = w.library.file_of(LIB, TABLE + ".sashdat")

    run = w.run("1", request(1), promote=promote)

    assert table_notes(run) == [NOTHING % (1, 1, 0)] and run.syscc == 0
    assert w.written() == ["table.loadTable"] + (["table.promote"] if promote == "1" else [])
    assert w.library.exists(LIB, TABLE) == exists and facts(w.rows()) == facts(saved["rows"])
    assert w.library.file_of(LIB, TABLE + ".sashdat") is saved


@PROGRAMS
def test_a_table_of_the_session_is_promoted_when_nothing_is_appended_and_a_promoted_one_stays(world, program):
    w = world(program)
    w.run("1", request(1), promote="0", save="0")
    assert w.library.exists(LIB, TABLE) == 1 and w.state()["file"] is None

    w.run("1", request(1), promote="1", save="1")

    # promoted as asked; the file of a table that was never saved is not written by a run that writes nothing
    assert w.library.exists(LIB, TABLE) == 2 and w.state()["file"] is None
    table = w.library.get(LIB, TABLE)
    w.run("1", request(1), promote="0")
    assert w.library.exists(LIB, TABLE) == 2 and w.library.get(LIB, TABLE) is table


@PROGRAMS
def test_an_append_that_only_replaces_a_row_writes_the_table(world, program):
    w = world(program)
    w.run("1", cut(1))
    table = w.library.get(LIB, TABLE)

    run = w.run("1", request(1))

    assert APPENDED.fullmatch(table_notes(run)[0]).groups() == ("1", "0", "1", "1", "1", "1", "0", "0", "0")
    assert w.library.get(LIB, TABLE) is not table and [whole(row) for row in w.rows()] == [True] and len(w.state()["file"]) == 1


@PROGRAMS
def test_rows_that_were_not_compared_are_neither_appended_nor_taken_for_nothing(world, program):
    """A DATA step of the comparison that does not run leaves the rows of the log as they were. The run ends with
    its ERROR line, and not with the NOTE of a run that had nothing to append."""
    w = world(program)
    w.run("1", request(1))
    before = w.state()
    w.library.fail_steps.add("_TEMP_LLM_LOGS_SEEN")

    run = w.run("1", request(1))

    assert run.errors[-1].startswith("The new table could not be created, the table Public.LLM_LOGS was left as it is.")
    assert run.errors[-1].endswith("ended with the return code 1012.")
    assert run.syscc == FAILED and table_notes(run) == [] and w.state() == before and run.macros["_log_append_nothing"] == "0"


@PROGRAMS
def test_a_step_that_does_not_take_the_rows_out_leaves_the_table(world, program):
    """The row of the log would stand beside the row it takes the place of: the new table is not put in place."""
    w = world(program)
    w.run("1", request(1), cut(2))
    before = w.state()
    w.library.fail_steps.add("_TEMP_LLM_LOGS_OUT")

    run = w.run("1", request(2), request(3))

    assert run.errors[-1] == (
        "The new table could not be created, the table Public.LLM_LOGS was left as it is. The DATA step that appends ended with the return "
        "code 1012 and wrote 4 rows, the table held 2 and the log 2, and 0 rows of the table were taken out for 1 rows of the log (-1 stands "
        "for a table that was not found). The DATA steps that leave out the requests the table holds already ended with the return code 0.")
    assert run.syscc == FAILED and table_notes(run) == [] and w.state() == before and nothing_left(w.library)


# ---------------------------------------------------------------------------
# What the program says of itself
# ---------------------------------------------------------------------------

@PROGRAMS
def test_no_text_of_a_message_is_longer_than_sas_takes_without_a_note(program):
    """SAS writes a NOTE about quotation marks for a text of more than 262 bytes. The names of the library and of
    the table go into the texts, so each is held well below that."""
    macro = program()[program().index("%macro _log_handle_output_table;"):program().index("%mend _log_handle_output_table;")]

    printed = [line.strip() for line in macro.split("\n") if line.strip().startswith("print (note)")]
    assert len(printed) == 5
    for line in printed:
        assert max(len(text) for text in re.findall(r'"([^"]*)"', line)) < 200, line


@PROGRAMS
def test_the_helper_tables_of_an_append_are_dropped_at_the_end(program):
    last = program()[program().index("%else %if &_log_row_count. EQ 0 %then %do;"):program().index("%mend _log_handle_output_table;")]

    for name in ("_TEMP_LLM_LOGS", "_TEMP_LLM_LOGS_SEEN", "_TEMP_LLM_LOGS_GONE", "_TEMP_LLM_LOGS_OUT"):
        assert re.search(rf"table\.dropTable /\s+casLib = 'CASUSER',\s+table = '{name}',\s+quiet = True;", last), name
    for name in ("_log_target_has_failed", "_log_kept_rows", "_log_took_rows", "_log_append_nothing"):
        assert f"%let {name} = 0;\n" in program()[:program().index("proc python restart;")], name
        assert f" {name}" in next(line for line in program().split("\n") if line.startswith("%symdel _log_ready ")), name


# ---------------------------------------------------------------------------
# What the README and the step say is what a run does
# ---------------------------------------------------------------------------

README = CODE_FILE.parent / "README.md"


def _said(line):
    """A line the README quotes, as the expression a line of a run has to match: <x> stands for a number."""
    quoted = re.escape(line.replace("<caslib>.<table>", "Public.LLM_LOGS"))
    return re.compile(re.sub(r"<\w>", r"\\d+", quoted))


def test_the_readme_quotes_the_lines_a_run_writes(world):
    readme = " ".join(README.read_text(encoding="utf-8").split())
    quoted = {
        "appended": "<r> request(s) were read from the log. <a> were appended to the table <caslib>.<table> and <p> took the place of <x> row(s) of "
                    "it whose failed was missing: the table held <o> row(s) and holds <n> now. <s> were left out, as another row holds their "
                    "request: <t> a row of the table, <l> a row of the log.",
        "started": "<m> were written to the table <caslib>.<table>, which was not there before. <l> were left out, as another row of the log "
                   "holds their request.",
        "nothing": "<r> request(s) were read from the log and none of them was written: the table <caslib>.<table> keeps its rows, and neither "
                   "the table nor its saved file was written. All were left out, as another row holds their request: <t> a row of the table, "
                   "<l> a row of the log.",
        "replaced": "The table <caslib>.<table> held <n> row(s). They were replaced by the <m> request(s) of the log, as _log_append_table is 0.",
        "left out": "<r> request(s) were read from the log. <m> were written to the table <caslib>.<table> and <l> were left out, as another row "
                    "of the log holds their request.",
        "not replaced": "The saved table <caslib>.<table> could not be loaded, nothing was replaced and the saved file was left as it is.",
        "not started": "The saved table <caslib>.<table> could not be loaded and _log_append_table is not set, so nothing was read and nothing "
                       "was written - the saved file was left as it is.",
    }
    for name, line in quoted.items():
        assert "`" + ("... " if name == "started" else "NOTE: " if name in ("appended", "nothing", "replaced", "left out") else "ERROR: ") + line + "`" in readme, name

    w = world()
    first = w.run("1", request(1), cut(2))
    assert _said("2 request(s) were read from the log. " + quoted["started"]).fullmatch(table_notes(first)[0])
    assert _said(quoted["appended"]).fullmatch(table_notes(w.run("1", request(2), request(3)))[0])
    assert _said(quoted["nothing"]).fullmatch(table_notes(w.run("1", request(3)))[0])
    replaced = table_notes(w.run("0", request(4), request(4)))
    assert _said(quoted["replaced"]).fullmatch(replaced[0]) and _said(quoted["left out"]).fullmatch(replaced[1])
    w.only_saved()
    w.library.unloadable.add(TABLE + ".sashdat")
    assert _said(quoted["not replaced"]).fullmatch(w.run("0", request(5)).errors[-1])
    assert _said(quoted["not started"]).fullmatch(w.run(None, request(5)).errors[-1])
    # the sums the README gives for the numbers of an append
    assert "`r` is `a` + `p` + `s`, `s` is `t` + `l`, and `n` is `o` + `a` + `p` - `x`" in readme


def test_the_readme_says_what_a_request_is_known_by_and_which_row_gives_way():
    readme = " ".join(README.read_text(encoding="utf-8").split())

    for said in (
            "A request is known by three columns together: its `transaction_id`, its `model` and its `timestamp`, the time of its request line.",
            "The ID alone is not taken for the request",
            "A row is **incomplete** when its `failed` is missing",
            "A complete row of the table is never replaced",
            "A request without a `transaction_id` is never taken for another one and is appended every time it is parsed.",
            "A run that appends nothing writes nothing",
            # what the file of release 2.2 took for a value of the setting, and what is refused now
            "The file of release 2.2 read the setting in `%if &_log_append_table. %then`, where the macro language takes every whole number other "
            "than 0 for true: `2`, `01` and `-1` meant append there, and `00` meant replace. Each of them is refused now"):
        assert said in readme, said
    for gone in ("A run that appends nothing writes the table again all the same", "is written as it is in this mode",
                 "A request that is in the table stays as it was read the first time", "earlier in the log"):
        assert gone not in readme, gone
    # what a release has not run yet is said in the changelog of the release, not here
    assert "not run in sas" not in readme.lower()


def test_the_step_says_what_its_program_does_with_a_request_it_holds_already():
    texts = {}

    def walk(children):
        for child in children:
            if "text" in child:
                texts[child["id"]] = child["text"]
            walk(child.get("children", []))

    walk(json.loads(step_definition()["ui"])["pages"])

    assert texts["textChangelog"].startswith("* Version: 2.9 (14OCT2026)\n     - A request is known by its transaction_id, its model and the time of its request line")
    for control, said in (
            ("_log_info", "Where that row holds neither a response nor an error and the log holds the request with one of the two, the row of the log takes its place."),
            ("_log_info", "Where nothing is left to add, the table and its saved table are not written"),
            ("_log_info", "A request the log holds more than once - a file that was collected twice - gets one row, whatever the checkbox says."),
            ("textAbout", "A request the log itself holds more than once gets one row in both cases."),
            ("textColumns", "A request is known by three columns together: its transaction_id, its model and its timestamp, the time of its request line."),
            ("textColumns", "Where that saved file can not be loaded, the step ends with one ERROR line of its own and writes nothing: no table is started and the saved file is not written over"),
            ("textColumns", "Where every request of the log is in the table already and none takes the place of a row, no table is written"),
            ("textPrereqs", "_TEMP_LLM_LOGS_SEEN and _TEMP_LLM_LOGS_GONE, and a third one, _TEMP_LLM_LOGS_OUT")):
        assert said in texts[control], said
    for text in texts.values():
        for gone in ("A request that is in the table stays as it was read the first time", "is written as it is: the request has a row for every time"):
            assert gone not in text, gone
        assert "not run in sas" not in text.lower() and "has not run" not in text.lower()
