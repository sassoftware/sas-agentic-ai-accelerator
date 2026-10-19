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
    embedding_length: Optional[int] = None
    input_price_per_m: Optional[float] = None
    output_price_per_m: Optional[float] = None
    knowledge_cutoff: Optional[str] = None
    release_date: Optional[str] = None
    supported_parameters: Optional[list[str]] = None
    reasoning: bool = False
    extended_thinking: bool = False
    # Thinking steered by an effort level instead of a token budget (Claude
    # 5.5: thinking type 'enabled' is refused) - offered as reasoning_effort.
    adaptive_thinking: bool = False
    # False: the model refuses sampling options (Claude 5.5: temperature
    # other than 1, top_p and top_k at any value), so none is written.
    sampling: bool = True
    # The reasoning-effort values the provider accepts for THIS model, lowest
    # first (GPT-6 Luna: none..xhigh). mdb add derives the option's value_map
    # from it where it differs from the family's translation.
    reasoning_efforts: Optional[list[str]] = None
    # Decision models only: what the model accepts (None = 255 options, all three question types)
    max_options: Optional[int] = None
    question_types: Optional[list[str]] = None
    source: str = "static"  # static | live

    @property
    def per_token_input(self) -> Optional[float]:
        return _per_token(self.input_price_per_m)

    @property
    def per_token_output(self) -> Optional[float]:
        return _per_token(self.output_price_per_m)


def _per_token(per_m: Optional[float]) -> Optional[float]:
    """A price per 1M tokens as the price per token, rounded to 12 significant
    digits: 0.1 / 1e6 is 1.0000000000000001e-07 in binary floating point,
    which definition.yaml would otherwise carry."""
    return None if per_m is None else float(f"{per_m / 1_000_000:.12g}")


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
    # Inconclusive: the call failed for a transient upstream reason (e.g. a
    # 429) that says nothing about the definition being wrong - reported as a
    # warning, not a validation failure.
    inconclusive: bool = False


def http_smoke_failure(response, body) -> "SmokeResult":
    """Uniform failure classification for provider smoke tests. A 429 proves
    the endpoint answered and the API key was accepted - the model is just
    temporarily rate-limited upstream - so it is inconclusive rather than a
    definition problem."""
    if response.status_code == 429:
        return SmokeResult(
            ok=False, inconclusive=True,
            detail=("HTTP 429 (rate-limited): the endpoint answered and the API key was accepted - "
                    f"the model is temporarily rate-limited upstream, retry shortly. {body}"),
        )
    return SmokeResult(ok=False, detail=f"HTTP {response.status_code}: {body}")


#: Every effort word a provider is known to use, lowest first: OpenAI's
#: none..xhigh and Anthropic's low..max on one ladder.
PROVIDER_EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
#: The max_tokens default of a model that thinks inside its output budget and
#: takes no sampling option (Claude 5.5).
ADAPTIVE_MAX_TOKENS = 4000


