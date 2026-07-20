# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""sasctl session factory with the framework's .env conventions."""
from __future__ import annotations

import os


class ViyaConfigError(RuntimeError):
    pass


def create_session():
    """Session from SAS_VIYA_URL / SAS_VIYA_USER / SAS_VIYA_PASSWORD /
    SAS_VIYA_VERIFY_SSL - the exact variables the existing register/publish
    scripts document."""
    try:
        from sasctl import Session
    except ImportError as exc:
        raise ViyaConfigError(
            "sasctl is not installed - install the Viya extra: pip install -e Model-Definition-Builder/cli[viya]"
        ) from exc
    url = os.environ.get("SAS_VIYA_URL")
    user = os.environ.get("SAS_VIYA_USER")
    password = os.environ.get("SAS_VIYA_PASSWORD")
    missing = [n for n, v in {
        "SAS_VIYA_URL": url, "SAS_VIYA_USER": user, "SAS_VIYA_PASSWORD": password,
    }.items() if not v]
    if missing:
        raise ViyaConfigError(
            f"Missing Viya configuration: {', '.join(missing)} (set them in the environment or .env)."
        )
    verify = os.environ.get("SAS_VIYA_VERIFY_SSL", "true").strip().lower() == "true"
    if not verify:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return Session(url, user, password, verify_ssl=verify)
