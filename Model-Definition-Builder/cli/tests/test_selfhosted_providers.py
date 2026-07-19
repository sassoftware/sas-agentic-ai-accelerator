# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ollama / vLLM self-hosted OpenAI-compatible adapters.

Definitions are environment-neutral: the server base URL resolves per call via
option > {OLLAMA,VLLM}_BASE_URL container env var > a localhost default baked in,
and an optional bearer token is read from a second env var. Model weights live
on the server, so the container only needs the thin api-wrapper requirements.
"""
import json

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
