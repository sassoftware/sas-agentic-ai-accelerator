# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Credential resolution through the SAS Viya Credentials service.

Convention (design §4a, owner decision 2026-07-27): ONE credential domain —
its name configured once per deployment (Options pane / job parameter) —
holds per-identity credentials whose ``secrets`` map carries every key the
accelerator needs, under the names it already uses:

    OpenAI, Anthropic, Google, ...        LLM provider API keys
    pgvector_user, pgvector_password      vector-store credentials, prefixed
    singlestore_user, ...                 with the backend name

A user credential overrides a group credential (``lookupInGroup=true``
searches groups only when the caller has none). The multi-key secrets map is
authored with the shipped ``create-credential-domain.sas`` admin script
(standard admin UIs only author single user/password pairs); domains and
credentials can be listed and deleted with the sas-viya credentials CLI.
Secrets stay in process memory — never in WORK files or logs.
"""
from __future__ import annotations

import base64
from typing import Optional

import requests


def _headers(token: str) -> dict:
    return {"Authorization": "Bearer " + token, "Accept": "application/json"}


def fetch_secrets(base: str, token: str, domain_id: str,
                  verify=True, timeout: float = 60.0,
                  session=None) -> Optional[dict]:
    """The caller's decoded secrets map in a domain — None when absent.

    Returns {name: decoded str} for every entry of the credential's secrets
    map (an undecodable entry decodes to "" rather than raising).
    """
    http = session or requests
    response = http.get(
        f"{base.rstrip('/')}/credentials/domains/{domain_id}/secrets",
        params={"lookupInGroup": "true"},
        headers=_headers(token), verify=verify, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    secrets = {}
    for name, value in (response.json().get("secrets") or {}).items():
        try:
            secrets[name] = base64.b64decode(value).decode("utf-8")
        except Exception:
            secrets[name] = ""
    return secrets


def secrets_available(base: str, token: str, domain_id: str,
                      verify=True, timeout: float = 30.0,
                      session=None) -> bool:
    """Cheap existence check (HEAD) — no secret material transferred."""
    http = session or requests
    response = http.head(
        f"{base.rstrip('/')}/credentials/domains/{domain_id}/secrets",
        params={"lookupInGroup": "true"},
        headers=_headers(token), verify=verify, timeout=timeout)
    return response.status_code == 200


def store_config_from_secrets(secrets: dict, backend: str, host: str, port,
                              dbname: str, sslmode: str = "prefer") -> dict:
    """Adapter connection config from the shared secrets map + setup config.

    Host/port/database/sslmode are configuration, not secrets — they come
    from the RAG setup (pipeline.yaml / step parameters). The secrets map
    contributes ``{backend}_user`` and ``{backend}_password``.
    """
    user = (secrets or {}).get(f"{backend}_user", "")
    password = (secrets or {}).get(f"{backend}_password", "")
    if not user or not password:
        raise KeyError(f"the credential domain has no {backend}_user / "
                       f"{backend}_password entries - add them with the "
                       "create-credential-domain.sas admin script")
    return {"host": host, "port": int(port or 5432), "dbname": dbname,
            "user": user, "password": password, "sslmode": sslmode or "prefer"}
