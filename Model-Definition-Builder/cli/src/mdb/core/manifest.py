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


class AuthBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["api_key", "none"] = "api_key"
    # Must equal the options.json API_KEY default and the LLM_API_KEYS CAS-table KeyName
    key_name: Optional[str] = None

    @model_validator(mode="after")
    def _key_name_required(self) -> "AuthBlock":
        if self.mode == "api_key" and not self.key_name:
            raise ValueError("auth.key_name is required when auth.mode is 'api_key'")
        return self


class ProviderBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    model_version: str  # the exact provider model string / deployment name
    endpoint: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    auth: AuthBlock = Field(default_factory=lambda: AuthBlock(mode="none"))


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

    size_class: Literal["LLM", "SLM", "Embedding"]
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
    kind: Literal["llm", "embedding"] = "llm"
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
        if not MODEL_ID_PATTERN.match(v):
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
