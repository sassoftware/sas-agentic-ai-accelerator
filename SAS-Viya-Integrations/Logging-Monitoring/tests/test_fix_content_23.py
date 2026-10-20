"""The class 'a program starts a CAS session of a name that is connected, or ends a session it did not start', closed
with one rule in every program that is not frozen and in the program of every step that is no RAG step
(fix-content-23).

Round 22 measured on SAS Viya: where the session the option SESSREF named had been ended while the session of the
program's own name was still connected - the parser's mySess, behind which a session zzs was started and ended -,
Get-All-Prompts.sas found SESSREF not connected and ran `cas mySess;`. SAS only wrote 'WARNING: A session with the name
MYSESS already exists.' and left SESSREF on the ended session, so every PROC CAS step halted, and at its end the program
ran `cas mySess terminate;` and ended the parser's session. The rule now: in front of every `cas <name>;` that starts a
session the program asks SESSFOUND for <name>, and a connected session of that name is used - the option SESSREF is
set to it where the program goes by the option - and is never ended.

The tests take every statement that starts or ends a CAS session from the programs themselves (all of
SAS-Viya-Integrations that is not frozen, *.sas and the non-RAG *.step) and run the statements of the session check and
of the clean-up in every state of the SAS session that matters: each subset of the sessions the program names and a
foreign one connected, and the option SESSREF on each of them, on a foreign session and on one that is not connected.
SESSFOUND, `cas <name>;`, `cas <name> terminate;` and OPTIONS SESSREF= do there what the SAS log of round 22 showed.
Up to this change the state 'SESSREF ended, the program's own session connected' made every program start its session
a second time, and every program but the first lines of Log-Parser-Code.sas, which never end mysess, end it.

SAS can not run here: the tests read the code as text.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import itertools
import re

import pytest

from test_fix_content_22 import FROZEN, INTEGRATIONS, _programs

# Every statement that starts or ends a CAS session, at any indentation
START = re.compile(r"^[ \t]*cas[ \t]+(\w+)[ \t]*;", flags=re.IGNORECASE | re.MULTILINE)
END = re.compile(r"^[ \t]*cas[ \t]+(\w+)[ \t]+terminate[ \t]*;", flags=re.IGNORECASE | re.MULTILINE)
SESSREF = re.compile(r"^[ \t]*options[ \t]+sessref[ \t]*=[ \t]*(\w+)[ \t]*;", flags=re.IGNORECASE | re.MULTILINE)
IF = re.compile(r"^%if (.+) %then %do;$", flags=re.IGNORECASE)

# The programs that name their session in every CAS statement, and still need the option SESSREF on a connected session
NAMES_ITS_SESSION = {"Prompt-Optimization/Create-Optimization-Dataset.sas"}
# Macro variables a condition of the check reads that the program sets before it, with each value they can have
SET_BEFORE = {"_lat_ready": ("0", "1")}
FOREIGN = "ZZS"


def _code(text):
    """The program without its comments: /* */ and the statements that start with an asterisk, also over several lines."""
    text = re.sub(r"/\*.*?\*/", "", text.replace("\r\n", "\n"), flags=re.DOTALL)
    return re.sub(r"(?m)^[ \t]*\*[^;]*;", "", text)


def _session_statements(code):
    """The statements of the session check and of the clean-up, in order: each a ('block', condition, body, else body) of
    a %IF at the start of a line whose branches start, end or name a session, or a ('line', statement) of a CAS or %LET
    statement at the start of a line that stands outside such a block."""
    lines = code.split("\n")
    found, index = [], 0

    def body(start):
        end = start
        while lines[end].strip().lower() != "%end;" or lines[end] != lines[end].lstrip():
            end += 1
        return [line.strip() for line in lines[start:end] if line.strip()], end + 1

    while index < len(lines):
        line = lines[index]
        match = IF.match(line)
        if match:
            then, after = body(index + 1)
            other = []
            following = after
            while following < len(lines) and not lines[following].strip():
                following += 1
            if following < len(lines) and lines[following].strip().lower() == "%else %do;" and lines[following][0] == "%":
                other, after = body(following + 1)
            branches = "\n".join(then + other)
            if START.search(branches) or END.search(branches) or SESSREF.search(branches):
                found.append(("block", match.group(1), then, other))
            index = after
            continue
        if line and line[0] != " " and (START.match(line) or END.match(line) or SESSREF.match(line)
                                        or re.match(r"%let \w+_clean_cas_session = \d;$", line, flags=re.IGNORECASE)):
            found.append(("line", line.strip()))
        index += 1
    return found


def _members():
    """{program: (the sessions it starts, the sessions it ends)} - every statement of the class, taken from the code."""
    found = {}
    for name, text in _programs().items():
        code = _code(text)
        started, ended = START.findall(code), END.findall(code)
        if started or ended:
            found[name] = (started, ended)
    return found


def test_the_class_is_every_statement_that_starts_or_ends_a_cas_session():
    members = _members()

    # A new program or step that starts or ends a session fails here until it is looked at below
    assert members == {
        "Custom-Steps/LLM - Get All Prompts.step": (["gapMySess"], ["gapMySess"]),
        "Custom-Steps/LLM - Log Parser.step": (["llmLogMySess"], ["llmLogMySess"]),
        "Logging-Monitoring/Build-RAG-Cost-View.sas": (["_rcvSess"], ["_rcvSess"]),
        "Logging-Monitoring/Get-All-Prompts.sas": (["mySess"], ["mySess"]),
        "Logging-Monitoring/Link-Logs-To-Models.sas": (["lnkMySess"], ["lnkMySess"]),
        "Logging-Monitoring/Load-Fact-Sheets.sas": (["mySess"], ["mySess"]),
        "Logging-Monitoring/Log-Parser-Code.sas": (["mysess", "llmLogMySess"], ["llmLogMySess"]),
        "Prompt-Optimization/Create-Optimization-Dataset.sas": (["_optbuild"], ["_optbuild"]),
        "Setup/Load-Accelerator-Tables.sas": (["_latSess"], ["_latSess"]),
    }
    # the programs looked at are every program that is not frozen and every step that is no RAG step
    every = {path.relative_to(INTEGRATIONS).as_posix() for path in INTEGRATIONS.rglob("*.sas") if "/tests/" not in path.as_posix()}
    steps = {"Custom-Steps/" + path.name for path in (INTEGRATIONS / "Custom-Steps").glob("*.step") if not path.name.startswith("RAG - ")}
    assert set(_programs()) == (every - set(FROZEN)) | steps
    # every such statement is one the session check or the clean-up runs: none hides in a macro or a branch of its own
    for name in members:
        statements = _session_statements(_code(_programs()[name]))
        reached = []
        for statement in statements:
            text = "\n".join(statement[2] + statement[3]) if statement[0] == "block" else statement[1]
            reached += [(session, "") for session in START.findall(text)] + [(session, "end") for session in END.findall(text)]
        started, ended = members[name]
        assert sorted(reached) == sorted([(session, "") for session in started] + [(session, "end") for session in ended]), name


class SasSession:
    """The CAS sessions of a SAS session and the option SESSREF, as the SAS log of round 22 showed them: `cas <name>;`
    connects a session and sets the option to it - for a name that is connected it only writes the WARNING that the
    session exists and leaves the option as it is -, `cas <name> terminate;` ends it and leaves the option on it."""

    def __init__(self, connected, sessref):
        self.connected = {name.upper() for name in connected}
        self.sessref = sessref.upper()
        self.started, self.ended, self.warnings = set(), set(), []

    def run(self, statement, flags):
        match = re.fullmatch(r"%let (\w+) = (\d);", statement, flags=re.IGNORECASE)
        if match:
            flags[match.group(1).lower()] = match.group(2)
            return
        match = SESSREF.fullmatch(statement)
        if match:
            self.sessref = match.group(1).upper()
            return
        match = END.fullmatch(statement)
        if match:
            name = match.group(1).upper()
            assert name in self.connected, f"ends {name}, which is not connected"
            self.connected.discard(name)
            self.ended.add(name)
            return
        match = START.fullmatch(statement)
        if match:
            name = match.group(1).upper()
            if name in self.connected:
                self.warnings.append(f"WARNING: A session with the name {name} already exists.")
            else:
                self.connected.add(name)
                self.started.add(name)
                self.sessref = name
            return
        raise AssertionError(f"a statement the session check does not know: {statement}")

    def condition(self, text, flags):
        """The condition with its functions given their results: SESSFOUND is 1 for a session that is connected."""
        text = text.replace("%sysfunc(getoption(sessref))", self.sessref)
        text = re.sub(r"%sysfunc\(sessfound\((\w+)\)\)", lambda found: str(int(found.group(1).upper() in self.connected)), text,
                      flags=re.IGNORECASE)
        text = re.sub(r"&(\w+)\.", lambda found: flags[found.group(1).lower()], text)
        python = re.sub(r"\bNE\b", "!=", re.sub(r"\bEQ\b", "==", text))
        assert re.fullmatch(r"[\d\s!=]+(?:\s(?:and|or)\s[\d\s!=]+)*", python), text
        return bool(eval(python))  # noqa: S307 - digits, != and ==, and, or only, checked above


def _run(name, session, flags):
    """Runs the session check and the clean-up of the program; returns the session the CAS steps between them run in,
    and the sessions connected then."""
    flags = dict(flags)
    during = None
    for statement in _session_statements(_code(_programs()[name])):
        lines = [statement[1]] if statement[0] == "line" else statement[2] + statement[3]
        if during is None and any(END.match(line) for line in lines):
            during = (session.sessref, set(session.connected))
        if statement[0] == "line":
            session.run(statement[1], flags)
            continue
        _, condition, then, other = statement
        for line in then if session.condition(condition, flags) else other:
            session.run(line, flags)
    return during


def _states(name):
    """Every state of the SAS session that matters to the program: each subset of its sessions and a foreign one
    connected, and the option on each of them, on a foreign one and on the default that is never connected."""
    started, _ = _members()[name]
    names = sorted({session.upper() for session in started} | {FOREIGN})
    for size in range(len(names) + 1):
        for connected in itertools.combinations(names, size):
            for sessref in names + ["CASAUTO"]:
                yield connected, sessref


def _flag_values(name):
    code = _code(_programs()[name])
    used = [variable for variable in SET_BEFORE if f"&{variable}." in code]
    for values in itertools.product(*(SET_BEFORE[variable] for variable in used)):
        yield dict(zip(used, values))


@pytest.mark.parametrize("name", sorted(_members()))
def test_a_session_of_the_name_the_program_starts_that_is_connected_is_used_and_not_ended(name):
    checked = 0
    for connected, sessref in _states(name):
        for flags in _flag_values(name):
            session = SasSession(connected, sessref)
            before = set(session.connected)
            during = _run(name, session, flags)
            state = (connected, sessref, flags)
            # no session is started a second time
            assert session.warnings == [], state
            # only a session the program started is ended, and every session that was connected stays connected
            assert session.ended <= session.started, state
            assert before <= session.connected, state
            if flags.get("_lat_ready") == "0":
                # the loader that refused a parameter asks CAS nothing
                assert session.started == set(), state
                continue
            if name in NAMES_ITS_SESSION:
                # PROC CASUTIL SESSION=_optbuild halts while the option names a session that was ended (round 26)
                assert during is not None and "_OPTBUILD" in during[1] and during[0] in during[1], state
            else:
                # the CAS steps run in a session that is connected: none halts for want of one
                assert during is not None and during[0] in during[1], state
                if sessref in before:
                    # a connected session the option names is used as it is
                    assert session.started == set() and during[0] == sessref, state
            checked += 1
    assert checked >= 12


def test_the_state_round_22_measured():
    """SESSREF on the ended zzs, the program's own session connected: Get-All-Prompts.sas now runs in mySess and leaves
    it connected - the WARNING, the halts and the end of the parser's session are gone."""
    session = SasSession(["MYSESS"], "ZZS")
    during = _run("Logging-Monitoring/Get-All-Prompts.sas", session, {})

    assert during == ("MYSESS", {"MYSESS"})
    assert session.warnings == [] and session.started == set() and session.ended == set()
    assert session.connected == {"MYSESS"}


