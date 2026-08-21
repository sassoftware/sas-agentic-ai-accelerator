# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The deferred review findings, and the cases that prove each one.

Every test here reproduces a failure that shipped: something the pipeline did
silently, so nothing raised and nothing in a log said it had happened.
"""
import importlib.util
import pathlib
import sys

import pytest

from rag_core.chunkers import CHUNKERS, document_text, locate
from rag_core.steps import run_list, run_load

RAG = pathlib.Path(__file__).resolve().parents[1]


def _retrieval():
    """retrieve_context.py is a standalone manifested file, not a package."""
    spec = importlib.util.spec_from_file_location(
        "retrieve_context_under_test", RAG / "retrieve_context.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. the deployed retrieval model could not embed its query
# ---------------------------------------------------------------------------
def test_the_query_embedding_carries_a_key_the_caller_supplied():
    """A decision in SAS Intelligent Decisioning has no Viya session, so the
    credential-domain lookup that serves the store credentials resolves
    nothing. The key has to arrive in the options input, as it does for the
    LLM score code - otherwise the container refuses every query and the
    degradation contract turns that into an empty datagrid."""
    options = _retrieval()._embed_options({"API_KEY": "sk-test-value"})
    assert "Embedding_Mode:query" in options
    assert "API_KEY:sk-test-value" in options


def test_a_locally_served_model_is_sent_no_key():
    options = _retrieval()._embed_options({})
    assert options == "{Embedding_Mode:query}"


def test_the_key_may_also_come_from_the_destination_environment(monkeypatch):
    monkeypatch.setenv("RAGEMBED_API_KEY", "from-the-env")
    assert "API_KEY:from-the-env" in _retrieval()._embed_options({})


# ---------------------------------------------------------------------------
# 5. citations lost their span whenever whitespace was rewritten
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label,chunker,texts,kwargs", [
    ("a paragraph gap wider than one blank line", "paragraph",
     ["Alpha beta gamma.\n\n\n\nDelta epsilon zeta."],
     dict(max_tokens=200, max_bytes=24000)),
    ("a paragraph padded with spaces", "paragraph",
     ["Alpha beta gamma.\n\n   Delta epsilon zeta.   "],
     dict(max_tokens=200, max_bytes=24000)),
    ("a recursive chunk spanning a collapsed run", "recursive",
     ["Alpha beta.\n\n\n\nGamma delta.\n\n" + "filler word " * 80],
     dict(max_tokens=40, max_bytes=24000, overlap_tokens=0)),
    ("the same, with overlap", "recursive",
     ["Alpha beta.\n\n\n\nGamma delta.\n\n" + "filler word " * 80],
     dict(max_tokens=40, max_bytes=24000, overlap_tokens=10)),
])
def test_every_chunk_can_still_be_found_in_the_document(label, chunker, texts, kwargs):
    """The chunkers do not emit verbatim slices: _split_recursive drops empty
    parts and re-joins with ONE separator, and paragraph_chunks strips each
    paragraph. An exact search missed the result, so the chunk got no span and
    the citation could not be opened where the text actually is."""
    joined = document_text(texts)
    chunks = CHUNKERS[chunker](texts, **kwargs)
    spans = locate(joined, chunks)
    assert chunks, label
    assert all(span is not None for span in spans), (
        label + ": " + repr([c for c, s in zip(chunks, spans) if s is None][:1]))
    # and the span points at that chunk's text, not merely at something
    for chunk, span in zip(chunks, spans):
        found = joined[span[0]:span[1]].split()
        assert found in (chunk.split(), chunk.partition("\n")[2].split()), label


def test_a_span_is_still_refused_when_the_text_is_not_there():
    """The flexible match must not become a guess: a chunk that is genuinely
    absent still returns None, because a wrong citation is worse than none."""
    assert locate("Alpha beta gamma.", ["nothing like this at all"]) == [None]


# ---------------------------------------------------------------------------
# 4. a header-only enrichment never got its provenance column
# ---------------------------------------------------------------------------
class _Schema:
    """Records what run_load asks the collection's schema for."""

    needs_flush = False

    def __init__(self):
        self.wanted = None

    def ensure_collection(self, name, dims, metric="cosine", schema=None):
        pass

    def sync_attributes(self, collection, wanted):
        self.wanted = wanted
        return {"added": ["enrich_version"], "kept": []}

    def attribute_columns(self, collection):
        return []

    def upsert(self, collection, records):
        return len(records)

    def retire(self, collection, ids=None, filter=None, run_id=None, keep_ids=None):
        return 0

    def count(self, collection, filter=None, include_retired=False, as_of=None):
        return 0


