# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""fix-mdb-26: the refusals of check_options_json on one line, what `mdb
import` reads of a hand-maintained options.json, and the order of `mdb
retire`.

- gate-26 (V003): an option whose name holds a line break was refused by
  `validate` over two lines; `import` and `add` printed the refusal as it
  was too.
- gate-26 (import): an options.json whose option had a default that is null,
  a list or an object, or whose API_KEY default was no text, ended `mdb
  import` in a traceback of the manifest's model; the one for API_KEY did
  not name the option.
- review-26 (retire): a generated file edited by hand was refused after the
  manifest had been saved with its deprecated tag; the rerun the refusal asks
  for then said "already deprecated." and did nothing.
"""
import ast
import contextlib
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from mdb import cli
from mdb.core import generator, importer, validator
from mdb.core.generator import GenerationError, OptionRuleError, _render_options_json, check_options_json
from mdb.core.importer import import_folder
from mdb.core.manifest import (
    MANIFEST_FILENAME, AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock, ProviderBlock,
    RuntimeBlock, TagsBlock,
)
from mdb.core.paths import DEFINITION_DIRS, FACT_SHEETS
from mdb.viya import registry, session as viya_session

#: A refusal whose option name holds a line break, as check_options_json words it.
BROKEN_NAME = "a\nb[x]"
SENTENCE = (f"Option '{BROKEN_NAME}' is a bool option with the default 1 - the default of a bool option is true "
            "or false.")
ONE_LINE = "Option 'a b[x]' is a bool option with the default 1 - the default of a bool option is true or false."

CLI_SOURCE = Path(cli.__file__)


def _printed(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))
    return out


def _relocated(tmp_path, monkeypatch, repo_root, model_id: str, kind: str = "llm") -> Path:
    source = repo_root / DEFINITION_DIRS[kind]
    target = tmp_path / "defs" / DEFINITION_DIRS[kind]
    shutil.copytree(source / model_id, target / model_id,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(source / FACT_SHEETS[kind], target / FACT_SHEETS[kind])
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    monkeypatch.setenv("SAS_SCR_ENDPOINT", "https://viya.example/llm")
    return target / model_id


def _files(folder: Path) -> dict:
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


@pytest.fixture
def no_viya(monkeypatch):
    """No SAS Viya: a session is refused as one without configuration, and
    the setup and registration of `register` and the download of `pull` are
    recorded instead."""
    calls = []

    def refused():
        raise viya_session.ViyaConfigError("no SAS Viya in this test")

    monkeypatch.setattr(viya_session, "create_session", refused)
    monkeypatch.setattr(cli, "_viya_session", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(registry, "ensure_repository_and_project",
                        lambda *args, **kwargs: calls.append("ensure") or SimpleNamespace(created=[]))
    monkeypatch.setattr(registry, "register_model", lambda *args, **kwargs: calls.append("register"))
    monkeypatch.setattr(registry, "pull_model", lambda session, model_id, defs_dir, force=False: SimpleNamespace(
        kind="llm", had_definition=False, reconstructed_config=False, files=[]))
    return calls


# -- every command that prints a refusal of the rule prints it on one line ---

#: What raises the refusal of check_options_json in a command of cli.py.
RULE_CALLS = {"render_assets", "_render_options_json", "check_options_file", "import_folder",
              "validate_folder", "validate_all"}


def _commands_that_reach_the_rule() -> set:
    """Every function of cli.py whose body calls one of RULE_CALLS, read from
    the source - each a command (a function under `@<app>.command`), or a
    helper that DRIVEN does not know and that fails the census."""
    found = set()
    for node in ast.walk(ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.FunctionDef):
            continue
        calls = {call.func.id for call in ast.walk(node)
                 if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}
        if calls & RULE_CALLS:
            found.add(node.name)
    return found


def _refusing(*args, **kwargs):
    raise GenerationError(SENTENCE)


#: Each such command, the argument list that drives it to the call and what is made to refuse there.
DRIVEN = {
    "add": (["add", "openai", "gpt-test", "--id", "zz_fix_mdb_26", "--yes", "--offline"],
            [(cli, "render_assets")]),
    "generate": (["generate", "gpt_5_mini"], [(cli, "render_assets")]),
    "validate": (["validate", "gpt_5_mini"], [(validator, "render_assets")]),
    "ship": (["ship", "gpt_5_mini"], [(validator, "render_assets")]),
    "_import_definition (import)": (["import", "gpt_5_mini"], [(cli, "import_folder")]),
    "_import_definition (render)": (["import", "gpt_5_mini"], [(cli, "render_assets")]),
    "_import_definition (pull --import)": (["pull", "gpt_5_mini", "--import", "--force"], [(cli, "import_folder")]),
    "register": (["register", "gpt_5_mini"], [(cli, "_render_options_json")]),
    "retire": (["retire", "gpt_5_mini"], [(cli, "render_assets")]),
    "provider_check": (["provider", "check", "openai"], [(cli, "render_assets")]),
}


def test_every_command_that_reaches_the_rule_is_driven():
    assert _commands_that_reach_the_rule() == {name.split(" ")[0] for name in DRIVEN}


@pytest.mark.parametrize("case", sorted(DRIVEN))
def test_a_refusal_of_the_rule_is_printed_on_one_line(case, tmp_path, monkeypatch, repo_root, no_viya):
    arguments, refusing = DRIVEN[case]
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    if arguments[0] == "import":
        (folder / MANIFEST_FILENAME).unlink()
    for module, name in refusing:
        monkeypatch.setattr(module, name, _refusing)
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    lines = out.getvalue().splitlines()
    assert [line for line in lines if ONE_LINE in line], out.getvalue()
    assert not [line for line in lines if line.endswith("Option 'a")], out.getvalue()


def test_validate_prints_the_refusal_of_a_name_with_a_line_break_on_one_line(tmp_path, monkeypatch, repo_root):
    """gate-26: the probe of the finding, a manifest whose option name holds a
    line break, through the real rule."""
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    data = yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    data["options"][BROKEN_NAME] = {"type": "bool", "default": 1, "description": "A flag."}
    (folder / MANIFEST_FILENAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["validate", "gpt_5_mini"])
    assert result.exit_code == 1
    assert f"[V003] gpt_5_mini: {ONE_LINE}" in out.getvalue().splitlines()


# -- mdb import reads a hand-maintained options.json by the rule -------------

def _hand_maintained(tmp_path, monkeypatch, repo_root) -> Path:
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    (folder / MANIFEST_FILENAME).unlink()
    (folder / ".mdb-lock.json").unlink(missing_ok=True)
    return folder


def _with_options(folder: Path, change) -> None:
    options = json.loads((folder / "options.json").read_text(encoding="utf-8"))
    options = change(options)
    (folder / "options.json").write_text(json.dumps(options, indent=4), encoding="utf-8")


def _set(name, value):
    def change(options):
        options[name] = value
        return options
    return change


def _set_default(name, value):
    def change(options):
        options[name]["default"] = value
        return options
    return change


#: Everything import reads of options.json that breaks the rule, with the line it is refused with.
IMPORT_REFUSED = {
    "options.json that is a list": (
        lambda options: [options], "options.json is ["),
    "an option that is a number": (
        _set("max_completion_tokens", 4000),
        "Option 'max_completion_tokens' is 4000, not an object that holds a default."),
    "a default that is null": (
        _set_default("max_completion_tokens", None),
        "Option 'max_completion_tokens' has the default null - the default of an option is a text, a number, "
        "true or false."),
    "a default that is a list": (
        _set_default("max_completion_tokens", [1]),
        "Option 'max_completion_tokens' has the default [1] - the default of an option is a text, a number, "
        "true or false."),
    "a default that is an object": (
        _set_default("max_completion_tokens", {"a": 1}),
        "Option 'max_completion_tokens' has the default {\"a\": 1} - the default of an option is a text, a "
        "number, true or false."),
    "a default that is NaN": (
        _set_default("max_completion_tokens", float("nan")),
        "Option 'max_completion_tokens' holds NaN or Infinity, which JSON does not hold."),
    "an API_KEY that is a text": (
        _set("API_KEY", "OpenAI"),
        "Option 'API_KEY' is \"OpenAI\", not an object that holds a default."),
    "an API_KEY default that is a number": (
        _set_default("API_KEY", 5),
        "Option 'API_KEY' has the default 5 - its default is the name of the provider key, a non-empty text."),
    "an API_KEY default that is true": (
        _set_default("API_KEY", True),
        "Option 'API_KEY' has the default true - its default is the name of the provider key, a non-empty "
        "text."),
    "an API_KEY default that is a list": (
        _set_default("API_KEY", ["OpenAI"]),
        "Option 'API_KEY' has the default [\"OpenAI\"] - its default is the name of the provider key, a "
        "non-empty text."),
}


@pytest.mark.parametrize("case", sorted(IMPORT_REFUSED))
def test_import_refuses_an_options_json_it_cannot_read_with_one_line(case, tmp_path, monkeypatch, repo_root):
    change, line = IMPORT_REFUSED[case]
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    _with_options(folder, change)
    before = _files(folder)
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["import", "gpt_5_mini"])
    assert result.exit_code == 1, (result.exception, out.getvalue())
    assert [shown for shown in out.getvalue().splitlines() if shown.startswith(f"gpt_5_mini: {line}")], \
        out.getvalue()
    assert ("Nothing was written - the folder stays fully hand-maintained. Repair its options.json, then rerun "
            "mdb import.") in out.getvalue().splitlines()
    assert _files(folder) == before


#: What import reads otherwise than as a refusal - unchanged by the rule. An
#: API_KEY whose default is empty or null, or that is null, was taken as no
#: key; it is refused now (test_fix_mdb_26_lows.py).
IMPORT_TAKEN = {
    "an option without a default: 1": (lambda options: {**options, "seed": {"range": "any"}}, 1),
    "a legacy Azure option of any default: left out": (_set("azure_api_version", {"default": None}), None),
}


@pytest.mark.parametrize("case", sorted(IMPORT_TAKEN))
def test_import_still_takes_what_it_does_not_read_as_a_key_or_a_default(case, tmp_path, monkeypatch, repo_root):
    change, seed = IMPORT_TAKEN[case]
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    _with_options(folder, change)
    manifest = import_folder(folder, folder.parent / FACT_SHEETS["llm"]).manifest
    if "API_KEY" in case:
        assert manifest.provider.auth.mode == "none"
    if seed is not None:
        assert manifest.options["seed"].default == seed
    assert "azure_api_version" not in manifest.options


def _everything_the_generator_writes(core) -> list:
    """The options.json the generator writes for every option of the
    vocabulary and an inline option of every type (test_mdb_26's round trip),
    with a key and without, for a provider and a self-hosted template."""
    def valid(vocab):
        return {"enum": (vocab.get("values") or ["x"])[0], "bool": True, "string": "text"}.get(vocab.get("type"), 1)
    options = {name: OptionSpec(default=valid(vocab)) for name, vocab in core.vocabulary.items()}
    options.update({
        "my_number": OptionSpec(type="number", default=0.5, description="A number."),
        "my_int": OptionSpec(type="int", default=3, description="An int."),
        "my_on": OptionSpec(type="bool", default=True, description="On."),
        "my_off": OptionSpec(type="bool", default=False, description="Off.", label="Off"),
        "my_choice": OptionSpec(type="enum", default="b", values=["a", "b"], description="A choice."),
        "my_text": OptionSpec(type="string", default="", description="A text."),
        "my_note": OptionSpec(type="string", default="n", description="Informational.", informational=True),
    })
    written = []
    for template, auth, params in (
            ("openai_chat", AuthBlock(mode="api_key", key_name="OpenAI"), {}),
            ("openai_compat_selfhosted", AuthBlock(mode="none"), {"base_url": "http://host:8000/v1"})):
        manifest = ModelManifest(
            model_id="test_fix_mdb_26", display_name="Test",
            provider=ProviderBlock(adapter="openai", model_version="gpt-test", params=params, auth=auth,
                                   endpoint="https://api.openai.com/v1/chat/completions"),
            runtime=RuntimeBlock(template=template, requirements_profile="api-wrapper"),
            options=options,
            tags=TagsBlock(size_class="LLM", license_class="Proprietary", provider_tag="OpenAI", scr_sizing="small"),
            metadata=MetadataBlock(description="Test.", pricing=PricingBlock(cost_type="Tokens")),
            modeler="tester",
        )
        written.append(json.loads(_render_options_json(manifest, core)))
    return written


def test_import_reads_every_options_json_mdb_writes_as_written(tmp_path, monkeypatch, repo_root, core):
    """Round trip: every options.json of the repository and everything the
    generator can write, in a hand-maintained folder, is read by import with
    the defaults and the key name it holds."""
    documents = [json.loads(path.read_text(encoding="utf-8"))
                 for kind in DEFINITION_DIRS.values() for path in sorted((repo_root / kind).glob("*/options.json"))]
    documents += _everything_the_generator_writes(core)
    assert len(documents) > 50
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    legacy = {"azure_openai_resource", "azure_api_version", "api_version", "endpoint_url", "azure_deployment"}
    for document in documents:
        check_options_json(document)
        if "API_KEY" not in document:
            # the folder's score template sends the key of API_KEY: an options.json without it is refused (round-29
            # review, test_fix_review_29), so the rest of it is read beside a key name
            (folder / "options.json").write_text(json.dumps(document), encoding="utf-8")
            with pytest.raises(OptionRuleError, match="options.json has no option API_KEY"):
                import_folder(folder, folder.parent / FACT_SHEETS["llm"])
            document = {**document, "API_KEY": {"default": "OpenAI"}}
        (folder / "options.json").write_text(json.dumps(document), encoding="utf-8")
        manifest = import_folder(folder, folder.parent / FACT_SHEETS["llm"]).manifest
        key = document.get("API_KEY", {}).get("default")
        assert manifest.provider.auth.key_name == (key or None) or key in ("ProviderName", "Provider")
        read = {name: spec.default for name, spec in manifest.options.items()}
        assert read == {name: option["default"] for name, option in document.items()
                        if name != "API_KEY" and name not in legacy}


# -- mdb retire refuses a file edited by hand before it saves the tag --------

def test_retire_refuses_a_file_edited_by_hand_before_it_saves_the_tag(tmp_path, monkeypatch, repo_root, no_viya):
    """review-26: the refusal asks to run mdb generate --force and then mdb
    retire again; that rerun retires the model."""
    folder = _relocated(tmp_path, monkeypatch, repo_root, "claude_sonnet_4_5")
    with (folder / "README.md").open("a", encoding="utf-8") as readme:
        readme.write("\nA line edited by hand.\n")
    before = _files(folder)
    out = _printed(monkeypatch)
    first = CliRunner().invoke(cli.app, ["retire", "claude_sonnet_4_5"])
    assert first.exit_code == 1, out.getvalue()
    assert "README.md was edited by hand - retire refuses to overwrite it." in out.getvalue()
    assert _files(folder) == before
    assert "deprecated" not in yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))["tags"].get(
        "extra", [])

    assert CliRunner().invoke(cli.app, ["generate", "claude_sonnet_4_5", "--force"]).exit_code == 0
    out = _printed(monkeypatch)
    second = CliRunner().invoke(cli.app, ["retire", "claude_sonnet_4_5"])
    assert second.exit_code == 0, out.getvalue()
    assert "claude_sonnet_4_5: tagged deprecated and regenerated." in out.getvalue()
    assert "deprecated" in yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))["tags"]["extra"]
    assert "The registered model's status was not updated" in out.getvalue()
