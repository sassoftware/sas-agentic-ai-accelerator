# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Round-29 review: the name of a credential-domain entry, and a definition
that takes no key with a score template that sends one.

- The SAS code of an LLM call (Create-LLM-Call.sas and the custom steps LLM -
  Create LLM Call and LLM - Get Options) writes the default of API_KEY with
  COMPRESS into `%let API_KEY=...;` and finds the entry with a Perl expression:
  an entry named 'Azure OpenAI' became AzureOpenAI and was never found, and a
  '/' would end the expression. auth.key_name and the default of API_KEY of an
  options.json now take letters, digits, '.', '_' and '-' only
  (KEY_NAME_PATTERN), and a name with anything else is refused with a line that
  names them. The Setup tests follow the SAS side.
- A score template that sends the key a call carries (KEY_OPTION_TEMPLATES)
  with auth.mode none rendered: its options.json had no API_KEY, and every call
  failed with 'No API key: pass the provider key in the API_KEY option of the
  call.' It is refused at generate and validate like env mode on a template
  that sends no key; a scorer kept by hand (generation.overrides) is not the
  template's and is not refused. `mdb import` of such a folder is refused
  naming options.json (test_fix_mdb_26_lows).
"""
import io
import json

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from mdb import cli
from mdb.core.generator import (
    KEY_OPTION_TEMPLATES, GenerationError, OptionRuleError, check_options_json, effective_score_file, render_assets,
)
from mdb.core.importer import import_folder
from mdb.core.manifest import KEY_NAME_PATTERN, MANIFEST_FILENAME, AuthBlock, ModelManifest, load_manifest
from mdb.core.paths import DEFINITION_DIRS, FACT_SHEETS
from mdb.core.validator import validate_folder
from test_fix_mdb_26 import _files, _hand_maintained, _relocated

RULE = ("letters, digits, '.', '_' and '-' only, no blank and no '/' - the code writes the name into %let API_KEY=...; "
        "and looks the entry up with a Perl expression. Rename the entry of the credential domain (such as AzureOpenAI "
        "for 'Azure OpenAI') and set that name.")
REFUSED = ["Azure OpenAI", " OpenAI", "OpenAI ", "team/openai", "a,b", "a:b", "a;b", "a&b", "a%b", 'a"b', "a'b", "a{b",
           "Ö", "a\tb"]
TAKEN = ["OpenAI", "AzureOpenAI", "AWSBedrock", "VoyageAI", "TypeSafe", "Voyage.ai", "team-openai_2"]
NO_KEY = ("every call would fail with 'No API key: pass the provider key in the API_KEY option of the call.' Set "
          "auth.mode 'api_key' with auth.key_name (the entry of the credential domain that holds the key, such as "
          "OpenAI), or auth.mode 'env' (the key from a container environment variable, auth.env_var).")


def _printed(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))
    return out


def _definitions(repo_root):
    return sorted(path.parent for kind in DEFINITION_DIRS.values() for path in (repo_root / kind).glob(f"*/{MANIFEST_FILENAME}"))


# -- the name of the entry ----------------------------------------------------

@pytest.mark.parametrize("name", REFUSED)
def test_a_key_name_the_sas_code_cannot_send_is_refused(name):
    with pytest.raises(ValidationError) as raised:
        AuthBlock(mode="api_key", key_name=name)
    assert f"auth.key_name {json.dumps(name, ensure_ascii=False)} is no name of a credential-domain entry the SAS code " \
           f"of an LLM call can send: {RULE}" in str(raised.value)
    with pytest.raises(OptionRuleError) as raised:
        check_options_json({"API_KEY": {"default": name}})
    assert str(raised.value) == (f"Option 'API_KEY' has the default {json.dumps(name, ensure_ascii=False)} is no name of "
                                 f"a credential-domain entry the SAS code of an LLM call can send: {RULE}")


@pytest.mark.parametrize("name", TAKEN)
def test_a_key_name_of_letters_digits_dot_underscore_and_dash_is_taken(name):
    assert AuthBlock(mode="api_key", key_name=name).key_name == name
    check_options_json({"API_KEY": {"default": name}})


def test_every_key_name_of_the_repository_keeps_the_rule(repo_root):
    names = set()
    for folder in _definitions(repo_root):
        manifest = load_manifest(folder)
        if manifest.provider.auth.key_name:
            names.add(manifest.provider.auth.key_name)
        options = json.loads((folder / "options.json").read_text(encoding="utf-8"))
        if "API_KEY" in options:
            assert options["API_KEY"]["default"] == manifest.provider.auth.key_name
    assert names == {"AWSBedrock", "Anthropic", "AzureOpenAI", "Google", "Mistral", "OpenAI", "OpenRouter", "VoyageAI"}
    assert all(KEY_NAME_PATTERN.fullmatch(name) for name in names)


def test_the_schema_names_the_pattern(repo_root):
    schema = json.loads((repo_root / "Model-Definition-Builder" / "definition-core" / "schema" / "manifest.schema.json")
                        .read_text(encoding="utf-8"))
    key_name = schema["$defs"]["AuthBlock"]["properties"]["key_name"]
    assert key_name["pattern"] == KEY_NAME_PATTERN.pattern == "^[A-Za-z0-9._-]+$"


def test_generate_and_import_refuse_a_key_name_with_a_blank(tmp_path, monkeypatch, repo_root):
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    data = yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    data["provider"]["auth"]["key_name"] = "Azure OpenAI"
    (folder / MANIFEST_FILENAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    before = _files(folder)
    out = _printed(monkeypatch)
    assert CliRunner().invoke(cli.app, ["generate", "gpt_5_mini"]).exit_code != 0
    assert "auth.key_name \"Azure OpenAI\" is no name of a credential-domain entry" in out.getvalue()
    assert _files(folder) == before

    folder = _hand_maintained(tmp_path / "import", monkeypatch, repo_root)
    options = json.loads((folder / "options.json").read_text(encoding="utf-8"))
    options["API_KEY"]["default"] = "Azure OpenAI"
    (folder / "options.json").write_text(json.dumps(options, indent=4), encoding="utf-8")
    with pytest.raises(OptionRuleError, match="Option 'API_KEY' has the default \"Azure OpenAI\" is no name of a "
                                              "credential-domain entry"):
        import_folder(folder, folder.parent / FACT_SHEETS["llm"])


# -- auth.mode none with a template that sends a key ---------------------------

@pytest.mark.parametrize("model_id", ["gpt_5_mini", "claude_sonnet_4_5", "voyage_35"])
def test_mode_none_is_refused_where_the_template_sends_the_key(model_id, repo_root, core):
    folder = next(folder for folder in _definitions(repo_root) if folder.name == model_id)
    data = load_manifest(folder).model_dump(by_alias=True)
    assert data["runtime"]["template"] in KEY_OPTION_TEMPLATES
    data["provider"]["auth"] = {"mode": "none"}
    manifest = ModelManifest.model_validate(data)
    with pytest.raises(GenerationError) as raised:
        render_assets(manifest, core)
    assert str(raised.value) == (f"auth.mode 'none' gives an options.json without API_KEY, and the score template "
                                 f"'{data['runtime']['template']}' sends the provider key a call carries in the API_KEY "
                                 f"option - {NO_KEY}")


def test_validate_names_the_refusal_once(tmp_path, monkeypatch, repo_root, core):
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    data = load_manifest(folder).model_dump(by_alias=True)
    data["provider"]["auth"] = {"mode": "none"}
    ModelManifest.model_validate(data).save(folder)
    issues = [issue for issue in validate_folder(folder, core, folder.parent / FACT_SHEETS["llm"]) if issue.rule == "V003"]
    assert len(issues) == 1 and issues[0].model_id == "gpt_5_mini"
    assert issues[0].message.startswith("auth.mode 'none' gives an options.json without API_KEY")
    out = _printed(monkeypatch)
    assert CliRunner().invoke(cli.app, ["generate", "gpt_5_mini"]).exit_code != 0
    assert NO_KEY in out.getvalue()


def test_every_definition_of_the_repository_renders(repo_root, core):
    """The committed definitions with auth.mode none and a template that sends a key keep their scorer by hand
    (the self-hosted Llama, Mistral NeMo and Phi folders): they are not refused. Every other one takes a key."""
    kept = []
    for folder in _definitions(repo_root):
        manifest = load_manifest(folder)
        render_assets(manifest, core)
        if manifest.provider.auth.mode == "none" and manifest.runtime.template in KEY_OPTION_TEMPLATES:
            assert effective_score_file(manifest) in manifest.generation.overrides, folder.name
            kept.append(folder.name)
    assert sorted(kept) == ["llama_32_1b", "llama_32_3b", "mistral_nemo", "phi_35_mini", "phi_3_mini_4k"]
