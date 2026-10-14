"""What the checks of 2026-10-14 found in the output table code of the log parser (version 2.9), followed against the
made-up CAS library of log_parser_table_harness:

- a run without a value of _log_append_table counted a table that CAS says is there and does not list as a table
  without rows, and replaced it;
- an append that could not build its table left the table it had loaded from its saved file for the append loaded,
  as a table of the session of the run.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import copy

import pytest

from log_parser_harness import as_log, code_file_program, llm_block, step_program
from log_parser_table_harness import FAILED, Library, run_program

LIB, TABLE = "Public", "LLM_LOGS"
PROGRAMS = pytest.mark.parametrize("program", [code_file_program, step_program], ids=["code file", "step"])
NOT_COUNTED = ("The rows of the table Public.LLM_LOGS could not be counted and _log_append_table is not set, so nothing was read and "
               "nothing was written - not the table and not its saved file. Set it in front of the program: %let _log_append_table = 1; "
               "adds the requests of the log to the table, %let _log_append_table = 0; replaces the table with them.")


def request(number, **more):
    return llm_block(timestamp=f"2026-03-01 10:{number:02d}:00", user_prompt=f"Made-up question {number}?", **more)


def without_id(number, **more):
    """A request whose lines name no transaction: the lines of its transaction and the Response Data line are gone."""
    return [line for line in request(number, **more) if " - transaction_" not in line and "Response Data:" not in line]


class World:
    def __init__(self, monkeypatch, tmp_path, program):
        self.monkeypatch, self.tmp_path, self.program = monkeypatch, tmp_path, program()
        self.library = Library()
        self.files = 0

    def run(self, setting, *blocks):
        self.files += 1
        path = self.tmp_path / f"llms-{id(self)}-{self.files}.log"
        path.write_text(as_log(*blocks), encoding="utf-8", newline="")
        return run_program(self.program, self.library, self.monkeypatch, path, setting=setting, caslib=LIB, table=TABLE)

    def state(self):
        return self.library.state(LIB, TABLE)

    def rows(self):
        return self.library.get(LIB, TABLE)["rows"]


@pytest.fixture
def world(monkeypatch, tmp_path):
    return lambda program=code_file_program: World(monkeypatch, tmp_path, program)


def not_listed(monkeypatch):
    """CAS answers tableExists for the output table and lists no table when it is asked for its rows (quiet)."""
    real = Library.__call__

    def call(self, action, parameters):
        if action == "table.tableInfo" and str(parameters.get("name", "")).upper() == TABLE:
            self.calls.append((action, parameters))
            return self._end(action, {})
        return real(self, action, parameters)
    monkeypatch.setattr(Library, "__call__", call)


# ---------------------------------------------------------------------------
# A table that is there and is not listed
# ---------------------------------------------------------------------------

@PROGRAMS
def test_a_run_without_the_setting_does_not_start_a_table_it_could_not_count(world, program, monkeypatch):
    """Version 2.9 counted such a table as 0 rows: the run replaced its 3 rows and its saved file with the 1 request
    of the log, without the setting and without an ERROR line. The count of a saved file already started at -1."""
    w = world(program)
    w.run("1", request(1), request(2), request(3))
    before = copy.deepcopy(w.state())
    assert len(before["rows"]) == 3
    not_listed(monkeypatch)

    run = w.run(None, request(7))

    assert run.errors == [NOT_COUNTED]
    assert run.syscc == FAILED and w.state() == before
    assert run.macros["_log_target_rows"] == "-2" and run.macros["_log_ready"] == "0"


@PROGRAMS
def test_a_run_that_replaces_a_table_it_could_not_count_replaces_it_as_before(world, program, monkeypatch):
    """With 0 the count only gives the number for the NOTE of the rows that were replaced, so the run replaces the
    table as 2.9 did, and says no number it does not have. Only a saved file that can not be loaded stops it."""
    w = world(program)
    w.run("1", request(1), request(2), request(3))
    not_listed(monkeypatch)

    run = w.run("0", request(7))

    assert run.errors == [] and run.syscc == 0
    assert len(w.rows()) == 1 and len(w.state()["file"]) == 1
    assert run.macros["_log_target_unloaded"] == "0"
    assert not [note for note in run.notes if note.startswith("The table Public.LLM_LOGS held")]


# ---------------------------------------------------------------------------
# An append that fails after it loaded the table from its saved file
# ---------------------------------------------------------------------------

def _only_saved(w):
    """The table as a restart of the CAS server leaves it: its saved file, and nothing in memory."""
    w.library.promoted.pop(w.library.key(LIB, TABLE), None)
    w.library.session.pop(w.library.key(LIB, TABLE), None)


@PROGRAMS
def test_an_append_that_fails_drops_the_table_it_loaded_for_the_append(world, program):
    """Version 2.9 left the table it had loaded from the saved file loaded, as a table of the session of the run."""
    w = world(program)
    w.run("1", request(1), request(2))
    _only_saved(w)
    before = copy.deepcopy(w.state())
    assert before["exists"] == 0 and len(before["file"]) == 2
    w.library.fail_steps.add("_TEMP_LLM_LOGS_SEEN")

    run = w.run("1", request(3))

    assert run.errors[-1].startswith("The new table could not be created, the table Public.LLM_LOGS was left as it is.")
    assert w.state() == before and run.syscc == FAILED
    assert w.library.tables_named("_TEMP_LLM_LOGS") == []


@PROGRAMS
@pytest.mark.parametrize("scope", ["session", "global"])
def test_an_append_that_fails_leaves_a_table_it_did_not_load(world, program, scope):
    """A table that was in memory before the run - of the session or promoted - is not dropped: the run did not load it."""
    w = world(program)
    w.run("1", request(1), request(2))
    if scope == "session":
        w.library.session[w.library.key(LIB, TABLE)] = w.library.promoted.pop(w.library.key(LIB, TABLE))
    before = copy.deepcopy(w.state())
    w.library.fail_steps.add("_TEMP_LLM_LOGS_SEEN")

    run = w.run("1", request(3))

    assert run.errors[-1].startswith("The new table could not be created, the table Public.LLM_LOGS was left as it is.")
    assert run.macros["_log_target_loaded"] == "0" and w.state() == before


@PROGRAMS
def test_an_append_that_works_keeps_the_table_it_loaded(world, program):
    """The drop is in the branch of the ERROR line only: a table loaded for an append that went through is the table."""
    w = world(program)
    w.run("1", request(1), request(2))
    _only_saved(w)

    run = w.run("1", request(3))

    assert run.errors == [] and run.macros["_log_target_loaded"] == "1"
    assert w.state()["exists"] == 2 and len(w.rows()) == 3 and len(w.state()["file"]) == 3


def test_the_drop_is_written_for_the_table_of_the_run_and_only_behind_the_error_line():
    program = code_file_program()
    macro = program[program.index("%macro _log_handle_output_table;"):program.index("%mend _log_handle_output_table;")]

    drop = ('%if &_log_target_loaded. %then %do;\n'
            '                            table.dropTable /\n'
            '                                casLib = "&_log_target_table_lib.",\n'
            '                                name = "&_log_target_table_name.",\n'
            '                                quiet = True;\n'
            '                        %end;')
    assert macro.count(drop) == 1 and macro.count("&_log_target_loaded.") == 1
    assert macro.index("print (error) \"The new table could not be created") < macro.index(drop) < macro.index("tableWritten = False;\n                end;")
    # set where the run loaded the file, and only to a table of the session: a promoted table of that name is not the run's
    assert "if savedFile NE '' and re.exists EQ 1 then do;\n                    call symputx('_log_target_loaded', '1');" in macro
    assert "%let _log_target_loaded = 0;\n" in program and " _log_target_loaded " in program[program.rindex("%symdel _log_ready"):]


# ---------------------------------------------------------------------------
# What the README of the folder says of these cases
# ---------------------------------------------------------------------------

def test_the_readme_says_what_the_program_does_in_these_cases():
    from log_parser_harness import CODE_FILE
    readme = " ".join((CODE_FILE.parent / "README.md").read_text(encoding="utf-8").split())

    quoted = NOT_COUNTED[:NOT_COUNTED.index(" Set it in front")].replace("Public.LLM_LOGS", "<caslib>.<table>")
    assert "`ERROR: " + quoted + " Set it in front of the program: ...`" in readme
    assert "a run with `0` replaces such a table and writes no NOTE of the rows it held" in readme
    assert "one that the run loaded from its saved file for the append is dropped again, so that only its saved file is left, as before the run" in readme
    assert "stays loaded, as a table of the session of the run" not in readme
