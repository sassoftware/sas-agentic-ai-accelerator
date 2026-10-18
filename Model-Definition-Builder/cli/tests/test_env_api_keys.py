# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""auth.mode env: a definition takes its provider key from an environment
variable of the SCR container instead of the API_KEY scoring option.

- the manifest: mode env, env_var (default per adapter), the refusals
- options.json without API_KEY; the score code of every template that sends
  the key reads the variable through one partial, and api_key/none output is
  untouched
- the generated scorer run against a stand-in server: the key travels in the
  provider's header, an API_KEY option still wins, neither fails naming the
  variable
- mdb add --auth env / --key-env, mdb test, mdb deploy --key-secret /
  --tls-secret / --namespace
- the review of 2026-10-18: the description derived from auth, an API_KEY
  option refused, mdb import keeps the mode, the Secret patch line, the TLS
  Secret and PV namespace notes, the refusal texts, and what the guides say
"""
import dataclasses
import difflib
import http.server
import importlib.util
import io
import json
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from conftest import PROCESS_TIMEOUT

from mdb import cli
from mdb.core import radar
from mdb.core.generator import (
    KEY_OPTION_TEMPLATES, GenerationError, effective_score_file, render_assets,
)
from mdb.core.manifest import (
    DEFAULT_KEY_ENV, MANIFEST_FILENAME, AuthBlock, ModelManifest, described, export_json_schema, load_manifest,
)
from mdb.core.validator import validate_folder
from mdb.core.paths import DEFINITION_DIRS, FACT_SHEETS
from mdb.providers import load_adapters
from mdb.providers.base import CatalogModel

DUMMY = "dummy-key-for-the-test"

#: One way to build a definition of each template that sends the key: adapter id, catalog model, wizard answers.
CASES = {
    "openai_chat": ("openai", CatalogModel(ref="gpt-x", display_name="GPT X"), {}),
    "anthropic_messages": ("anthropic", CatalogModel(ref="claude-x", display_name="Claude X"), {}),
    "gemini_generate": ("google", CatalogModel(ref="gemini-x", display_name="Gemini X"), {}),
    "bedrock_converse": ("bedrock", CatalogModel(ref="anthropic.claude-x", display_name="Claude X"),
                         {"region": "us-east-1"}),
    "azure_openai_v1": ("azure-foundry", CatalogModel(ref="gpt-x-deploy", display_name="GPT X"),
                        {"resource": "res"}),
    "dec_systemone_api": ("openrouter", CatalogModel(ref="typesafe/jev-x", display_name="Jev X", kind="decision"),
                          {}),
    "emb_openai": ("openai", CatalogModel(ref="text-embedding-x", display_name="Emb X", kind="embedding",
                                          embedding_length=8), {}),
    "emb_voyage": ("voyage", CatalogModel(ref="voyage-x", display_name="Voyage X", kind="embedding",
                                          embedding_length=8), {}),
    "emb_gemini": ("google", CatalogModel(ref="gemini-embedding-x", display_name="Gemini Emb X", kind="embedding",
                                          embedding_length=8), {}),
    "emb_bedrock_titan": ("bedrock", CatalogModel(ref="amazon.titan-embed-x", display_name="Titan X",
                                                  kind="embedding", embedding_length=8), {"region": "us-east-1"}),
    "emb_bedrock_cohere": ("bedrock", CatalogModel(ref="cohere.embed-x", display_name="Cohere X", kind="embedding",
                                                   embedding_length=8), {"region": "us-east-1"}),
    "emb_azure_openai_v1": ("azure-foundry", CatalogModel(ref="emb-deploy", display_name="Emb X", kind="embedding",
                                                          embedding_length=8), {"resource": "res"}),
}
#: The header each provider takes the key in.
HEADER = {
    "anthropic_messages": "x-api-key", "gemini_generate": "x-goog-api-key", "emb_gemini": "x-goog-api-key",
    "azure_openai_v1": "api-key", "emb_azure_openai_v1": "api-key",
}


def _manifest(template: str, mode: str = "api_key", env_var=None) -> ModelManifest:
    adapter_id, catalog_model, answers = CASES[template]
    adapter = load_adapters()[adapter_id]
    manifest = adapter.build_manifest(dataclasses.replace(catalog_model), f"t_{template}", dict(answers), "tester")
    assert manifest.runtime.template == template
    if mode == "env":
        data = manifest.model_dump(by_alias=True)
        data["provider"]["auth"] = {"mode": "env", **({"env_var": env_var} if env_var else {})}
        manifest = ModelManifest.model_validate(data)
    return manifest


def _printed(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))
    return out


def _load_scorer(tmp_path: Path, manifest: ModelManifest, core):
    rendered = render_assets(manifest, core)
    path = tmp_path / effective_score_file(manifest)
    path.write_bytes(rendered[effective_score_file(manifest)])
    spec = importlib.util.spec_from_file_location(f"scorer_{manifest.model_id}_{id(path)}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(module, kind: str, options: dict):
    if kind == "decision":
        return module.scoreModel(["state"], [json.dumps({"q": {"type": "noul", "instructions": "x"}})],
                                 [json.dumps(options)])
    if kind == "embedding":
        return module.scoreModel(["document"], ["project"], [json.dumps(options)])
    return module.scoreModel(["user"], ["system"], [json.dumps(options)])


# -- the manifest --------------------------------------------------------------

@pytest.mark.parametrize("adapter, expected", [
    ("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"), ("openrouter", "OPENROUTER_API_KEY"),
    ("google", "GEMINI_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("mistral", "MISTRAL_API_KEY"),
    ("voyage", "VOYAGE_API_KEY"), ("bedrock", "AWS_BEDROCK_API_KEY"), ("huggingface", "HUGGINGFACE_API_KEY"),
    ("typesafe", "TYPESAFE_API_KEY"), ("azure-foundry", "AZURE_OPENAI_API_KEY"),
    ("azure-foundry-env", "AZURE_OPENAI_API_KEY"),
])
def test_env_mode_defaults_the_variable_per_adapter(adapter, expected):
    provider = ModelManifest.model_fields["provider"].annotation.model_validate(
        {"adapter": adapter, "model_version": "m", "auth": {"mode": "env"}})
    assert provider.auth.env_var == expected


def test_the_defaults_are_the_variables_the_adapters_read():
    for adapter in load_adapters().values():
        if adapter.env_key_var and adapter.key_name:
            expected = "AZURE_OPENAI_API_KEY" if adapter.id.startswith("azure") else DEFAULT_KEY_ENV[adapter.id]
            assert adapter.env_key_var == expected, adapter.id


def test_the_defaults_are_named_in_env_example(repo_root):
    names = {line.split("=", 1)[0] for line in (repo_root / ".env.example").read_text(encoding="utf-8").splitlines()
             if "=" in line and not line.startswith("#")}
    missing = {value for key, value in DEFAULT_KEY_ENV.items() if key != "typesafe"} - names
    assert not missing


def test_env_mode_keeps_a_named_variable():
    assert AuthBlock(mode="env", env_var="MY_KEY_2").env_var == "MY_KEY_2"


@pytest.mark.parametrize("auth, words", [
    ({"mode": "env", "env_var": "my-key"}, "is not an environment variable name"),
    ({"mode": "env", "env_var": "1KEY"}, "is not an environment variable name"),
    ({"mode": "env", "key_name": "OpenAI"}, "auth.key_name is not used when auth.mode is 'env'"),
    ({"mode": "api_key", "key_name": "OpenAI", "env_var": "OPENAI_API_KEY"}, "auth.env_var is only read"),
    ({"mode": "none", "env_var": "OPENAI_API_KEY"}, "auth.env_var is only read"),
])
def test_auth_refusals(auth, words):
    with pytest.raises(ValueError, match=words):
        AuthBlock.model_validate(auth)


def test_an_adapter_without_a_default_needs_the_variable():
    provider_type = ModelManifest.model_fields["provider"].annotation
    with pytest.raises(ValueError, match="auth.env_var is required when auth.mode is 'env': adapter 'acme'"):
        provider_type.model_validate({"adapter": "acme", "model_version": "m", "auth": {"mode": "env"}})
    named = provider_type.model_validate(
        {"adapter": "acme", "model_version": "m", "auth": {"mode": "env", "env_var": "ACME_KEY"}})
    assert named.auth.env_var == "ACME_KEY"


def test_the_committed_schema_is_the_exported_one(repo_root):
    committed = (repo_root / "Model-Definition-Builder" / "definition-core" / "schema"
                 / "manifest.schema.json").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == json.dumps(export_json_schema(), indent=4, ensure_ascii=False) + "\n"
    auth = json.loads(committed)["$defs"]["AuthBlock"]["properties"]
    assert auth["mode"]["enum"] == ["api_key", "env", "none"]
    assert auth["env_var"]["pattern"] == "^[A-Z_][A-Z0-9_]*$"


# -- what is generated ---------------------------------------------------------

def test_the_key_templates_are_the_ones_that_send_api_key(core):
    templates = core.core_dir / "templates" / "score"
    sending = {path.name[:-len(".py.j2")] for path in templates.glob("*.py.j2")
               if "options['API_KEY']" in path.read_text(encoding="utf-8")
               or 'options["API_KEY"]' in path.read_text(encoding="utf-8")}
    assert sending == set(KEY_OPTION_TEMPLATES) == set(CASES)


@pytest.mark.parametrize("template", sorted(CASES))
def test_env_mode_only_adds_to_the_score_code(template, core):
    keyed = render_assets(_manifest(template), core)
    from_env = render_assets(_manifest(template, "env"), core)
    score = effective_score_file(_manifest(template))
    env_var = _manifest(template, "env").provider.auth.env_var
    assert "API_KEY" in json.loads(keyed["options.json"])
    assert "API_KEY" not in json.loads(from_env["options.json"])
    code = from_env[score].decode()
    compile(code, score, "exec")
    assert f"API_KEY_ENV = '{env_var}'" in code
    assert '    options["API_KEY"] = _api_key(options)\n' in code
    removed = [line for line in difflib.ndiff(keyed[score].decode().splitlines(), code.splitlines())
               if line.startswith("- ")]
    assert removed == []
    assert "_api_key" not in keyed[score].decode() and "API_KEY_ENV" not in keyed[score].decode()
    for doc in ("README.md", "Model-Card.md"):
        assert f"container environment variable `{env_var}`" in from_env[doc].decode()
        assert "arrives per call in the `API_KEY` option" not in from_env[doc].decode()


def test_a_named_variable_reaches_the_score_code(core):
    code = render_assets(_manifest("openai_chat", "env", "TEAM_OPENAI_KEY"), core)[
        effective_score_file(_manifest("openai_chat"))].decode()
    assert "API_KEY_ENV = 'TEAM_OPENAI_KEY'" in code


@pytest.mark.parametrize("folder", ["LLM-Definitions/qwen_25_05b", "LLM-Definitions/azure_openai_env",
                                    "Decision-Definitions/von_1_2", "Embedding-Definitions/all_minilm_l6_v2"])
def test_env_mode_is_refused_where_the_template_sends_no_key(folder, repo_root, core):
    manifest = load_manifest(repo_root / folder)
    data = manifest.model_dump(by_alias=True)
    data["provider"]["auth"] = {"mode": "env", "env_var": "SOME_KEY"}
    with pytest.raises(GenerationError, match="auth.mode 'env' needs a score template that sends a provider key"):
        render_assets(ModelManifest.model_validate(data), core)


# -- the generated scorer ------------------------------------------------------

@pytest.mark.parametrize("template", sorted(CASES))
def test_the_scorer_sends_the_variable_and_an_option_wins(template, tmp_path, monkeypatch, core):
    manifest = _manifest(template, "env")
    env_var = manifest.provider.auth.env_var
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "res")
    module = _load_scorer(tmp_path, manifest, core)
    sent = []

    class Captured(Exception):
        pass

    def post(url, headers=None, **_):
        sent.append(headers)
        raise Captured()

    monkeypatch.setattr(module.requests, "post", post)
    header = HEADER.get(template, "Authorization")
    expected = (lambda key: f"Bearer {key}") if header == "Authorization" else (lambda key: key)

    monkeypatch.setenv(env_var, DUMMY)
    for options in ({}, {"API_KEY": ""}, {"API_KEY": "   "}):
        with pytest.raises(Captured):
            _call(module, manifest.kind, options)
        assert sent[-1][header] == expected(DUMMY)
    with pytest.raises(Captured):
        _call(module, manifest.kind, {"API_KEY": "from-the-caller"})
    assert sent[-1][header] == expected("from-the-caller")

    monkeypatch.delenv(env_var)
    calls = len(sent)
    with pytest.raises(RuntimeError) as refused:
        _call(module, manifest.kind, {})
    assert str(refused.value) == (f"No API key: set the {env_var} environment variable on this container "
                                  "(or pass an API_KEY option).")
    assert len(sent) == calls


class _StandIn(http.server.BaseHTTPRequestHandler):
    """A provider that answers every POST like the OpenAI chat API and keeps the headers it got."""
    seen: list = []

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append(dict(self.headers))
        body = json.dumps({"choices": [{"message": {"content": "OK"}}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def stand_in():
    _StandIn.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StandIn)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions", _StandIn.seen
    server.shutdown()
    server.server_close()


def test_the_scorer_against_a_stand_in_server(stand_in, tmp_path, monkeypatch, core):
    url, seen = stand_in
    manifest = _manifest("openai_chat", "env")
    manifest.provider.endpoint = url
    module = _load_scorer(tmp_path, manifest, core)
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY)
    response, _, prompt_length, output_length = _call(module, "llm", {})
    assert (response, prompt_length, output_length) == ("OK", 3, 1)
    assert seen[-1]["Authorization"] == f"Bearer {DUMMY}"


# -- mdb add -------------------------------------------------------------------

def _relocate(tmp_path, monkeypatch, repo_root) -> Path:
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    monkeypatch.setenv("MDB_OFFLINE", "true")
    monkeypatch.setenv("SAS_SCR_ENDPOINT", "https://viya.example/llm")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "defs"


@pytest.mark.parametrize("args, kind, folder, env_var", [
    (["openai", "gpt-6-luna"], "llm", "gpt_6_luna", "OPENAI_API_KEY"),
    (["anthropic", "claude-sonnet-5-5"], "llm", "claude_sonnet_5_5", "ANTHROPIC_API_KEY"),
    (["openrouter", "gpt-6-luna", "--id", "or_luna"], "llm", "or_luna", "OPENROUTER_API_KEY"),
    (["openrouter", "typesafe/jev-1.13", "--id", "jev_1_13"], "decision", "jev_1_13", "OPENROUTER_API_KEY"),
])
def test_add_auth_env_offline(args, kind, folder, env_var, tmp_path, monkeypatch, repo_root):
    defs = _relocate(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["add", *args, "--auth", "env", "--yes", "--offline"])
    assert result.exit_code == 0, out.getvalue() + str(result.exception)
    target = defs / DEFINITION_DIRS[kind] / folder
    manifest = load_manifest(target)
    assert (manifest.provider.auth.mode, manifest.provider.auth.env_var, manifest.provider.auth.key_name) == (
        "env", env_var, None)
    assert "API_KEY" not in json.loads((target / "options.json").read_text(encoding="utf-8"))
    assert f"API_KEY_ENV = '{env_var}'" in (target / effective_score_file(manifest)).read_text(encoding="utf-8")
    sentence = f"The key comes from the container environment variable {env_var}."
    assert "environment variable" not in manifest.metadata.description
    assert json.loads((target / "modelConfiguration.json").read_text(encoding="utf-8"))["description"].endswith(
        " " + sentence)
    assert f"environment variable {env_var}" in (defs / DEFINITION_DIRS[kind] / FACT_SHEETS[kind]).read_text(
        encoding="utf-8")
    out.truncate(0)
    validated = CliRunner().invoke(cli.app, ["validate", folder])
    assert validated.exit_code == 0, out.getvalue()
    assert "V0" not in out.getvalue().replace("V008", "")
    out.truncate(0)
    assert CliRunner().invoke(cli.app, ["generate", folder, "--check"]).exit_code == 0, out.getvalue()


def test_add_auth_env_with_a_named_variable_and_a_description(tmp_path, monkeypatch, repo_root):
    defs = _relocate(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["add", "openai", "gpt-x", "--auth", "env", "--key-env", "TEAM_KEY",
                                          "--description", "Mine.", "--yes", "--offline"])
    assert result.exit_code == 0, out.getvalue()
    manifest = load_manifest(defs / "LLM-Definitions" / "gpt_x")
    assert manifest.provider.auth.env_var == "TEAM_KEY"
    assert manifest.metadata.description == "Mine."
    assert "env_var: TEAM_KEY" in (defs / "LLM-Definitions" / "gpt_x" / MANIFEST_FILENAME).read_text(encoding="utf-8")


def test_add_auth_api_key_is_what_add_wrote_before(tmp_path, monkeypatch, repo_root):
    defs = _relocate(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    assert CliRunner().invoke(cli.app, ["add", "openai", "gpt-x", "--yes", "--offline"]).exit_code == 0
    plain = {p.name: p.read_bytes() for p in (defs / "LLM-Definitions" / "gpt_x").iterdir()}
    shutil.rmtree(defs / "LLM-Definitions" / "gpt_x")
    result = CliRunner().invoke(cli.app, ["add", "openai", "gpt-x", "--auth", "api_key", "--yes", "--offline"])
    assert result.exit_code == 0, out.getvalue()
    assert {p.name: p.read_bytes() for p in (defs / "LLM-Definitions" / "gpt_x").iterdir()} == plain


@pytest.mark.parametrize("args, line", [
    (["openai", "gpt-x", "--auth", "keys"], "--auth must be api_key or env, not 'keys'."),
    (["openai", "gpt-x", "--key-env", "MY_KEY"], "--key-env names the variable of --auth env - pass --auth env with it."),
    (["openai", "gpt-x", "--auth", "env", "--key-env", "my-key"], "--key-env 'my-key' is not an environment variable name"),
    (["ollama", "llama3.2", "--auth", "env"], "take no provider key - --auth env does not apply."),
    (["azure-foundry-env", "gpt-x-deploy", "--resource", "res", "--deployment", "gpt-x-deploy", "--api-version", "v1",
      "--auth", "env"],
     "Azure AI Foundry (environment-configured) llm definitions (azure_openai_env) read their key from the container "
     "environment variable AZURE_OPENAI_API_KEY already - --auth env does not apply."),
])
def test_add_refusals(args, line, tmp_path, monkeypatch, repo_root):
    defs = _relocate(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["add", *args, "--yes", "--offline"])
    assert result.exit_code in (1, 2)
    assert line in out.getvalue()
    assert not any((defs / "LLM-Definitions").glob("*/definition.yaml"))


def test_apply_auth_refuses_an_adapter_without_a_default():
    manifest = _manifest("openai_chat")
    adapter = SimpleNamespace(id="acme", display_name="Acme", env_key_var=None)
    with pytest.raises(ValueError, match="Provider 'acme' has no default key variable - pass --key-env NAME."):
        cli._apply_auth(manifest, adapter, "env", None)
    cli._apply_auth(manifest, adapter, "env", "ACME_KEY")
    assert manifest.provider.auth == AuthBlock(mode="env", env_var="ACME_KEY")


# -- mdb test, the key lookups -------------------------------------------------

def _env_definition(tmp_path, monkeypatch, repo_root, endpoint: str) -> Path:
    defs = _relocate(tmp_path, monkeypatch, repo_root)
    _printed(monkeypatch)
    assert CliRunner().invoke(cli.app, ["add", "openai", "gpt-x", "--auth", "env", "--yes", "--offline"]).exit_code == 0
    folder = defs / "LLM-Definitions" / "gpt_x"
    text = (folder / MANIFEST_FILENAME).read_text(encoding="utf-8")
    (folder / MANIFEST_FILENAME).write_text(
        text.replace("endpoint: https://api.openai.com/v1/chat/completions", f"endpoint: {endpoint}"), encoding="utf-8")
    assert CliRunner().invoke(cli.app, ["generate", "gpt_x"]).exit_code == 0
    return folder


def test_mdb_test_reads_the_variable(stand_in, tmp_path, monkeypatch, repo_root):
    url, seen = stand_in
    _env_definition(tmp_path, monkeypatch, repo_root, url)
    out = _printed(monkeypatch)
    missing = CliRunner().invoke(cli.app, ["test", "gpt_x"])
    assert missing.exit_code == 2
    assert "No OPENAI_API_KEY found - the score code reads the key from that environment variable" in out.getvalue()
    assert seen == []
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY)
    out.truncate(0)
    result = CliRunner().invoke(cli.app, ["test", "gpt_x"])
    assert result.exit_code == 0, out.getvalue() + str(result.exception)
    assert seen[-1]["Authorization"] == f"Bearer {DUMMY}"
    assert DUMMY not in out.getvalue()


def test_the_key_lookups_read_the_definitions_variable(monkeypatch):
    manifest = _manifest("openai_chat", "env", "TEAM_KEY")
    adapter = load_adapters()["openai"]
    monkeypatch.setenv("OPENAI_API_KEY", "adapter-key")
    monkeypatch.setenv("TEAM_KEY", "team-key")
    assert cli._env_api_key(adapter, manifest) == "team-key"
    assert radar.env_key_for(adapter, manifest) == "team-key"
    assert cli._env_api_key(adapter, _manifest("openai_chat")) == "adapter-key"
    assert radar.env_key_for(adapter) == "adapter-key"
    monkeypatch.delenv("TEAM_KEY")
    assert cli._env_api_key(adapter, manifest) == "adapter-key"


# -- mdb deploy ----------------------------------------------------------------

def _deployed(tmp_path, monkeypatch, out_dir: Path, *args) -> tuple:
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["deploy", *args, "--registry", "registry.example.com/llm",
                                          "--host", "viya.example.com", "--out", str(out_dir)])
    return result, out.getvalue()


def _env_entries(documents) -> dict:
    deployment = next(doc for doc in documents if doc and doc["kind"] == "Deployment")
    entries = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    names = [entry["name"] for entry in entries]
    assert len(names) == len(set(names))
    return {entry["name"]: entry for entry in entries}


def test_deploy_env_mode_reads_the_key_from_a_secret(tmp_path, monkeypatch, repo_root):
    _env_definition(tmp_path, monkeypatch, repo_root, "https://api.openai.com/v1/chat/completions")
    result, printed = _deployed(tmp_path, monkeypatch, tmp_path / "yaml", "gpt_x", "--tls-secret",
                                "sas-ingress-certificate", "--key-secret", "llm-api-keys", "--namespace", "models")
    assert result.exit_code == 0, printed
    documents = list(yaml.safe_load_all((tmp_path / "yaml" / "deploy-gpt_x.yaml").read_text(encoding="utf-8")))
    entries = _env_entries(documents)
    assert entries["OPENAI_API_KEY"] == {"name": "OPENAI_API_KEY", "valueFrom": {
        "secretKeyRef": {"name": "llm-api-keys", "key": "OPENAI_API_KEY"}}}
    ingress = next(doc for doc in documents if doc and doc["kind"] == "Ingress")
    assert ingress["spec"]["tls"][0]["secretName"] == "sas-ingress-certificate"
    assert {doc["metadata"]["namespace"] for doc in documents if doc} == {"models"}
    assert ('kubectl create secret generic llm-api-keys --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY" -n models'
            in printed)
    assert "key from Secret llm-api-keys/OPENAI_API_KEY" in printed
    # a Secret that exists gets a merge patch, never an apply that drops the keys this run does not list
    assert ("kubectl patch secret llm-api-keys -n models --type merge "
            """-p '{"stringData":{"OPENAI_API_KEY":"'"$OPENAI_API_KEY"'"}}'""") in printed
    assert "kubectl apply -f -" not in printed and "--dry-run" not in printed
    # the TLS Secret is read in the namespace of the Ingress
    assert ("The Ingress reads the TLS Secret sas-ingress-certificate from the namespace models - an Ingress takes "
            "no Secret of another namespace") in printed


def test_deploy_keeps_an_api_key_definition_as_it_was(tmp_path, monkeypatch, repo_root):
    model_id = "claude_sonnet_4_5"
    shutil.copytree(repo_root / "LLM-Definitions" / model_id, tmp_path / "defs" / "LLM-Definitions" / model_id)
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    result, printed = _deployed(tmp_path, monkeypatch, tmp_path / "yaml", model_id)
    assert result.exit_code == 0, printed
    text = (tmp_path / "yaml" / f"deploy-{model_id}.yaml").read_text(encoding="utf-8")
    documents = list(yaml.safe_load_all(text))
    assert "secretKeyRef" not in text and "kubectl create secret" not in printed
    assert {doc["metadata"]["namespace"] for doc in documents if doc} == {"llm"}
    assert "secretName: llm-tls-certs" in text
    assert "kubectl apply -f" in printed and printed.rstrip().endswith("-n llm")
    assert "TLS Secret" not in printed and "kubectl patch" not in printed


def test_deploy_env_mode_on_azure_replaces_the_templates_key_entry(tmp_path, monkeypatch, repo_root, core):
    manifest = _manifest("azure_openai_v1", "env")
    folder = tmp_path / "defs" / "LLM-Definitions" / manifest.model_id
    folder.mkdir(parents=True)
    manifest.save(folder)
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "res")
    result, printed = _deployed(tmp_path, monkeypatch, tmp_path / "yaml", manifest.model_id)
    assert result.exit_code == 0, printed
    entries = _env_entries(yaml.safe_load_all(
        (tmp_path / "yaml" / f"deploy-{manifest.model_id}.yaml").read_text(encoding="utf-8")))
    assert entries["AZURE_OPENAI_API_KEY"]["valueFrom"] == {
        "secretKeyRef": {"name": "llm-api-keys", "key": "AZURE_OPENAI_API_KEY"}}
    assert entries["AZURE_OPENAI_RESOURCE"]["value"] == "res"


@pytest.mark.parametrize("option, value", [
    ("--namespace", "Models"), ("--namespace", "a.b"), ("--tls-secret", "x-"), ("--key-secret", "keys_1"),
])
def test_deploy_refuses_a_name_kubernetes_does_not_take(option, value, tmp_path, monkeypatch, repo_root):
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    result, printed = _deployed(tmp_path, monkeypatch, tmp_path / "yaml", "--all", option, value)
    assert result.exit_code == 2
    assert f"{option} '{value}' is not a Kubernetes" in printed
    assert not (tmp_path / "yaml").exists()


# -- the review of 2026-10-18 ----------------------------------------------------

def test_the_description_follows_auth(tmp_path, monkeypatch, repo_root):
    """The sentence that names the variable is derived from auth at every generate: switching definition.yaml to
    env by hand, naming another variable and going back to api_key each give the fact sheet, modelConfiguration.json
    and the model card the description that holds."""
    defs = _relocate(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    assert CliRunner().invoke(cli.app, ["add", "openai", "gpt-x", "--yes", "--offline"]).exit_code == 0, out.getvalue()
    folder = defs / "LLM-Definitions" / "gpt_x"
    plain = load_manifest(folder).metadata.description
    original = (folder / MANIFEST_FILENAME).read_text(encoding="utf-8")
    keyed = "  auth:\n    mode: api_key\n    key_name: OpenAI\n"
    assert original.count(keyed) == 1

    def shown(auth_text):
        (folder / MANIFEST_FILENAME).write_text(original.replace(keyed, auth_text), encoding="utf-8")
        out.truncate(0)
        assert CliRunner().invoke(cli.app, ["generate", "gpt_x"]).exit_code == 0, out.getvalue()
        assert CliRunner().invoke(cli.app, ["sync", "gpt_x"]).exit_code == 0, out.getvalue()
        configuration = json.loads((folder / "modelConfiguration.json").read_text(encoding="utf-8"))["description"]
        card = (folder / "Model-Card.md").read_text(encoding="utf-8")
        sheet = (defs / "LLM-Definitions" / FACT_SHEETS["llm"]).read_text(encoding="utf-8")
        return configuration, card, sheet

    configuration, card, sheet = shown("  auth:\n    mode: env\n")
    assert configuration == plain + " The key comes from the container environment variable OPENAI_API_KEY."
    assert configuration in card and configuration in sheet
    configuration, card, sheet = shown("  auth:\n    mode: env\n    env_var: TEAM_KEY\n")
    assert configuration == plain + " The key comes from the container environment variable TEAM_KEY."
    assert "OPENAI_API_KEY." not in sheet and configuration in sheet and configuration in card
    configuration, card, sheet = shown(keyed)
    assert configuration == plain and "environment variable" not in sheet
    # a description that still holds the sentence of an earlier mode or variable gets the one that holds now
    manifest = load_manifest(folder)
    manifest.metadata.description = plain + " The key comes from the container environment variable OLD_KEY."
    assert described(manifest) == plain
    manifest.provider.auth = AuthBlock(mode="env", env_var="NEW_KEY")
    assert described(manifest) == plain + " The key comes from the container environment variable NEW_KEY."


def test_every_existing_description_is_shown_as_it_is(repo_root):
    """api_key and none mode: the description of every definition of the repository reaches the generated files
    unchanged."""
    for kind, directory in DEFINITION_DIRS.items():
        for folder in sorted((repo_root / directory).iterdir()):
            if (folder / MANIFEST_FILENAME).is_file():
                manifest = load_manifest(folder)
                assert manifest.provider.auth.mode != "env"
                assert described(manifest) == manifest.metadata.description, folder.name


def test_an_api_key_option_is_refused_in_env_mode():
    data = _manifest("openai_chat", "env").model_dump(by_alias=True)
    data["options"]["API_KEY"] = {"default": "SENTINEL-LITERAL-KEY", "type": "string", "description": "key"}
    with pytest.raises(ValueError, match="options.API_KEY is not declared when provider.auth.mode is 'env'"):
        ModelManifest.model_validate(data)
    # the other modes keep what they had: none takes such an option as before
    data["provider"]["auth"] = {"mode": "none"}
    assert "API_KEY" in ModelManifest.model_validate(data).options


def test_an_api_key_option_in_env_mode_fails_validate_and_generate(tmp_path, monkeypatch, repo_root):
    folder = _env_definition(tmp_path, monkeypatch, repo_root, "https://api.openai.com/v1/chat/completions")
    text = (folder / MANIFEST_FILENAME).read_text(encoding="utf-8")
    (folder / MANIFEST_FILENAME).write_text(
        text.replace("options:\n", "options:\n  API_KEY:\n    default: SENTINEL-LITERAL-KEY\n    type: string\n", 1),
        encoding="utf-8")
    before = {path.name: path.read_bytes() for path in folder.iterdir() if path.name != MANIFEST_FILENAME}
    out = _printed(monkeypatch)
    assert CliRunner().invoke(cli.app, ["generate", "gpt_x"]).exit_code != 0
    assert "options.API_KEY is not declared when provider.auth.mode is 'env'" in out.getvalue()
    assert {path.name: path.read_bytes() for path in folder.iterdir() if path.name != MANIFEST_FILENAME} == before
    assert not any(b"SENTINEL-LITERAL-KEY" in content for content in before.values())


def test_the_template_refusal_names_the_model_once(repo_root, core, tmp_path):
    folder = tmp_path / "qwen_25_05b"
    shutil.copytree(repo_root / "LLM-Definitions" / "qwen_25_05b", folder)
    manifest = load_manifest(folder)
    data = manifest.model_dump(by_alias=True)
    data["provider"]["auth"] = {"mode": "env", "env_var": "SOME_KEY"}
    ModelManifest.model_validate(data).save(folder)
    issues = [issue for issue in validate_folder(folder, core, repo_root / "LLM-Definitions" / FACT_SHEETS["llm"])
              if issue.rule == "V003"]
    assert len(issues) == 1 and issues[0].model_id == "qwen_25_05b"
    assert issues[0].message.startswith("auth.mode 'env' needs a score template that sends a provider key")


def test_import_keeps_env_mode(tmp_path, monkeypatch, repo_root):
    """A folder generated in env mode, imported without its definition.yaml (mdb import, mdb pull --import), keeps
    the mode and the variable: the score code names it. Before, the import made it auth.mode none and regenerated
    a scorer that read options['API_KEY'], which no call held."""
    defs = _relocate(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    for args in (["openai", "gpt-x", "--auth", "env", "--key-env", "TEAM_KEY"],
                 ["openrouter", "typesafe/jev-1.13", "--id", "jev_x", "--auth", "env"]):
        assert CliRunner().invoke(cli.app, ["add", *args, "--yes", "--offline"]).exit_code == 0, out.getvalue()
    for kind, model_id, env_var in (("llm", "gpt_x", "TEAM_KEY"), ("decision", "jev_x", "OPENROUTER_API_KEY")):
        folder = defs / DEFINITION_DIRS[kind] / model_id
        generated = {path.name: path.read_bytes() for path in folder.iterdir() if path.name != MANIFEST_FILENAME}
        (folder / MANIFEST_FILENAME).unlink()
        out.truncate(0)
        result = CliRunner().invoke(cli.app, ["import", model_id, "--apply"])
        assert result.exit_code == 0, out.getvalue() + str(result.exception)
        assert f"container environment variable {env_var} - auth.mode env" in out.getvalue()
        manifest = load_manifest(folder)
        assert (manifest.provider.auth.mode, manifest.provider.auth.env_var) == ("env", env_var)
        score = effective_score_file(manifest)
        assert (folder / score).read_bytes() == generated[score]
        assert json.loads((folder / "options.json").read_text(encoding="utf-8")) == json.loads(generated["options.json"])
        assert "API_KEY" not in json.loads((folder / "options.json").read_text(encoding="utf-8"))


def test_import_of_the_environment_configured_azure_template_is_unchanged(repo_root, tmp_path):
    """azure_openai_env names API_KEY_ENV too, and is no key-option template: its import stays what it was."""
    from mdb.core.importer import import_folder
    folder = tmp_path / "azure_openai_env"
    shutil.copytree(repo_root / "LLM-Definitions" / "azure_openai_env", folder)
    (folder / MANIFEST_FILENAME).unlink()
    result = import_folder(folder, repo_root / "LLM-Definitions" / FACT_SHEETS["llm"])
    assert result.manifest.provider.auth.mode == "none"
    assert not any("auth.mode env" in note for note in result.notes)


def test_the_secret_patch_line_puts_the_value_the_shell_holds(tmp_path, monkeypatch, repo_root):
    """The line `mdb deploy` prints for a Secret that exists, run by bash with kubectl stood in for: the merge
    patch carries each variable's value from the shell's environment, as JSON."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    _env_definition(tmp_path, monkeypatch, repo_root, "https://api.openai.com/v1/chat/completions")
    result, printed = _deployed(tmp_path, monkeypatch, tmp_path / "yaml", "gpt_x")
    assert result.exit_code == 0, printed
    line = next(line.strip() for line in printed.splitlines() if line.strip().startswith("kubectl patch secret"))
    script = 'kubectl() { printf "%s\\n" "$@"; }\n' + line + "\n"
    environment = dict(__import__("os").environ, OPENAI_API_KEY="stand-in-key-1")
    ran = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=environment, check=True,
                         timeout=PROCESS_TIMEOUT)
    arguments = ran.stdout.splitlines()
    assert arguments[:7] == ["patch", "secret", "llm-api-keys", "-n", "llm", "--type", "merge"]
    assert arguments[7] == "-p" and json.loads(arguments[8]) == {"stringData": {"OPENAI_API_KEY": "stand-in-key-1"}}
    assert "stand-in-key-1" not in printed