def test_a_header_only_enrichment_still_gets_its_provenance_column():
    """The headline enrichment - a contextual header with no stored outputs -
    passes attributes=None, and sync_attributes is the only place that creates
    enrich_version. Skipping the call left the stamp with nowhere to land, so
    "which prompt wrote this chunk" answered "column does not exist"."""
    adapter = _Schema()
    chunk = {"doc_id": "d1", "chunk_id": "c1", "content": "x",
             "embedding": [0.1], "enrich_version": "header@1.1"}
    run_load([chunk], [{"doc_id": "d1", "status": "new"}], adapter, "coll", 1,
             "v1", attributes=None, log=lambda *_: None)
    assert adapter.wanted == {}, "sync_attributes must still be called"


def test_a_run_that_enriched_nothing_adds_no_enrichment_column():
    """The other side: a plain setup must not grow an enrich_version column
    it will never fill."""
    adapter = _Schema()
    run_load([{"doc_id": "d1", "chunk_id": "c1", "content": "x",
               "embedding": [0.1]}],
             [{"doc_id": "d1", "status": "new"}], adapter, "coll", 1, "v1",
             attributes=None, log=lambda *_: None)
    assert adapter.wanted is None


# ---------------------------------------------------------------------------
# the skipped/excluded distinction
# ---------------------------------------------------------------------------
class _Adapter:
    needs_flush = False

    def __init__(self, existing=1):
        self.existing = existing
        self.retired = []
        self.upserted = []

    def ensure_collection(self, name, dims, metric="cosine", schema=None):
        pass

    def sync_attributes(self, collection, wanted):
        return {"added": [], "kept": []}

    def attribute_columns(self, collection):
        return []

    def upsert(self, collection, records):
        self.upserted.extend(records)
        return len(records)

    def retire(self, collection, ids=None, filter=None, run_id=None, keep_ids=None):
        self.retired.append(filter)
        return self.existing

    def delete(self, collection, ids=None, filter=None):
        raise AssertionError("an excluded document must never be purged")

    def count(self, collection, filter=None, include_retired=False, as_of=None):
        return 0


def test_a_source_file_the_setup_excludes_is_not_called_skipped(tmp_path):
    (tmp_path / "build.py").write_text("print('hi')", encoding="utf-8")
    from rag_core.sources import FileSystemSource
    rows = run_list(FileSystemSource(str(tmp_path)), [], "run-1", "v1", "cfg",
                    include_code=False, log=lambda *_: None)
    assert [r["status"] for r in rows] == ["excluded"]


def test_excluding_a_document_retires_the_chunks_it_already_had():
    """Turning code ingestion off is how someone gets those files OUT of the
    index. Leaving the chunks live meant they kept being returned, with
    nothing in the ledger to explain why."""
    adapter = _Adapter(existing=3)
    seen = []
    run_load([], [{"doc_id": "d1", "status": "excluded",
                   "source_uri": "/x/build.py"}],
             adapter, "coll", 2, "v1", run_id="run-2", log=seen.append)
    assert adapter.retired == [{"doc_id": "d1"}]
    assert any("excluded by this setup" in line for line in seen)


@pytest.mark.parametrize("reason", ["no extractor for .docx files",
                                    "pdfminer found no text"])
def test_a_document_the_pipeline_could_not_read_keeps_its_chunks(reason):
    """The other half of the distinction. An absent extractor package is true
    today and false tomorrow, so retiring on it would let one missing
    dependency quietly empty a corpus."""
    adapter = _Adapter(existing=3)
    run_load([], [{"doc_id": "d1", "status": "skipped",
                   "source_uri": "/x/a.docx", "error_text": reason}],
             adapter, "coll", 2, "v1", run_id="run-2", log=lambda *_: None)
    assert adapter.retired == []


def test_an_excluded_document_is_reported_like_a_skipped_one():
    """Both mean "not ingested, and here is why" to a reader; only their
    effect on existing chunks differs."""
    from rag_core.steps import log_skipped
    seen = []
    log_skipped([{"status": "excluded", "error_text": "code file (.py)",
                  "source_uri": "/x/a.py"}], log=seen.append)
    assert seen and "code file (.py)" in seen[0]
