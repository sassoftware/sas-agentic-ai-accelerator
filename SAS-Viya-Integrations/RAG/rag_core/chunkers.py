# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure-python chunkers (design §2 — no LangChain, validated by 2026 benchmarks).

P1 ships `recursive` and `paragraph`; heading-aware and table-isolating land in
P3. Every chunker enforces BOTH caps from §2a/§2b:
  - max_tokens: never above the embedding model's window (estimate + margin)
  - max_bytes:  the physical transport cap for the chunk `content` column

Input is the extracted element texts of ONE document, output is a list of
chunk content strings in reading order. Chunk objects are assembled by the
step (it owns doc_id/hash/version context).
"""
from __future__ import annotations

from .tokens import estimate_tokens

_SEPARATORS = ["\n\n", "\n", ". ", " "]  # recursive split hierarchy


def _fits(text: str, max_tokens: int, max_bytes: int) -> bool:
    return estimate_tokens(text) <= max_tokens and len(text.encode("utf-8")) <= max_bytes


def _hard_split(text: str, max_tokens: int, max_bytes: int) -> list:
    """Last resort for a separator-free run: split on a character budget."""
    budget = max(1, min(max_tokens * 4, max_bytes // 2))
    return [text[i:i + budget] for i in range(0, len(text), budget)]


def _split_recursive(text: str, max_tokens: int, max_bytes: int, level: int = 0) -> list:
    if _fits(text, max_tokens, max_bytes):
        return [text] if text.strip() else []
    if level >= len(_SEPARATORS):
        return _hard_split(text, max_tokens, max_bytes)
    sep = _SEPARATORS[level]
    parts = [p for p in text.split(sep) if p.strip()]
    if len(parts) <= 1:
        return _split_recursive(text, max_tokens, max_bytes, level + 1)

    pieces: list = []
    for part in parts:
        pieces.extend(_split_recursive(part, max_tokens, max_bytes, level + 1))

    # Greedy re-merge so we emit chunks near the budget, not confetti
    merged: list = []
    current = ""
    for piece in pieces:
        candidate = (current + sep + piece) if current else piece
        if _fits(candidate, max_tokens, max_bytes):
            current = candidate
        else:
            if current:
                merged.append(current)
            current = piece
    if current:
        merged.append(current)
    return merged


def _tail(text: str, overlap_tokens: int) -> str:
    words = text.split()
    take = max(1, int(overlap_tokens / 1.33))
    tail = " ".join(words[-take:])
    while estimate_tokens(tail) > overlap_tokens and take > 1:
        take = take // 2
        tail = " ".join(words[-take:])
    return tail


def recursive_chunks(texts: list, max_tokens: int, max_bytes: int = 24000,
                     overlap_tokens: int = 30) -> list:
    """Recursive character chunking with token-aware caps and tail overlap.

    The overlap is budgeted INSIDE max_tokens (split at max_tokens - overlap,
    then prepend the previous tail), so the final chunks never exceed the
    model-window budget the caller passed.
    """
    joined = document_text(texts)
    if not joined.strip():
        return []
    overlap_tokens = max(0, min(overlap_tokens, max_tokens // 3))
    chunks = _split_recursive(joined, max_tokens - overlap_tokens, max_bytes)
    if overlap_tokens <= 0 or len(chunks) <= 1:
        return chunks

    overlapped = [chunks[0]]
    for prev, cur in zip(chunks, chunks[1:]):
        candidate = f"{_tail(prev, overlap_tokens)}\n{cur}"
        overlapped.append(candidate if len(candidate.encode("utf-8")) <= max_bytes else cur)
    return overlapped


def paragraph_chunks(texts: list, max_tokens: int, max_bytes: int = 24000) -> list:
    """Blank-line paragraph chunking; adjacent paragraphs merge up to the budget."""
    paragraphs: list = []
    for text in texts:
        paragraphs.extend(p.strip() for p in text.split("\n\n") if p.strip())

    chunks: list = []
    current = ""
    for para in paragraphs:
        if not _fits(para, max_tokens, max_bytes):
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_recursive(para, max_tokens, max_bytes))
            continue
        candidate = (current + "\n\n" + para) if current else para
        if _fits(candidate, max_tokens, max_bytes):
            current = candidate
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


CHUNKERS = {
    "recursive": recursive_chunks,
    "paragraph": paragraph_chunks,
}

JOIN = "\n\n"      # how run_chunk joins element texts into one document text


def document_text(texts: list) -> str:
    """The document exactly as the chunkers see it.

    The chunkers drop blank elements before joining. Anything locating chunks
    afterwards MUST filter identically, or the offsets describe a different
    string than the one that was chunked — which produced spans pointing at
    the wrong text, one character off and half a chunk short, whenever an
    extractor emitted a whitespace-only element (HTML and PDF do).
    """
    return JOIN.join(t for t in texts if t and t.strip())


def locate(joined: str, contents: list) -> list:
    """Where each chunk sits in the document text: [(start, end) or None].

    The chunkers return strings, not positions — they split, re-merge and (for
    the recursive chunker) prepend the previous chunk's tail, so an offset
    threaded through all of that would be wrong in a way nobody would notice.
    Finding each chunk afterwards is boring and checkable instead.

    A cursor moves forward so repeated text matches the occurrence in reading
    order. An overlapped chunk does not appear verbatim — it starts with the
    tail of its predecessor — so the first line is dropped and the remainder
    located, which is the part of the chunk that is genuinely new.

    That fallback is GUARDED: it only applies when the dropped line really is
    text from the previous chunk. Unguarded, it fired for any chunk that could
    not be found for any reason, chopped its first line and located the
    remainder — a plausible, wrong, silent citation. Anything not confidently
    located returns None: a wrong citation is worse than an absent one.
    """
    spans: list = []
    cursor = 0
    previous = ""
    for content in contents:
        found = joined.find(content, cursor) if content else -1
        text = content
        if found < 0 and content and previous and "\n" in content:
            head, _, rest = content.partition("\n")
            # the overlap tail is words taken from the END of the previous
            # chunk; if it is not there, this is not an overlap and the chunk
            # simply is not in the document text
            if head and rest and head in previous:
                candidate = joined.find(rest, cursor)
                if candidate >= 0:
                    text, found = rest, candidate
        if found < 0:
            spans.append(None)
            previous = content
            continue
        spans.append((found, found + len(text)))
        cursor = found + len(text)
        previous = content
    return spans


def _page_number(value):
    """A real 1-based page, or None.

    Elements cross a CAS table between the steps, and a numeric column with
    no value arrives as 0.0 rather than None - so an extractor that knew no
    page produced `page: 0` in the citation, which reads like a fact and is
    not one. Pages are 1-based; anything below that is 'unknown'.
    """
    try:
        page = int(float(value))
    except (TypeError, ValueError):
        return None
    return page if page >= 1 else None


def element_pages(texts: list, pages: list) -> list:
    """[(start, end, page)] for each element inside the joined document text."""
    offsets: list = []
    cursor = 0
    for text, page in zip(texts, pages):
        offsets.append((cursor, cursor + len(text), _page_number(page)))
        cursor += len(text) + len(JOIN)
    return offsets


def page_at(offsets: list, position: int):
    """The page of the element covering `position`, if one is known."""
    for start, end, page in offsets:
        if start <= position < end:
            return page
    return None
