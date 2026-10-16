# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""mdb-26: the rule of an option of options.json (check_options_json), as the
Prompt Builder reads it, kept by everything mdb writes and uploads.

review-25b: mdb wrote an enum option without values and a bool option whose
default was 1, and nothing kept the default of API_KEY a text; the page drew
the enum as a number input and sent it empty, and the bool unticked."""
import contextlib
import json
import math
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from mdb import cli
from mdb.core.generator import (
    GenerationError, _render_options_json, check_options_file, check_options_json, render_assets,
)
from mdb.core.manifest import (
    MANIFEST_FILENAME, AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock, ProviderBlock,
    RuntimeBlock, TagsBlock, load_manifest,
)
from mdb.core.paths import DEFINITION_DIRS, FACT_SHEETS
from mdb.viya import registry

SAMPLE = ("llm", "claude_sonnet_4_5")


def _manifest(options, auth=None, template="openai_chat", params=None) -> ModelManifest:
    return ModelManifest(
        model_id="test_rule_26",
        display_name="Test Rule 26",
        provider=ProviderBlock(
            adapter="openai", model_version="gpt-test",
            endpoint="https://api.openai.com/v1/chat/completions",
            params=params or {},
            auth=auth or AuthBlock(mode="api_key", key_name="OpenAI"),
        ),
        runtime=RuntimeBlock(template=template, requirements_profile="api-wrapper"),
        options=options,
        tags=TagsBlock(size_class="LLM", license_class="Proprietary", provider_tag="OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="Option rule test.",
            pricing=PricingBlock(cost_type="Tokens", input_token_price=1e-7, output_token_price=6e-7),
        ),
        modeler="tester",
    )


def _text(output: str) -> str:
    return " ".join(output.split())


# -- every options.json of the repository ------------------------------------

def _definition_folders(repo_root: Path) -> list:
    return sorted(folder for kind in DEFINITION_DIRS.values() for folder in (repo_root / kind).iterdir()
                  if folder.is_dir() and (folder / "options.json").is_file())


def test_every_options_json_of_the_repository_keeps_the_rule(repo_root, core):
    """Every definition folder of the three kinds: the options.json it holds
    keeps the rule, and where mdb manages it, the options.json its manifest
    renders is that file, byte for byte."""
    folders = _definition_folders(repo_root)
    managed = [folder for folder in folders if (folder / MANIFEST_FILENAME).is_file()]
    for kind in DEFINITION_DIRS.values():
        assert any(folder.parent.name == kind for folder in managed), kind
    for folder in folders:
        check_options_file(folder / "options.json")
    for folder in managed:
        rendered = render_assets(load_manifest(folder), core)["options.json"]
        assert rendered == (folder / "options.json").read_bytes().replace(b"\r\n", b"\n"), folder.name
    # every manifest of the repository was enumerated, not a sample
    manifests = sorted(path.parent for kind in DEFINITION_DIRS.values()
                       for path in (repo_root / kind).glob(f"*/{MANIFEST_FILENAME}"))
    assert managed == manifests


def test_generate_all_check_passes(repo_root, monkeypatch):
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.delenv("MDB_DEFINITIONS", raising=False)
    result = CliRunner().invoke(cli.app, ["generate", "--all", "--check"])
    assert result.exit_code == 0, result.output
    assert "Option '" not in result.output


# -- round trip: what the generator can write, read back by the rule ---------

def _valid_default(spec_type, vocab):
    if spec_type == "enum":
        return vocab["values"][0]
    if spec_type == "bool":
        return True
    if spec_type == "string":
        return "text"
    return 1


def test_every_option_the_generator_can_write_is_read_back_as_written(core):
    """Every option of the vocabulary and an inline option of every type, with
    a default the rule takes, plus API_KEY and the base_url of the
    self-hosted templates: the options.json the generator writes passes the
    rule, and each entry holds the default, type and values it was given."""
    options = {name: OptionSpec(default=_valid_default(vocab.get("type"), vocab))
               for name, vocab in core.vocabulary.items()}
    options.update({
        "my_number": OptionSpec(type="number", default=0.5, description="A number."),
        "my_int": OptionSpec(type="int", default=3, description="An int."),
        "my_on": OptionSpec(type="bool", default=True, description="On."),
        "my_off": OptionSpec(type="bool", default=False, description="Off.", label="Off"),
        "my_choice": OptionSpec(type="enum", default="b", values=["a", "b"], description="A choice."),
        "my_text": OptionSpec(type="string", default="", description="A text."),
        "my_note": OptionSpec(type="string", default="n", description="Informational.", informational=True),
    })
    for template in ("openai_chat", "openai_compat_selfhosted"):
        written = json.loads(_render_options_json(_manifest(options, template=template), core))
        check_options_json(written)
        assert written["API_KEY"]["default"] == "OpenAI"
        assert ("base_url" in written) == (template == "openai_compat_selfhosted")
        for name, spec in options.items():
            entry = written[name]
            assert entry["default"] == spec.default and type(entry["default"]) is type(spec.default), name
            kind = spec.type or core.vocabulary.get(name, {}).get("type", "number")
            if kind in ("enum", "bool", "string"):
                assert entry["type"] == kind, name
            else:
                assert "type" not in entry, name
            if kind == "enum":
                assert entry["default"] in entry["values"] and entry["values"], name
            else:
                assert "values" not in entry, name


# -- a manifest that breaks the rule is refused, naming the option -----------

BROKEN = {
    "an enum without values": (
        {"mode": OptionSpec(type="enum", default="fast", description="A choice.")}, None,
        "Option 'mode' is an enum option whose values are null - an enum option needs a non-empty list of "
        "texts as its values."),
    "an enum with an empty list of values": (
        {"mode": OptionSpec(type="enum", default="fast", values=[], description="A choice.")}, None,
        "Option 'mode' is an enum option whose values are null - an enum option needs a non-empty list of "
        "texts as its values."),
    "an enum whose default is not one of its values": (
        {"mode": OptionSpec(type="enum", default="slow", values=["fast", "careful"], description="A choice.")},
        None, "Option 'mode' has the default \"slow\", which is not one of its values [\"fast\", \"careful\"]."),
    "an enum whose default is a number": (
        {"mode": OptionSpec(type="enum", default=1, values=["1", "2"], description="A choice.")}, None,
        "Option 'mode' has the default 1, which is not one of its values [\"1\", \"2\"]."),
    "a vocabulary enum whose default is not one of its values": (
        {"reasoning_effort": OptionSpec(default="extreme")}, None,
        "Option 'reasoning_effort' has the default \"extreme\", which is not one of its values "
        "[\"minimal\", \"low\", \"medium\", \"high\", \"maximum\"]."),
    "a bool whose default is 1": (
        {"my_flag": OptionSpec(type="bool", default=1, description="A flag.")}, None,
        "Option 'my_flag' is a bool option with the default 1 - the default of a bool option is true or false."),
    "a bool whose default is the text true": (
        {"my_flag": OptionSpec(type="bool", default="true", description="A flag.")}, None,
        "Option 'my_flag' is a bool option with the default \"true\" - the default of a bool option is true "
        "or false."),
    "a vocabulary bool whose default is 0": (
        {"normalize": OptionSpec(default=0)}, None,
        "Option 'normalize' is a bool option with the default 0 - the default of a bool option is true or "
        "false."),
    "an API_KEY option whose default is a number": (
        {"API_KEY": OptionSpec(type="number", default=5, description="A key.")}, AuthBlock(mode="none"),
        "Option 'API_KEY' has the default 5 - its default is the name of the provider key, a non-empty text."),
    "an API_KEY option whose default is empty": (
        {"API_KEY": OptionSpec(type="string", default="", description="A key.")}, AuthBlock(mode="none"),
        "Option 'API_KEY' has the default \"\" - its default is the name of the provider key, a non-empty "
        "text."),
    "an option whose default is NaN": (
        {"temperature": OptionSpec(default=math.nan)}, None,
        "Option 'temperature' holds NaN or Infinity, which JSON does not hold."),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_manifest_that_breaks_the_rule_is_not_rendered(core, case):
    options, auth, sentence = BROKEN[case]
    with pytest.raises(GenerationError) as raised:
        render_assets(_manifest(options, auth=auth), core)
    assert str(raised.value) == sentence


FILES = {
    "a list": ([], "options.json is [], not an object of options."),
    "an option that is a number": ({"temperature": 0.7},
                                   "Option 'temperature' is 0.7, not an object that holds a default."),
    "an option without a default": ({"temperature": {"range": "0 - 1"}},
                                    "Option 'temperature' is {\"range\": \"0 - 1\"}, not an object that holds a "
                                    "default."),
    "a type that is a number": ({"x": {"default": 1, "type": 2}},
                                "Option 'x' has the type 2, which is not a text."),
    "a label that is a number": ({"x": {"default": 1, "label": 7}},
                                 "Option 'x' has the label 7, which is not a text."),
    "a description that is a list": ({"x": {"default": 1, "description": ["a"]}},
                                     "Option 'x' has the description [\"a\"], which is not a text."),
    "enum values that are numbers": ({"x": {"default": 1, "type": "enum", "values": [1, 2]}},
                                     "Option 'x' is an enum option whose values are [1, 2] - an enum option needs "
                                     "a non-empty list of texts as its values."),
    "an API_KEY default that is a list": ({"API_KEY": {"default": ["OpenAI"]}},
                                          "Option 'API_KEY' has the default [\"OpenAI\"] - its default is the name "
                                          "of the provider key, a non-empty text."),
}


@pytest.mark.parametrize("case", sorted(FILES))
def test_a_file_that_breaks_the_rule_is_refused(case):
    document, sentence = FILES[case]
    with pytest.raises(GenerationError) as raised:
        check_options_json(document)
    assert str(raised.value) == sentence


def test_what_the_rule_leaves_alone_passes():
    """A field given as null is not given; values of an option that is no
    enum and range are not read."""
    check_options_json({
        "x": {"default": 0.5, "label": None, "description": None, "type": None, "values": [1], "range": 5},
        "y": {"default": "", "type": "string"},
        "API_KEY": {"default": "OpenAI", "range": "sk-****", "description": "The key."},
    })


# -- generate and register refuse before anything is written -----------------

def _relocated(tmp_path, monkeypatch, repo_root) -> Path:
    kind, model_id = SAMPLE
    source = repo_root / DEFINITION_DIRS[kind]
    target = tmp_path / "defs" / DEFINITION_DIRS[kind]
    shutil.copytree(source / model_id, target / model_id)
    shutil.copy2(source / FACT_SHEETS[kind], target / FACT_SHEETS[kind])
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    monkeypatch.setenv("SAS_SCR_ENDPOINT", "https://viya.example/llm")
    return target / model_id


def _break_manifest(folder: Path) -> None:
    data = yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    data["options"]["mode"] = {"type": "enum", "default": "fast", "description": "A choice."}
    (folder / MANIFEST_FILENAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _files(folder: Path) -> dict:
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def test_generate_refuses_a_manifest_that_breaks_the_rule_and_writes_nothing(tmp_path, monkeypatch, repo_root):
    folder = _relocated(tmp_path, monkeypatch, repo_root)
    _break_manifest(folder)
    before = _files(folder)
    result = CliRunner().invoke(cli.app, ["generate", folder.name, "--force"])
    assert result.exit_code == 1, result.output
    assert (f"{folder.name}: Option 'mode' is an enum option whose values are null - an enum option needs a "
            "non-empty list of texts as its values.") in _text(result.output)
    assert _files(folder) == before


def test_retire_refuses_a_manifest_that_breaks_the_rule_and_writes_nothing(tmp_path, monkeypatch, repo_root):
    """retire saved the manifest with its new tag before it rendered, so a
    refusal left a tagged manifest and files that were not regenerated."""
    folder = _relocated(tmp_path, monkeypatch, repo_root)
    _break_manifest(folder)
    before = _files(folder)
    result = CliRunner().invoke(cli.app, ["retire", folder.name])
    assert result.exit_code == 1, result.output
    assert (f"{folder.name}: Option 'mode' is an enum option whose values are null - an enum option needs a "
            "non-empty list of texts as its values.") in _text(result.output)
    assert _files(folder) == before


@pytest.fixture
def viya(monkeypatch):
    """No SAS Viya: the session, the repository/project setup and the
    registration are recorded instead."""
    calls = []
    monkeypatch.setattr(cli, "_viya_session", lambda: contextlib.nullcontext(object()))

    def ensure(session, kind, core, responsible_party):
        calls.append(("ensure", kind))
        return SimpleNamespace(created=[])

    def register_model(session, manifest, folder, row, scr, update=False):
        calls.append(("register", manifest.model_id))
        return SimpleNamespace(action="created", model_id=manifest.model_id, url="/modelRepository/models/x")

    monkeypatch.setattr(registry, "ensure_repository_and_project", ensure)
    monkeypatch.setattr(registry, "register_model", register_model)
    return calls


def test_register_registers_a_definition_that_keeps_the_rule(tmp_path, monkeypatch, repo_root, viya):
    folder = _relocated(tmp_path, monkeypatch, repo_root)
    result = CliRunner().invoke(cli.app, ["register", folder.name])
    assert result.exit_code == 0, result.output
    assert viya == [("ensure", "llm"), ("register", folder.name)]


def test_register_refuses_a_manifest_that_breaks_the_rule_before_anything_is_written(
        tmp_path, monkeypatch, repo_root, viya):
    folder = _relocated(tmp_path, monkeypatch, repo_root)
    _break_manifest(folder)
    result = CliRunner().invoke(cli.app, ["register", folder.name])
    assert result.exit_code == 1, result.output
    assert (f"{folder.name}: Option 'mode' is an enum option whose values are null - an enum option needs a "
            "non-empty list of texts as its values.") in _text(result.output)
    assert viya == []


def test_register_refuses_an_options_json_it_would_upload_that_breaks_the_rule(
        tmp_path, monkeypatch, repo_root, viya):
    """A folder that owns its options.json (generation.overrides) uploads
    that file: it is checked as it is."""
    folder = _relocated(tmp_path, monkeypatch, repo_root)
    data = yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    data.setdefault("generation", {})["overrides"] = ["options.json"]
    (folder / MANIFEST_FILENAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    options = json.loads((folder / "options.json").read_text(encoding="utf-8"))
    options["my_flag"] = {"default": 1, "range": "", "description": "A flag.", "type": "bool"}
    (folder / "options.json").write_text(json.dumps(options, indent=4), encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["register", folder.name])
    assert result.exit_code == 1, result.output
    assert (f"{folder.name}: Option 'my_flag' is a bool option with the default 1 - the default of a bool "
            "option is true or false.") in _text(result.output)
    assert viya == []
