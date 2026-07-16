# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reverse-engineer a hand-written definition folder into a manifest.

Best-effort by design: everything that can be read from the existing files
(modelConfiguration, options.json, fact-sheet row, score script markers) is
folded into a definition.yaml, and everything that cannot is reported so the
maintainer reviews the generate diff before converging the folder.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .facts import read_row
from .manifest import (
    AuthBlock, GenerationBlock, MetadataBlock, ModelManifest, OptionSpec,
    PricingBlock, ProviderBlock, RuntimeBlock, TagsBlock,
)

KNOWN_SIZE_CLASSES = {"LLM", "SLM", "Embedding"}
KNOWN_LICENSE_CLASSES = {"Proprietary", "Open-Source"}
KNOWN_SIZINGS = {"small", "medium", "large"}
CORE_OPTIONS = {"temperature", "top_p", "top_k", "max_tokens"}


@dataclass
class ImportResult:
    manifest: ModelManifest
    notes: list[str] = field(default_factory=list)


def _detect_family(score_text: str) -> tuple[str, str]:
    """Returns (template, adapter_id) guessed from score-script markers."""
    if "AutoModelForCausalLM" in score_text:
        return "hf_transformers", "hf-selfhosted"
    if "api.anthropic.com" in score_text:
        return "anthropic_messages", "anthropic"
    if "openai.azure.com" in score_text or "cognitive.microsoft.com" in score_text or "azure_openai_resource" in score_text:
        return "azure_openai_v1", "azure-foundry"
    if "openrouter.ai" in score_text:
        return "openai_chat", "openrouter"
    if "api.mistral.ai" in score_text:
        return "openai_chat", "mistral"
    if "api.openai.com" in score_text:
        return "openai_chat", "openai"
    return "openai_chat", "openai"


def _parse_tags(tags: list[str], notes: list[str]) -> TagsBlock:
    size_class = next((t for t in tags if t in KNOWN_SIZE_CLASSES), None)
    license_class = next((t for t in tags if t in KNOWN_LICENSE_CLASSES), None)
    sizing = next((t for t in tags if t in KNOWN_SIZINGS), None)
    consumed = {size_class, license_class, sizing}
    rest = [t for t in tags if t not in consumed]
    provider_tag = rest[0] if rest else "Unknown"
    extra = rest[1:]
    if size_class is None:
        notes.append("No LLM/SLM tag found - defaulted size_class to LLM.")
        size_class = "LLM"
    if license_class is None:
        notes.append("No Proprietary/Open-Source tag found - defaulted to Proprietary.")
        license_class = "Proprietary"
    if sizing is None:
        notes.append("No sizing tag (small/medium/large) found - defaulted to small "
                     "(publish-LLMs.py silently assumes small today).")
        sizing = "small"
    return TagsBlock(
        size_class=size_class, license_class=license_class,
        provider_tag=provider_tag, scr_sizing=sizing, extra=extra,
    )


def _num(value, cast=float):
    try:
        if value in (None, "", "."):
            return None
        return cast(value)
    except (TypeError, ValueError):
        return None


