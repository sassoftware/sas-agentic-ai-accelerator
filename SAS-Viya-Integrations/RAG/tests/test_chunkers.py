# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from rag_core.chunkers import paragraph_chunks, recursive_chunks
from rag_core.schema import Chunk, link_neighbors, make_chunk_id
from rag_core.tokens import estimate_tokens, token_budget


def test_token_estimate_monotone_and_conservative():
    short = estimate_tokens("hello world")
    long = estimate_tokens("hello world " * 100)
    assert 0 < short < long
    # wordpiece counts exceed whitespace words; estimate must not undercount words
    assert estimate_tokens("one two three four") >= 4


def test_token_budget_applies_margin():
    assert token_budget(256) == int(256 * 0.85)
    assert token_budget(10) == 16  # floor


def test_recursive_respects_token_cap():
    text = ("Sentence number one. " * 40 + "\n\n") * 5
    chunks = recursive_chunks([text], max_tokens=120, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(estimate_tokens(c) <= 120 for c in chunks)
    # order and content survive
    assert "Sentence number one." in chunks[0]


def test_recursive_respects_byte_cap():
    text = "ü" * 50_000  # 2 bytes per char in UTF-8
    chunks = recursive_chunks([text], max_tokens=10_000, max_bytes=8_000, overlap_tokens=0)
    assert all(len(c.encode("utf-8")) <= 8_000 for c in chunks)


def test_recursive_overlap_carries_tail():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = recursive_chunks([text], max_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1
    # the second chunk starts with the tail of the first
    first_tail = chunks[0].split()[-1]
    assert first_tail in chunks[1].split()[:30]


def test_paragraph_merges_up_to_budget():
    paragraphs = ["Para %d sentence." % i for i in range(10)]
    chunks = paragraph_chunks(["\n\n".join(paragraphs)], max_tokens=30)
    assert 1 < len(chunks) < 10  # merged, but not into one
    joined = "\n\n".join(chunks)
    for p in paragraphs:
        assert p in joined


def test_paragraph_splits_oversized_paragraph():
    big = "word " * 500
    chunks = paragraph_chunks([big], max_tokens=100)
    assert all(estimate_tokens(c) <= 100 for c in chunks)


def test_chunk_ids_deterministic_and_hash_sensitive():
    a = make_chunk_id("doc1", 0, "hash1")
    assert a == make_chunk_id("doc1", 0, "hash1")
    assert a != make_chunk_id("doc1", 1, "hash1")
    assert a != make_chunk_id("doc1", 0, "hash2")


def test_link_neighbors():
    chunks = [
        Chunk.build("d", "s", i, f"c{i}", "h", "plaintext", "v1", "2026-07-27T00:00:00Z")
        for i in range(3)
    ]
    link_neighbors(chunks)
    assert chunks[0].prev_id is None and chunks[0].next_id == chunks[1].chunk_id
    assert chunks[1].prev_id == chunks[0].chunk_id
    assert chunks[2].next_id is None