def test_deploy_pv_into_another_namespace_names_the_templates_it_does_not_render(tmp_path, monkeypatch, repo_root):
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    model_id = "qwen_25_05b"
    shutil.copytree(repo_root / "LLM-Definitions" / model_id, tmp_path / "defs" / "LLM-Definitions" / model_id)
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    result, printed = _deployed(tmp_path, monkeypatch, tmp_path / "yaml", model_id, "--pv", "--namespace", "team-a")
    assert result.exit_code == 0, printed
    assert ("The PV variant mounts the claim llm-weights in team-a: llm-weights-pvc-template.yaml and "
            "stage-weights-job-template.yaml name the namespace llm") in printed
    result, printed = _deployed(tmp_path, monkeypatch, tmp_path / "yaml", model_id, "--pv")
    assert result.exit_code == 0 and "The PV variant mounts" not in printed


def _guide(repo_root, name) -> str:
    return " ".join((repo_root / "website" / "docs" / "Administration-Guide" / name).read_text(encoding="utf-8").split())


def test_the_guides_say_what_the_review_found_missing(repo_root):
    builder = _guide(repo_root, "Model-Definition-Builder.md")
    deployment = _guide(repo_root, "Container-Deployment.md")
    credentials = _guide(repo_root, "Managing-Credentials.md")
    calls = " ".join((repo_root / "SAS-Viya-Integrations" / "SAS-Code-LLM-Calls" / "README.md").read_text(
        encoding="utf-8").split())
    # the Secret: a merge patch, and why not apply
    assert "--dry-run=client -o yaml | kubectl apply -f -`." not in builder
    assert "kubectl patch secret llm-api-keys -n llm --type merge" in builder and "kubectl patch" in deployment
    # the TLS Secret lives in the namespace of the Ingress
    assert "--tls-secret sas-ingress-certificate" not in builder
    assert "The TLS Secret has to exist in the namespace of `--namespace`" in builder
    assert "for example the certificate secret of your SAS Viya ingress)" not in deployment
    assert "The Secret has to exist in the namespace of the Ingress" in deployment
    # the PV templates keep llm
    assert "every object of a template names it; `mdb deploy --namespace <name>` renders another one." not in deployment
    assert "`llm-weights-pvc-template.yaml` and `stage-weights-job-template.yaml` are not rendered by `mdb deploy`" in deployment
    # the unauthenticated Ingress
    for text in (builder, deployment):
        assert "nginx.ingress.kubernetes.io/whitelist-source-range" in text and "has no authentication" in text
    # the RAG maps
    assert "python -m rag_core.providers --from" in credentials and "regenerate the maps" in credentials
    assert "rag_core/providers.py" in builder
    # generated SAS code that still sends API_KEY
    assert "SAS code generated by `Create-LLM-Call.sas` or the custom step *LLM - Create LLM Call***" in builder
    assert "`%let API_KEY=<entry name>;`" in calls and "the provider answers 401" in calls
