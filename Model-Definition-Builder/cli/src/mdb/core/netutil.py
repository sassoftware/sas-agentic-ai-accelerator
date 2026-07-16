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


def make_session(verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.trust_env = True  # proxies + CA bundles from the environment
    if not verify_ssl or env_flag("MDB_VERIFY_SSL", True) is False:
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
