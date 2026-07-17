# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deprecation radar: is every fleet model still served by its provider?

Two levels of evidence:
- listing: the model ref appears in the provider's live catalog (cheap; but
  a listed model can still be retired - Gemini 2.5 taught us that)
- probe (--probe): one real 1-token inference call per model, the ground truth

Providers without a live surface (self-hosted, static-catalog providers,
keyless sessions) are reported as 'skipped', never failed - silence is not
treated as success, absence of evidence is labeled as such.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class RadarResult:
    model_id: str
    provider: str
    model_version: str
    status: str  # ok | missing | not-serving | skipped
    detail: str


def check_model(manifest, adapter, session, api_key: Optional[str], probe: bool) -> RadarResult:
    base = dict(model_id=manifest.model_id, provider=manifest.provider.adapter,
                model_version=manifest.provider.model_version)
    if adapter is None:
        return RadarResult(**base, status="skipped", detail="no adapter for this definition")
    if manifest.provider.adapter == "hf-selfhosted":
        return RadarResult(**base, status="skipped", detail="self-hosted - weights live in the image")

    listed: Optional[bool] = None
    try:
        catalog = adapter.live_catalog(session, api_key)
        listed = manifest.provider.model_version in {m.ref for m in catalog}
    except Exception as exc:
        if not probe:
            return RadarResult(**base, status="skipped", detail=f"no live catalog ({str(exc)[:60]})")

    if probe:
        result = adapter.smoke_test(manifest, api_key, session)
        if result.skipped:
            if listed is None:
                return RadarResult(**base, status="skipped", detail=result.detail[:80])
            return RadarResult(**base, status="ok" if listed else "missing",
                               detail="listed (probe unavailable)" if listed else "absent from the live catalog")
        if result.ok:
            return RadarResult(**base, status="ok", detail="probe answered")
        return RadarResult(**base, status="not-serving", detail=result.detail[:100])

    if listed:
        return RadarResult(**base, status="ok", detail="listed in the live catalog")
    return RadarResult(**base, status="missing", detail="absent from the live catalog")


def env_key_for(adapter) -> Optional[str]:
    if not getattr(adapter, "env_key_var", None):
        return None
    return os.environ.get(adapter.env_key_var)
