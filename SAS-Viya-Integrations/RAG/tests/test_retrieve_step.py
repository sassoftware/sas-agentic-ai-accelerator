# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retrieval as a flow step: questions in, ranked chunks out.

Same retrieval the manifested model performs, landing in a table instead of a
decision. The behaviours worth pinning are the ones that make a result table
trustworthy: a question that fails does not take the others with it, and a
question that matched nothing is visible rather than absent.
"""
import pytest

from rag_core.adapters.base import SearchHit
from rag_core.steps import RETRIEVE_COLUMNS, column_labels, run_retrieve


class FakeEmbedder:
    def __init__(self, fail_on=()):
        self.calls = []
        self._fail_on = fail_on

    def embed(self, text, mode="document"):
        self.calls.append((text, mode))
        if text in self._fail_on:
            raise RuntimeError("embedding endpoint returned 503")
        return [0.1, 0.2]


def _hit(chunk_id, content, distance, **extra):
    record = {"doc_id": "d1", "chunk_id": chunk_id, "content": content,
              "source_uri": "/policies/travel.md", "heading_path": "Travel",
              "span": {"page": 2, "start": 5, "end": 40},
              "ingested_at": "2026-07-30 09:00:00", "run_id": "run-7",
              "distance": distance}
    record.update(extra)
    return SearchHit(chunk_id, 1.0 - distance, record)


class FakeAdapter:
    def __init__(self, hits=None, fail_on=()):
        self.searched = []
        self._hits = hits if hits is not None else [
            _hit("c1", "economy class", 0.1), _hit("c2", "hotel limits", 0.3)]
        self._fail_on = fail_on

    def search(self, collection, vector, k=5, filter=None, **kw):
        self.searched.append({"k": k, "filter": filter})
        if collection in self._fail_on:
            raise RuntimeError("relation does not exist")
        return self._hits[:k]


def test_one_row_per_hit_with_the_citation_fields():
    rows = run_retrieve(["how long can a flight be?"], FakeEmbedder(),
                        FakeAdapter(), "coll", k=2, log=lambda *_: None)
    assert len(rows) == 2
    first = rows[0]
    assert first["question"] == "how long can a flight be?"
    assert first["rank"] == 1 and rows[1]["rank"] == 2
    assert first["filename"] == "travel.md"
    assert first["page"] == 2 and first["span_start"] == 5 and first["span_end"] == 40
    assert first["corpus_run_id"] == "run-7"
    assert first["distance"] == pytest.approx(0.1)
    assert first["score"] == pytest.approx(0.9)
    assert set(first) == set(RETRIEVE_COLUMNS)


def test_the_question_is_embedded_in_query_mode():
    """Ingestion embeds documents; retrieval must ask the same model for the
    query-side vector or the two live in different spaces."""
    embedder = FakeEmbedder()
    run_retrieve(["a question"], embedder, FakeAdapter(), "coll",
                 log=lambda *_: None)
    assert embedder.calls == [("a question", "query")]


def test_a_question_that_matches_nothing_still_appears():
    rows = run_retrieve(["nothing like this"], FakeEmbedder(),
                        FakeAdapter(hits=[]), "coll", log=lambda *_: None)
    assert len(rows) == 1
    assert rows[0]["rank"] == 0
    assert "no chunks matched" in rows[0]["error_text"]


def test_one_failing_question_does_not_lose_the_others():
    embedder = FakeEmbedder(fail_on=("bad one",))
    rows = run_retrieve(["good one", "bad one", "another good"], embedder,
                        FakeAdapter(), "coll", k=1, log=lambda *_: None)
    by_question = {}
    for row in rows:
        by_question.setdefault(row["question"], []).append(row)
    assert len(by_question) == 3
    assert by_question["bad one"][0]["rank"] == 0
    assert "503" in by_question["bad one"][0]["error_text"]
    assert by_question["good one"][0]["rank"] == 1
    assert by_question["another good"][0]["error_text"] == ""


def test_blank_questions_are_skipped_not_asked():
    embedder = FakeEmbedder()
    rows = run_retrieve(["", "   ", None, "real"], embedder, FakeAdapter(),
                        "coll", k=1, log=lambda *_: None)
    assert [q for q, _ in embedder.calls] == ["real"]
    assert {r["question"] for r in rows} == {"real"}


def test_k_and_filter_reach_the_adapter():
    adapter = FakeAdapter()
    run_retrieve(["q"], FakeEmbedder(), adapter, "coll", k=3,
                 filter={"doc_id": "d1"}, log=lambda *_: None)
    assert adapter.searched == [{"k": 3, "filter": {"doc_id": "d1"}}]


def test_a_broken_collection_reports_per_question_rather_than_raising():
    rows = run_retrieve(["q1", "q2"], FakeEmbedder(),
                        FakeAdapter(fail_on=("coll",)), "coll",
                        log=lambda *_: None)
    assert len(rows) == 2
    assert all(r["rank"] == 0 and "does not exist" in r["error_text"]
               for r in rows)


def test_every_output_column_carries_a_label():
    labels = column_labels(RETRIEVE_COLUMNS)
    missing = [c for c in RETRIEVE_COLUMNS if c not in labels]
    assert not missing, f"unlabelled output columns: {missing}"
