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
import re
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


def effective_score_file(manifest: ModelManifest) -> str:
    """The score filename this definition actually uses (migrated folders keep
    their legacy name via generation.score_code_file)."""
    return manifest.generation.score_code_file or score_file_name(manifest.model_id)


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
    # Options the provider refuses to accept together, keyed by group name. The
    # first member declared in the manifest wins when the caller sets neither.
    exclusive: dict[str, list[tuple[str, str, str]]] = {}
    thinking_block = ""

    for name, spec in manifest.options.items():
        resolved = _resolve_option(name, spec, core)
        if resolved["informational"]:
            continue  # options.json only - never enters the score script
        defaults_lines.append(f'        "{name}": {_py_literal(spec.default)},')
        if resolved["custom"]:
            # Custom pass-through: sent to the provider as-is under its own
            # name - the author owns compatibility (mdb warns about this)
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                raise GenerationError(
                    f"Custom option name '{name}' is not a valid identifier - it would render "
                    "broken score code. Use letters, digits and underscores only."
                )
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
                # Extended thinking pins temperature to 1 and forbids top_p
                # entirely, so drop whatever the sampling block just decided.
                thinking_block = (
                    '    if int(options.get("thinking_budget", 0)) > 0:\n'
                    '        payload["thinking"] = {"type": "enabled", '
                    '"budget_tokens": int(options["thinking_budget"])}\n'
                    '        payload["temperature"] = 1\n'
                    '        payload.pop("top_p", None)\n'
                )
            elif "exclusive_group" in family_map:
                exclusive.setdefault(family_map["exclusive_group"], []).append(
                    (name, body_key, cast)
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
        "exclusive_options_block": _exclusive_block(exclusive),
        "thinking_block": thinking_block,
    }


