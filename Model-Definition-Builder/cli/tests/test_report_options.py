# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The report client behind options-save / options-restore, and the option
rewrite it feeds. Report content is BIRD XML on both legs: the read must ask
for it and the write must declare it - declaring +json for XML bytes was a 400
on every restore, and nothing here was tested."""
import pytest

from mdb.core.options import read_options, write_options
from mdb.viya.reports import CONTENT_MEDIA, find_report, get_content, put_content


class _Response:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None, payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self):
        return self._payload


class _Session:
    """Records every call; answers with whatever the test handed it."""

    def __init__(self, get: _Response | None = None, put: _Response | None = None):
        self._get, self._put = get, put
        self.calls: list = []

    def get(self, path, params=None, headers=None):
        self.calls.append(("GET", path, headers or {}, params))
        return self._get

    def put(self, path, data=None, headers=None):
        self.calls.append(("PUT", path, headers or {}, data))
        return self._put


SAS_ERROR = ('{"errorCode":10755,"message":"An error occurred. The content is invalid, '
             'possibly in the wrong format.","httpStatusCode":400}')

BIRD = """<?xml version="1.0" encoding="UTF-8"?>
<SASReport xmlns="http://www.sas.com/sasreportmodel/bird-4.2.4">
  <PromptDefinition label="contentRoot" hidden="true" isParameter="true" dataType="string" name="pr26">
    <DefaultValue><String behavior="fixed">/SAS Agentic AI Accelerator</String></DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="SCREndpoint" hidden="true" isParameter="true" dataType="string" name="pr27">
    <DefaultValue><String behavior="fixed">https://old-host/llm</String></DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="API_KEYS" hidden="true" isParameter="true" dataType="string" name="pr28">
    <DefaultValue><String behavior="fixed">key-value</String></DefaultValue>
  </PromptDefinition>
</SASReport>
"""


# -- the client: what goes over the wire ---------------------------------------

def test_content_media_is_the_xml_representation():
    assert CONTENT_MEDIA == "application/vnd.sas.report.content+xml"


def test_read_asks_for_xml_and_returns_the_etag():
    session = _Session(get=_Response(200, BIRD, {"ETag": '"ms4gykwt"'}))
    content, etag = get_content(session, "rep-1")
    assert content == BIRD and etag == '"ms4gykwt"'
    method, path, headers, _ = session.calls[0]
    assert (method, path) == ("GET", "/reports/reports/rep-1/content")
    # Pinned, not left to the server default: the same endpoint serves JSON
    # when asked, and the option rewrite would find nothing in that.
    assert headers["Accept"] == CONTENT_MEDIA


def test_write_declares_xml_and_guards_with_the_etag():
    session = _Session(put=_Response(204))
    put_content(session, "rep-1", BIRD, etag='"ms4gykwt"')
    method, path, headers, data = session.calls[0]
    assert (method, path) == ("PUT", "/reports/reports/rep-1/content")
    assert headers["Content-Type"] == CONTENT_MEDIA
    assert headers["If-Match"] == '"ms4gykwt"'
    assert data == BIRD.encode("utf-8")


def test_write_without_an_etag_sends_no_if_match():
    session = _Session(put=_Response(204))
    put_content(session, "rep-1", BIRD)
    assert "If-Match" not in session.calls[0][2]


def test_write_failure_carries_the_service_message():
    """A SAS REST error body names the problem; the status line alone does not."""
    session = _Session(put=_Response(400, SAS_ERROR))
    with pytest.raises(RuntimeError) as failure:
        put_content(session, "rep-1", BIRD)
    assert "HTTP 400" in str(failure.value)
    assert "10755" in str(failure.value) and "wrong format" in str(failure.value)


def test_read_failure_carries_the_service_message():
    session = _Session(get=_Response(404, '{"errorCode":11001,"message":"The report was not found."}'))
    with pytest.raises(RuntimeError, match="HTTP 404.*not found"):
        get_content(session, "rep-1")


def test_find_report_matches_the_exact_name_only():
    items = [{"id": "old", "name": "RAG Builder (old)"}, {"id": "cur", "name": "RAG Builder"}]
    session = _Session(get=_Response(200, payload={"items": items}))
    assert find_report(session, "RAG Builder")["id"] == "cur"
    assert find_report(_Session(get=_Response(200, payload={"items": items[:1]})), "RAG Builder") is None
    assert find_report(_Session(get=_Response(403)), "RAG Builder") is None


# -- the rewrite: what options-restore does to the XML -------------------------

def test_options_are_read_by_label():
    assert read_options(BIRD) == {
        "contentRoot": "/SAS Agentic AI Accelerator",
        "SCREndpoint": "https://old-host/llm",
        "API_KEYS": "key-value",
    }


def test_restore_rewrites_only_the_named_values_and_reports_the_rest():
    updated, result = write_options(BIRD, {
        "SCREndpoint": "https://new-host/llm?a=1&b=2",   # changed, needs escaping
        "contentRoot": "/SAS Agentic AI Accelerator",     # already matches
        "API_KEYS": "sk-should-never-be-written",          # non-portable: ignored
        "vectorStores": "chroma",                          # not in this report version
    })
    assert result.applied == {"SCREndpoint": "https://new-host/llm?a=1&b=2"}
    assert result.unchanged == {"contentRoot": "/SAS Agentic AI Accelerator"}
    assert result.missing == ["vectorStores"]
    assert result.changed
    # The rewritten XML reads back with the new value, entities intact, and
    # the key placeholder untouched.
    assert "https://new-host/llm?a=1&amp;b=2" in updated
    assert read_options(updated)["SCREndpoint"] == "https://new-host/llm?a=1&b=2"
    assert read_options(updated)["API_KEYS"] == "key-value"
    # Everything outside the touched DefaultValue is byte-identical.
    assert updated.replace("https://new-host/llm?a=1&amp;b=2", "https://old-host/llm") == BIRD


def test_restore_with_nothing_to_change_is_a_no_op():
    updated, result = write_options(BIRD, {"contentRoot": "/SAS Agentic AI Accelerator"})
    assert updated == BIRD and not result.changed and result.missing == []
