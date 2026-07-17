# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic asset generation: manifest in, bytes out.

Same manifest + same tool version = same bytes on every platform:
all files are rendered with LF line endings, fixed key order and no
timestamps. This module is pure (no filesystem writes) so the CLI, CI
and tests all share one code path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .. import __version__
from .manifest import ModelManifest, OptionSpec

SCORE_TEMPLATE_SUFFIX = ".py.j2"


class GenerationError(RuntimeError):
    pass


@dataclass
class CoreAssets:
    """Static definition-core content, loaded once."""

    core_dir: Path
    vocabulary: dict[str, Any]
    api_key_meta: dict[str, str]
    boilerplate: dict[str, Any]
    var_files: dict[str, tuple[str, str]]  # kind -> (inputVar, outputVar)

    @classmethod
    def load(cls, core_dir: Path) -> "CoreAssets":
        static = core_dir / "static"
        vocab_doc = json.loads((static / "option-vocabulary.json").read_text(encoding="utf-8"))
        return cls(
            core_dir=core_dir,
            vocabulary=vocab_doc["options"],
            api_key_meta=vocab_doc["api_key"],
            boilerplate=json.loads((static / "modelconfig-boilerplate.json").read_text(encoding="utf-8")),
            var_files={
                "llm": (
                    (static / "inputVar-llm.json").read_text(encoding="utf-8"),
                    (static / "outputVar-llm.json").read_text(encoding="utf-8"),
                ),
                "embedding": (
                    (static / "inputVar-emb.json").read_text(encoding="utf-8"),
                    (static / "outputVar-emb.json").read_text(encoding="utf-8"),
                ),
            },
        )

    def jinja(self) -> Environment:
        return Environment(
            loader=FileSystemLoader(str(self.core_dir / "templates")),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )


def score_file_name(model_id: str) -> str:
    parts = model_id.split("_")
    camel = parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])
    return f"{camel}Score.py"


def _py_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value))


def _trim_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return json.dumps(value)


def _resolve_option(name: str, spec: OptionSpec, core: CoreAssets) -> dict[str, Any]:
    vocab = core.vocabulary.get(name, {})
    if not vocab and spec.type is None:
        raise GenerationError(
            f"Option '{name}' is not in the option vocabulary and declares no inline type. "
            "Add it to definition-core/static/option-vocabulary.json, or declare it inline "
            "with type/description (it then passes through to the provider under its own name)."
        )
    resolved = {
        "type": spec.type or vocab.get("type", "number"),
        "default": spec.default,
        "min": spec.min if spec.min is not None else vocab.get("min"),
        "max": spec.max if spec.max is not None else vocab.get("max"),
        "values": spec.values or vocab.get("values"),
        "label": spec.label or vocab.get("label"),
        "description": spec.description or vocab.get("description", ""),
        "range": spec.range or vocab.get("range"),
        "families": vocab.get("families", {}),
        "informational": spec.informational if spec.informational is not None
        else vocab.get("informational", False),
        # Not in the vocabulary: UIs show the raw name/author description, and
        # there is no standardized label or cross-provider translation
        "custom": not vocab,
    }
    return resolved


def list_custom_options(manifest: ModelManifest, core: CoreAssets) -> list[str]:
    """Names of options this manifest declares outside the standardized vocabulary."""
    return [name for name in manifest.options if name not in core.vocabulary]


def _legacy_range(name: str, resolved: dict[str, Any], manifest: ModelManifest) -> str:
    template = resolved.get("range")
    if template and "{max}" in template:
        max_value = resolved.get("max") or manifest.metadata.context_length or 200000
        return template.replace("{max}", str(int(max_value)))
    if template and "{default}" in template:
        return template.replace("{default}", str(resolved["default"]))
    if template:
        return template
    if resolved["type"] == "enum" and resolved.get("values"):
        return " | ".join(resolved["values"])
    if resolved.get("min") is not None and resolved.get("max") is not None:
        return f"{_trim_number(resolved['min'])} - {_trim_number(resolved['max'])}"
    return ""


