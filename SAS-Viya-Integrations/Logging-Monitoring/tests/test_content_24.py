"""content-24: what the READMEs count.

Gate-23 found 'Three test files read all the programs at once' in front of five test files. The class is a count of
files, programs or steps in a README of SAS-Viya-Integrations (every README.md below it but the one at its top). The
test takes every such count from the text of the READMEs - a number word or 'both' in front of the words that name a
file, a program, a step or a job definition - and fails for one that is not in the table below; for each count of the
repository the table says how it is taken from the tree or the code, and for each other one that it counts no file,
program or step of the repository.
"""
import json
import re
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[1]
INTEGRATIONS = FOLDER.parent
STEPS = INTEGRATIONS / "Custom-Steps"

NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "both": 2}
COUNT = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|both|\d+)"
                   r"((?:\s+(?!of\b|and\b|or\b)[\w\-*`']+){0,2}?)\s+"
                   r"(files?|programs?|steps?|test files?|code files?|scripts?|job definitions?|custom steps?)\b", re.I)


def readmes():
    return sorted(path for path in INTEGRATIONS.rglob("README.md") if path.parent != INTEGRATIONS)


def text(folder):
    return (INTEGRATIONS / folder / "README.md").read_text(encoding="utf-8")


def paragraph(folder, words):
    return next(part for part in text(folder).split("\n\n") if words in part)


def programs(folder):
    return sorted(path for path in (INTEGRATIONS / folder).glob("*.sas"))


def with_python(folder):
    return [path for path in programs(folder) if "proc python" in path.read_text(encoding="utf-8").lower()]


def steps_named(folder):
    """The steps of Custom-Steps that run a program of the folder: '(or the step *<name>*)' in the README."""
    return [path for path in STEPS.glob("*.step") if "(or the step *%s*)" % path.stem in text(folder)]


def syscc_1012(paths):
    return [path for path in paths if "%let SYSCC = 1012;" in path.read_text(encoding="utf-8")]


def files_named(folder, words):
    return set(re.findall(r"`(tests/test_\w+\.py)`", paragraph(folder, words)))


LM, SETUP = "Logging-Monitoring", "Setup"

# (folder of the README, the words behind the number of a count) -> the number of the tree, or None with what is
# counted where it is no file, program or step of the repository
TABLE = {
    # the numbered list behind it names three code files
    (LM, "programs of this folder work on"): lambda: len(set(re.findall(
        r"^\d\. \*(\S+\.sas)\*", text(LM), re.M))),
    (LM, "steps, with the code"): lambda: len(steps_named(LM)),
    (LM, "programs runs in the"): lambda: len(with_python(LM)),
    (LM, "DATA steps that leave"): None,             # statements of the program
    (LM, "DATA steps in front"): None,
    (LM, "more DATA step, which"): None,
    (LM, "such files are the"): None,                # files of a log
    (LM, "files of a generator"): None,
    (LM, "files that are the"): None,
    (LM, "programs of this folder take"): lambda: len(programs(LM)),
    (LM, "custom steps do not"): lambda: len(steps_named(LM)),
    (LM, "other programs carry: in"): lambda: len(with_python(LM)) - 1,
    (LM, "PROC PYTHON step reads"): None,            # a statement of the program
    (LM, "programs outside of SAS"): lambda: len(with_python(LM)),
    (LM, "other programs and stands"): lambda: len(with_python(LM)) - 1,
    (LM, "test files read several"): lambda: len(files_named(LM, "test files read")),
    (LM, "programs and the two"): lambda: len(with_python(LM)),
    (LM, "steps that hands a"): lambda: len(steps_named(LM)),
    (LM, "steps: each stands under"): lambda: len(steps_named(LM)),
    (LM, "programs: the macro that"): lambda: len(programs(LM)),
    (LM, "steps gets every setting"): lambda: len(steps_named(LM)),
    (LM, "programs that they hand"): lambda: len(with_python(LM)),
    (LM, "fact sheet files of"): lambda: len(re.findall(
        r"^%_lfs_read\(", (INTEGRATIONS / LM / "Load-Fact-Sheets.sas").read_text(encoding="utf-8"), re.M)),
    # Build-RAG-Cost-View.sas, the program of that paragraph, is not one of the programs above it
    (LM, "programs above do."): lambda: len(syscc_1012(with_python(LM))),
    (SETUP, "programs for two job"): lambda: len(programs(SETUP)),
    (SETUP, "job definitions of the"): lambda: len(re.findall(
        r"code: SAS-Viya-Integrations/Setup/\S+\.sas", (INTEGRATIONS / "package-manifest.yaml").read_text(encoding="utf-8"))),
    (SETUP, "file had been taken"): None,            # a file of a test folder
    (SETUP, "files of that name"): None,             # a state of a site
    (SETUP, "file the program writes"): None,        # the page the program writes
    (SETUP, "program loads the sheets,"): lambda: len(re.findall(
        r"^%include _latlfs;", (INTEGRATIONS / SETUP / "Load-Accelerator-Tables.sas").read_text(encoding="utf-8"), re.M)),
    # the release table and the three fact sheets
    (SETUP, "files |"): lambda: 1 + len(re.findall(
        r"^%_lfs_read\(", (INTEGRATIONS / LM / "Load-Fact-Sheets.sas").read_text(encoding="utf-8"), re.M)),
    (SETUP, "called a file `present`"): None,         # a date
    # the two programs of the folder Setup: Start-Here.sas and Load-Accelerator-Tables.sas
    (SETUP, "programs does not run"): lambda: len(programs(SETUP)),
    (SETUP, "steps - it reads"): lambda: len([path for path in STEPS.glob("*.step") if path.stem in (
        "LLM - Create LLM Call", "LLM - Get Options") and "*" + path.stem + "*" in text(SETUP)]),
    (SETUP, "steps with the macro"): lambda: len([path for path in STEPS.glob("*.step") if path.stem in (
        "LLM - Create LLM Call", "LLM - Get Options") and "*" + path.stem + "*" in text(SETUP)]),
    (SETUP, "for a step that"): None,                 # a date
    (SETUP, "steps that were saved"): lambda: sum(
        "createdBy" in json.loads(path.read_text(encoding="utf-8")) for path in STEPS.glob("*.step")),
}


