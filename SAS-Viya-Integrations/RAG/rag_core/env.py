# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Vector-store credential resolution from the environment (design §4).

Used where no SAS Viya session token exists (ID/MAS/SCR destinations, local
development via .env). ONE naming convention everywhere - the .env file, the
SAS Viya credential domain, and environment variables on a destination:

    <BACKEND>_RAG_USER / <BACKEND>_RAG_PW    secrets, e.g. PGVECTOR_RAG_USER
    <BACKEND>_HOST / _PORT / _DB / _SSLMODE  connection settings

Connection settings are backend-prefixed too, so one deployment can address
several vector databases at once - a second backend is a second store, not a
replacement. The unprefixed RAGSTORE_* names remain the fallback for any
backend without its own, which is what every existing deployment uses.

Secrets never appear in logs or errors raised from here.
"""
from __future__ import annotations

import os

_SETTINGS = ("HOST", "PORT", "DB", "SSLMODE")
_FALLBACK_PREFIX = "RAGSTORE"
_DEFAULT_PORTS = {"PGVECTOR": 5432, "SINGLESTORE": 3306}


def _prefix(backend: str) -> str:
    return str(backend or "").upper()


def _secret_keys(backend: str) -> tuple:
    prefix = _prefix(backend)
    return (f"{prefix}_RAG_USER", f"{prefix}_RAG_PW")


def _config_keys(backend: str) -> list:
    """Every name that could supply a connection setting, prefixed first."""
    prefix = _prefix(backend)
    keys = [f"{prefix}_{setting}" for setting in _SETTINGS]
    keys += [f"{_FALLBACK_PREFIX}_{setting}" for setting in _SETTINGS]
    return keys


def _setting(values: dict, backend: str, setting: str) -> str:
    """A backend's own value if it has one, otherwise the shared fallback."""
    prefixed = values.get(f"{_prefix(backend)}_{setting}")
    if str(prefixed or "").strip():
        return str(prefixed).strip()
    return str(values.get(f"{_FALLBACK_PREFIX}_{setting}") or "").strip()


def _to_adapter_config(values: dict, backend: str) -> dict:
    user_key, pw_key = _secret_keys(backend)
    host = _setting(values, backend, "HOST")
    database = _setting(values, backend, "DB")
    missing = []
    if not host:
        missing.append(f"{_prefix(backend)}_HOST (or {_FALLBACK_PREFIX}_HOST)")
    if not database:
        missing.append(f"{_prefix(backend)}_DB (or {_FALLBACK_PREFIX}_DB)")
    missing += [key for key in (user_key, pw_key) if not values.get(key)]
    if missing:
        raise KeyError("vector store credentials incomplete - missing: "
                       + ", ".join(missing))
    port = _setting(values, backend, "PORT")
    sslmode = _setting(values, backend, "SSLMODE")
    return {
        "host": host,
        "port": int(port or _DEFAULT_PORTS.get(_prefix(backend), 5432)),
        "dbname": database,
        "user": values[user_key],
        "password": values[pw_key],
        "sslmode": sslmode or "prefer",
    }


def config_from_env(backend: str = "pgvector") -> dict:
    keys = _config_keys(backend) + list(_secret_keys(backend))
    return _to_adapter_config({k: os.environ.get(k, "") for k in keys}, backend)


def config_from_rows(rows, backend: str = "pgvector") -> dict:
    """rows: iterable of (name, value) pairs."""
    values = {str(name).strip().upper(): str(value).strip() for name, value in rows}
    return _to_adapter_config(values, backend)


def config_from_dotenv(path: str, backend: str = "pgvector") -> dict:
    """Local development only: parse a .env file (never logged, never committed)."""
    keys = set(_config_keys(backend)) | set(_secret_keys(backend))
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
