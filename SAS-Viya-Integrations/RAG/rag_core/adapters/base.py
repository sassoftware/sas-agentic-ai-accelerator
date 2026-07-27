# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Vector store adapter interface (design §4).

Scores are normalized HIGHER-IS-BETTER on every backend (fixes the portal
pgVector direction bug); absolute scores are never comparable across backends.
`ddl()` returns what ensure_collection would execute — the vector-store-ddl.sql
governance artifact and the DBA pre-creation path. `cutover()` is the §2
collection-versioning switch; backends without aliases implement it as a
transactional rename or pointer and say so in capabilities().
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class SearchHit:
    __slots__ = ("chunk_id", "score", "record")

    def __init__(self, chunk_id: str, score: float, record: dict):
        self.chunk_id = chunk_id
        self.score = score          # higher is better, always
        self.record = record        # full canonical-schema round-trip


class VectorStoreAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def connect(self, config: dict) -> None: ...

    @abstractmethod
    def ensure_collection(self, name: str, dims: int, metric: str = "cosine",
                          schema: Optional[dict] = None) -> None: ...

    @abstractmethod
    def ddl(self, name: str, dims: int, metric: str = "cosine",
            schema: Optional[dict] = None) -> str:
        """The DDL/collection-spec ensure_collection would execute (governance artifact)."""

    @abstractmethod
    def upsert(self, collection: str, records: list) -> int: ...

    @abstractmethod
    def search(self, collection: str, vector: list, k: int = 5,
               filter: Optional[dict] = None, mode: str = "vector",
               query_text: Optional[str] = None,
               raw_filter: Optional[str] = None) -> list: ...

    @abstractmethod
    def delete(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None) -> int: ...

    @abstractmethod
    def drop_collection(self, name: str) -> None: ...

    @abstractmethod
    def list_collections(self) -> list: ...

    @abstractmethod
    def count(self, collection: str, filter: Optional[dict] = None) -> int: ...

    @abstractmethod
    def cutover(self, alias: str, new_collection: str) -> None: ...

    def flush(self, collection: str) -> None:
        """Eventually-consistent stores refresh here; default no-op."""

    @property
    def needs_flush(self) -> bool:
        return False

    def capabilities(self) -> dict:
        return {"alias": False, "sparse": False, "hybrid": False,
                "namespace": False, "cutover": "none"}

    def close(self) -> None:
        pass