def import_folder(folder: Path, fact_sheet: Path) -> ImportResult:
    notes: list[str] = []
    model_id = folder.name

    config = json.loads((folder / "modelConfiguration.json").read_text(encoding="utf-8"))
    options_doc = json.loads((folder / "options.json").read_text(encoding="utf-8"))
    score_path = folder / config["scoreCodeFile"]
    score_text = score_path.read_text(encoding="utf-8") if score_path.is_file() else ""
    if not score_text:
        notes.append(f"Score file {config.get('scoreCodeFile')} not found - template guessed without markers.")

    template, adapter_id = _detect_family(score_text)

    model_version = ""
    match = re.search(r"modelVersion\s*=\s*'([^']+)'", score_text)
    if match:
        model_version = match.group(1)
    endpoint = None
    match = re.search(r"modelEndpoint\s*=\s*'([^']+)'", score_text)
    if match:
        endpoint = match.group(1)

    params: dict = {}
    requirements_profile = "api-wrapper"
    if template == "hf_transformers":
        requirements_profile = "hf-transformers"
        endpoint = None
        requirements_text = (folder / "requirements.json").read_text(encoding="utf-8") \
            if (folder / "requirements.json").is_file() else ""
        repo_match = re.search(r"download\s+(?:--quiet\s+)?(\S+/\S+?)\s+--local-dir", requirements_text)
        repo = repo_match.group(1) if repo_match else ""
        gated = "hf login" in requirements_text or "huggingface-cli login" in requirements_text
        if not repo:
            notes.append("Could not find the HF repo in requirements.json - fill provider.params.hf.repo by hand.")
        params["hf"] = {"repo": repo, "gated": gated}
        model_version = repo or model_id
    if template == "azure_openai_v1":
        resource_match = re.search(r'"azure_openai_resource"\s*:\s*"([^"]*)"', score_text)
        params["resource"] = resource_match.group(1) if resource_match else ""
        if not model_version:
            deploy_match = re.search(r"deploymentName\s*=\s*'([^']+)'", score_text)
            model_version = deploy_match.group(1) if deploy_match else model_id
        notes.append("Azure definitions are converged onto the GA v1 endpoint - the generated scorer "
                     "differs from legacy dated-api-version scripts by design.")

    # Options: keep the folder's defaults; API_KEY becomes auth.key_name
    api_key_entry = options_doc.pop("API_KEY", None)
    key_name = (api_key_entry or {}).get("default")
    option_specs: dict[str, OptionSpec] = {}
    for name, meta in options_doc.items():
        if name in ("azure_openai_resource", "endpoint_url", "api_version"):
            continue  # regenerated as azure connection-config options
        if name not in CORE_OPTIONS:
            notes.append(f"Option '{name}' is outside the core set - verify it against the vocabulary.")
        option_specs[name] = OptionSpec(default=meta.get("default", 1))

    row = read_row(fact_sheet, model_id) or {}
    if not row:
        notes.append("No fact-sheet row found - metadata defaults are minimal; fill definition.yaml by hand.")

    tags = _parse_tags(list(config.get("tags", [])), notes)
    manifest = ModelManifest(
        model_id=model_id,
        display_name=row.get("model") or config.get("name", model_id),
        provider=ProviderBlock(
            adapter=adapter_id,
            model_version=model_version or model_id,
            endpoint=endpoint,
            params=params,
            auth=AuthBlock(mode="api_key", key_name=key_name) if key_name else AuthBlock(mode="none"),
        ),
        runtime=RuntimeBlock(template=template, requirements_profile=requirements_profile),
        options=option_specs,
        tags=tags,
        metadata=MetadataBlock(
            description=config.get("description", ""),
            release_date=row.get("release_date") or None,
            knowledge_cutoff=row.get("knowledge_cut_off") or None,
            context_length=_num(row.get("context_length"), int),
            size=_num(row.get("size"), int),
            deployment_type=(row.get("deployment_type") or ("SCR" if template == "hf_transformers" else "API")).strip('"') or "API",
            pricing=PricingBlock(
                cost_type=(row.get("cost_type") or "Tokens").strip('"') or "Tokens",
                input_token_price=_num(row.get("input_token_price")),
                output_token_price=_num(row.get("output_token_price")),
                second_cost=_num(row.get("second_cost")),
            ),
        ),
        modeler=config.get("modeler", ""),
        generation=GenerationBlock(catalog_provenance=f"mdb import of hand-written folder"),
    )

    from .generator import score_file_name
    generated_score = score_file_name(model_id)
    if config.get("scoreCodeFile") and config["scoreCodeFile"] != generated_score:
        notes.append(
            f"Legacy score file {config['scoreCodeFile']} will be superseded by {generated_score} "
            f"on converge - delete {config['scoreCodeFile']} afterwards so only one scorer remains."
        )

    # Preserve an existing PDF model card - the generator only writes Markdown
    if (folder / "Model-Card.pdf").is_file():
        manifest.generation.overrides.append("Model-Card.md")
        notes.append("Existing Model-Card.pdf preserved (Model-Card.md added to generation.overrides; "
                     "register-LLMs.py prefers the PDF).")

    notes.append("Known intended normalizations vs legacy files: canonical options parser, "
                 "provider usage-based token counting where available, central inputVar typo fix, "
                 "pip-upgrade step in requirements.")
    return ImportResult(manifest=manifest, notes=notes)