def effort_value_map(accepted: Optional[list[str]]) -> Optional[dict[str, str]]:
    """The reasoning_effort value_map of a model that accepts `accepted`
    (provider words), or None when the catalog does not say.

    minimal -> 'minimal' where accepted, else the lowest accepted value
    (GPT-6 Luna: none, GPT-6.1 Sol: low); maximum -> the highest accepted
    value (xhigh, Anthropic's max); low / medium / high -> themselves, else
    the nearest accepted value on PROVIDER_EFFORT_LADDER (the higher on a
    tie). `mdb add` drops a map equal to the family's (drop_family_value_maps).
    """
    if not accepted:
        return None
    rank = {word: index for index, word in enumerate(PROVIDER_EFFORT_LADDER)}
    known = sorted({word for word in accepted if word in rank}, key=rank.__getitem__)
    if not known:
        return None

    def nearest(word: str) -> str:
        if word in known:
            return word
        return min(known, key=lambda value: (abs(rank[value] - rank[word]), -rank[value]))

    return {
        "minimal": "minimal" if "minimal" in known else known[0],
        "low": nearest("low"),
        "medium": nearest("medium"),
        "high": nearest("high"),
        "maximum": known[-1],
    }


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
    #: further variables accepted for the same key (e.g. a vendor's own name)
    env_key_fallbacks: tuple = ()
    docs_url: str = ""
    template: str = "openai_chat"
    embedding_template: Optional[str] = None  # set when the adapter supports kind=embedding
    decision_template: Optional[str] = None  # set when the adapter supports kind=decision
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
                embedding_length=entry.get("embedding_length"),
                input_price_per_m=entry.get("input_price_per_m"),
                output_price_per_m=entry.get("output_price_per_m"),
                knowledge_cutoff=entry.get("knowledge_cutoff"),
                release_date=entry.get("release_date"),
                reasoning=entry.get("reasoning", False),
                extended_thinking=entry.get("extended_thinking", False),
                adaptive_thinking=entry.get("adaptive_thinking", False),
                sampling=entry.get("sampling", True),
                reasoning_efforts=entry.get("reasoning_efforts"),
                max_options=entry.get("max_options"),
                question_types=entry.get("question_types"),
                # An entry added after the file's snapshot names its own date,
                # so the provenance says when ITS values were taken.
                source=f"static snapshot {entry.get('snapshot_date', snapshot)}",
            ))
        return models

    def questions(self) -> list[Question]:
        return []

    def embedding_options(self, cm: CatalogModel) -> dict[str, OptionSpec]:
        options: dict[str, OptionSpec] = {}
        if cm.embedding_length:
            options["Embedding_Length"] = OptionSpec(default=cm.embedding_length)
        if cm.context_length:
            options["Input_Token_Limit"] = OptionSpec(default=cm.context_length)
        return options

    def default_options(self, cm: CatalogModel) -> dict[str, OptionSpec]:
        if cm.kind == "embedding":
            return self.embedding_options(cm)
        if cm.kind == "decision":
            return {}  # typed questions carry their own settings; there is nothing to sample
        if cm.reasoning:
            return {
                "reasoning_effort": OptionSpec(default="medium",
                                               value_map=effort_value_map(cm.reasoning_efforts)),
                "max_completion_tokens": OptionSpec(default=4000, max=float(cm.max_output_tokens or 128000)),
            }
        if cm.adaptive_thinking or not cm.sampling:
            # No sampling option (the model refuses any but its default), and
            # an output budget that leaves room for thinking: with 1000 the
            # Prompt Builder judge's verdict was cut off on Claude 5.5 (the
            # judge, which then sent no output limit, now asks for >= 4000).
            options: dict[str, OptionSpec] = {}
            if cm.adaptive_thinking:
                options["reasoning_effort"] = OptionSpec(default="medium",
                                                         value_map=effort_value_map(cm.reasoning_efforts))
            options["max_tokens"] = OptionSpec(default=ADAPTIVE_MAX_TOKENS,
                                               max=float(cm.max_output_tokens) if cm.max_output_tokens else None)
            return options
        options = {
            "temperature": OptionSpec(default=1),
            "top_p": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=float(cm.max_output_tokens) if cm.max_output_tokens else None),
        }
        # Newer OpenAI-style models reject max_tokens in favor of
        # max_completion_tokens - when the catalog says which one the model
        # actually supports, follow it.
        supported = cm.supported_parameters or []
        if supported and "max_tokens" not in supported and "max_completion_tokens" in supported:
            options["max_completion_tokens"] = options.pop("max_tokens")
        if cm.extended_thinking:
            options["thinking_budget"] = OptionSpec(default=0, max=float(cm.max_output_tokens or 64000))
        return options

    @abstractmethod
    def endpoint(self, answers: dict) -> Optional[str]:
        ...

    def embedding_endpoint(self, answers: dict) -> Optional[str]:
        return None

    def decision_endpoint(self, answers: dict) -> Optional[str]:
        return None

    def endpoint_for(self, cm: CatalogModel, answers: dict) -> Optional[str]:
        if cm.kind == "embedding":
            return self.embedding_endpoint(answers)
        if cm.kind == "decision":
            return self.decision_endpoint(answers)
        return self.endpoint(answers)

    def provider_params(self, cm: CatalogModel, answers: dict) -> dict:
        return {}

    def score_template_for(self, cm: CatalogModel) -> str:
        if cm.kind == "embedding":
            if not self.embedding_template:
                raise ValueError(f"{self.display_name} does not support embedding definitions.")
            return self.embedding_template
        if cm.kind == "decision":
            if not self.decision_template:
                raise ValueError(f"{self.display_name} does not support decision definitions.")
            return self.decision_template
        return self.template

    def build_manifest(self, cm: CatalogModel, model_id: str, answers: dict, modeler: str) -> ModelManifest:
        return ModelManifest(
            kind=cm.kind,
            model_id=model_id,
            display_name=cm.display_name,
            provider=ProviderBlock(
                adapter=self.id,
                model_version=cm.ref,
                endpoint=self.endpoint_for(cm, answers),
                params=self.provider_params(cm, answers),
                auth=AuthBlock(mode="api_key", key_name=self.key_name) if self.key_name
                else AuthBlock(mode="none"),
            ),
            runtime=RuntimeBlock(template=self.score_template_for(cm),
                                 requirements_profile=self.requirements_profile),
            options=self.default_options(cm),
            tags=TagsBlock(
                size_class={"embedding": "Embedding", "decision": "Decision"}.get(cm.kind, "LLM"),
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
                max_options=cm.max_options if cm.kind == "decision" else None,
                question_types=cm.question_types if cm.kind == "decision" else None,
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