CAST_FN = {"float": "float", "int": "int", "str": "str", "bool": "bool"}
# Python coercion for custom pass-through options, keyed by option type
TYPE_CAST = {"number": "float", "int": "int", "bool": "bool", "enum": "str", "string": "str"}


def _score_blocks(manifest: ModelManifest, core: CoreAssets) -> dict[str, str]:
    family = manifest.runtime.template
    defaults_lines: list[str] = []
    body_lines: list[str] = []
    generate_lines: list[str] = []
    thinking_block = ""

    for name, spec in manifest.options.items():
        resolved = _resolve_option(name, spec, core)
        if resolved["informational"]:
            continue  # options.json only - never enters the score script
        defaults_lines.append(f'        "{name}": {_py_literal(spec.default)},')
        if resolved["custom"]:
            # Custom pass-through: sent to the provider as-is under its own
            # name - the author owns compatibility (mdb warns about this)
            cast = CAST_FN[TYPE_CAST.get(resolved["type"], "str")]
            if family == "hf_transformers":
                generate_lines.append(f"        {name}={cast}(options['{name}'])")
            else:
                body_lines.append(f'        "{name}": {cast}(options["{name}"]),')
            continue
        family_map = resolved["families"].get(family)
        if family_map is None:
            raise GenerationError(
                f"Option '{name}' is not supported by score template family '{family}'. "
                "Remove the option or extend the vocabulary."
            )
        if family_map.get("builtin"):
            continue  # the template itself consumes the option (e.g. Embedding_Mode branch)
        cast = CAST_FN.get(family_map.get("cast", "str"), "str")
        if "value_map" in family_map and "body_key" in family_map:
            # Normalized enum translated to the provider's own value set, e.g.
            # the 5-level reasoning scale where 'maximum' maps to OpenAI 'high'
            value_map = family_map["value_map"]
            fallback = value_map.get(str(spec.default), next(iter(value_map.values())))
            body_lines.append(
                f'        "{family_map["body_key"]}": {json.dumps(value_map)}'
                f'.get(str(options["{name}"]), "{fallback}"),'
            )
            continue
        if "body_key" in family_map:
            body_key = family_map["body_key"]
            if body_key == "__thinking__":
                thinking_block = (
                    '    if int(options.get("thinking_budget", 0)) > 0:\n'
                    '        payload["thinking"] = {"type": "enabled", '
                    '"budget_tokens": int(options["thinking_budget"])}\n'
                    '        payload["temperature"] = 1\n'
                )
            else:
                body_lines.append(f'        "{body_key}": {cast}(options["{name}"]),')
        elif "generate_key" in family_map:
            generate_lines.append(f"        {family_map['generate_key']}={cast}(options['{name}'])")

    return {
        "options_defaults_block": "\n".join(defaults_lines),
        "body_options_block": "\n".join(body_lines),
        "body_options_block_nested": "\n".join("    " + line for line in body_lines),
        "generate_options_block": ",\n".join(generate_lines),
        "thinking_block": thinking_block,
    }


