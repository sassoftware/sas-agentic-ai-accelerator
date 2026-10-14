"""What the checks of 2026-10-14 found in the output table code of the log parser (version 2.9), followed against the
made-up CAS library of log_parser_table_harness:

- a run without a value of _log_append_table counted a table that CAS says is there and does not list as a table
  without rows, and replaced it.

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
# What the README of the folder says of these cases
# ---------------------------------------------------------------------------

def test_the_readme_says_what_the_program_does_in_these_cases():
    from log_parser_harness import CODE_FILE
    readme = " ".join((CODE_FILE.parent / "README.md").read_text(encoding="utf-8").split())

    quoted = NOT_COUNTED[:NOT_COUNTED.index(" Set it in front")].replace("Public.LLM_LOGS", "<caslib>.<table>")
    assert "`ERROR: " + quoted + " Set it in front of the program: ...`" in readme
    assert "a run with `0` replaces such a table and writes no NOTE of the rows it held" in readme
