# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Vector-store credential resolution (design §4).

Two sources, exactly like the Optimize-Prompt-DSPy split:
  - RAGSTORE_* environment variables (ID/MAS/SCR destinations, local dev via .env)
  - a governed key table (Studio/Job Execution): rows named RAGSTORE_HOST etc.,
    read server-side by the step; the browser/launcher passes only library and
    table NAMES, never values.

Secrets never appear in logs or errors raised from here.
"""
from __future__ import annotations

import os

_KEYS = ["RAGSTORE_HOST", "RAGSTORE_PORT", "RAGSTORE_DB", "RAGSTORE_USER",
         "RAGSTORE_PW", "RAGSTORE_SSLMODE"]


def _to_adapter_config(values: dict) -> dict:
    missing = [k for k in ("RAGSTORE_HOST", "RAGSTORE_DB", "RAGSTORE_USER", "RAGSTORE_PW")
               if not values.get(k)]
    if missing:
        raise KeyError("vector store credentials incomplete - missing: "
                       + ", ".join(missing))
    return {
        "host": values["RAGSTORE_HOST"],
        "port": int(values.get("RAGSTORE_PORT") or 5432),
        "dbname": values["RAGSTORE_DB"],
        "user": values["RAGSTORE_USER"],
        "password": values["RAGSTORE_PW"],
        "sslmode": values.get("RAGSTORE_SSLMODE") or "prefer",
    }


def config_from_env() -> dict:
    return _to_adapter_config({k: os.environ.get(k, "") for k in _KEYS})


def config_from_rows(rows) -> dict:
    """rows: iterable of (name, value) pairs — the governed key-table shape."""
    values = {str(name).strip().upper(): str(value).strip() for name, value in rows}
    return _to_adapter_config(values)


def config_from_dotenv(path: str) -> dict:
    """Local development only: parse a .env file (never logged, never committed)."""
    values: dict = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key in _KEYS:
                values[key] = val.strip().strip('"').strip("'")
    return _to_adapter_config(values)
