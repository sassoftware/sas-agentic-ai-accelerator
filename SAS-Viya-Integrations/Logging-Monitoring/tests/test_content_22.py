"""What the checks of round 21 found in the output table code of the log parser (version 2.10 of the step), followed
against the made-up CAS library of log_parser_table_harness:

- a listing of the saved files of the CAS library that CAS refused (table.fileInfo with an error) was taken for a
  library without the saved file of the table, in the count and in append mode: in every state of _log_append_table
  the run started a new table and saved it over the file that held the rows of the runs before, behind the ERROR
  lines of CAS;
- the same holds for the whole class: every action of CAS whose answer the count and the output macro go by - the
  question whether a table is there, the listings of the tables, of the saved files and of the columns. The last test
  of the class takes the actions from the program itself, refuses each one in turn and follows every state of the
  setting, with the table in memory and with the table as its saved file only.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import copy
import re

import pytest

from log_parser_harness import CODE_FILE, as_log, code_file_program, llm_block, step_definition, step_program
from log_parser_table_harness import FAILED, Library, run_program

LIB, TABLE = "Public", "LLM_LOGS"
PROGRAMS = pytest.mark.parametrize("program", [code_file_program, step_program], ids=["code file", "step"])
STATES = [None, "0", "1"]

REFUSED_START = ("CAS refused an action that the count of the rows of the table Public.LLM_LOGS needs and _log_append_table is not set, "
                 "so nothing was read and nothing was written - not the table and not its saved file.")
REFUSED = {"1": "CAS refused an action that the run needs for the table Public.LLM_LOGS, nothing was appended and neither the table nor "
                "its saved file was written.",
           "0": "CAS refused an action that the run needs for the table Public.LLM_LOGS, nothing was replaced and neither the table nor "
                "its saved file was written.",
           None: REFUSED_START}
OF_CAS = re.compile(r"The action table\.\w+ was refused\.|The action stopped due to errors\.")


def request(number):
    return llm_block(timestamp=f"2026-03-01 10:{number:02d}:00", user_prompt=f"Made-up question {number}?")


class Refusing(Library):
    """The made-up CAS library, which refuses the one call of the program that carries the parameter zzRefused - the
    action a test marked in a copy of the program - with an ERROR line, as it refuses an action named in `refuse`."""

    def __init__(self, **more):
        super().__init__(**more)
        self.refused = []

    def __call__(self, action, parameters):
        if parameters.pop("zzrefused", None):
            self.refused.append(action)
            self.calls.append((action, parameters))
            return self._end(action, error=f"The action {action} was refused.")
        return super().__call__(action, parameters)


class World:
    def __init__(self, monkeypatch, tmp_path, program, library=None):
        self.monkeypatch, self.tmp_path, self.program = monkeypatch, tmp_path, program
        self.library = library or Refusing()
        self.files = 0

    def run(self, setting, *blocks, program=None):
        self.files += 1
        path = self.tmp_path / f"llms-{id(self)}-{self.files}.log"
        path.write_text(as_log(*blocks), encoding="utf-8", newline="")
        return run_program(program or self.program, self.library, self.monkeypatch, path, setting=setting, caslib=LIB, table=TABLE)

    def state(self):
        return copy.deepcopy(self.library.state(LIB, TABLE))

    def only_saved(self):
        self.library.promoted.pop(self.library.key(LIB, TABLE), None)
        self.library.session.pop(self.library.key(LIB, TABLE), None)


def table_with_3_rows(monkeypatch, tmp_path, program, saved_only, library=None):
    """A table of 3 requests that a run with 1 wrote, promoted and saved - in memory, or as its saved file only, as a
    restart of the CAS server leaves it."""
    w = World(monkeypatch, tmp_path, program, library)
    first = w.run("1", request(1), request(2), request(3))
    assert first.errors == [] and len(w.state()["file"]) == 3
    if saved_only:
        w.only_saved()
    return w


def nothing_written(w, before, run, setting):
    """The run wrote nothing: the table and its saved file are as before, no table of the program is left, the job
    fails, and the last ERROR line is the program's own, behind the ones of CAS."""
    assert w.state() == before
    assert w.library.tables_named("_TEMP_LLM_LOGS") == [] and w.library.work == {}
    assert run.syscc == FAILED
    own = [error for error in run.errors if not OF_CAS.fullmatch(error)]
    assert own and run.errors[-1] == own[-1]
    # nothing of the log reached the table: no NOTE of the table
    assert not [note for note in run.notes if "were written to the table" in note or "were appended to the table" in note
                or note.startswith("The table Public.LLM_LOGS held")]


# ---------------------------------------------------------------------------
# C1: a listing of the saved files that CAS refused, in the three states of the setting
# ---------------------------------------------------------------------------

@PROGRAMS
@pytest.mark.parametrize("setting", STATES, ids=["no value", "0", "1"])
def test_a_refused_listing_of_the_saved_files_writes_nothing_in_every_state(monkeypatch, tmp_path, program, setting):
    """Version 2.9 started a new table at a library whose files CAS did not list and saved it over the file: the file
    held 1 row of the 3 afterwards, in every state, behind the ERROR lines of CAS and with SYSCC 1012. The count
    (no value, 0) and append mode (1) take such a listing for an action that was refused now."""
    w = table_with_3_rows(monkeypatch, tmp_path, program(), saved_only=True, library=Refusing())
    before = w.state()
    assert before["exists"] == 0 and len(before["file"]) == 3
    w.library.refuse.add("table.fileInfo")

    run = w.run(setting, request(7))

    nothing_written(w, before, run, setting)
    assert run.errors == ["The action table.fileInfo was refused.", "The action stopped due to errors.", REFUSED[setting]]
    # a run without the setting stops in front of the log, the two others have read it
    assert (run.uploaded is None) == (setting is None)
    assert run.macros["_log_failed"] == ("0" if setting is None else "1")
    assert run.macros["_log_target_rows"] == ("0" if setting == "1" else "-3")


