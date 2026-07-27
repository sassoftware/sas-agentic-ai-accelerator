# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import base64

import pytest

from rag_core.credentials import (credential_available, fetch_credential,
                                  resolve_secret, store_config_from_credential)


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
URL = BASE + "/credentials/domains/agentic-ai-pgvector/secrets"


def test_fetch_decodes_secrets_and_keeps_properties():
    session = FakeSession({URL: FakeResponse(200, {
        "properties": {"userId": "rag_ingest"},
        "secrets": {"password": _b64("s3cret")},
    })})
    credential = fetch_credential(BASE, "tok", "agentic-ai-pgvector", session=session)
    assert credential["secrets"]["password"] == "s3cret"
    assert credential["properties"]["userId"] == "rag_ingest"
    method, url, kwargs = session.calls[0]
    assert kwargs["params"] == {"lookupInGroup": "true"}
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_fetch_returns_none_on_404():
    session = FakeSession({URL: FakeResponse(404)})
    assert fetch_credential(BASE, "tok", "agentic-ai-pgvector", session=session) is None


def test_available_uses_head():
    session = FakeSession({URL: FakeResponse(200)})
    assert credential_available(BASE, "tok", "agentic-ai-pgvector", session=session)
    assert session.calls[0][0] == "HEAD"


def test_resolve_secret_builds_domain_from_prefix():
    url = BASE + "/credentials/domains/agentic-ai-OpenAI/secrets"
    session = FakeSession({url: FakeResponse(200, {
        "secrets": {"password": _b64("sk-test")}, "properties": {}})})
    credential = resolve_secret(BASE, "tok", "agentic-ai-", "OpenAI", session=session)
    assert credential["secrets"]["password"] == "sk-test"


def test_store_config_merges_credential_and_setup_config():
    credential = {"properties": {"userId": "rag_ingest"},
                  "secrets": {"password": "s3cret"}}
    config = store_config_from_credential(credential, "dbhost", 5432, "vectors",
                                          sslmode="false")
    assert config["user"] == "rag_ingest"
    assert config["password"] == "s3cret"
    assert config["sslmode"] == "false"   # adapter normalizes booleans later


def test_store_config_rejects_incomplete_credential():
    with pytest.raises(KeyError):
        store_config_from_credential({"properties": {}, "secrets": {}},
                                     "h", 5432, "db")


def test_undecodable_secret_becomes_empty_not_crash():
    session = FakeSession({URL: FakeResponse(200, {
        "secrets": {"password": "%%%not-base64%%%"}, "properties": {}})})
    credential = fetch_credential(BASE, "tok", "agentic-ai-pgvector", session=session)
    assert credential["secrets"]["password"] == ""
