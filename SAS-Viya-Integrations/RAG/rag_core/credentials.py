# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Credential resolution through the SAS Viya Credentials service.

Convention (design §4a): every external secret lives in its own credential
domain named ``{prefix}{name}`` — the prefix is configured once (Options pane
/ step parameter), the name is the provider key name the accelerator already
uses (``OpenAI``, ``Anthropic``, ...) or the vector-store backend name
(``pgvector``, ``singlestore``, ...). Domains are of the standard password
type, so admins manage them with SAS Environment Manager (Security > Domains)
or the sas-viya CLI credentials plugin; a user credential overrides a group
credential (``lookupInGroup=true`` searches groups only when the caller has
no credential of their own).

The secret part is the ``password`` entry; the non-secret ``userId`` property
carries the companion user name where one exists (for a vector store: the
database user; for an API key: unused). Secrets stay in process memory —
they never touch WORK files or logs.
"""
from __future__ import annotations

import base64
from typing import Optional

import requests


def _headers(token: str) -> dict:
    return {"Authorization": "Bearer " + token, "Accept": "application/json"}


def fetch_credential(base: str, token: str, domain_id: str,
                     verify=True, timeout: float = 60.0,
                     session=None) -> Optional[dict]:
    """The caller's credential in a domain, secrets decoded — None if absent.

    Returns {"properties": {...}, "secrets": {name: decoded str}}.
    """
    http = session or requests
    response = http.get(
        f"{base.rstrip('/')}/credentials/domains/{domain_id}/secrets",
        params={"lookupInGroup": "true"},
        headers=_headers(token), verify=verify, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    secrets = {}
    for name, value in (payload.get("secrets") or {}).items():
        try:
            secrets[name] = base64.b64decode(value).decode("utf-8")
        except Exception:
            secrets[name] = ""
    return {"properties": payload.get("properties") or {}, "secrets": secrets}


def credential_available(base: str, token: str, domain_id: str,
                         verify=True, timeout: float = 30.0,
                         session=None) -> bool:
    """Cheap existence check (HEAD) — no secret material transferred."""
    http = session or requests
    response = http.head(
        f"{base.rstrip('/')}/credentials/domains/{domain_id}/secrets",
        params={"lookupInGroup": "true"},
        headers=_headers(token), verify=verify, timeout=timeout)
    return response.status_code == 200


def resolve_secret(base: str, token: str, prefix: str, name: str,
                   verify=True, session=None) -> Optional[dict]:
    """Convenience: fetch the ``{prefix}{name}`` domain credential."""
    return fetch_credential(base, token, f"{prefix}{name}",
                            verify=verify, session=session)


def store_config_from_credential(credential: dict, host: str, port,
                                 dbname: str, sslmode: str = "prefer") -> dict:
    """Adapter connection config from a vector-store credential + setup config.

    Host/port/database/sslmode are configuration, not secrets — they come
    from the RAG setup (pipeline.yaml / step parameters). The credential
    contributes the user (``userId`` property) and password (secret).
    """
    user = (credential.get("properties") or {}).get("userId", "")
    password = (credential.get("secrets") or {}).get("password", "")
    if not user or not password:
        raise KeyError("vector-store credential is incomplete: it needs the "
                       "userId property and the password secret")
    return {"host": host, "port": int(port or 5432), "dbname": dbname,
            "user": user, "password": password, "sslmode": sslmode or "prefer"}