@PROGRAMS
@pytest.mark.parametrize("setting", STATES, ids=["no value", "0", "1"])
def test_a_table_in_memory_is_not_asked_for_its_saved_file(monkeypatch, tmp_path, program, setting):
    """The listing of the saved files is asked for only where the table is not in memory, so a library whose files CAS
    does not list changes nothing for such a table: a run with 1 appends, one with 0 replaces, and one without the
    setting stops at its rows, as before."""
    w = table_with_3_rows(monkeypatch, tmp_path, program(), saved_only=False)
    w.library.refuse.add("table.fileInfo")
    calls = len(w.library.calls)

    run = w.run(setting, request(7))

    assert "table.fileInfo" not in [action for action, _ in w.library.calls[calls:]]
    assert len(w.state()["rows"]) == len(w.state()["file"]) == {None: 3, "0": 1, "1": 4}[setting]
    if setting is None:
        assert len(run.errors) == 1 and run.errors[0].startswith("The table Public.LLM_LOGS holds 3 rows and _log_append_table is not set")
    else:
        assert run.errors == []
    assert run.syscc == (FAILED if setting is None else 0)


# ---------------------------------------------------------------------------
# The class: every action of CAS whose answer the two macros go by
# ---------------------------------------------------------------------------

ASKS = re.compile(r"table\.(tableExists|tableInfo|fileInfo|columnInfo) result=\w+(?: status=\w+)? /")


def _asks(program):
    """The actions of the count and of the output macro whose answer the program reads, as positions in the program."""
    found = []
    for macro in ("_log_count_target", "_log_handle_output_table"):
        start, end = program.index(f"%macro {macro};"), program.index(f"%mend {macro};")
        found += [match for match in ASKS.finditer(program, start, end)]
    return found


def _refusing(program, ask):
    """The program with the one action marked, so that the made-up library refuses that call and no other."""
    slash = ask.end()
    return program[:slash] + " zzRefused = 1," + program[slash:]


def test_the_class_holds_every_action_the_two_macros_read():
    """The class is taken from the program: twelve actions, and each of them is followed below."""
    names = [ask.group(1) for ask in _asks(code_file_program())]
    assert names == ["tableExists", "tableInfo", "fileInfo", "tableInfo",
                     "tableExists", "fileInfo", "tableExists", "columnInfo", "tableInfo", "tableInfo", "tableInfo", "tableExists"]
    assert [ask.group(1) for ask in _asks(step_program())] == names


@PROGRAMS
def test_an_action_cas_refuses_is_never_taken_for_no_table_and_no_file(monkeypatch, tmp_path, program):
    """Each action of the class is refused in turn, in every state of the setting, at a table in memory (promoted and
    saved) and at a table that is only its saved file. Every run that reaches the action writes nothing, and every
    action is reached by at least one of them.

    Two of them are asked with quiet = True and give the counts of -2 and -1 when they are refused - a table in memory
    whose rows could not be counted, a saved file that could not be counted - which the program does not take for 0
    rows either. A run with 0 replaces a table in memory whose rows could not be counted (-2): it is the table the run
    was asked to replace, and the NOTE of the rows it held is left out. The count of such a run is the only answer
    of the class that does not stop a run."""
    text = program()
    reached = {}
    for number, ask in enumerate(_asks(text)):
        marked = _refusing(text, ask)
        for saved_only in (False, True):
            for setting in STATES:
                w = table_with_3_rows(monkeypatch, tmp_path, text, saved_only)
                before = w.state()
                run = w.run(setting, request(7), program=marked)
                if not w.library.refused:
                    continue
                reached.setdefault(number, []).append((saved_only, setting))
                if ask.group(1) == "tableInfo" and "foundTables" in ask.group(0) and setting == "0":
                    # the table in memory is replaced as the run was asked to; its rows could not be counted
                    assert len(w.state()["rows"]) == 1 and run.macros["_log_target_rows"] == "-2", (number, saved_only, setting)
                    continue
                nothing_written(w, before, run, setting)
    assert sorted(reached) == list(range(len(_asks(text)))), reached


# ---------------------------------------------------------------------------
# What the texts say of it
# ---------------------------------------------------------------------------

def test_the_readme_says_what_a_run_does_where_cas_refused_an_action():
    readme = " ".join((CODE_FILE.parent / "README.md").read_text(encoding="utf-8").split())

    assert "`ERROR: " + REFUSED_START.replace("Public.LLM_LOGS", "<caslib>.<table>") + "`" in readme
    for setting in ("1", "0"):
        assert "`ERROR: " + REFUSED[setting].replace("Public.LLM_LOGS", "<caslib>.<table>") + "`" in readme


def _about():
    import json
    found = {}

    def walk(children):
        for child in children:
            if child.get("type") == "text":
                found[child["id"]] = child["text"]
            walk(child.get("children", []))
    walk(json.loads(step_definition()["ui"])["pages"])
    return found


def test_the_step_says_it_in_its_about_tab_and_its_changelog():
    texts = _about()
    assert texts["textChangelog"].startswith("* Version: 2.10 (14OCT2026)\n")
    entry = texts["textChangelog"][:texts["textChangelog"].index("* Version: 2.9 (")]
    assert "CAS refuses" in entry and "writes nothing" in entry
    assert "an action that CAS refused" in texts["textAbout"]
