"""The step files of SAS-Viya-Integrations/Custom-Steps as they are sent to SAS Viya.

`mdb content-deploy` registers a step by sending the bytes of its file, so what a file says of
who made it goes to every site. The ten steps that were saved from SAS Studio carry createdBy
and modifiedBy at their top; the nine RAG steps were written by hand and carry neither. The two
fields hold the text `mdb package-export` writes into the release package in the place of a
user id.

They sit in this folder because its tests run with the package checks, and Custom-Steps has none.
"""
import json
import re
from pathlib import Path

import pytest

STEPS = Path(__file__).resolve().parents[2] / "Custom-Steps"
REPOSITORY = Path(__file__).resolve().parents[3]
STEP_FILES = sorted(STEPS.glob("*.step"))
# A field that names a user: the two a step saved from SAS Studio has, and what an export or a share adds
USER_FIELDS = {"createdby", "modifiedby", "username", "userid", "owner", "author", "sharedby", "sharedwith", "principal", "addedby"}
WRITTEN = ("createdBy", "modifiedBy")


def neutral_user():
    """The text mdb writes in the place of a user id, read from its source: one constant, and the steps hold it."""
    source = (REPOSITORY / "Model-Definition-Builder" / "cli" / "src" / "mdb" / "core" / "packages.py").read_text(encoding="utf-8")
    found = re.findall(r'^NEUTRAL_USER = "([^"]+)"$', source, flags=re.MULTILINE)
    assert len(found) == 1
    return found[0]


def user_fields(value, path=""):
    """Every field of a step that names a user, as (path, value): the texts that hold JSON - the controls
    of the step - are read as JSON as well."""
    found = []
    if isinstance(value, dict):
        for key, member in value.items():
            if key.lower() in USER_FIELDS:
                found.append((f"{path}/{key}", member))
            found += user_fields(member, f"{path}/{key}")
    elif isinstance(value, list):
        for position, member in enumerate(value):
            found += user_fields(member, f"{path}[{position}]")
    elif isinstance(value, str) and path in ("/ui", "/localUi"):
        found += user_fields(json.loads(value), path + "{}")
    return found


def test_the_steps_of_the_folder():
    names = [path.name for path in STEP_FILES]

    assert len(names) == 19 and sum(name.startswith("RAG - ") for name in names) == 9
    assert sum(name.startswith(("LLM - ", "MM - ")) for name in names) == 10


@pytest.mark.parametrize("path", STEP_FILES, ids=[path.stem for path in STEP_FILES])
def test_no_step_file_names_a_user(path):
    """gate-19: the ten steps saved from SAS Studio held the user id of the person who saved them in
    createdBy and modifiedBy. Each of the two holds the neutral text now, and no step has another field
    that names a user."""
    step = json.loads(path.read_bytes().decode("utf-8"))
    found = user_fields(step)

    if path.name.startswith("RAG - "):
        # written by hand: a RAG step says nothing of who made it
        assert found == []
    else:
        assert sorted(found) == [(f"/{field}", neutral_user()) for field in WRITTEN]


@pytest.mark.parametrize("path", [path for path in STEP_FILES if not path.name.startswith("RAG - ")],
                         ids=[path.stem for path in STEP_FILES if not path.name.startswith("RAG - ")])
def test_a_step_file_is_one_line_as_sas_studio_saved_it(path):
    """What stays when a field of a step is written: one line of JSON, no byte order mark, no line end, and
    the form the file had - so that a change of two fields is a change of two values and of nothing else."""
    raw = path.read_bytes()

    assert raw.count(b"\n") == 0 and raw.count(b"\r") == 0 and not raw.startswith(b"\xef\xbb\xbf")
    assert json.dumps(json.loads(raw.decode("utf-8")), separators=(",", ":")).encode("utf-8") == raw
