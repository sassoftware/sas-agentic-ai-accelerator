# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fact-sheet CSV upsert.

The fact sheet is consumed by register-LLMs.py (model metadata enrichment)
and Load-Fact-Sheets.sas (CAS monitoring data). Managed rows are derived
from the manifest with model_id forced equal to the folder name, which makes
the historical key-typo class of bug unrepresentable. Legacy rows (models
without a manifest) are preserved byte-verbatim - only the row belonging to
the model being synced is ever touched.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from .manifest import ModelManifest

# Fact-sheet columns per kind, in order
COLUMNS_BY_KIND = {
    "llm": [
        "model_id", "model", "provider", "description", "release_date", "size",
        "deployment_type", "license", "cost_type", "input_token_price",
        "output_token_price", "second_cost", "context_length", "temperature",
        "top_p", "top_k", "max_tokens", "knowledge_cut_off",
    ],
    "embedding": [
        "model_id", "model", "provider", "description", "size",
        "deployment_type", "license", "cost_type", "input_token_price",
        "second_cost", "max_tokens", "embedding_length",
    ],
}
QUOTED_COLUMNS = {"model_id", "model", "provider", "description", "deployment_type", "license", "cost_type"}
NULL = "."


def _fmt_number(value: float | int | None) -> str:
    if value is None:
        return NULL
    if isinstance(value, int) or float(value) == int(value):
        return str(int(value))
    return f"{value:.12f}".rstrip("0").rstrip(".")


def _option_default(manifest: ModelManifest, name: str) -> str:
    spec = manifest.options.get(name)
    if spec is None:
        return NULL
    return _fmt_number(spec.default) if isinstance(spec.default, (int, float)) else str(spec.default)


def row_values(manifest: ModelManifest) -> dict[str, str]:
    md = manifest.metadata
    pricing = md.pricing
    common = {
        "model_id": manifest.model_id,
        "model": manifest.display_name,
        "provider": manifest.tags.provider_tag,
        "description": md.description,
        "size": _fmt_number(md.size),
        "deployment_type": md.deployment_type,
        "license": manifest.tags.license_class,
        "cost_type": pricing.cost_type,
        "input_token_price": _fmt_number(pricing.input_token_price),
        "second_cost": _fmt_number(pricing.second_cost),
    }
    if manifest.kind == "embedding":
        return {
            **common,
            "max_tokens": _option_default(manifest, "Input_Token_Limit"),
            "embedding_length": _option_default(manifest, "Embedding_Length"),
        }
    return {
        **common,
        "release_date": md.release_date or NULL,
        "output_token_price": _fmt_number(pricing.output_token_price),
        "context_length": _fmt_number(md.context_length),
        "temperature": _option_default(manifest, "temperature"),
        "top_p": _option_default(manifest, "top_p"),
        "top_k": _option_default(manifest, "top_k"),
        "max_tokens": _option_default(manifest, "max_tokens"),
        "knowledge_cut_off": md.knowledge_cutoff or NULL,
    }


def _format_row(values: dict[str, str], kind: str) -> str:
    fields = []
    for column in COLUMNS_BY_KIND[kind]:
        value = values.get(column, NULL)
        if column in QUOTED_COLUMNS:
            fields.append('"' + value.replace('"', '""') + '"')
        else:
            fields.append(value)
    return ",".join(fields)


def upsert_row(fact_sheet: Path, manifest: ModelManifest) -> str:
    """Insert or replace the manifest's row. Returns 'added', 'updated' or 'unchanged'."""
    raw = fact_sheet.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)
    trailing_empty = lines and lines[-1] == ""
    if trailing_empty:
        lines = lines[:-1]

    new_line = _format_row(row_values(manifest), manifest.kind)
    result = "added"
    out: list[str] = []
    replaced = False
    for index, line in enumerate(lines):
        if index == 0 or not line.strip():
            out.append(line)
            continue
        try:
            first_field = next(csv.reader(io.StringIO(line)))[0]
        except (StopIteration, IndexError):
            out.append(line)
            continue
        if first_field == manifest.model_id:
            replaced = True
            result = "unchanged" if line == new_line else "updated"
            out.append(new_line)
        else:
            out.append(line)
    if not replaced:
        out.append(new_line)

    content = newline.join(out)
    if trailing_empty or not replaced:
        content += newline
    if content != raw:
        fact_sheet.write_bytes(content.encode("utf-8"))
    return result


def read_row(fact_sheet: Path, model_id: str) -> dict[str, str] | None:
    with fact_sheet.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            if record.get("model_id") == model_id:
                return record
    return None
