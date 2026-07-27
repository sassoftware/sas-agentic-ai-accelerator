# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Adapter registry. P1: pgvector. P2 adds weaviate/qdrant/opensearch/singlestore/chroma."""
from __future__ import annotations

from .base import SearchHit, VectorStoreAdapter  # noqa: F401


def get_adapter(backend: str) -> VectorStoreAdapter:
    backend = (backend or "").strip().lower()
    if backend == "pgvector":
        from .pgvector import PgVectorAdapter
        return PgVectorAdapter()
    raise LookupError(f"Unknown vector store backend {backend!r}. "
                      "P1 supports: pgvector.")