def test_the_program_that_names_its_session_names_it_in_every_cas_statement():
    """Every statement of Create-Optimization-Dataset.sas that reaches CAS names _optbuild. PROC CASUTIL still halts
    while the option SESSREF names a session that was ended (live pass of round 26), so the program has the guard of the
    other programs in front of its session check."""
    code = _code(_programs()["Prompt-Optimization/Create-Optimization-Dataset.sas"])

    assert re.findall(r"^caslib .*;$", code, flags=re.MULTILINE) == ["caslib _all_ assign sessref=_optbuild;"]
    assert re.findall(r"proc casutil([^;]*);", code) == [" session=_optbuild"] * 2
    assert "proc cas;" not in code.lower()
    # the macro variable of the check is deleted with the end of the program
    assert code.rstrip().endswith("%symdel _opt_clean_cas_session / nowarn;")


def test_every_program_sets_the_option_only_to_its_own_session_and_only_where_the_option_names_none_that_is_connected():
    """OPTIONS SESSREF= stands only in the guard in front of the start of the program's own session."""
    for name, (started, _) in _members().items():
        code = _code(_programs()[name])
        guards = [statement for statement in _session_statements(code) if statement[0] == "block" and SESSREF.search("\n".join(statement[2]))]
        assert len(SESSREF.findall(code)) == len(guards), name
        for _, condition, then, other in guards:
            session = SESSREF.match(then[0]).group(1)
            assert then == [f"options sessref={session};"] and other == [], name
            assert session in started, name
            assert condition.endswith(f"%sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 and %sysfunc(sessfound({session})) EQ 1"), name