def _render_score(manifest: ModelManifest, core: CoreAssets) -> str:
    env = core.jinja()
    template_name = f"score/{manifest.runtime.template}{SCORE_TEMPLATE_SUFFIX}"
    try:
        template = env.get_template(template_name)
    except Exception as exc:
        raise GenerationError(f"Score template '{template_name}' not found in definition-core.") from exc
    context: dict[str, Any] = {
        "generated_header": (
            f"# GENERATED by mdb {__version__} from definition.yaml - edit definition.yaml "
            f"and run 'mdb generate {manifest.model_id}' instead of editing this file.\n"
        ),
        "model_id": manifest.model_id,
        "model_version": manifest.provider.model_version,
        "endpoint": manifest.provider.endpoint or "",
        "timeout_s": manifest.runtime.timeout_s,
        **_score_blocks(manifest, core),
    }
    if manifest.runtime.template == "azure_openai_v1":
        context["deployment_name"] = manifest.provider.model_version
        # The resource is only baked in when explicitly committed; otherwise the
        # definition stays environment-neutral (AZURE_OPENAI_RESOURCE env var /
        # per-call option decide at runtime).
        params = manifest.provider.params
        context["azure_resource"] = params.get("resource", "") if params.get("commit_resource") else ""
    if manifest.runtime.template == "anthropic_messages":
        context["anthropic_version"] = manifest.provider.params.get("anthropic_version", "2023-06-01")
    if "bedrock" in manifest.runtime.template:
        context["region"] = manifest.provider.params.get("region", "")
    rendered = template.render(**context)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def _render_options_json(manifest: ModelManifest, core: CoreAssets) -> str:
    entries: dict[str, dict[str, Any]] = {}
    for name, spec in manifest.options.items():
        resolved = _resolve_option(name, spec, core)
        entry: dict[str, Any] = {
            "default": spec.default,
            "range": _legacy_range(name, resolved, manifest),
            "description": resolved["description"],
        }
        # Additive typed-option fields for UI consumers (Prompt Builder): only
        # emitted for non-numeric types so legacy numeric entries stay byte-identical.
        if resolved["type"] in ("enum", "bool", "string"):
            entry["type"] = resolved["type"]
            if resolved["type"] == "enum" and resolved.get("values"):
                entry["values"] = resolved["values"]
        # Human-readable display label (additive; UIs fall back to the key)
        if resolved.get("label"):
            entry["label"] = resolved["label"]
        entries[name] = entry
    if manifest.runtime.template == "azure_openai_v1":
        params = manifest.provider.params
        entries["azure_openai_resource"] = {
            "default": params.get("resource", "") if params.get("commit_resource") else "",
            "range": "your-resource.openai.azure.com",
            "description": (
                "The Azure OpenAI / Azure AI Foundry resource host that serves the deployment. "
                "A short resource name is expanded to <name>.openai.azure.com. Resolution order: "
                "this option > the AZURE_OPENAI_RESOURCE environment variable of the container > "
                "this default - set the environment variable per deployment to serve different "
                "subscriptions/projects from the same image."
            ),
        }
        entries["endpoint_url"] = {
            "default": "",
            "range": "https://**** (optional)",
            "description": "Optional full chat-completions URL that overrides the resource-based endpoint.",
        }
    if manifest.provider.auth.mode == "api_key":
        entries["API_KEY"] = {
            "default": manifest.provider.auth.key_name,
            "range": core.api_key_meta["range"],
            "description": core.api_key_meta["description"].replace("{key_name}", manifest.provider.auth.key_name or ""),
        }
    return json.dumps(entries, indent=4, ensure_ascii=False)


def _render_model_configuration(manifest: ModelManifest, core: CoreAssets) -> str:
    values: dict[str, Any] = {
        "name": manifest.model_id,
        "scoreCodeFile": score_file_name(manifest.model_id),
        "description": manifest.metadata.description,
        "modeler": manifest.modeler,
        "tags": manifest.tags.as_list(),
        **core.boilerplate["constants"],
        **core.boilerplate["kind_constants"][manifest.kind],
        **core.boilerplate["prose"],
    }
    ordered = {key: values[key] for key in core.boilerplate["keyOrder"]}
    return json.dumps(ordered, indent=4, ensure_ascii=False)


