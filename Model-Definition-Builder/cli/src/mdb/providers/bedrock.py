# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""AWS Bedrock adapter.

Bearer-first: the default score template calls the Converse REST API with a
Bedrock API key carried in the framework's API_KEY option - no AWS SDK in the
container. Shops that standardize on IAM identities pick the SigV4 variant
(--auth-variant sigv4), whose scorer uses boto3 with the standard credential
chain and carries no key at all. The region resolves like the Azure resource:
per-call option > AWS_BEDROCK_REGION container env var > baked default.
"""
from __future__ import annotations

import os
import urllib.parse
from typing import Optional

import requests

from ..core.manifest import AuthBlock, ModelManifest
from .base import CatalogModel, ProviderAdapter, Question, SmokeResult, http_smoke_failure


class BedrockAdapter(ProviderAdapter):
    id = "bedrock"
    display_name = "AWS Bedrock"
    provider_tag = "AWS Bedrock"
    key_name = "AWSBedrock"
    env_key_var = "AWS_BEARER_TOKEN_BEDROCK"
    docs_url = "https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html"
    template = "bedrock_converse"
    embedding_template = "emb_bedrock_titan"
    requirements_profile = "api-wrapper"
    static_catalog_file = "bedrock.json"

    def endpoint(self, answers: dict) -> Optional[str]:
        return None  # built at runtime from the region

    def questions(self) -> list[Question]:
        return [
            Question("region", "AWS region (e.g. us-east-1, eu-central-1)",
                     default=os.environ.get("AWS_BEDROCK_REGION", "us-east-1")),
            Question("auth_variant", "Auth for the score script: bearer (Bedrock API key) or sigv4 (boto3/IAM)",
                     default="bearer", required=False),
        ]

    def provider_params(self, cm: CatalogModel, answers: dict) -> dict:
        return {
            "region": answers.get("region", "us-east-1"),
            "auth_variant": (answers.get("auth_variant") or "bearer").strip().lower(),
        }

    def score_template_for(self, cm: CatalogModel) -> str:
        if cm.kind == "embedding" and "cohere" in cm.ref.lower():
            return "emb_bedrock_cohere"
        return super().score_template_for(cm)

    def build_manifest(self, cm: CatalogModel, model_id: str, answers: dict, modeler: str) -> ModelManifest:
        manifest = super().build_manifest(cm, model_id, answers, modeler)
        if manifest.provider.params.get("auth_variant") == "sigv4" and manifest.kind == "llm":
            manifest.runtime.template = "bedrock_converse_sigv4"
            manifest.provider.auth = AuthBlock(mode="none")  # IAM identity, no key option
        return manifest

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if manifest.provider.params.get("auth_variant") == "sigv4":
            return SmokeResult(ok=True, skipped=True,
                               detail="SigV4 variant - smoke-test from an AWS-credentialed shell "
                                      "or after deployment (no bearer key to test with).")
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        region = manifest.provider.params.get("region", "us-east-1")
        model_ref = urllib.parse.quote(manifest.provider.model_version, safe="")
        try:
            if manifest.kind == "embedding":
                response = session.post(
                    f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_ref}/invoke",
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                    json={"inputText": "OK"},
                    timeout=60,
                )
                body = response.json()
                if response.status_code >= 300:
                    return http_smoke_failure(response, body)
                return SmokeResult(ok=True, detail=f"Provider responded with a "
                                                   f"{len(body['embedding'])}-dimension embedding.")
            response = session.post(
                f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_ref}/converse",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json={"messages": [{"role": "user", "content": [{"text": "Reply with the single word OK."}]}],
                      "inferenceConfig": {"maxTokens": 16}},
                timeout=60,
            )
            body = response.json()
            if response.status_code >= 300:
                return http_smoke_failure(response, body)
            text = "".join(b.get("text", "") for b in body["output"]["message"]["content"])
            usage = body.get("usage", {})
            return SmokeResult(ok=True, detail=(
                f"Provider responded ({usage.get('inputTokens', '?')} in / "
                f"{usage.get('outputTokens', '?')} out tokens): {text[:60]!r}"
            ))
        except Exception as exc:
            return SmokeResult(ok=False, detail=str(exc))
