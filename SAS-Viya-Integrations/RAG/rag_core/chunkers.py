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
    joined = "\n\n".join(t for t in texts if t and t.strip())
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