def _render_requirements(manifest: ModelManifest) -> str:
    profile = manifest.runtime.requirements_profile
    upgrade_step = {
        "step": "upgrade pip before pip install",
        "command": "pip3 -q install --upgrade pip setuptools wheel",
    }
    if profile == "api-wrapper":
        steps = [
            upgrade_step,
            {"step": "install common packages", "command": "pip3 -q install requests numpy==1.26.4"},
        ]
    elif profile == "hf-transformers":
        hf = manifest.provider.params.get("hf", {})
        repo = hf.get("repo") or manifest.provider.model_version
        steps = [
            {"step": "install git-lfs", "command": "microdnf install git-lfs"},
            {"step": "Verify git-lfs install", "command": "git lfs install"},
            upgrade_step,
            {
                "step": "install huggingface CLI and other packages",
                "command": "pip3 -q install huggingface-hub>=0.18.0 transformers torch accelerate numpy==1.26.4",
            },
        ]
        if hf.get("gated"):
            steps.append({
                "step": f"Login with huggingface - ensure you have accepted the license: https://huggingface.co/{repo}",
                "command": "hf login --token $(cat /etc/secret-volume/huggingfacetoken)",
            })
        steps.append({
            "step": "download model",
            "command": f"hf download --quiet {repo} --local-dir /pybox/model/{manifest.model_id}",
        })
    elif profile == "hf-sentence-transformers":
        hf = manifest.provider.params.get("hf", {})
        repo = hf.get("repo") or manifest.provider.model_version
        steps = [
            {"step": "install git-lfs", "command": "microdnf install git-lfs"},
            {"step": "Verify git-lfs install", "command": "git lfs install"},
            upgrade_step,
            {
                "step": "install huggingface CLI and other packages",
                "command": "pip3 -q install huggingface-hub>=0.18.0 sentence-transformers numpy==1.26.4",
            },
        ]
        if hf.get("gated"):
            steps.append({
                "step": f"Login with huggingface - ensure you have accepted the license: https://huggingface.co/{repo}",
                "command": "hf login --token $(cat /etc/secret-volume/huggingfacetoken)",
            })
        steps.append({
            "step": "download model",
            "command": f"hf download --quiet {repo} --local-dir /pybox/model/{manifest.model_id}",
        })
    else:
        raise GenerationError(f"Unknown requirements profile '{profile}'.")
    return json.dumps(steps, indent=4, ensure_ascii=False)


def _fmt_price_per_m(per_token: float | None) -> str:
    if per_token is None:
        return "unknown"
    return f"${per_token * 1_000_000:g} per 1M tokens"


def _render_docs(manifest: ModelManifest, core: CoreAssets, options_json: str) -> dict[str, str]:
    env = core.jinja()
    entries = json.loads(options_json)
    table_lines = [
        "| Option | Default | Range | Description |",
        "| ------ | ------- | ----- | ----------- |",
    ]
    for name, meta in entries.items():
        default = "" if meta["default"] is None else str(meta["default"])
        table_lines.append(f"| `{name}` | {default} | {meta['range']} | {meta['description']} |")
    pricing = manifest.metadata.pricing
    if pricing.cost_type == "Tokens":
        pricing_line = (
            f"Input: {_fmt_price_per_m(pricing.input_token_price)} - "
            f"Output: {_fmt_price_per_m(pricing.output_token_price)}"
        )
    else:
        pricing_line = f"Cost per second: {pricing.second_cost if pricing.second_cost is not None else 'unknown'}"
    context = {
        "model_id": manifest.model_id,
        "display_name": manifest.display_name,
        "description": manifest.metadata.description,
        "provider_display": manifest.tags.provider_tag,
        "model_version": manifest.provider.model_version,
        "deployment_type": manifest.metadata.deployment_type,
        "license": manifest.tags.license_class,
        "context_length": manifest.metadata.context_length or "unknown",
        "knowledge_cutoff": manifest.metadata.knowledge_cutoff or "unknown",
        "release_date": manifest.metadata.release_date or "unknown",
        "options_table": "\n".join(table_lines),
        "pricing_line": pricing_line,
        "catalog_provenance": manifest.generation.catalog_provenance,
    }
    return {
        "README.md": env.get_template("files/README.md.j2").render(**context),
        "Model-Card.md": env.get_template("files/Model-Card.md.j2").render(**context),
    }


def render_assets(manifest: ModelManifest, core: CoreAssets) -> dict[str, bytes]:
    """Render every generated asset for a definition. Returns filename -> bytes (LF)."""
    options_json = _render_options_json(manifest, core)
    input_var, output_var = core.var_files[manifest.kind]
    text_assets: dict[str, str] = {
        score_file_name(manifest.model_id): _render_score(manifest, core),
        "inputVar.json": input_var,
        "outputVar.json": output_var,
        "modelConfiguration.json": _render_model_configuration(manifest, core),
        "options.json": options_json,
        "requirements.json": _render_requirements(manifest),
        **_render_docs(manifest, core, options_json),
    }
    for name in manifest.generation.overrides:
        text_assets.pop(name, None)
    return {
        name: content.replace("\r\n", "\n").encode("utf-8")
        for name, content in text_assets.items()
    }
