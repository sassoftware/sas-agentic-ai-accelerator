# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Extractor contract (design §3).

An extractor turns document BYTES into ordered elements. Elements are plain
dicts (they cross a CAS table between steps — §2a):
    {"type": "heading"|"text"|"table"|"code",
     "text": str, "level": int|None, "page": int|None, "heading_path": str|None}

`requires` lists python packages the extractor needs — they are CHECKED at
registry load, never installed (read-only sas-pyconfig environments).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


def element(text: str, etype: str = "text", level=None, page=None, heading_path=None) -> dict:
    return {"type": etype, "text": text, "level": level, "page": page,
            "heading_path": heading_path}


@runtime_checkable
class Extractor(Protocol):
    name: str
    formats: set          # e.g. {".pdf"}
    requires: list        # package names; checked, not installed

    def extract(self, data: bytes, source_uri: str, **params) -> list: ...