def _exclusive_block(exclusive: dict[str, list[tuple[str, str, str]]]) -> str:
    """Render the runtime picker for mutually exclusive options.

    Anthropic rejects `temperature` and `top_p` in the same request ("please use
    only one"), so the choice cannot be baked into the payload literal - it has
    to follow what the caller actually asked for on each call.
    """
    lines: list[str] = []
    for group, members in exclusive.items():
        if not members:
            continue
        preferred, preferred_key, preferred_cast = members[0]
        if len(members) == 1:
            lines.append(f'    payload["{preferred_key}"] = {preferred_cast}(options["{preferred}"])')
            continue
        lines.append(
            f"    # The provider accepts only one of {group}: "
            f"{', '.join(name for name, _, _ in members)}."
        )
        keyword = "if"
        for name, body_key, cast in members[1:]:
            lines.append(f'    {keyword} "{name}" in requested and "{preferred}" not in requested:')
            lines.append(f'        payload["{body_key}"] = {cast}(options["{name}"])')
            keyword = "elif"
        lines.append("    else:")
        lines.append(f'        payload["{preferred_key}"] = {preferred_cast}(options["{preferred}"])')
    return "\n".join(lines) + "\n" if lines else ""


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
    if manifest.runtime.template in ("azure_openai_v1", "emb_azure_openai_v1"):
        context["deployment_name"] = manifest.provider.model_version
        # The resource is only baked in when explicitly committed; otherwise the
        # definition stays environment-neutral (AZURE_OPENAI_RESOURCE env var /
        # per-call option decide at runtime).
        params = manifest.provider.params
        context["azure_resource"] = params.get("resource", "") if params.get("commit_resource") else ""
        # Empty = the GA v1 endpoint; a version pins the legacy deployment-
        # scoped route some resources/policies still require. Not gated on
        # commit_resource - the API style belongs to the definition, while the
        # AZURE_OPENAI_API_VERSION container env var can still override it.
        context["azure_api_version"] = params.get("api_version", "") or ""
    if manifest.runtime.template in ("openai_compat_selfhosted", "emb_openai_compat_selfhosted"):
        # Self-hosted OpenAI-compatible servers (Ollama, vLLM). The base URL
        # resolves at runtime from an env var so one image serves any server;
        # the localhost default is a non-secret convenience, always baked in.
        params = manifest.provider.params
        context["base_url_env"] = params.get("base_url_env", "OPENAI_COMPAT_BASE_URL")
        context["base_url_default"] = params.get("base_url", "")
        context["token_env"] = params.get("token_env", "OPENAI_COMPAT_API_KEY")
    if manifest.runtime.template == "anthropic_messages":
        context["anthropic_version"] = manifest.provider.params.get("anthropic_version", "2023-06-01")
    if "bedrock" in manifest.runtime.template:
        context["region"] = manifest.provider.params.get("region", "")
    # Where the scorer loads a self-hosted model's weights from. "baked" keeps the
    # historical relative path next to the score code; "mounted" reads them from the
    # shared llm-weights volume, which every model container mounts at the same
    # place, so one staged copy serves them all.
    mounted = manifest.runtime.weights_source == "mounted"
    model_path = f"/pybox/model/mount/{manifest.model_id}" if mounted else f"./{manifest.model_id}"
    context["model_path"] = model_path
    if manifest.runtime.template == "hf_onnx":
        hf = manifest.provider.params.get("hf", {})
        # onnx_dir is a subdirectory of the model, so it follows the weights.
        onnx_dir = hf.get("onnx_dir", ".")
        context["onnx_dir"] = (
            model_path if onnx_dir == "." else f"{model_path}/{onnx_dir.lstrip('./')}"
        ) if mounted else onnx_dir
        context["chat_template"] = hf.get(
            "chat_template", "<|system|>\\n{systemPrompt}<|end|><|user|>\\n{userPrompt} <|end|>\\n<|assistant|>"
        )
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
    if manifest.runtime.template in ("azure_openai_v1", "emb_azure_openai_v1"):
        is_embedding_template = manifest.runtime.template == "emb_azure_openai_v1"
        route = "embeddings" if is_embedding_template else "chat/completions"
        params = manifest.provider.params
        entries["azure_openai_resource"] = {
            "default": params.get("resource", "") if params.get("commit_resource") else "",
            "range": "your-resource.openai.azure.com",
            "description": (
                "The Azure OpenAI / Azure AI Foundry resource host that serves the deployment. "
                "A short resource name is expanded to the full openai.azure.com host. Resolution order: "
                "this option > the AZURE_OPENAI_RESOURCE environment variable of the container > "
                "this default - set the environment variable per deployment to serve different "
                "subscriptions/projects from the same image."
            ),
            "type": "string",
        }
        entries["azure_api_version"] = {
            "default": params.get("api_version", "") or "",
            "range": "2024-10-21 (empty = GA v1 endpoint)",
            "description": (
                f"Empty uses the GA v1 endpoint (/openai/v1/{route}). Set an API version "
                "(e.g. 2024-10-21 or 2025-01-01-preview) to call the legacy deployment-scoped route "
                f"(/openai/deployments/<name>/{route}?api-version=...) that some resources "
                "or policies still require. Resolution order: this option > the "
                "AZURE_OPENAI_API_VERSION environment variable of the container > this default."
            ),
            "type": "string",
        }
        entries["endpoint_url"] = {
            "default": "",
            "range": "https://**** (optional)",
            "description": f"Optional full {route} URL that overrides the resource-based endpoint.",
            "type": "string",
        }
    if manifest.runtime.template in ("openai_compat_selfhosted", "emb_openai_compat_selfhosted"):
        params = manifest.provider.params
        env_var = params.get("base_url_env", "OPENAI_COMPAT_BASE_URL")
        entries["base_url"] = {
            "default": params.get("base_url", ""),
            "range": "http://host:port/v1",
            "description": (
                "Base URL of the self-hosted OpenAI-compatible server (Ollama, vLLM, ...). "
                f"Resolution order: this option > the {env_var} environment variable of the "
                "container > this default - set the environment variable per deployment to point "
                "at a different inference server from the same image."
            ),
            "type": "string",
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
        "scoreCodeFile": effective_score_file(manifest),
        "description": manifest.metadata.description,
        "modeler": manifest.modeler,
        "tags": manifest.tags.as_list(),
        **core.boilerplate["constants"],
        **core.boilerplate["kind_constants"][manifest.kind],
        **core.boilerplate["prose"],
    }
    ordered = {key: values[key] for key in core.boilerplate["keyOrder"]}
    return json.dumps(ordered, indent=4, ensure_ascii=False)


