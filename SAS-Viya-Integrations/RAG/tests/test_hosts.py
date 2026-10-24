"""No host name or address in the text of a failure that is stored.

SingleStore writes the internal host and port of the node that failed into its error text, psycopg the server
it could not reach, urllib3 the host of the request - and that text reached `error_text` of the ledger, the step
tables and `rag_runs` / `rag_doc_events`, which everyone who reads a setup sees. without_hosts (rag_core/history.py) puts <host>
in its place; a file name or a module path in the same text stays.
"""
import re
from pathlib import Path

import pytest

from rag_core.history import HOST, without_hosts

RAG = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text, expected", [
    ("Leaf Error (node-1.svc-singlestore.svc.cluster.local:3306): Lost connection",
     "Leaf Error (<host>:3306): Lost connection"),
    ('could not translate host name "pg.example.com" to address', 'could not translate host name "<host>" to address'),
    ('connection to server at "pg.corp.example.com" (10.0.3.17), port 5432 failed',
     'connection to server at "<host>" (<host>), port 5432 failed'),
    ("HTTPSConnectionPool(host='api.openai.com', port=443): Max retries exceeded with url: /v1/embeddings",
     "HTTPSConnectionPool(host='<host>', port=443): Max retries exceeded with url: /v1/embeddings"),
    ("POST https://user@scr.viya.example.com:8443/llm/x failed", "POST https://user@<host>:8443/llm/x failed"),
    ("GET http://[fe80::1]:8080/ failed", "GET http://<host>:8080/ failed"),
    ("memsql-0.memsql.default.svc refused", "<host> refused"),
    ("10.12.0.4:3306 timed out", "<host>:3306 timed out"),
], ids=["singlestore", "psycopg name", "psycopg server", "urllib3", "url", "ipv6 url", "cluster domain", "address"])
def test_a_host_and_an_address_are_masked(text, expected):
    assert without_hosts(text) == expected


@pytest.mark.parametrize("text", [
    "unreadable: [Errno 2] No such file: annual.report.2024.pdf",
    "singlestoredb.exceptions.OperationalError: 2003 cannot connect",
    "no extractor for .pptx files",
    "rag_core.steps: chunk 12 failed",
    "",
], ids=["file name", "module path", "suffix", "module", "empty"])
def test_the_rest_of_the_text_stays(text):
    assert without_hosts(text) == text


def test_none_is_an_empty_text():
    assert without_hosts(None) == ""


def test_every_failure_text_steps_stores_goes_through_it():
    """Each error_text of steps.py made from an exception is masked; the fixed texts it writes name no host."""
    code = (RAG / "rag_core" / "steps.py").read_text(encoding="utf-8")
    from_exception = re.findall(r'error_text"?\s*[=:]\s*([^\n]*exc[^\n]*)', code)
    assert len(from_exception) == 5
    assert all("without_hosts(exc)" in line for line in from_exception), from_exception
    assert "str(exc)[:500]" not in code


def test_the_history_masks_what_it_writes():
    history = (RAG / "rag_core" / "history.py").read_text(encoding="utf-8")
    assert '"error_text": without_hosts(row.get("error_text"))[:500],' in history
    assert 'values["error_text"] = without_hosts(values["error_text"])' in history
    job = (RAG.parent / "RAG-Ingestion" / "Ingest-Documents.sas").read_text(encoding="utf-8")
    assert 'error_text = without_hosts(f"{type(error).__name__}: {error}")' in job
    assert HOST == "<host>"
