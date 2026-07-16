# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Self-hosted Hugging Face adapter (transformers runtime).

There is no remote catalog: the user names the HF repo, and the model
weights are downloaded into the SCR image at build time (the repo's
established pattern). Gated repos add the documented secret-volume login
step to requirements.json.
"""
from __future__ import annotations

from typing import Optional

from ..core.manifest import (
    AuthBlock, GenerationBlock, MetadataBlock, ModelManifest, OptionSpec,
    PricingBlock, ProviderBlock, RuntimeBlock, TagsBlock,
)
from .base import CatalogModel, ProviderAdapter, Question


class HuggingFaceAdapter(ProviderAdapter):
    id = "hf-selfhosted"
    display_name = "Self-hosted Hugging Face"
    provider_tag = "Hugging Face"
    key_name = None  # no API key at scoring time
    env_key_var = None
    docs_url = "https://huggingface.co/models"
    template = "hf_transformers"
    requirements_profile = "hf-transformers"

    def endpoint(self, answers: dict) -> Optional[str]:
        return None

    def questions(self) -> list[Question]:
        return [
            Question("repo", "Hugging Face repo id (e.g. Qwen/Qwen2.5-0.5B-Instruct)"),
            Question("runtime", "Runtime family: transformers (LLM) or sentence-transformers (embedding)",
                     default="transformers", required=False),
            Question("gated", "Is the repo gated (license acceptance required)? [y/N]", default="n", required=False),
            Question("params_billions", "Parameter count in billions (e.g. 0.5) - sets SLM/LLM and sizing", default="", required=False),
        ]

    def default_options(self, cm: CatalogModel) -> dict[str, OptionSpec]:
        return {
            "temperature": OptionSpec(default=0.7),
            "top_p": OptionSpec(default=0.8),
            "max_tokens": OptionSpec(default=512, max=8192),
        }

    def build_manifest(self, cm: CatalogModel, model_id: str, answers: dict, modeler: str) -> ModelManifest:
        repo = answers.get("repo", cm.ref)
        gated = str(answers.get("gated", "n")).strip().lower() in ("y", "yes", "true", "1")
        runtime = (answers.get("runtime") or "transformers").strip().lower()
        is_embedding = runtime.startswith("sentence")
        kind = "embedding" if is_embedding else "llm"
        try:
            params_billions = float(answers.get("params_billions") or 0)
        except ValueError:
            params_billions = 0.0
        if is_embedding:
            size_class = "Embedding"
            options = {
                "Embedding_Mode": OptionSpec(default="query"),
                "Embedding_Length": OptionSpec(default=cm.embedding_length or 384),
                "Input_Token_Limit": OptionSpec(default=cm.context_length or 512),
            }
            template, profile = "emb_sentence_transformers", "hf-sentence-transformers"
        else:
            size_class = "LLM" if params_billions > 7 else "SLM"
            options = self.default_options(cm)
            template, profile = self.template, self.requirements_profile
        sizing = "small" if params_billions <= 1 else ("medium" if params_billions <= 8 else "large")
        return ModelManifest(
            kind=kind,
            model_id=model_id,
            display_name=cm.display_name,
            provider=ProviderBlock(
                adapter=self.id,
                model_version=repo,
                params={"hf": {"repo": repo, "gated": gated}},
                auth=AuthBlock(mode="none"),
            ),
            runtime=RuntimeBlock(template=template, requirements_profile=profile),
            options=options,
            tags=TagsBlock(
                size_class=size_class,
                license_class="Open-Source",
                provider_tag=self.provider_tag,
                scr_sizing=sizing,
            ),
            metadata=MetadataBlock(
                description=answers.get("description") or f"{cm.display_name}, self-hosted from Hugging Face repo {repo}.",
                size=int(params_billions * 1_000_000_000) if params_billions else None,
                deployment_type="SCR",
                pricing=PricingBlock(cost_type="Seconds"),
            ),
            modeler=modeler,
            generation=GenerationBlock(catalog_provenance="manual entry (self-hosted)"),
        )
