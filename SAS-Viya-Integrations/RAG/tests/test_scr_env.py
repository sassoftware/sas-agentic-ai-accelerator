# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest

from rag_core.scr import EmbeddingClient, options_string


def test_options_string_scr_form():
    assert options_string({"Embedding_Mode": "query"}) == "{Embedding_Mode:query}"


def test_from_env_reads_ragembed_vars(monkeypatch):
    monkeypatch.setenv("RAGEMBED_ENDPOINT", "https://viya.example.com/llm")
    monkeypatch.setenv("RAGEMBED_MODEL", "all_minilm_l6_v2")
    monkeypatch.setenv("RAGEMBED_SSLVERIFY", "false")
    client = EmbeddingClient.from_env()
    assert client.url == "https://viya.example.com/llm/all_minilm_l6_v2/all_minilm_l6_v2"
    assert client.verify_ssl is False


def test_from_env_aca_url_shape(monkeypatch):
    monkeypatch.setenv("RAGEMBED_ENDPOINT", "https://apps.example.io")
    monkeypatch.setenv("RAGEMBED_MODEL", "all_minilm_l6_v2")
    monkeypatch.setenv("RAGEMBED_DEPLOYMENT_TYPE", "aca")
    client = EmbeddingClient.from_env()
    assert client.url == "https://all-minilm-l6-v2.apps.example.io/all_minilm_l6_v2"


def test_from_env_overrides_win(monkeypatch):
    monkeypatch.setenv("RAGEMBED_ENDPOINT", "https://wrong.example.com/llm")
    monkeypatch.setenv("RAGEMBED_MODEL", "wrong_model")
    client = EmbeddingClient.from_env(
        scr_endpoint="https://right.example.com/llm", model_name="right_model")
    assert client.url == "https://right.example.com/llm/right_model/right_model"


def test_from_env_ca_bundle_path(monkeypatch):
    monkeypatch.setenv("RAGEMBED_ENDPOINT", "https://viya.example.com/llm")
    monkeypatch.setenv("RAGEMBED_MODEL", "m")
    monkeypatch.setenv("RAGEMBED_SSLVERIFY", "/security/ca.pem")
    assert EmbeddingClient.from_env().verify_ssl == "/security/ca.pem"


def test_from_env_incomplete_raises(monkeypatch):
    monkeypatch.delenv("RAGEMBED_ENDPOINT", raising=False)
    monkeypatch.delenv("RAGEMBED_MODEL", raising=False)
    with pytest.raises(KeyError):
        EmbeddingClient.from_env()
