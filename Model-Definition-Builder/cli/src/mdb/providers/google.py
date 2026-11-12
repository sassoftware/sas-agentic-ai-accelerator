# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Google Gemini adapter - native generateContent / embedContent APIs, and
the Decisions API for decision models (Gemini Decision 1).

The models.list endpoint is one of the few that returns token limits, and
supportedGenerationMethods distinguishes chat models from embedding models
(and createDecision the decision models).
"""
from __future__ import annotations

from typing import Optional

import requests

from ..core.manifest import ModelManifest
from ..core.netutil import get_json
from .base import CatalogModel, ProviderAdapter, SmokeResult, http_smoke_failure
from .systemone import SMOKE_QUESTIONS, SMOKE_STATE

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
    # Google's Decisions API: the System One questions and answers, but the
    # state as `input`, the key in x-goog-api-key and its own usage names -
    # the scorer translates, so the container keeps the System One contract.
    decision_template = "dec_gemini"
    requirements_profile = "api-wrapper"
    static_catalog_file = "google.json"

    def endpoint(self, answers: dict) -> Optional[str]:
        return None  # built in the template from the model version

    def decision_endpoint(self, answers: dict) -> Optional[str]:
        return f"{BASE}/decisions"

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
            elif "createDecision" in methods:
                kind = "decision"
            else:
                continue
            ref = entry.get("name", "").removeprefix("models/")
            models.append(CatalogModel(
                ref=ref,
                display_name=entry.get("displayName") or ref,
                kind=kind,
                context_length=entry.get("inputTokenLimit"),
                max_output_tokens=entry.get("outputTokenLimit") if kind != "decision" else None,
                source="live",
            ))
        return models

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        model = manifest.provider.model_version
        try:
            if manifest.kind == "decision":
                response = session.post(
                    manifest.provider.endpoint or f"{BASE}/decisions",
                    headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                    json={"model": model, "input": SMOKE_STATE, "questions": SMOKE_QUESTIONS},
                    timeout=60,
                )
                body = response.json()
                if response.status_code >= 300:
                    return http_smoke_failure(response, body)
                team = body["answers"]["team"]
                return SmokeResult(ok=True, detail=(
                    f"Provider responded ({body.get('usage', {}).get('total_input_tokens', '?')} input tokens): "
                    f"team={team.get('choice')!r} at confidence {float(team.get('confidence', 0)):.2f}"
                ))
            if manifest.kind == "embedding":
                response = session.post(
                    f"{BASE}/models/{model}:embedContent",
                    headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                    json={"content": {"parts": [{"text": "OK"}]}},
                    timeout=60,
                )
                body = response.json()
                if response.status_code >= 300:
                    return http_smoke_failure(response, body)
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
                return http_smoke_failure(response, body)
            text = "".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"])
            usage = body.get("usageMetadata", {})
            return SmokeResult(ok=True, detail=(
                f"Provider responded ({usage.get('promptTokenCount', '?')} in / "
                f"{usage.get('candidatesTokenCount', '?')} out tokens): {text[:60]!r}"
            ))
        except Exception as exc:
            return SmokeResult(ok=False, detail=str(exc))
