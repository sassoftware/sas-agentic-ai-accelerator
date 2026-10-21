"""The CAS session of every program of this folder and of the two custom steps.

A program that runs 'cas mySess;' whatever the SAS session holds, and 'cas mySess terminate;'
at its end, does two things behind another program in one SAS session: SAS warns that a
session of that name exists, which SAS Job Execution reports as the error of the job, and the
session the librefs of the other program are bound to is ended. Log-Parser-Code.sas,
Get-All-Prompts.sas and Link-Logs-To-Models.sas ask first; Load-Fact-Sheets.sas and
Build-RAG-Cost-View.sas did not.

Every program asks the same way: SESSFOUND of the session the option SESSREF names. Where that
one is not connected but the session of the program's own name is, the option is set to that
session (fix-content-23, test_fix_content_23.py). It starts a session only when neither is
connected, and ends only the one it started.

The SAS code can not run here. The tests read it as text.
"""
import json
import re
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[1]
STEPS = FOLDER.parent / "Custom-Steps"

CHECK = "%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;"


def guard(session, condition="%sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1"):
    """The three lines in front of the check: a connected session of the program's own name is used."""
    return (f"%if {condition} and %sysfunc(sessfound({session})) EQ 1 %then %do;\n"
            f"    options sessref={session};\n%end;\n")
# 'cas <name>;' and 'cas <name> terminate;', with whatever stands between the name and the semicolon
STATEMENT = re.compile(r"^[ \t]*cas[ \t]+(\w+)\b([^;\n]*);[ \t]*$", re.IGNORECASE | re.MULTILINE)

# Per program: the session it starts, and the macro variable that says whether it did
SESSIONS = {
    "Log-Parser-Code.sas": ("llmLogMySess", "_log_clean_cas_session"),
    "LLM - Log Parser.step": ("llmLogMySess", "_log_clean_cas_session"),
    "Get-All-Prompts.sas": ("mySess", "_gap_clean_cas_session"),
    "LLM - Get All Prompts.step": ("gapMySess", "_gap_clean_cas_session"),
    "Link-Logs-To-Models.sas": ("lnkMySess", "_lnk_clean_cas_session"),
    "Load-Fact-Sheets.sas": ("mySess", "_lfs_clean_cas_session"),
    "Build-RAG-Cost-View.sas": ("_rcvSess", "_rcv_clean_cas_session"),
}


def program(name):
    if name.endswith(".step"):
        return json.loads((STEPS / name).read_text(encoding="utf-8"))["templates"]["SAS"].replace("\r\n", "\n")
    return (FOLDER / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def statements(text):
    """The statements of a program that start or end a CAS session, as (line number, session, 'terminate' or '')."""
    found = []
    for match in STATEMENT.finditer(text):
        found.append((text.count("\n", 0, match.start()), match.group(1), " ".join(match.group(2).split()).lower()))
    return found


def test_every_program_of_the_folder_is_looked_at():
    """A program that is added to the folder has to say here which session it starts."""
    programs = sorted(path.name for path in FOLDER.glob("*.sas"))
    assert programs == sorted(name for name in SESSIONS if name.endswith(".sas"))
    for name in SESSIONS:
        assert statements(program(name)), name


@pytest.mark.parametrize("name", list(SESSIONS))
def test_program_starts_a_session_only_when_none_is_connected_and_ends_only_its_own(name):
    session, flag = SESSIONS[name]
    text = program(name)
    lines = text.split("\n")
    found = statements(text)
    if name == "Log-Parser-Code.sas":
        # The code file starts mysess for its two librefs in its first lines, under the same check, and leaves it:
        # the librefs stay when the program ends. The custom step is the program from below these lines on
        line, first, rest = found.pop(0)
        assert (first, rest) == ("mysess", "") and lines[line - 1] == CHECK and lines[line + 1] == "%end;"
        assert text.count(guard("mysess") + CHECK + "\n    cas mysess;\n") == 1
        assert "mysess terminate" not in text.lower().replace("llmlogmysess", "")

    assert [(started, rest) for _, started, rest in found] == [(session, ""), (session, "terminate")]
    (start, _, _), (end, _, _) = found
    # Started in the branch of a session that is not connected, with the macro variable that says so
    assert [line.strip() for line in lines[start - 2:start + 5]] == [
        CHECK, f"%let {flag} = 1;", f"cas {session};", "%end;", "%else %do;", f"%let {flag} = 0;", "%end;"]
    # In front of the check a connected session of the program's own name is made the session SESSREF names
    assert text.count(guard(session) + CHECK + f"\n    %let {flag} = 1;\n") == 1
    # Ended under that macro variable and nowhere else
    assert [line.strip() for line in lines[end - 1:end + 2]] == [f"%if &{flag}. %then %do;", f"cas {session} terminate;", "%end;"]
    assert start < end
    # No other statement names a session: nothing runs in a session of a fixed name but in the guards
    rest = text.replace(guard(session), "").replace(guard("mysess"), "").replace(CHECK, "")
    assert not re.search(r"sessref\s*=|session\s*=\s*" + session, rest, flags=re.IGNORECASE)
    # The macro variable is deleted with the others
    deleted = " ".join(" ".join(part.split()) for part in re.findall(r"%symdel\s+([^;]*);", text))
    assert flag in deleted.split()


@pytest.mark.parametrize("name", ["Load-Fact-Sheets.sas", "Build-RAG-Cost-View.sas"])
def test_nothing_asks_cas_for_anything_before_the_session_is_there(name):
    session, _ = SESSIONS[name]
    text = program(name)
    started = text.index(f"cas {session};")
    first_use = min(position for position in (text.lower().find(word) for word in ("proc casutil", "caslib _all_", "proc cas;")) if position != -1)
    assert text.index(CHECK) < started < first_use
    # The comment in front of the check is closed, and holds nothing SAS reads as code
    assert text.index(guard(session)) + len(guard(session)) == text.index(CHECK)
    above = text[:text.index(guard(session))].rstrip().split("\n")[-1]
    if above.startswith("*"):
        assert above.endswith(";") and not re.search(r"[%&'\";]", above[:-1])
    else:
        assert above.endswith("*/")


def test_fact_sheets_are_loaded_in_the_session_that_is_connected():
    """Behind Log-Parser-Code.sas in one SAS session the program found mysess connected, started it again and
    ended it: the WARNING of a session that exists, and the librefs public and casuser of the parser
    without a session."""
    text = program("Load-Fact-Sheets.sas")

    assert statements(text) == [(statements(text)[0][0], "mySess", ""), (statements(text)[1][0], "mySess", "terminate")]
    # The sheets are uploaded by PROC CASUTIL and put in the place of their tables by PROC CAS, both of which run in
    # the session SESSREF names: one upload, one promote and one save in the two macros, called once per sheet
    assert 'proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";' in text
    assert text.count("proc casUtil") == 1 and text.count("table.promote ") == 1 and text.count("table.save ") == 1
    assert text.count("%_lfs_swap(") == 3 and "cas mySess;" not in text[text.index("%macro _lfs_read("):]
    assert text.rstrip().endswith("%symdel _lfs_path _lfs_caslib _lfs_clean_cas_session / nowarn;")
    assert "A CAS session will be created for you" not in text
    assert "A CAS session is started for you when neither the session the option\n      SESSREF names nor a session mySess is connected" in text