def counts_in_the_readmes():
    found = []
    for path in readmes():
        folder = path.parent.relative_to(INTEGRATIONS).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            for match in COUNT.finditer(line):
                found.append((folder, match.group(1), line[match.start():]))
    return found


def key_of(folder, number, behind):
    """The entry of a count: the words behind its number, which is what the test holds against the tree."""
    behind = behind[len(number):].lstrip()
    keys = [key for key in TABLE if key[0] == folder and behind.startswith(key[1])]
    return keys[0] if len(keys) == 1 else None


def test_every_count_of_files_programs_or_steps_in_a_readme_is_in_the_table():
    found = counts_in_the_readmes()
    assert len(found) == len(TABLE), [(folder, behind[:60]) for folder, _, behind in found]
    assert sorted(key_of(folder, number, behind) or ("missing", behind[:60]) for folder, number, behind in found) == sorted(TABLE)


@pytest.mark.parametrize("folder,number,behind", counts_in_the_readmes(), ids=lambda value: str(value)[:40])
def test_a_count_of_the_repository_is_the_number_of_the_tree(folder, number, behind):
    key = key_of(folder, number, behind)
    assert key, (folder, behind[:60])
    measure = TABLE[key]
    if measure is None:
        return
    said = NUMBERS[number.lower()] if number.lower() in NUMBERS else int(number)
    assert said == measure(), (folder, behind[:60])


def test_the_five_test_files_are_the_ones_the_paragraph_names():
    named = files_named(LM, "test files read")
    assert named == {"tests/test_handed_to_sas.py", "tests/test_job_state.py", "tests/test_cas_sessions.py",
                     "tests/test_fix_content_23.py", "tests/test_settings.py"}
    assert all((FOLDER / name).is_file() for name in named)


# Round 23 changed the programs of LLM - Log Parser 2.11 and LLM - Get All Prompts 2.6 (4cc86c1) after both had run in
# the ninth pass (on 36124b8): the use of a connected session of the step's own name is versions 2.12 and 2.7.

def _changelog(step):
    definition = json.loads((STEPS / step).read_text(encoding="utf-8"))
    stack = list(json.loads(definition["ui"])["pages"])
    while stack:
        node = stack.pop()
        if node.get("id") == "textChangelog":
            return node["text"]
        stack.extend(node.get("children", []))
    raise AssertionError(step)


@pytest.mark.parametrize("step,new,old,session", [
    ("LLM - Log Parser.step", "2.12", "2.11", "llmLogMySess"),
    ("LLM - Get All Prompts.step", "2.7", "2.6", "gapMySess"),
])
def test_the_own_session_is_a_version_of_its_own(step, new, old, session):
    changelog = _changelog(step)
    entries = re.split(r"(?m)^\* Version: ", changelog)[1:]
    assert entries[0].startswith(new + " (15OCT2026)\n") and entries[1].startswith(old + " (15OCT2026)\n")
    bullet = "Where the session the option SESSREF names is not connected but %s is" % session
    assert bullet in entries[0] and ("Version %s ran cas %s there" % (old, session)) in entries[0]
    assert len(re.findall(r"(?m)^ +- ", entries[0])) == 1
    assert session not in entries[1] and changelog.count(bullet) == 1


def test_the_readme_names_the_versions_of_the_two_steps():
    assert "The programs of this folder are versions 2.12 and 2.7 of the two steps" in text(LM)
