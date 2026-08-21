# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The canonical chunk schema every adapter round-trips (design §2).

`chunk_id` is deterministic — sha1(doc_id + chunk_index + content_hash +
pipeline_version) — so re-ingesting an unchanged document is idempotent and
the Embed step can reuse its vectors.

The pipeline version is part of it, and that matters: without it, re-chunking
the same documents with a different chunker produced the SAME ids, the upsert
updated the live rows in place, and the previous generation was silently
overwritten instead of retired — verified live, which is how the omission was
found. Since the drift guard requires a version bump for any configuration
change, including the version is enough to make each configuration its own
generation. It also invalidates the embedding cache on a bump, which is
correct: new chunk boundaries need new vectors.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional


def make_chunk_id(doc_id: str, chunk_index: int, content_hash: str,
                  pipeline_version: str = "") -> str:
    raw = f"{doc_id}\x1f{chunk_index}\x1f{content_hash}\x1f{pipeline_version}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    source_uri: str
    chunk_index: int
    content: str
    content_hash: str
    extractor: str
    pipeline_version: str
    ingested_at: str                      # ISO-8601 UTC
    span: Optional[dict] = None           # {"page": int, "start": int, "end": int}
    heading_path: Optional[str] = None
    tags: dict = field(default_factory=dict)
    prev_id: Optional[str] = None
    next_id: Optional[str] = None
    context_header: Optional[str] = None  # contextual-chunk-header enrich slot
    entities: Optional[list] = None       # KG hooks — round-tripped even when None
    relations: Optional[list] = None
    embedding: Optional[list] = None      # list[float], filled by the Embed step

    @classmethod
    def build(cls, doc_id: str, source_uri: str, chunk_index: int, content: str,
              content_hash: str, extractor: str, pipeline_version: str,
              ingested_at: str, **optional: Any) -> "Chunk":
        return cls(
            chunk_id=make_chunk_id(doc_id, chunk_index, content_hash,
                                   pipeline_version),
            doc_id=doc_id, source_uri=source_uri, chunk_index=chunk_index,
            content=content, content_hash=content_hash, extractor=extractor,
            pipeline_version=pipeline_version, ingested_at=ingested_at, **optional,
        )


def link_neighbors(chunks: list) -> list:
    """Fill prev/next ids on an ordered per-document chunk list, in place."""
    for i, chunk in enumerate(chunks):
        chunk.prev_id = chunks[i - 1].chunk_id if i > 0 else None
        chunk.next_id = chunks[i + 1].chunk_id if i < len(chunks) - 1 else None
    return chunks
