# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Where a chunk came from in its document.

Without this the datagrid's source location is decorative: every citation
reported page 0, span 0-0, and a reader had no way back to the passage. The
rule these tests defend is that a span, when present, must be TRUE - the
document text at [start:end] has to be the chunk's own text - and absent
rather than guessed when it cannot be established.
"""
from rag_core.chunkers import (JOIN, element_pages, locate, page_at,
                               paragraph_chunks, recursive_chunks)
from rag_core.steps import run_chunk


def _elements(doc_id, texts, pages=None):
    pages = pages or [None] * len(texts)
    return [{"doc_id": doc_id, "type": "text", "text": t, "page": p,
             "heading_path": None}
            for t, p in zip(texts, pages)]


def _inventory(doc_id="d1"):
    return [{"doc_id": doc_id, "source_uri": f"/docs/{doc_id}.txt",
             "content_hash": "h1", "extractor": "plaintext", "status": "new"}]


# ---------------------------------------------------------------------------
# locate()
# ---------------------------------------------------------------------------
def test_a_span_points_at_the_chunks_own_text():
    joined = "alpha beta gamma delta"
    spans = locate(joined, ["alpha beta", "gamma delta"])
    assert spans == [(0, 10), (11, 22)]
    for content, (start, end) in zip(["alpha beta", "gamma delta"], spans):
        assert joined[start:end] == content


def test_repeated_text_resolves_in_reading_order():
    """The same sentence twice must not both point at the first occurrence."""
    joined = "same text\n\nmiddle\n\nsame text"
    spans = locate(joined, ["same text", "middle", "same text"])
    assert [s[0] for s in spans] == [0, 11, 19]


def test_an_overlapped_chunk_is_located_by_its_new_part():
    """The recursive chunker prepends the previous chunk's tail, so the chunk
    does not appear verbatim - the span must cover the part that is new."""
    joined = "first part here\n\nsecond part here"
    spans = locate(joined, ["first part here", "here\nsecond part here"])
    assert joined[spans[0][0]:spans[0][1]] == "first part here"
    assert joined[spans[1][0]:spans[1][1]] == "second part here"


def test_text_that_cannot_be_found_gets_no_span():
    """A wrong citation is worse than an absent one."""
    assert locate("the document", ["nothing like this"]) == [None]


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
def test_the_page_comes_from_the_element_the_chunk_starts_in():
    texts = ["page one text", "page two text"]
    offsets = element_pages(texts, [1, 2])
    joined = JOIN.join(texts)
    assert page_at(offsets, 0) == 1
    assert page_at(offsets, joined.index("page two")) == 2


def test_no_page_when_the_extractor_did_not_know_one():
    offsets = element_pages(["a", "b"], [None, None])
    assert page_at(offsets, 0) is None


def test_a_zero_page_means_unknown_not_page_zero():
    """Elements cross a CAS table, where a numeric column with no value
    arrives as 0.0 - which produced `page: 0` in live citations. Pages are
    1-based, so anything below that is unknown."""
    for absent in (0, 0.0, "", None, "not a number"):
        assert page_at(element_pages(["a"], [absent]), 0) is None
    assert page_at(element_pages(["a"], [3.0]), 0) == 3


# ---------------------------------------------------------------------------
# through the step
# ---------------------------------------------------------------------------
def test_run_chunk_stamps_a_true_span_on_every_chunk():
    texts = ["Economy class is standard for flights under eight hours.",
             "Receipts must be submitted within thirty days of the expense."]
    chunks = run_chunk(_elements("d1", texts), _inventory(), "paragraph",
                       256, "v1", log=lambda *_: None)
    joined = JOIN.join(texts)
    assert chunks, "no chunks produced"
    for chunk in chunks:
        span = chunk["span"]
        assert span is not None, "a chunk without a span cannot be cited"
        assert joined[span["start"]:span["end"]] in chunk["content"]


def test_run_chunk_carries_the_page_through_to_the_chunk():
    # long enough that the two pages cannot merge into one chunk
    texts = [" ".join(["first page sentence about travel."] * 12),
             " ".join(["second page sentence about expenses."] * 12)]
    chunks = run_chunk(_elements("d1", texts, pages=[3, 4]), _inventory(),
                       "paragraph", 64, "v1", log=lambda *_: None)
    pages = [c["span"].get("page") for c in chunks if c["span"]]
    assert 3 in pages and 4 in pages


def test_a_chunk_spanning_two_pages_reports_the_one_it_starts_on():
    """Merged paragraphs can cross a page boundary. Reporting the starting
    page sends a reader to where the passage begins, which is the useful
    answer; claiming both would need a range the datagrid has no column for."""
    texts = ["Short first.", "Short second."]
    chunks = run_chunk(_elements("d1", texts, pages=[7, 8]), _inventory(),
                       "paragraph", 256, "v1", log=lambda *_: None)
    assert len(chunks) == 1, "expected the two short pages to merge"
    assert chunks[0]["span"]["page"] == 7


def test_spans_survive_the_recursive_chunker_with_overlap():
    body = " ".join(f"sentence number {i} about travel policy." for i in range(40))
    chunks = run_chunk(_elements("d1", [body]), _inventory(), "recursive",
                       64, "v1", overlap_tokens=10, log=lambda *_: None)
    assert len(chunks) > 1, "expected the text to split"
    located = [c for c in chunks if c["span"]]
    assert len(located) == len(chunks), "every chunk should be locatable"
    starts = [c["span"]["start"] for c in located]
    assert starts == sorted(starts), "spans must advance through the document"


def test_a_span_is_a_dict_the_retrieval_layer_understands():
    """retrieve_context and run_retrieve both read span['page'/'start'/'end']."""
    chunks = run_chunk(_elements("d1", ["some plain content"]), _inventory(),
                       "paragraph", 256, "v1", log=lambda *_: None)
    span = chunks[0]["span"]
    assert set(span) <= {"page", "start", "end"}
    assert isinstance(span["start"], int) and isinstance(span["end"], int)


# ---------------------------------------------------------------------------
# the wrong-citation defect (found by adversarial review, 2026-07-31)
# ---------------------------------------------------------------------------
def test_a_blank_element_does_not_shift_every_span():
    """The chunkers drop blank elements before joining. When run_chunk did
    not, the chunk was no longer findable, the overlap fallback fired on a
    chunk that was never overlapped, and the span pointed one character early
    at half the chunk - a plausible, wrong, silent citation."""
    texts = ["First real paragraph here.", "   ", "Second real paragraph here."]
    chunks = run_chunk(_elements("d1", texts), _inventory(), "recursive",
                       32, "v1", log=lambda *_: None)
    joined = JOIN.join(t for t in texts if t.strip())
    for chunk in chunks:
        span = chunk["span"]
        assert span is not None
        located = joined[span["start"]:span["end"]]
        assert located in chunk["content"], (
            f"span points at {located!r}, which is not part of the chunk")


def test_the_overlap_fallback_does_not_fire_on_unrelated_text():
    """Unguarded, it located any unfindable chunk by chopping its first line."""
    joined = "alpha line\n\nbeta line"
    # a chunk that simply is not in the document, whose first line is not a
    # tail of anything before it
    assert locate(joined, ["nowhere line\nbeta line"]) == [None]


def test_a_genuine_overlap_is_still_located():
    joined = "first part here\n\nsecond part here"
    spans = locate(joined, ["first part here", "here\nsecond part here"])
    assert spans[0] is not None and spans[1] is not None
    assert joined[spans[1][0]:spans[1][1]] == "second part here"
