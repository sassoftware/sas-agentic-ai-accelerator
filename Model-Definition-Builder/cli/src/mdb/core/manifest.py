# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The model manifest (definition.yaml) - the single source of truth per model.

Everything else in a definition folder is generated from this file.
The pydantic models below also export the language-neutral JSON Schema
committed at definition-core/schema/manifest.schema.json, which the
phase-2 web app consumes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MANIFEST_FILENAME = "definition.yaml"
MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")

OptionType = Literal["number", "int", "bool", "enum", "string"]


class OptionSpec(BaseModel):
    """A single scoring-time option. Type/UI metadata not set here is
    resolved from the option vocabulary in definition-core."""

    model_config = ConfigDict(extra="forbid")

    type: Optional[OptionType] = None
    default: Union[float, int, bool, str]
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[list[str]] = None  # enum options only
    label: Optional[str] = None  # human-readable display label, overrides the vocabulary
    description: Optional[str] = None  # overrides the vocabulary description
    range: Optional[str] = None  # overrides the legacy free-text range string
    # Custom options only: documented in options.json but never sent to the provider
    informational: Optional[bool] = None


#: The name of an environment variable as a container and a Kubernetes Secret
#: key both take it.
ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")

#: The variable an `auth.mode: env` definition reads its key from when it names
#: none: the name .env.example (and the adapter's own env_key_var) uses for that
#: provider's key. Every azure* adapter shares AZURE_OPENAI_API_KEY.
DEFAULT_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "google": "GEMINI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "voyage": "VOYAGE_API_KEY",
    "bedrock": "AWS_BEDROCK_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "typesafe": "TYPESAFE_API_KEY",
}


def default_key_env(adapter: str) -> Optional[str]:
    """The default key variable of an adapter id, None where it has none."""
    if adapter.startswith("azure"):
        return "AZURE_OPENAI_API_KEY"
    return DEFAULT_KEY_ENV.get(adapter)


class AuthBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # api_key: the key arrives per call in the API_KEY scoring option, which
    #          names a credential-domain entry (key_name).
    # env:     the key is read from an environment variable of the SCR
    #          container (env_var); options.json has no API_KEY entry.
    # none:    the model takes no provider key.
    mode: Literal["api_key", "env", "none"] = "api_key"
    # Must equal the options.json API_KEY default and the LLM_API_KEYS CAS-table KeyName
    key_name: Optional[str] = None
    # auth.mode env only: the container environment variable that holds the
    # key. Absent, the adapter's default (DEFAULT_KEY_ENV) is used.
    env_var: Optional[str] = Field(None, json_schema_extra={"pattern": ENV_VAR_PATTERN.pattern})

    @model_validator(mode="after")
    def _key_name_required(self) -> "AuthBlock":
        if self.mode == "api_key" and not self.key_name:
            raise ValueError("auth.key_name is required when auth.mode is 'api_key'")
        if self.mode == "env" and self.key_name:
            raise ValueError(
                "auth.key_name is not used when auth.mode is 'env' - the key comes from the container "
                "environment variable auth.env_var, not from a credential-domain entry; remove key_name")
        if self.mode != "env" and self.env_var:
            raise ValueError(f"auth.env_var is only read when auth.mode is 'env' (it is '{self.mode}')")
        if self.env_var is not None and not ENV_VAR_PATTERN.fullmatch(self.env_var):
            raise ValueError(
                f"auth.env_var '{self.env_var}' is not an environment variable name "
                "(upper-case letters, digits and underscores, not starting with a digit)")
        return self


class ProviderBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    model_version: str  # the exact provider model string / deployment name
    endpoint: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    auth: AuthBlock = Field(default_factory=lambda: AuthBlock(mode="none"))

    @model_validator(mode="after")
    def _env_var_resolved(self) -> "ProviderBlock":
        # An env-mode definition always knows its variable after loading: the
        # one it names, else its adapter's default.
        if self.auth.mode == "env" and not self.auth.env_var:
            default = default_key_env(self.adapter)
            if not default:
                raise ValueError(
                    f"auth.env_var is required when auth.mode is 'env': adapter '{self.adapter}' has no "
                    "default key variable")
            self.auth.env_var = default
        return self


class RuntimeBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str  # score template family, e.g. "openai_chat"
    requirements_profile: str  # e.g. "api-wrapper", "hf-transformers"
    timeout_s: int = 60
    # Where a self-hosted model's weights come from.
    #   "baked"   - downloaded during the container build into /pybox/model/<id>
    #   "mounted" - staged once on the shared llm-weights volume and read from
    #               /pybox/model/mount/<id> at run time, so the image stays small
    #               and one copy of the weights serves every container and replica
    # Ignored for hosted (api-wrapper) models.
    weights_source: Literal["baked", "mounted"] = "baked"


class TagsBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_class: Literal["LLM", "SLM", "Embedding", "Decision"]
    license_class: Literal["Proprietary", "Open-Source"] = "Proprietary"
    provider_tag: str
    scr_sizing: Literal["small", "medium", "large"]
    extra: list[str] = Field(default_factory=list)

    def as_list(self) -> list[str]:
        return [self.size_class, self.license_class, self.provider_tag, self.scr_sizing, *self.extra]


class PricingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost_type: Literal["Tokens", "Seconds"] = "Tokens"
    # USD per single token (fact-sheet convention), not per million
    input_token_price: Optional[float] = None
    output_token_price: Optional[float] = None
    second_cost: Optional[float] = None


class MetadataBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    release_date: Optional[str] = None
    knowledge_cutoff: Optional[str] = None

    @field_validator("release_date", "knowledge_cutoff", mode="before")
    @classmethod
    def _dates_as_strings(cls, v):
        # Hand-edited YAML with unquoted ISO dates parses to datetime.date
        import datetime
        if isinstance(v, (datetime.date, datetime.datetime)):
            return v.isoformat()[:10]
        return v
    context_length: Optional[int] = None
    size: Optional[int] = None  # parameter count; None renders as '.' in the fact sheet
    deployment_type: Literal["API", "SCR"] = "API"
    pricing: PricingBlock = Field(default_factory=PricingBlock)
    # Decision kind: what the model accepts. None means the usual 255 options
    # and all three question types; a model that takes less (Tev1: 24 choice
    # options, Solar Decide: 26) says so here, and the Prompt Builder greys it
    # out for a template it cannot answer.
    max_options: Optional[int] = None
    question_types: Optional[list[Literal["choice", "noul", "score"]]] = None
    # A short markdown paragraph of measured results (accuracy, calibration,
    # latency, cost, one caution), rendered as the model card's Evaluation
    # section when set.
    evaluation: Optional[str] = None


class HuggingFaceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    gated: bool = False


class GenerationBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Files the user owns by hand; mdb generate skips and reports them
    overrides: list[str] = Field(default_factory=list)
    # Keeps a legacy score filename on migrated folders (default: <camelCase>Score.py)
    score_code_file: Optional[str] = None
    catalog_provenance: str = "manual entry"


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(1, alias="schema")
    kind: Literal["llm", "embedding", "decision"] = "llm"
    model_id: str
    display_name: str
    provider: ProviderBlock
    runtime: RuntimeBlock
    options: dict[str, OptionSpec] = Field(default_factory=dict)
    tags: TagsBlock
    metadata: MetadataBlock
    modeler: str = ""
    generation: GenerationBlock = Field(default_factory=GenerationBlock)

    @field_validator("model_id")
    @classmethod
    def _valid_model_id(cls, v: str) -> str:
        if not MODEL_ID_PATTERN.fullmatch(v):      # `match` let a line feed at the end through (`$`)
            raise ValueError(
                f"model_id '{v}' must be snake_case ([a-z0-9_], starting with a letter or digit) - "
                "it becomes the folder name, SAS Model Manager model name, fact-sheet key, "
                "container image name and ingress path."
            )
        return v

    # -- serialization ----------------------------------------------------

    FIELD_ORDER: ClassVar[list[str]] = [
        "schema", "kind", "model_id", "display_name", "provider", "runtime",
        "options", "tags", "metadata", "modeler", "generation",
    ]

    def to_yaml(self) -> str:
        data = self.model_dump(by_alias=True, exclude_none=True)
        ordered = {key: data[key] for key in self.FIELD_ORDER if key in data}
        return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100)

    def save(self, folder: Path) -> Path:
        target = folder / MANIFEST_FILENAME
        target.write_bytes(self.to_yaml().encode("utf-8"))
        return target


def load_manifest(folder: Path) -> ModelManifest:
    source = folder / MANIFEST_FILENAME
    if not source.is_file():
        raise FileNotFoundError(f"No {MANIFEST_FILENAME} in {folder}")
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    return ModelManifest.model_validate(data)


def export_json_schema() -> dict:
    schema = ModelManifest.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "SAS Agentic AI Accelerator model definition manifest"
    return schema
