# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CAS fact-sheet load: REST call sequence (unload -> upload+promote -> save).

No network: a fake session records the calls so the casManagement contract
(global scope on upload, sashdat replace on save, unload-before-reload) is
asserted without a live Viya.
"""
import json

import pytest

from mdb.viya import cas


class FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, servers=None, upload_status=201):
        self.calls = []
        self._servers = servers if servers is not None else [{"name": "cas-shared-default"}]
        self._upload_status = upload_status

    def get(self, path, **kw):
        self.calls.append(("GET", path, kw))
        if path.startswith("/casManagement/servers") and "caslibs" not in path:
            return FakeResponse(200, {"items": self._servers})
        return FakeResponse(200, {})

    def put(self, path, **kw):
        self.calls.append(("PUT", path, kw))
        return FakeResponse(200)

    def post(self, path, **kw):
        self.calls.append(("POST", path, kw))
        # the tables collection endpoint is the upload; the item endpoint is save
        if path.endswith("/tables"):
            return FakeResponse(self._upload_status, {"name": "T"})
        return FakeResponse(200, {})

    def delete(self, path, **kw):
        self.calls.append(("DELETE", path, kw))
        return FakeResponse(204)


def _csv(tmp_path):
    p = tmp_path / "sheet.csv"
    p.write_bytes(b"model_id,provider\n\"x\",\"y\"\n")
    return p


def test_resolve_server_prefers_default_then_first():
    assert cas.resolve_server(FakeSession(), "custom") == "custom"
    assert cas.resolve_server(FakeSession([{"name": "other"}, {"name": "cas-shared-default"}]), None) == "cas-shared-default"
    assert cas.resolve_server(FakeSession([{"name": "only-one"}]), None) == "only-one"


def test_load_fact_sheet_sequences_unload_upload_save(tmp_path):
    session = FakeSession()
    result = cas.load_fact_sheet(session, _csv(tmp_path), "llm", "Public", "cas-shared-default")

    assert result == {"table": "LLM_FACT_SHEET", "dropped": True}
    methods = [(m, p) for m, p, _ in session.calls]
    base = "/casManagement/servers/cas-shared-default/caslibs/Public/tables"

    # 1) unload the existing loaded table first
    assert methods[0] == ("PUT", f"{base}/LLM_FACT_SHEET/state?value=unloaded")

    # 2) upload with global scope (= promote), CSV file field, header row
    upload_method, upload_path, upload_kw = session.calls[1]
    assert (upload_method, upload_path) == ("POST", base)
    assert upload_kw["data"] == {
        "tableName": "LLM_FACT_SHEET",
        "format": "csv",
        "containsHeaderRow": "true",
        "scope": "global",
    }
    assert "file" in upload_kw["files"]

    # 3) save to disk with replace, sashdat, and the save-request media type
    save_method, save_path, save_kw = session.calls[2]
    assert (save_method, save_path) == ("POST", f"{base}/LLM_FACT_SHEET")
    assert json.loads(save_kw["data"]) == {"replace": True, "format": "sashdat"}
    assert save_kw["headers"]["Content-Type"] == "application/vnd.sas.cas.table.save.request+json"


def test_embedding_table_name_and_upload_failure(tmp_path):
    # embedding maps to the other well-known table name
    assert cas.TABLE_BY_KIND["embedding"] == "EMBEDDING_FACT_SHEET"

    session = FakeSession(upload_status=409)
    with pytest.raises(RuntimeError, match="Uploading EMBEDDING_FACT_SHEET"):
        cas.load_fact_sheet(session, _csv(tmp_path), "embedding", "Public", "cas-shared-default")
