# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Networking with restricted environments as a first-class citizen.

- HTTPS_PROXY / HTTP_PROXY / NO_PROXY are honored (requests trust_env).
- REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE provide corporate CA bundles.
- MDB_VERIFY_SSL=false mirrors the repo's existing -k convention.
- MDB_OFFLINE=true (or --offline) suppresses every outbound call; catalogs
  then come exclusively from the static snapshots in definition-core.
"""
from __future__ import annotations

import os

import requests

DEFAULT_TIMEOUT = 20


class OfflineError(RuntimeError):
    pass


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


#: The values that switch TLS verification off - the words the programs and
#: custom steps that run in SAS take for SAS_VIYA_VERIFY_SSL as well.
TLS_OFF_WORDS = ("false", "no", "0")


def tls_verify_off(name: str) -> bool:
    """True only when the variable holds one of TLS_OFF_WORDS. Unset, true, a
    typo - anything else keeps verification on: a mistyped setting must not
    stop the check."""
    return os.environ.get(name, "").strip().lower() in TLS_OFF_WORDS


def make_session(verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.trust_env = True  # proxies + CA bundles from the environment
    if not verify_ssl or tls_verify_off("MDB_VERIFY_SSL"):
        session.verify = False
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session


def get_json(session: requests.Session, url: str, headers: dict | None = None, offline: bool = False):
    if offline or env_flag("MDB_OFFLINE"):
        raise OfflineError(f"Offline mode - skipped GET {url}")
    response = session.get(url, headers=headers or {}, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.json()
