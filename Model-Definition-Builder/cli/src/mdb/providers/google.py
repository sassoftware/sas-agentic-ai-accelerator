# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Google Gemini adapter - native generateContent / embedContent APIs.

The models.list endpoint is one of the few that returns token limits, and
supportedGenerationMethods distinguishes chat models from embedding models.
"""
from __future__ import annotations

from typing import Optional

import requests

from ..core.manifest import ModelManifest
from ..core.netutil import get_json
from .base import CatalogModel, ProviderAdapter, SmokeResult

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GoogleAdapter(ProviderAdapter):
    id = "google"
    display_name = "Google Gemini"
    provider_tag = "Google"
    key_name = "Google"
    env_key_var = "GEMINI_API_KEY"
    docs_url = "https://aistudio.google.com/apikey"
    template = "gemini_generate"
    embedding_template = "emb_gemini"
    requirements_profile = "api-wrapper"
    static_catalog_file = "google.json"

    def endpoint(self, answers: dict) -> Optional[str]:
        return None  # built in the template from the model version

    def live_catalog(self, session: requests.Session, api_key: Optional[str]) -> list[CatalogModel]:
        if not api_key:
            raise PermissionError(f"Google needs an API key ({self.env_key_var}) to list models.")
        payload = get_json(session, f"{BASE}/models?pageSize=200",
                           headers={"x-goog-api-key": api_key})
        models = []
        for entry in payload.get("models", []):
            methods = entry.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                kind = "llm"
            elif "embedContent" in methods:
                kind = "embedding"
            else:
                continue
            ref = entry.get("name", "").removeprefix("models/")
            models.append(CatalogModel(
                ref=ref,
                display_name=entry.get("displayName") or ref,
                kind=kind,
                context_length=entry.get("inputTokenLimit"),
                max_output_tokens=entry.get("outputTokenLimit"),
                source="live",
            ))
        return models

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        model = manifest.provider.model_version
        try:
            if manifest.kind == "embedding":
                response = session.post(
                    f"{BASE}/models/{model}:embedContent",
                    headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                    json={"content": {"parts": [{"text": "OK"}]}},
                    timeout=60,
                )
                body = response.json()
                if response.status_code >= 300:
                    return SmokeResult(ok=False, detail=f"HTTP {response.status_code}: {body}")
                return SmokeResult(ok=True, detail=f"Provider responded with a "
                                                   f"{len(body['embedding']['values'])}-dimension embedding.")
            response = session.post(
                f"{BASE}/models/{model}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json={"contents": [{"parts": [{"text": "Reply with the single word OK."}]}]},
                timeout=60,
            )
            body = response.json()
            if response.status_code >= 300:
                return SmokeResult(ok=False, detail=f"HTTP {response.status_code}: {body}")
            text = "".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"])
            usage = body.get("usageMetadata", {})
            return SmokeResult(ok=True, detail=(
                f"Provider responded ({usage.get('promptTokenCount', '?')} in / "
                f"{usage.get('candidatesTokenCount', '?')} out tokens): {text[:60]!r}"
            ))
        except Exception as exc:
            return SmokeResult(ok=False, detail=str(exc))
