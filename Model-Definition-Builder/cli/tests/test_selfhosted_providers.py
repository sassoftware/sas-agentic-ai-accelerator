# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ollama / vLLM self-hosted OpenAI-compatible adapters.

Definitions are environment-neutral: the server base URL is the
{OLLAMA,VLLM}_BASE_URL container env var, else a default baked in - never a
scoring option - and an optional bearer token is read from a second env var.
Model weights live on the server, so the container only needs the thin
api-wrapper requirements.
"""
import json

import pytest

from mdb.core.generator import effective_score_file, render_assets
from mdb.providers import load_adapters
from mdb.providers.base import CatalogModel


def _adapter(provider_id):
    return load_adapters()[provider_id]


def test_registry_exposes_ollama_and_vllm():
    adapters = load_adapters()
    assert "ollama" in adapters and "vllm" in adapters
    assert adapters["ollama"].key_name is None  # self-hosted: no API key option
    assert adapters["vllm"].key_name is None


def test_ollama_llm_is_environment_neutral(core):
    ollama = _adapter("ollama")
    cm = CatalogModel(ref="llama3.1:8b", display_name="Llama 3.1 8B", source="manual entry")
    manifest = ollama.build_manifest(cm, "llama31_8b_ollama", {"kind": "llm"}, "tester")
    assert manifest.kind == "llm"
    rendered = render_assets(manifest, core)
    score = rendered[effective_score_file(manifest)].decode()
    # base URL resolves from the env var with the localhost default baked in
    assert 'os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"' in score
    assert "/chat/completions" in score
    # optional bearer token, never a required key
    assert 'os.environ.get("OLLAMA_API_KEY", "")' in score
    assert "options['API_KEY']" not in score and 'options["API_KEY"]' not in score
    # standard chat options route to the OpenAI-compatible body
    assert '"temperature": float(options["temperature"]),' in score
    options = json.loads(rendered["options.json"])
    # the server is the deployment's business: no option a UI shows or a caller sets
    assert "base_url" not in options
    assert "API_KEY" not in options
    # weights live on the server: thin api-wrapper, no torch download
    reqs = json.loads(rendered["requirements.json"])
    assert not any("torch" in step["command"] for step in reqs)


def test_ollama_embedding_contract(core):
    ollama = _adapter("ollama")
    cm = CatalogModel(ref="granite-embedding:30m", display_name="Granite Embedding", source="manual entry")
    manifest = ollama.build_manifest(
        cm, "granite_embed_ollama",
        {"kind": "embedding", "embedding_length": "384", "context_length": "8192"}, "tester")
    assert manifest.kind == "embedding"
    rendered = render_assets(manifest, core)
    score = rendered[effective_score_file(manifest)].decode()
    assert "def scoreModel(document, project, options):" in score
    assert "/embeddings" in score
    assert "return embedding, run_time, tokens" in score
    assert 'os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"' in score
    options = json.loads(rendered["options.json"])
    assert "base_url" not in options
    assert options["Embedding_Length"]["default"] == 384
    assert options["Input_Token_Limit"]["default"] == 8192
    config = json.loads(rendered["modelConfiguration.json"])
    assert config["function"] == "embedding" and config["targetVariable"] == "embedding"


def test_vllm_custom_base_url_is_baked(core):
    vllm = _adapter("vllm")
    cm = CatalogModel(ref="meta-llama/Llama-3.1-8B-Instruct", display_name="Llama 3.1 8B", source="manual entry")
    manifest = vllm.build_manifest(
        cm, "llama31_8b_vllm", {"kind": "llm", "base_url": "http://vllm-svc:8000/v1"}, "tester")
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode()
    # a non-default base URL is baked as the default; the env var still overrides at runtime
    assert 'os.environ.get("VLLM_BASE_URL") or "http://vllm-svc:8000/v1"' in score
    assert 'os.environ.get("VLLM_API_KEY", "")' in score


def test_vllm_default_base_url_stays_localhost(core):
    vllm = _adapter("vllm")
    cm = CatalogModel(ref="Qwen/Qwen2.5-0.5B-Instruct", display_name="Qwen2.5 0.5B", source="manual entry")
    manifest = vllm.build_manifest(cm, "qwen_vllm", {"kind": "llm"}, "tester")
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode()
    assert 'os.environ.get("VLLM_BASE_URL") or "http://localhost:8000/v1"' in score


def test_invalid_kind_is_rejected(core):
    ollama = _adapter("ollama")
    cm = CatalogModel(ref="nomic-embed-text", display_name="Nomic", source="manual")
    with pytest.raises(ValueError, match="kind must be"):
        ollama.build_manifest(cm, "nomic_bad", {"kind": "embeddings"}, "tester")  # plural typo


# --- behavioral: compile and run the generated self-hosted scorers ------------

def _render_score(adapter, model_id, answers):
    cm = CatalogModel(ref=answers.get("ref", "m"), display_name="M", source="manual")
    manifest = adapter.build_manifest(cm, model_id, answers, "tester")
    src = render_assets(manifest, core_assets())[effective_score_file(manifest)].decode()
    compile(src, f"{model_id}.py", "exec")  # never a SyntaxError, for any option combo
    return src


def core_assets():
    from mdb.core.generator import CoreAssets
    from mdb.core.paths import core_dir
    from pathlib import Path
    return CoreAssets.load(core_dir(Path(__file__).resolve().parents[3]))


def test_selfhosted_chat_scorer_resolves_env_url_and_tolerates_empty_usage(monkeypatch):
    import requests
    src = _render_score(_adapter("ollama"), "llama31_8b_ollama", {"kind": "llm"})
    captured = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "hi"}}]}  # NO 'usage' key

    monkeypatch.setattr(requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        captured.update(url=url, auth=(headers or {}).get("Authorization")) or _Resp())
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://envhost:1234/v1/")  # trailing slash
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    ns: dict = {}
    exec(src, ns)
    resp, run_time, prompt_length, output_length = ns["scoreModel"](["hi"], ["sys"], [""])
    assert resp == "hi"
    assert prompt_length == 0 and output_length == 0  # empty-usage tolerance (hosted openai_chat would KeyError)
    assert captured["url"] == "http://envhost:1234/v1/chat/completions"  # env wins, trailing slash stripped
    assert captured["auth"] is None  # no token env set -> no Authorization header


def test_selfhosted_embedding_scorer_runs_and_counts_tokens(monkeypatch):
    import requests
    src = _render_score(_adapter("ollama"), "emb_ollama",
                        {"kind": "embedding", "embedding_length": "8", "context_length": "512"})

    class _Resp:
        status_code = 200
        def json(self):
            return {"data": [{"embedding": [0.1] * 8}], "usage": {"prompt_tokens": 3}}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://envhost:9/v1")
    ns: dict = {}
    exec(src, ns)
    embedding, run_time, tokens = ns["scoreModel"](["doc"], ["proj"], [""])
    assert json.loads(embedding) == [0.1] * 8
    assert tokens == 3


def test_selfhosted_base_url_is_no_option_and_the_token_is_sent(monkeypatch):
    import requests
    src = _render_score(_adapter("vllm"), "llama_vllm", {"kind": "llm"})
    captured = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        captured.update(url=url, auth=(headers or {}).get("Authorization")) or _Resp())
    monkeypatch.setenv("VLLM_BASE_URL", "http://envhost:8000/v1")
    monkeypatch.setenv("VLLM_API_KEY", "tok-123")

    ns: dict = {}
    exec(src, ns)
    # a base_url a caller sends (an older Prompt Builder state) redirects nothing
    ns["scoreModel"](["hi"], ["sys"], ['{"base_url": "http://percall:5000/v1"}'])
    assert captured["url"] == "http://envhost:8000/v1/chat/completions"
    assert captured["auth"] == "Bearer tok-123"
    # an empty variable counts as unset: the baked default answers
    monkeypatch.setenv("VLLM_BASE_URL", "")
    ns["scoreModel"](["hi"], ["sys"], [""])
    assert captured["url"] == "http://localhost:8000/v1/chat/completions"


def test_selfhosted_answer_cut_off_by_the_budget_fails_naming_the_remedy(monkeypatch):
    # gpt-oss on Ollama, 1000 tokens: all of them reasoning, content '' and
    # finish_reason 'length' - measured. An empty answer is no answer.
    import requests
    src = _render_score(_adapter("ollama"), "gpt_oss_ollama", {"kind": "llm"})

    class _Resp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "", "reasoning": "We need to..."}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 110, "completion_tokens": 1000}}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    ns: dict = {}
    exec(src, ns)
    with pytest.raises(RuntimeError, match="raise max_tokens or lower reasoning_effort"):
        ns["scoreModel"](["hi"], ["sys"], [""])


class _Session:
    """GET /v1/models and POST /api/show of a stand-in server."""

    def __init__(self, models: list, shown: dict):
        self.models, self.shown, self.posted = models, shown, []

    def get(self, url, headers=None, timeout=None):
        return _Json({"object": "list", "data": self.models})

    def post(self, url, headers=None, json=None, timeout=None):
        self.posted.append(url)
        return _Json(self.shown.get(json["model"], {}), 200 if json["model"] in self.shown else 404)


class _Json:
    def __init__(self, body, status_code=200):
        self.body, self.status_code = body, status_code

    def json(self):
        return self.body

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


# What Ollama 0.40 answered for these three on the DGX Spark (2026-10-26), trimmed.
OLLAMA_SHOWN = {
    "gpt-oss:20b": {"capabilities": ["completion", "tools", "thinking"],
                    "model_info": {"gpt-oss.context_length": 131072, "gpt-oss.embedding_length": 2880}},
    "gemma3:12b": {"capabilities": ["completion", "vision"],
                   "model_info": {"gemma3.context_length": 131072, "gemma3.embedding_length": 3840}},
    "qwen3-embedding:0.6b": {"capabilities": ["tools", "thinking", "embedding"],
                             "model_info": {"qwen3.context_length": 32768, "qwen3.embedding_length": 1024}},
}


def test_ollama_catalog_knows_reasoning_embedding_and_lengths(monkeypatch, core):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://spark:11434/v1")
    session = _Session([{"id": ref} for ref in OLLAMA_SHOWN] + [{"id": "gone:1b"}], OLLAMA_SHOWN)
    catalog = {cm.ref: cm for cm in _adapter("ollama").live_catalog(session, None)}
    assert session.posted[0] == "http://spark:11434/api/show"  # the server root, not /v1
    oss, gemma, embed = catalog["gpt-oss:20b"], catalog["gemma3:12b"], catalog["qwen3-embedding:0.6b"]
    assert (oss.kind, oss.reasoning, oss.context_length) == ("llm", True, 131072)
    assert (gemma.kind, gemma.reasoning, gemma.context_length) == ("llm", False, 131072)
    # an embedding model that also lists "thinking" is still an embedding model
    assert (embed.kind, embed.reasoning, embed.embedding_length, embed.context_length) == ("embedding", False, 1024, 32768)
    assert catalog["gone:1b"].kind == "llm" and catalog["gone:1b"].context_length is None  # no /api/show answer

    ollama = _adapter("ollama")
    manifest = ollama.build_manifest(oss, "gpt_oss_20b", {}, "tester")
    # the budget holds the reasoning and the answer, the call the reasoning time
    assert list(manifest.options) == ["reasoning_effort", "max_tokens"]
    assert manifest.options["max_tokens"].default == 4000
    assert manifest.runtime.timeout_s == 300
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode()
    assert '"reasoning_effort": ' in score and '"max_tokens": int(options["max_tokens"])' in score
    assert "max_completion_tokens" not in score  # Ollama ignores it
    plain = ollama.build_manifest(gemma, "gemma3_12b", {}, "tester")
    assert set(plain.options) == {"temperature", "top_p", "max_tokens"} and plain.runtime.timeout_s == 60
    # a blank kind answer follows the server: the embedding model becomes an embedding definition
    embedding = ollama.build_manifest(embed, "qwen3_embedding", {"kind": ""}, "tester")
    assert embedding.kind == "embedding"
    assert embedding.options["Embedding_Length"].default == 1024
    assert embedding.options["Input_Token_Limit"].default == 32768


def test_vllm_catalog_takes_the_context_length_and_asks_no_show_api(monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "http://spark:8000/v1")
    session = _Session([{"id": "Qwen/Qwen3-4B-Instruct-2507", "max_model_len": 32768}], {})
    [cm] = _adapter("vllm").live_catalog(session, None)
    assert cm.context_length == 32768 and session.posted == []


def test_deploy_writes_the_server_and_an_optional_token(tmp_path, monkeypatch, repo_root):
    import yaml
    from typer.testing import CliRunner
    from mdb import cli
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    monkeypatch.setenv("MDB_OFFLINE", "true")
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    import io
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))
    added = CliRunner().invoke(cli.app, ["add", "vllm", "Qwen/Qwen3-4B", "--id", "qwen_vllm", "--kind", "llm",
                                         "--base-url", "http://spark:8000/v1", "--yes", "--offline"])
    assert added.exit_code == 0, out.getvalue()
    result = CliRunner().invoke(cli.app, ["deploy", "qwen_vllm", "--registry", "registry.example.com/llm",
                                          "--host", "viya.example.com", "--out", str(tmp_path / "yaml")])
    printed = out.getvalue()
    assert result.exit_code == 0, printed
    documents = list(yaml.safe_load_all((tmp_path / "yaml" / "deploy-qwen_vllm.yaml").read_text(encoding="utf-8")))
    deployment = next(doc for doc in documents if doc and doc["kind"] == "Deployment")
    entries = {entry["name"]: entry for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert entries["VLLM_BASE_URL"] == {"name": "VLLM_BASE_URL", "value": "http://spark:8000/v1"}
    assert entries["VLLM_API_KEY"] == {"name": "VLLM_API_KEY", "valueFrom": {
        "secretKeyRef": {"name": "llm-api-keys", "key": "VLLM_API_KEY", "optional": True}}}
    assert "server http://spark:8000/v1, optional token llm-api-keys/VLLM_API_KEY" in printed
    # the Secret may not exist yet (zeus had none): create it, or patch the one there
    assert 'kubectl create secret generic llm-api-keys --from-literal=VLLM_API_KEY="$VLLM_API_KEY" -n llm' in printed
    assert ("kubectl patch secret llm-api-keys -n llm --type merge "
            """-p '{"stringData":{"VLLM_API_KEY":"'"$VLLM_API_KEY"'"}}'""") in printed
    # the workstation's variable names the server the cluster should call
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.llm.svc:8000/v1")
    assert CliRunner().invoke(cli.app, ["deploy", "qwen_vllm", "--registry", "registry.example.com/llm",
                                        "--host", "viya.example.com", "--out", str(tmp_path / "yaml")]).exit_code == 0
    assert 'value: "http://vllm.llm.svc:8000/v1"' in (tmp_path / "yaml" / "deploy-qwen_vllm.yaml").read_text(
        encoding="utf-8")


def test_embedding_smoke_test_refuses_a_length_the_server_does_not_return(monkeypatch):
    # vLLM lists no vector length: an added definition keeps the 768 fallback,
    # Qwen3-Embedding-0.6B returns 1024 (measured) - the live check says so.
    vllm = _adapter("vllm")
    cm = CatalogModel(ref="Qwen/Qwen3-Embedding-0.6B", display_name="Qwen3 Embedding", source="live")
    manifest = vllm.build_manifest(cm, "qwen_embed_vllm", {"kind": "embedding"}, "tester")
    assert manifest.options["Embedding_Length"].default == 768

    class _Server:
        def post(self, url, headers=None, json=None, timeout=None):
            return _Json({"data": [{"embedding": [0.0] * 1024}]})

    monkeypatch.setenv("VLLM_BASE_URL", "http://spark:8001/v1")
    result = vllm.smoke_test(manifest, None, _Server())
    assert not result.ok
    assert "1024-dim" in result.detail and "set options.Embedding_Length.default to 1024" in result.detail
    manifest.options["Embedding_Length"].default = 1024
    assert vllm.smoke_test(manifest, None, _Server()).ok


@pytest.mark.parametrize("provider, model_id, kind, args", [
    ("vllm", "qwen_vllm", "llm", (["hi"], ["sys"], [""])),
    ("vllm", "embed_vllm", "embedding", (["doc"], ["proj"], [""])),
])
def test_a_refused_token_names_the_variable_on_the_failing_line(provider, model_id, kind, args, monkeypatch):
    # vLLM with --api-key answers 401 to a container without VLLM_API_KEY
    # (measured on zeus); SCR shows the caller the source line that raised.
    import inspect
    import requests
    src = _render_score(_adapter(provider), model_id, {"kind": kind, "embedding_length": "8"})

    class _Resp:
        status_code = 401
        def json(self):
            return {"error": "Unauthorized"}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    ns: dict = {}
    exec(src, ns)
    with pytest.raises(RuntimeError) as raised:
        ns["scoreModel"](*args)
    assert "set VLLM_API_KEY in the key Secret" in str(raised.value)
    line = src.splitlines()[raised.traceback[-1].lineno]
    assert "VLLM_API_KEY" in line and "raise RuntimeError(" in line
