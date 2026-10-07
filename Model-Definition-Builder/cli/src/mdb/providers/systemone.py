# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Decision models: TypeSafe's Jev and the open reimplementations of it.

A decision model (a "System One" model) takes a state and typed questions -
a choice among options, a yes/no noul, a score on described levels - and
returns calibrated answers instead of text. Every server in this family
speaks the same wire format, `POST .../v1/systemone` with
``{"model", "state", "questions"}`` in and ``{"model", "answers", "usage"}``
out, so one score template covers TypeSafe's own API, OpenRouter (which
hosts Jev, see the openrouter adapter) and self-hosted servers such as Kev
and Von. Von can also run inside the container: its checkpoint is baked
into the image, like the framework's other open-weight models.
"""
from __future__ import annotations

from typing import Optional

import requests

from ..core.manifest import (
    AuthBlock, GenerationBlock, MetadataBlock, ModelManifest, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)
from .base import CatalogModel, ProviderAdapter, Question, SmokeResult, http_smoke_failure

SMOKE_STATE = "The customer was charged twice for one invoice and asks for a refund."
SMOKE_QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Which team should handle this?",
        "criteria": {"billing": "Payments and refunds", "technical": "Bugs and outages"},
    },
    "escalate": {"type": "noul", "instructions": "The customer threatens to leave"},
}


def systemone_smoke_test(manifest: ModelManifest, api_key: Optional[str],
                         session: requests.Session) -> SmokeResult:
    """One real System One call against the definition's endpoint."""
    try:
        response = session.post(
            manifest.provider.endpoint or "",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={"model": manifest.provider.model_version, "state": SMOKE_STATE, "questions": SMOKE_QUESTIONS},
            timeout=60,
        )
        body = response.json()
        if response.status_code >= 300:
            return http_smoke_failure(response, body)
        team = body["answers"]["team"]
        usage = body.get("usage", {})
        return SmokeResult(ok=True, detail=(
            f"Provider responded ({usage.get('input_tokens', '?')} input tokens): "
            f"team={team.get('choice')!r} at confidence {float(team.get('confidence', 0)):.2f}"
        ))
    except Exception as exc:
        return SmokeResult(ok=False, detail=str(exc))


class TypeSafeAdapter(ProviderAdapter):
    """TypeSafe's own API for Jev. Decision definitions only."""

    id = "typesafe"
    display_name = "TypeSafe"
    provider_tag = "TypeSafe"
    key_name = "TypeSafe"
    env_key_var = "TYPESAFE_API_KEY"
    docs_url = "https://docs.typesafe.ai/api"
    template = "dec_systemone_api"
    decision_template = "dec_systemone_api"
    requirements_profile = "api-wrapper"
    static_catalog_file = "typesafe.json"
    base_url = "https://api.typesafe.ai/v1"

    def endpoint(self, answers: dict) -> Optional[str]:
        return f"{self.base_url}/systemone"

    def decision_endpoint(self, answers: dict) -> Optional[str]:
        return self.endpoint(answers)

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        return systemone_smoke_test(manifest, api_key, session)


class VonAdapter(ProviderAdapter):
    """Von (wfzyx/von): an open, Apache-2.0 decision model on a ModernBERT
    encoder, run inside the SCR container with its checkpoint baked into the
    image. No catalog and no key: there is exactly one model."""

    id = "von"
    display_name = "Von (self-hosted decision model)"
    provider_tag = "Von"
    key_name = None
    env_key_var = None
    docs_url = "https://huggingface.co/wfzyx/von"
    template = "dec_von"
    decision_template = "dec_von"
    requirements_profile = "von"
    HF_REPO = "wfzyx/von"
    MODEL_VERSION = "von-1.2.0"

    def endpoint(self, answers: dict) -> Optional[str]:
        return None

    def questions(self) -> list[Question]:
        return [
            Question("repo", "Hugging Face repo of the Von checkpoint", default=self.HF_REPO, required=False),
        ]

    def build_manifest(self, cm: CatalogModel, model_id: str, answers: dict, modeler: str) -> ModelManifest:
        repo = (answers.get("repo") or "").strip() or self.HF_REPO
        return ModelManifest(
            kind="decision",
            model_id=model_id,
            display_name=cm.display_name if cm.display_name != cm.ref else "Von 1.2",
            provider=ProviderBlock(
                adapter=self.id,
                model_version=self.MODEL_VERSION,
                params={"hf": {"repo": repo, "gated": False}},
                auth=AuthBlock(mode="none"),
            ),
            runtime=RuntimeBlock(template=self.template, requirements_profile=self.requirements_profile),
            options={},
            tags=TagsBlock(
                size_class="Decision",
                license_class="Open-Source",
                provider_tag=self.provider_tag,
                scr_sizing="medium",
                extra=["Apache-2"],
            ),
            metadata=MetadataBlock(
                description=answers.get("description") or (
                    "Von 1.2 - an open System One decision model (ModernBERT-large, 395M parameters) "
                    "that answers typed questions about a state with calibrated probabilities: "
                    "a choice among options, a yes/no noul, a score on described levels. "
                    "Self-hosted in the container from the Hugging Face checkpoint."
                ),
                size=395_000_000,
                context_length=8192,
                release_date="2026-09",
                deployment_type="SCR",
                pricing=PricingBlock(cost_type="Seconds"),
            ),
            modeler=modeler,
            generation=GenerationBlock(catalog_provenance="manual entry (self-hosted Von)"),
        )
