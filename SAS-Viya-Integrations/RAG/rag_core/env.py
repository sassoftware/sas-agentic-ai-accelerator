# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Vector-store credential resolution from the environment (design §4).

Used where no SAS Viya session token exists (ID/MAS/SCR destinations, local
development via .env). Connection CONFIG stays generic — one store per
deployment: RAGSTORE_HOST / RAGSTORE_PORT / RAGSTORE_DB / RAGSTORE_SSLMODE.
The SECRETS are backend-prefixed with the same names the credential domain
and the .env file use, so one naming convention works everywhere:

    <BACKEND>_RAG_USER / <BACKEND>_RAG_PW   e.g. PGVECTOR_RAG_USER

Secrets never appear in logs or errors raised from here.
"""
from __future__ import annotations

import os

_CONFIG_KEYS = ["RAGSTORE_HOST", "RAGSTORE_PORT", "RAGSTORE_DB", "RAGSTORE_SSLMODE"]


def _secret_keys(backend: str) -> tuple:
    prefix = str(backend or "").upper()
    return (f"{prefix}_RAG_USER", f"{prefix}_RAG_PW")


def _to_adapter_config(values: dict, backend: str) -> dict:
    user_key, pw_key = _secret_keys(backend)
    missing = [k for k in ("RAGSTORE_HOST", "RAGSTORE_DB", user_key, pw_key)
               if not values.get(k)]
    if missing:
        raise KeyError("vector store credentials incomplete - missing: "
                       + ", ".join(missing))
    return {
        "host": values["RAGSTORE_HOST"],
        "port": int(values.get("RAGSTORE_PORT") or 5432),
        "dbname": values["RAGSTORE_DB"],
        "user": values[user_key],
        "password": values[pw_key],
        "sslmode": values.get("RAGSTORE_SSLMODE") or "prefer",
    }


def config_from_env(backend: str = "pgvector") -> dict:
    keys = _CONFIG_KEYS + list(_secret_keys(backend))
    return _to_adapter_config({k: os.environ.get(k, "") for k in keys}, backend)


def config_from_rows(rows, backend: str = "pgvector") -> dict:
    """rows: iterable of (name, value) pairs."""
    values = {str(name).strip().upper(): str(value).strip() for name, value in rows}
    return _to_adapter_config(values, backend)


def config_from_dotenv(path: str, backend: str = "pgvector") -> dict:
    """Local development only: parse a .env file (never logged, never committed)."""
    keys = set(_CONFIG_KEYS) | set(_secret_keys(backend))
    values: dict = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key in keys:
                values[key] = val.strip().strip('"').strip("'")
    return _to_adapter_config(values, backend)
