# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-file coherence rules with stable rule IDs and fix-it hints.

Managed folders (with a definition.yaml) are validated strictly; legacy
hand-written folders are reported as unmanaged and never fail validation -
the tool is an on-ramp, not a gate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from .drift import FileStatus, classify
from .facts import read_row
from .generator import CoreAssets, GenerationError, list_custom_options, render_assets
from .manifest import MANIFEST_FILENAME, ModelManifest, load_manifest

Severity = Literal["error", "warning", "info"]

# Secret-shaped strings must never appear in manifests or generated files
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\b(bearer|token|secret)\s+[A-Za-z0-9_\-]{24,}"),
]


@dataclass
class Issue:
    rule: str
    severity: Severity
    model_id: str
    message: str
    hint: Optional[str] = None

    def format(self) -> str:
        text = f"[{self.rule}] {self.model_id}: {self.message}"
        if self.hint:
            text += f"\n        fix: {self.hint}"
        return text


def validate_folder(folder: Path, core: CoreAssets, fact_sheet: Path) -> list[Issue]:
    model_id = folder.name
    issues: list[Issue] = []

    if not (folder / MANIFEST_FILENAME).is_file():
        issues.append(Issue(
            "V000", "info", model_id,
            "Unmanaged legacy folder (no definition.yaml) - left untouched.",
            f"Adopt it with: mdb import {model_id}",
        ))
        return issues

    # V001 - manifest loads and passes the schema
    try:
        manifest = load_manifest(folder)
    except Exception as exc:
        issues.append(Issue("V001", "error", model_id, f"definition.yaml is invalid: {exc}"))
        return issues

    # V002 - the model_id is the universal join key
    if manifest.model_id != folder.name:
        issues.append(Issue(
            "V002", "error", model_id,
            f"manifest model_id '{manifest.model_id}' != folder name '{folder.name}' "
            "(folder name == Model Manager name == fact-sheet key is a framework invariant).",
            "Rename the folder or fix model_id, then run mdb generate.",
        ))

    # V003 - options resolve against the vocabulary and template family
    try:
        rendered = render_assets(manifest, core)
    except GenerationError as exc:
        issues.append(Issue("V003", "error", model_id, str(exc)))
        return issues

    # V004 - generated files on disk match a fresh render (the CI drift gate)
    for item in classify(folder, rendered):
        if item.status == FileStatus.NEW:
            issues.append(Issue(
                "V004", "error", model_id,
                f"{item.filename} has not been generated yet.",
                f"Run: mdb generate {model_id}",
            ))
        elif item.status == FileStatus.STALE:
            issues.append(Issue(
                "V004", "error", model_id,
                f"{item.filename} is stale (definition.yaml or the templates changed since it was rendered).",
                f"Run: mdb generate {model_id}",
            ))
        elif item.status in (FileStatus.HAND_EDITED, FileStatus.UNTRACKED):
            issues.append(Issue(
                "V005", "error", model_id,
                f"{item.filename} was edited by hand and no longer matches its lockfile entry.",
                "Fold the change into definition.yaml and regenerate, list the file under "
                "generation.overrides to own it manually, or overwrite with mdb generate --force.",
            ))

    # V006 - fact-sheet row exists and is current
    row = read_row(fact_sheet, manifest.model_id)
    if row is None:
        issues.append(Issue(
            "V006", "error", model_id,
            "No row in the fact sheet - registration metadata and cost monitoring would be incomplete.",
            f"Run: mdb sync {model_id}",
        ))
    else:
        from .facts import row_values
        expected = row_values(manifest)
        drifted = [c for c, v in expected.items() if row.get(c, "") != v]
        if drifted:
            issues.append(Issue(
                "V006", "error", model_id,
                f"Fact-sheet row differs from the manifest in: {', '.join(drifted)}.",
                f"Run: mdb sync {model_id}",
            ))

    # V007 - no secret-shaped strings in the manifest or generated files
    manifest_text = (folder / MANIFEST_FILENAME).read_text(encoding="utf-8")
    scan_targets = {MANIFEST_FILENAME: manifest_text}
    scan_targets.update({name: content.decode("utf-8", "replace") for name, content in rendered.items()})
    for name, text in scan_targets.items():
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append(Issue(
                    "V007", "error", model_id,
                    f"{name} contains a secret-shaped string ({pattern.pattern}).",
                    "Remove the secret - keys belong in .env / LLM_API_KEYS, never in committed files.",
                ))

    # V009 - environment-specific hosts committed into a shareable definition
    params = manifest.provider.params
    if manifest.runtime.template == "azure_openai_v1" and params.get("commit_resource") and params.get("resource"):
        issues.append(Issue(
            "V009", "warning", model_id,
            f"The Azure resource '{params['resource']}' is baked into the definition - "
            "it is bound to that subscription/project.",
            "Set provider.params.commit_resource: false and regenerate; deployed containers then "
            "resolve the resource via the AZURE_OPENAI_RESOURCE environment variable or a per-call option.",
        ))

    # V010 - options outside the standardized vocabulary (allowed, but the
    # author should know what they are giving up)
    for option_name in list_custom_options(manifest, core):
        issues.append(Issue(
            "V010", "warning", model_id,
            f"Option '{option_name}' is not in the standardized option vocabulary. It is passed to "
            f"the provider as-is under its own name, and UIs like the Prompt Builder show it with "
            f"that raw name and your description - it gets no standardized label, no typed control "
            f"metadata beyond what you declared inline, and no cross-provider value translation.",
            "That can be perfectly fine for a provider-specific option. To standardize it, add an "
            "entry to definition-core/static/option-vocabulary.json (label, type, per-family mapping).",
        ))

    # V011 - self-hosted OpenAI-compatible definition with no base URL: the score
    # script has no endpoint to call unless the env var is set in the deployment.
    if manifest.runtime.template in ("openai_compat_selfhosted", "emb_openai_compat_selfhosted") \
            and not (params.get("base_url") or "").strip():
        env_var = params.get("base_url_env", "the base-URL environment variable")
        issues.append(Issue(
            "V011", "warning", model_id,
            "No base_url is set for this self-hosted OpenAI-compatible definition.",
            f"Set provider.params.base_url, or ensure {env_var} is set in every deployment - "
            "otherwise scoring fails with a missing-URL error.",
        ))

    # V008 - pricing placeholders that would silently corrupt cost monitoring.
    # Only when the prices are UNKNOWN (absent): an explicit 0 is the correct
    # answer for a genuinely free model and produces a correct costPerCall.
    pricing = manifest.metadata.pricing
    if pricing.cost_type == "Tokens" and pricing.input_token_price is None and pricing.output_token_price is None:
        issues.append(Issue(
            "V008", "warning", model_id,
            "Token pricing is unknown - costPerCall in SAS Model Manager will be 0.",
            "Fill metadata.pricing from the provider's price list (the wizard prefills it when online "
            "and asks otherwise), or set both prices to 0 explicitly if the model is genuinely free.",
        ))

    return issues


def validate_all(defs_dir: Path, core: CoreAssets, fact_sheet: Path) -> list[Issue]:
    issues: list[Issue] = []
    for folder in sorted(defs_dir.iterdir()):
        if not folder.is_dir() or folder.name.startswith(("_", ".")):
            continue
        issues.extend(validate_folder(folder, core, fact_sheet))
    return issues
