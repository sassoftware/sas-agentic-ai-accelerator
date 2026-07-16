# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Provider adapter contract.

Adapters translate provider reality into the manifest vocabulary and pick a
template - they never write files or talk to SAS Viya. The generation
surface (static catalog, manifest building) is SDK-free and works offline;
only the live surface (catalog listing, smoke test) reaches the network.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from ..core.manifest import (
    AuthBlock, GenerationBlock, MetadataBlock, ModelManifest, OptionSpec,
    PricingBlock, ProviderBlock, RuntimeBlock, TagsBlock,
)


@dataclass
class CatalogModel:
    ref: str
    display_name: str
    kind: str = "llm"
    context_length: Optional[int] = None
    max_output_tokens: Optional[int] = None
    input_price_per_m: Optional[float] = None
    output_price_per_m: Optional[float] = None
    knowledge_cutoff: Optional[str] = None
    release_date: Optional[str] = None
    supported_parameters: Optional[list[str]] = None
    reasoning: bool = False
    extended_thinking: bool = False
    source: str = "static"  # static | live

    @property
    def per_token_input(self) -> Optional[float]:
        return None if self.input_price_per_m is None else self.input_price_per_m / 1_000_000

    @property
    def per_token_output(self) -> Optional[float]:
        return None if self.output_price_per_m is None else self.output_price_per_m / 1_000_000


@dataclass
class Question:
    """An extra wizard question an adapter needs answered (e.g. Azure resource)."""

    param: str
    prompt: str
    default: str = ""
    required: bool = True


@dataclass
class SmokeResult:
    ok: bool
    detail: str
    skipped: bool = False


def slugify(text: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in text)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


class ProviderAdapter(ABC):
    id: str
    display_name: str
    provider_tag: str
    key_name: Optional[str]  # options.json API_KEY default == LLM_API_KEYS KeyName; None = self-hosted
    env_key_var: Optional[str]
    docs_url: str = ""
    template: str = "openai_chat"
    requirements_profile: str = "api-wrapper"
    static_catalog_file: Optional[str] = None

    # -- generation surface (offline, SDK-free) ---------------------------

    def static_catalog(self, core_dir: Path) -> list[CatalogModel]:
        if not self.static_catalog_file:
            return []
        path = core_dir / "catalog" / self.static_catalog_file
        if not path.is_file():
            return []
        doc = json.loads(path.read_text(encoding="utf-8"))
        snapshot = doc.get("snapshot_date", "unknown")
        models = []
        for entry in doc.get("models", []):
            models.append(CatalogModel(
                ref=entry["ref"],
                display_name=entry.get("display_name", entry["ref"]),
                kind=entry.get("kind", "llm"),
                context_length=entry.get("context_length"),
                max_output_tokens=entry.get("max_output_tokens"),
                input_price_per_m=entry.get("input_price_per_m"),
                output_price_per_m=entry.get("output_price_per_m"),
                knowledge_cutoff=entry.get("knowledge_cutoff"),
                release_date=entry.get("release_date"),
                reasoning=entry.get("reasoning", False),
                extended_thinking=entry.get("extended_thinking", False),
                source=f"static snapshot {snapshot}",
            ))
        return models

    def questions(self) -> list[Question]:
        return []

    def default_options(self, cm: CatalogModel) -> dict[str, OptionSpec]:
        if cm.reasoning:
            return {
                "reasoning_effort": OptionSpec(default="medium"),
                "max_completion_tokens": OptionSpec(default=4000, max=float(cm.max_output_tokens or 128000)),
            }
        options = {
            "temperature": OptionSpec(default=1),
            "top_p": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=float(cm.max_output_tokens) if cm.max_output_tokens else None),
        }
        if cm.extended_thinking:
            options["thinking_budget"] = OptionSpec(default=0, max=float(cm.max_output_tokens or 64000))
        return options

    @abstractmethod
    def endpoint(self, answers: dict) -> Optional[str]:
        ...

    def provider_params(self, cm: CatalogModel, answers: dict) -> dict:
        return {}

    def build_manifest(self, cm: CatalogModel, model_id: str, answers: dict, modeler: str) -> ModelManifest:
        return ModelManifest(
            model_id=model_id,
            display_name=cm.display_name,
            provider=ProviderBlock(
                adapter=self.id,
                model_version=cm.ref,
                endpoint=self.endpoint(answers),
                params=self.provider_params(cm, answers),
                auth=AuthBlock(mode="api_key", key_name=self.key_name) if self.key_name
                else AuthBlock(mode="none"),
            ),
            runtime=RuntimeBlock(template=self.template, requirements_profile=self.requirements_profile),
            options=self.default_options(cm),
            tags=TagsBlock(
                size_class="LLM",
                license_class="Proprietary",
                provider_tag=self.provider_tag,
                scr_sizing="small",
            ),
            metadata=MetadataBlock(
                description=answers.get("description")
                or f"{cm.display_name} by {self.provider_tag}, served through the {self.display_name} API.",
                release_date=cm.release_date,
                knowledge_cutoff=cm.knowledge_cutoff,
                context_length=cm.context_length,
                deployment_type="API",
                pricing=PricingBlock(
                    cost_type="Tokens",
                    input_token_price=cm.per_token_input,
                    output_token_price=cm.per_token_output,
                ),
            ),
            modeler=modeler,
            generation=GenerationBlock(catalog_provenance=f"{self.id} ({cm.source})"),
        )

    # -- live surface (network) -------------------------------------------

    def live_catalog(self, session: requests.Session, api_key: Optional[str]) -> list[CatalogModel]:
        raise NotImplementedError(f"{self.display_name} has no live catalog - the static snapshot is used.")

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        return SmokeResult(ok=True, detail="No smoke test for this provider.", skipped=True)
