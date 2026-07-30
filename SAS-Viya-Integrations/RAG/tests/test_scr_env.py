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


# ---------------------------------------------------------------------------
# two vector stores at once: prefixed settings with a shared fallback
# ---------------------------------------------------------------------------
def test_a_backend_uses_its_own_connection_settings():
    from rag_core.env import config_from_rows
    rows = [("SINGLESTORE_HOST", "s2.example"), ("SINGLESTORE_DB", "vectors"),
            ("SINGLESTORE_RAG_USER", "u"), ("SINGLESTORE_RAG_PW", "p"),
            ("RAGSTORE_HOST", "postgres.example"), ("RAGSTORE_DB", "postgres")]
    config = config_from_rows(rows, backend="singlestore")
    assert config["host"] == "s2.example"
    assert config["dbname"] == "vectors"
    assert config["port"] == 3306, "SingleStore's default port, not Postgres's"


def test_the_shared_fallback_serves_a_backend_without_its_own():
    from rag_core.env import config_from_rows
    rows = [("RAGSTORE_HOST", "postgres.example"), ("RAGSTORE_DB", "postgres"),
            ("PGVECTOR_RAG_USER", "u"), ("PGVECTOR_RAG_PW", "p")]
    config = config_from_rows(rows, backend="pgvector")
    assert config["host"] == "postgres.example"
    assert config["port"] == 5432


def test_two_backends_resolve_to_different_stores_from_one_environment():
    from rag_core.env import config_from_rows
    rows = [("RAGSTORE_HOST", "postgres.example"), ("RAGSTORE_DB", "postgres"),
            ("PGVECTOR_RAG_USER", "pu"), ("PGVECTOR_RAG_PW", "pp"),
            ("SINGLESTORE_HOST", "s2.example"), ("SINGLESTORE_DB", "vectors"),
            ("SINGLESTORE_PORT", "3307"),
            ("SINGLESTORE_RAG_USER", "su"), ("SINGLESTORE_RAG_PW", "sp")]
    pg = config_from_rows(rows, backend="pgvector")
    s2 = config_from_rows(rows, backend="singlestore")
    assert (pg["host"], pg["user"]) == ("postgres.example", "pu")
    assert (s2["host"], s2["user"], s2["port"]) == ("s2.example", "su", 3307)


def test_a_missing_host_names_both_the_prefixed_and_the_fallback_key():
    import pytest as _pytest
    from rag_core.env import config_from_rows
    with _pytest.raises(KeyError) as raised:
        config_from_rows([("SINGLESTORE_RAG_USER", "u"),
                          ("SINGLESTORE_RAG_PW", "p")], backend="singlestore")
    message = str(raised.value)
    assert "SINGLESTORE_HOST" in message and "RAGSTORE_HOST" in message
    assert "u" not in message.replace("SINGLESTORE_RAG_USER", ""), \
        "a secret value must never reach an error message"
