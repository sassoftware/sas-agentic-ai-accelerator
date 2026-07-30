# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Adapter registry.

One table, not a chain of if/elif: the Load step's backend dropdown, the
credential-domain key prefix and the driver an admin has to install all follow
from the same entry, so adding a backend is adding a row. Imports stay lazy —
a deployment that only uses one store must not need the other's driver.
"""
from __future__ import annotations

from .base import SearchHit, VectorStoreAdapter  # noqa: F401

# backend -> (module, class, label for the step UI, python driver to install)
REGISTRY = {
    "pgvector": (".pgvector", "PgVectorAdapter", "pgvector (PostgreSQL)",
                 "psycopg2-binary"),
    "singlestore": (".singlestore", "SingleStoreAdapter", "SingleStore",
                    "singlestoredb"),
}


def backends() -> list:
    """[(backend, label)] for the step dropdowns, in a stable order."""
    return sorted((name, entry[2]) for name, entry in REGISTRY.items())


def driver_for(backend: str) -> str:
    """The python package this backend's adapter imports lazily."""
    entry = REGISTRY.get((backend or "").strip().lower())
    return entry[3] if entry else ""


def get_adapter(backend: str) -> VectorStoreAdapter:
    key = (backend or "").strip().lower()
    entry = REGISTRY.get(key)
    if entry is None:
        raise LookupError(f"Unknown vector store backend {backend!r}. "
                          f"Supported: {', '.join(sorted(REGISTRY))}.")
    module_name, class_name = entry[0], entry[1]
    from importlib import import_module

    module = import_module(module_name, __name__)
    return getattr(module, class_name)()
