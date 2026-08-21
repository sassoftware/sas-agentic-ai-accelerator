# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Extractor registry with defensive loading (design §3, the mdb rule).

P1 discovery tier: built-ins only. P3 adds the governed SAS-Content plugin
folder (each .py defines EXTRACTOR; broken plugins log and skip, never fail
the flow). An extractor whose `requires` packages are missing registers as
UNAVAILABLE with the reason — selecting it fails the DOCUMENT with a clear
error, not the flow (§2 failure contract).
"""
from __future__ import annotations

import importlib.util

from .builtin import BUILTINS


class ExtractorRegistry:
    def __init__(self):
        self._by_name: dict = {}
        self._unavailable: dict = {}     # name -> missing package
        self._by_format: dict = {}       # ".pdf" -> [names in priority order]
        for extractor in BUILTINS:
            self.register(extractor)

    def register(self, extractor) -> None:
        missing = [pkg for pkg in getattr(extractor, "requires", [])
                   if importlib.util.find_spec(pkg) is None]
        if missing:
            self._unavailable[extractor.name] = missing[0]
            return
        self._by_name[extractor.name] = extractor
        for fmt in extractor.formats:
            self._by_format.setdefault(fmt.lower(), []).append(extractor.name)

    def get(self, name: str):
        if name in self._unavailable:
            raise LookupError(
                f"Extractor '{name}' is unavailable: python package "
                f"'{self._unavailable[name]}' is not installed in this environment."
            )
        if name not in self._by_name:
            raise LookupError(f"Unknown extractor '{name}'. Available: {sorted(self._by_name)}")
        return self._by_name[name]

    def chain_for(self, source_uri: str) -> list:
        """Extractor names for a document, in fallback order."""
        suffix = ("." + source_uri.rsplit(".", 1)[-1].lower()) if "." in source_uri else ""
        return list(self._by_format.get(suffix, []))

    def extract(self, data: bytes, source_uri: str, extractor_name=None, **params) -> tuple:
        """Run the named extractor, or the format's fallback chain.

        Returns (elements, extractor_name_used). Raises on failure — the STEP
        catches per document and marks the ledger row failed (§2).
        """
        names = [extractor_name] if extractor_name else self.chain_for(source_uri)
        if not names:
            raise LookupError(f"No extractor registered for '{source_uri}'.")
        last_error: Exception = LookupError("no extractor attempted")
        for name in names:
            try:
                return self.get(name).extract(data, source_uri, **params), name
            except Exception as exc:  # try next in chain, keep the first failure readable
                last_error = exc
        raise RuntimeError(f"All extractors failed for '{source_uri}' "
                           f"(tried {names}): {last_error}") from last_error

    def catalog(self) -> dict:
        return {
            "available": {n: sorted(e.formats) for n, e in self._by_name.items()},
            "unavailable": dict(self._unavailable),
        }