def _hf_weight_steps(
    manifest: ModelManifest, hf: dict[str, Any], repo: str
) -> list[dict[str, str]]:
    """Build steps that put a self-hosted model's weights in place.

    With ``weights_source: mounted`` the weights are staged once on the shared
    ``llm-weights`` volume and read from ``/pybox/model/mount/<id>`` at run time,
    so nothing is downloaded during the build: the image stays small and one copy
    of the weights serves every container and replica. See the Administration
    Guide page "Serving Open-Weight Models".

    With the default ``baked`` the weights are downloaded into the image at build
    time. That path cannot authenticate to Hugging Face, so a gated repository
    has to use ``mounted``.
    """
    if manifest.runtime.weights_source == "mounted":
        return []
    if hf.get("gated"):
        raise GenerationError(
            f"{manifest.model_id}: '{repo}' is a gated Hugging Face repository, which "
            f"cannot be downloaded during the container build. Set "
            f"'runtime.weights_source: mounted' in definition.yaml and stage the "
            f"weights on the shared llm-weights volume instead - see the "
            f"Administration Guide page 'Serving Open-Weight Models'."
        )
    return [{
        "step": "download model",
        "command": f"hf download --quiet {repo} --local-dir /pybox/model/{manifest.model_id}",
    }]


def _render_requirements(manifest: ModelManifest) -> str:
    profile = manifest.runtime.requirements_profile
    upgrade_step = {
        "step": "upgrade pip before pip install",
        "command": "pip3 -q install --upgrade pip setuptools wheel",
    }
    # SCR containers run on CPU; the default torch wheel bundles ~2GB of unused
    # CUDA libraries, which pushes the image build past the publish timeout.
    # --extra-index-url keeps PyPI as the primary index (so torch's transitive
    # deps still resolve there) while the CPU-only torch wheel (the +cpu local
    # version, which sorts higher) is preferred from the PyTorch index.
    torch_cpu_step = {
        "step": "install CPU-only PyTorch (SCR runs on CPU - the default wheel bundles ~2GB of unused CUDA libraries)",
        "command": "pip3 -q install --extra-index-url https://download.pytorch.org/whl/cpu torch",
    }
    if profile == "api-wrapper":
        steps = [
            upgrade_step,
            {"step": "install common packages", "command": "pip3 -q install requests numpy==1.26.4"},
        ]
    elif profile == "hf-transformers":
        hf = manifest.provider.params.get("hf", {})
        repo = hf.get("repo") or manifest.provider.model_version
        # hf download uses the huggingface_hub HTTP API (snapshot_download); git-lfs
        # is not involved, so no git-lfs install step is needed.
        steps = [
            upgrade_step,
            torch_cpu_step,
            {
                "step": "install huggingface CLI and other packages",
                "command": "pip3 -q install 'huggingface-hub>=0.18.0' transformers accelerate numpy==1.26.4",
            },
        ]
        steps.extend(_hf_weight_steps(manifest, hf, repo))
    elif profile == "hf-onnx":
        hf = manifest.provider.params.get("hf", {})
        repo = hf.get("repo") or manifest.provider.model_version
        # onnxruntime-genai does not use torch; hf download needs no git-lfs.
        steps = [
            upgrade_step,
            {
                "step": "install huggingface CLI and other packages",
                "command": "pip3 -q install 'huggingface-hub>=0.18.0' numpy onnxruntime-genai",
            },
        ]
        steps.extend(_hf_weight_steps(manifest, hf, repo))
    elif profile == "hf-sentence-transformers":
        hf = manifest.provider.params.get("hf", {})
        repo = hf.get("repo") or manifest.provider.model_version
        # sentence-transformers pulls in torch; install the CPU build explicitly.
        # hf download uses the huggingface_hub HTTP API, so no git-lfs is needed.
        steps = [
            upgrade_step,
            torch_cpu_step,
            {
                "step": "install huggingface CLI and other packages",
                # Floor matches the 5.x APIs the generated scorer calls (preprocess,
                # encode_query, encode_document).
                "command": "pip3 -q install 'huggingface-hub>=0.18.0' 'sentence-transformers>=5.1' numpy==1.26.4",
            },
        ]
        steps.extend(_hf_weight_steps(manifest, hf, repo))
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
        effective_score_file(manifest): _render_score(manifest, core),
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