def test_the_about_tabs_and_the_readme_say_what_the_check_does():
    """The texts that describe the check name the guard: a connected session of the program's own name is used."""
    import json

    def texts(step):
        found = {}

        def walk(children):
            for child in children:
                if child.get("type") == "text":
                    found[child["id"]] = " ".join(child["text"].split())
                walk(child.get("children", []))
        walk(json.loads(json.loads((INTEGRATIONS / "Custom-Steps" / step).read_bytes().decode("utf-8"))["ui"])["pages"])
        return found

    gap = texts("LLM - Get All Prompts.step")
    assert ("Otherwise, where a session gapMySess is connected, the step sets the option SESSREF to that session, runs in it and "
            "leaves it as it is; where gapMySess is not connected either, it starts a CAS session of its own (gapMySess) and ends "
            "it as its last action.") in gap["textAbout"]
    assert "the step sets the option to gapMySess and runs in it without ending it" in gap["textChangelog"]
    parser = texts("LLM - Log Parser.step")
    assert ("If it is not and a session llmLogMySess is connected, the step sets the option to llmLogMySess, runs in it and does "
            "not end it either. Otherwise it starts a CAS session of its own (llmLogMySess) and ends it as its last action;") in parser["textPrereqs"]
    assert "the step sets the option to llmLogMySess and runs in it without ending it" in parser["textChangelog"]
    readme = " ".join((INTEGRATIONS / "Logging-Monitoring" / "README.md").read_text(encoding="utf-8").split())
    assert "a connected `mysess` is used, and the option is set to it" in readme
    assert "When it is not and a session `llmLogMySess` is connected, the step sets the option to that session" in readme
    assert ("*Get-All-Prompts.sas* runs in the CAS session the option `SESSREF` names when that session is connected, and otherwise "
            "in a connected session `mySess`, to which it sets the option; only where neither is connected does it start `mySess`, "
            "and it ends only a session it started.") in readme
