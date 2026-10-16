# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""fix-26: check_options_json refuses what the Prompt Builder's isModelOptions
refuses of the options string of a run.

A run of the Builder sends the options of an LLM as one text,
{name:value,name:value}, which the score code splits at each comma and each
name from its value at the first colon. The page (round 26) refuses an
option name with a comma or a colon, and a text default or an enum value with
a comma; mdb wrote all of them, so the page refused files mdb wrote
(verify-round-trip.js, which has mdb write every shape and the page read it
back, is the round trip of this rule).
"""
import io
import json
import shutil

import pytest
import yaml
from typer.testing import CliRunner

from mdb import cli
from mdb.core.generator import GenerationError, check_options_json
from mdb.core.manifest import MANIFEST_FILENAME
from mdb.core.paths import DEFINITION_DIRS, FACT_SHEETS

BUILDER = "the Builder sends the options as {name:value,name:value}"

#: Each option the page refuses for the options string, with the sentence mdb refuses it with.
REFUSED = {
    "a name with a comma": (
        {"a,b": {"default": 1}},
        f"Option 'a,b' has a comma or a colon in its name - {BUILDER}, so a name holds neither."),
    "a name with a colon": (
        {"a:b": {"default": 1}},
        f"Option 'a:b' has a comma or a colon in its name - {BUILDER}, so a name holds neither."),
    "an informational name with a comma": (
        {"a,b": {"type": "string", "default": "x", "description": "A note."}},
        f"Option 'a,b' has a comma or a colon in its name - {BUILDER}, so a name holds neither."),
    "a text default of no type with a comma": (
        {"stop": {"default": "a,b"}},
        f"Option 'stop' has the default \"a,b\" - {BUILDER}, so a text default holds no comma."),
    "a string default with a comma": (
        {"stop": {"type": "string", "default": "a,b"}},
        f"Option 'stop' has the default \"a,b\" - {BUILDER}, so a text default holds no comma."),
    "a text default of a number field with a comma": (
        {"temperature": {"default": "0,7"}},
        f"Option 'temperature' has the default \"0,7\" - {BUILDER}, so a text default holds no comma."),
    "an enum value with a comma": (
        {"m": {"type": "enum", "default": "low", "values": ["low", "a,b"]}},
        f"Option 'm' has the values [\"low\", \"a,b\"] - {BUILDER}, so a value of an enum holds no comma."),
    "an enum default with a comma": (
        {"m": {"type": "enum", "default": "a,b", "values": ["a,b"]}},
        f"Option 'm' has the values [\"a,b\"] - {BUILDER}, so a value of an enum holds no comma."),
}

#: What the page takes and mdb therefore keeps writing.
TAKEN = {
    "a text default with a colon": {"stop": {"default": "a:b"}},
    "a string default with a colon": {"stop": {"type": "string", "default": "a:b"}},
    "an enum value with a colon": {"m": {"type": "enum", "default": "a:b", "values": ["a:b", "c"]}},
    "an API_KEY default with a comma (a run does not send it)": {"API_KEY": {"default": "Open,AI"}},
    "a label and a description with a comma": {"x": {"default": 1, "label": "a,b", "description": "c, d"}},
    "a range with a comma": {"x": {"default": 1, "range": "0, 1"}},
    "a name with a dash and a percent": {"a-b%c": {"default": 1}},
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_rule_refuses_what_the_options_string_cannot_carry(case):
    entries, sentence = REFUSED[case]
    with pytest.raises(GenerationError) as refused:
        check_options_json(entries)
    assert str(refused.value) == sentence


@pytest.mark.parametrize("case", sorted(TAKEN))
def test_the_rule_takes_what_the_options_string_carries(case):
    check_options_json(TAKEN[case])


def _relocated(tmp_path, monkeypatch, repo_root, model_id):
    source = repo_root / DEFINITION_DIRS["llm"]
    target = tmp_path / "defs" / DEFINITION_DIRS["llm"]
    shutil.copytree(source / model_id, target / model_id, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(source / FACT_SHEETS["llm"], target / FACT_SHEETS["llm"])
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    monkeypatch.setenv("SAS_SCR_ENDPOINT", "https://viya.example/llm")
    return target / model_id


def _files(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def _printed(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))
    return out


def test_generate_refuses_a_text_default_with_a_comma_and_writes_nothing(tmp_path, monkeypatch, repo_root):
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    data = yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    data["options"]["my_stop"] = {"type": "string", "default": "a,b", "description": "Stop words."}
    (folder / MANIFEST_FILENAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    before = _files(folder)
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["generate", "gpt_5_mini"])
    assert result.exit_code == 1, (result.exception, out.getvalue())
    assert (f"gpt_5_mini: Option 'my_stop' has the default \"a,b\" - {BUILDER}, so a text default holds no "
            "comma.") in out.getvalue().splitlines()
    assert _files(folder) == before


def test_import_refuses_an_option_name_with_a_colon_and_writes_nothing(tmp_path, monkeypatch, repo_root):
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    (folder / MANIFEST_FILENAME).unlink()
    (folder / ".mdb-lock.json").unlink(missing_ok=True)
    options = json.loads((folder / "options.json").read_text(encoding="utf-8"))
    options["a:b"] = {"default": 1}
    (folder / "options.json").write_text(json.dumps(options, indent=4), encoding="utf-8")
    before = _files(folder)
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["import", "gpt_5_mini"])
    assert result.exit_code == 1, (result.exception, out.getvalue())
    assert (f"gpt_5_mini: Option 'a:b' has a comma or a colon in its name - {BUILDER}, so a name holds "
            "neither.") in out.getvalue().splitlines()
    assert _files(folder) == before


def test_every_options_json_of_the_repository_keeps_the_rule(repo_root):
    paths = [path for kind in DEFINITION_DIRS.values() for path in sorted((repo_root / kind).glob("*/options.json"))]
    assert len(paths) > 50
    for path in paths:
        check_options_json(json.loads(path.read_text(encoding="utf-8")))
