# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ollama / vLLM self-hosted OpenAI-compatible adapters.

Definitions are environment-neutral: the server base URL resolves per call via
option > {OLLAMA,VLLM}_BASE_URL container env var > a localhost default baked in,
and an optional bearer token is read from a second env var. Model weights live
on the server, so the container only needs the thin api-wrapper requirements.
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
    assert 'os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")' in score
    assert "/chat/completions" in score
    # optional bearer token, never a required key
    assert 'os.environ.get("OLLAMA_API_KEY", "")' in score
    assert "options['API_KEY']" not in score and 'options["API_KEY"]' not in score
    # standard chat options route to the OpenAI-compatible body
    assert '"temperature": float(options["temperature"]),' in score
    options = json.loads(rendered["options.json"])
    assert options["base_url"]["default"] == "http://localhost:11434/v1"
    assert "OLLAMA_BASE_URL" in options["base_url"]["description"]
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
    assert 'os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")' in score
    options = json.loads(rendered["options.json"])
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
    assert 'os.environ.get("VLLM_BASE_URL", "http://vllm-svc:8000/v1")' in score
    assert 'os.environ.get("VLLM_API_KEY", "")' in score


def test_vllm_default_base_url_stays_localhost(core):
    vllm = _adapter("vllm")
    cm = CatalogModel(ref="Qwen/Qwen2.5-0.5B-Instruct", display_name="Qwen2.5 0.5B", source="manual entry")
    manifest = vllm.build_manifest(cm, "qwen_vllm", {"kind": "llm"}, "tester")
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode()
    assert 'os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1")' in score


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


def test_selfhosted_percall_base_url_overrides_env_and_sends_bearer(monkeypatch):
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
    # a per-call base_url option must win over the env var (runtime precedence: option > env > default)
    ns["scoreModel"](["hi"], ["sys"], ['{"base_url": "http://percall:5000/v1"}'])
    assert captured["url"] == "http://percall:5000/v1/chat/completions"
    assert captured["auth"] == "Bearer tok-123"
