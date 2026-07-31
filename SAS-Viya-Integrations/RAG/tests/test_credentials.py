# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import base64

import pytest

from rag_core.credentials import (fetch_secrets, secrets_available,
                                  store_config_from_secrets)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses[url]

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url, kwargs))
        return self.responses[url]


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


BASE = "https://viya.example.com"
URL = BASE + "/credentials/domains/agentic-ai-keys/secrets"


def test_fetch_decodes_whole_map():
    session = FakeSession({URL: FakeResponse(200, {
        "secrets": {"OpenAI": _b64("sk-test"),
                    "PGVECTOR_RAG_USER": _b64("rag_ingest"),
                    "PGVECTOR_RAG_PW": _b64("s3cret")},
    })})
    secrets = fetch_secrets(BASE, "tok", "agentic-ai-keys", session=session)
    assert secrets == {"OpenAI": "sk-test", "PGVECTOR_RAG_USER": "rag_ingest",
                       "PGVECTOR_RAG_PW": "s3cret"}
    method, url, kwargs = session.calls[0]
    assert kwargs["params"] == {"lookupInGroup": "true"}
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_fetch_returns_none_on_404():
    session = FakeSession({URL: FakeResponse(404)})
    assert fetch_secrets(BASE, "tok", "agentic-ai-keys", session=session) is None


def test_available_uses_head():
    session = FakeSession({URL: FakeResponse(200)})
    assert secrets_available(BASE, "tok", "agentic-ai-keys", session=session)
    assert session.calls[0][0] == "HEAD"


def test_store_config_uses_backend_prefixed_entries():
    secrets = {"PGVECTOR_RAG_USER": "rag_ingest", "PGVECTOR_RAG_PW": "s3cret",
               "OpenAI": "sk-x"}
    config = store_config_from_secrets(secrets, "pgvector", "dbhost", 5432,
                                       "vectors", sslmode="false")
    assert config["user"] == "rag_ingest"
    assert config["password"] == "s3cret"
    assert config["sslmode"] == "false"   # adapter normalizes booleans later


def test_store_config_rejects_missing_backend_entries():
    with pytest.raises(KeyError, match="PGVECTOR_RAG_USER"):
        store_config_from_secrets({"OpenAI": "sk-x"}, "pgvector", "h", 5432, "db")


def test_a_blank_port_follows_the_backend():
    """The step's port field may be left empty; a SingleStore setup must then
    reach 3306, not Postgres's 5432."""
    secrets = {"SINGLESTORE_RAG_USER": "u", "SINGLESTORE_RAG_PW": "p",
               "PGVECTOR_RAG_USER": "u", "PGVECTOR_RAG_PW": "p"}
    assert store_config_from_secrets(
        secrets, "singlestore", "h", "", "db")["port"] == 3306
    assert store_config_from_secrets(
        secrets, "pgvector", "h", "", "db")["port"] == 5432
    assert store_config_from_secrets(
        secrets, "singlestore", "h", 3307, "db")["port"] == 3307


def test_undecodable_secret_becomes_empty_not_crash():
    session = FakeSession({URL: FakeResponse(200, {
        "secrets": {"OpenAI": "%%%not-base64%%%"}})})
    secrets = fetch_secrets(BASE, "tok", "agentic-ai-keys", session=session)
    assert secrets["OpenAI"] == ""


# ---------------------------------------------------------------------------
# connection settings from the domain (so a UI need not ask for them)
# ---------------------------------------------------------------------------
FULL = {"PGVECTOR_RAG_USER": "u", "PGVECTOR_RAG_PW": "p",
        "PGVECTOR_HOST": "store.example.com", "PGVECTOR_PORT": "6432",
        "PGVECTOR_DB": "vectors", "PGVECTOR_SSLMODE": "require"}


def test_the_domain_can_supply_the_whole_connection():
    """So the Builder can stop asking users for details they should not hold."""
    config = store_config_from_secrets(FULL, "pgvector", "", "", "", "")
    assert config["host"] == "store.example.com"
    assert config["port"] == 6432
    assert config["dbname"] == "vectors"
    assert config["sslmode"] == "require"


def test_an_explicit_value_still_wins():
    """A step author who typed a host gets that host."""
    config = store_config_from_secrets(FULL, "pgvector", "typed.example.com",
                                       5433, "typed_db", "disable")
    assert config["host"] == "typed.example.com"
    assert config["port"] == 5433
    assert config["dbname"] == "typed_db"
    assert config["sslmode"] == "disable"


def test_a_missing_host_names_what_to_add():
    secrets = {"PGVECTOR_RAG_USER": "u", "PGVECTOR_RAG_PW": "p"}
    try:
        store_config_from_secrets(secrets, "pgvector", "", "", "", "")
    except KeyError as error:
        assert "PGVECTOR_HOST" in str(error) and "PGVECTOR_DB" in str(error)
    else:
        raise AssertionError("expected a KeyError naming the entries")


def test_the_port_still_falls_back_to_the_backend_default():
    secrets = dict(FULL)
    secrets.pop("PGVECTOR_PORT")
    assert store_config_from_secrets(secrets, "pgvector", "", "", "", "")["port"] == 5432


def test_the_unprefixed_names_are_the_fallback():
    """Same precedence as env.py, so a .env and a domain spell it the same."""
    secrets = {"PGVECTOR_RAG_USER": "u", "PGVECTOR_RAG_PW": "p",
               "RAGSTORE_HOST": "shared.example.com", "RAGSTORE_DB": "vectors"}
    config = store_config_from_secrets(secrets, "pgvector", "", "", "", "")
    assert config["host"] == "shared.example.com"
    assert config["dbname"] == "vectors"


def test_the_prefixed_name_beats_the_fallback():
    secrets = {"SINGLESTORE_RAG_USER": "u", "SINGLESTORE_RAG_PW": "p",
               "RAGSTORE_HOST": "postgres.example.com", "RAGSTORE_DB": "a",
               "SINGLESTORE_HOST": "s2.example.com", "SINGLESTORE_DB": "b"}
    config = store_config_from_secrets(secrets, "singlestore", "", "", "", "")
    assert config["host"] == "s2.example.com"
    assert config["dbname"] == "b"
