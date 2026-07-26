# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fact-sheet CSV upsert.

The fact sheet is consumed by `mdb register` (model metadata enrichment)
and Load-Fact-Sheets.sas (CAS monitoring data). Managed rows are derived
from the manifest with model_id forced equal to the folder name, which makes
the historical key-typo class of bug unrepresentable. Legacy rows (models
without a manifest) are preserved byte-verbatim - only the row belonging to
the model being synced is ever touched.
"""
from __future__ import annotations

import csv
import io
import time
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


def _write_bytes_retrying(path: Path, data: bytes) -> None:
    # Windows indexers/AV can hold the file briefly - retry transient errors
    for attempt in range(5):
        try:
            path.write_bytes(data)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.5)


def _legacy_lines(fact_sheet: Path, managed_ids: set[str]) -> tuple[list[str], str, bool]:
    """Return (rows whose model_id is not managed, newline convention, existed).

    Used to preserve hand-maintained rows that have no definition folder. When
    the sheet does not exist yet, returns ([], "\\n", False).
    """
    if not fact_sheet.exists():
        return [], "\n", False
    raw = fact_sheet.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    kept: list[str] = []
    # splitlines() tolerates a sheet with mixed \n / \r\n line endings
    for index, line in enumerate(raw.splitlines()):
        if index == 0 or not line.strip():
            continue
        try:
            first_field = next(csv.reader(io.StringIO(line)))[0]
        except (StopIteration, IndexError):
            continue
        if first_field not in managed_ids:
            kept.append(line)
    return kept, newline, True


def _format_row(values: dict[str, str], kind: str) -> str:
    fields = []
    for column in COLUMNS_BY_KIND[kind]:
        # A record must stay one physical line - the upsert works line-wise
        value = " ".join(values.get(column, NULL).split())
        if column in QUOTED_COLUMNS or "," in value or '"' in value:
            fields.append('"' + value.replace('"', '""') + '"')
        else:
            fields.append(value)
    return ",".join(fields)


def upsert_row(fact_sheet: Path, manifest: ModelManifest) -> str:
    """Insert or replace the manifest's row. Returns 'added', 'updated' or 'unchanged'."""
    # read_bytes avoids universal-newline translation, so CRLF sheets stay CRLF
    raw = fact_sheet.read_bytes().decode("utf-8")
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
        # Insert in model_id order (the same order rebuild_sheet produces), so
        # an add never desyncs the committed sheet from a fresh rebuild.
        insert_at = len(out)
        for index, line in enumerate(out):
            if index == 0 or not line.strip():
                continue
            try:
                first_field = next(csv.reader(io.StringIO(line)))[0]
            except (StopIteration, IndexError):
                continue
            if first_field > manifest.model_id:
                insert_at = index
                break
        out.insert(insert_at, new_line)

    content = newline.join(out)
    if trailing_empty or not replaced:
        content += newline
    if content != raw:
        _write_bytes_retrying(fact_sheet, content.encode("utf-8"))
    return result


def remove_row(fact_sheet: Path, model_id: str) -> str:
    """Delete a model's row from the fact sheet (used when a model is archived
    out of the active set). Returns 'removed' or 'absent'. Only the matching row
    is touched; every other line stays byte-verbatim."""
    raw = fact_sheet.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)
    trailing_empty = lines and lines[-1] == ""
    if trailing_empty:
        lines = lines[:-1]
    out: list[str] = []
    removed = False
    for index, line in enumerate(lines):
        if index == 0 or not line.strip():
            out.append(line)
            continue
        try:
            first_field = next(csv.reader(io.StringIO(line)))[0]
        except (StopIteration, IndexError):
            out.append(line)
            continue
        if first_field == model_id:
            removed = True
            continue
        out.append(line)
    if not removed:
        return "absent"
    content = newline.join(out)
    if trailing_empty:
        content += newline
    _write_bytes_retrying(fact_sheet, content.encode("utf-8"))
    return "removed"


def rebuild_sheet(
    fact_sheet: Path, manifests: list[ModelManifest], keep_legacy: bool = True
) -> dict[str, int | bool]:
    """Regenerate the whole fact sheet from ``manifests`` (all of one kind).

    Managed rows are derived fresh from each manifest and sorted by model_id, so
    the sheet becomes a pure function of the definitions - no hand editing needed.
    Rows in the existing sheet whose model_id has no manifest ("legacy" models,
    hand-maintained without a definition folder) are kept verbatim after the
    managed rows when ``keep_legacy`` is True, otherwise dropped. The file is
    created if it does not exist. Returns a summary dict with the counts.
    """
    if not manifests:
        raise ValueError("rebuild_sheet requires at least one manifest")
    kinds = {m.kind for m in manifests}
    if len(kinds) != 1:
        # A mixed list would silently render one kind against the other's columns
        raise ValueError(f"rebuild_sheet requires manifests of a single kind, got: {sorted(kinds)}")
    kind = manifests[0].kind
    header = ",".join(COLUMNS_BY_KIND[kind])
    managed_ids = {m.model_id for m in manifests}
    managed_lines = [
        _format_row(row_values(m), kind)
        for m in sorted(manifests, key=lambda m: m.model_id)
    ]

    legacy, newline, existed = _legacy_lines(fact_sheet, managed_ids)
    kept = legacy if keep_legacy else []
    content = newline.join([header, *managed_lines, *kept]) + newline
    _write_bytes_retrying(fact_sheet, content.encode("utf-8"))
    return {
        "written": len(managed_lines),
        "legacy_kept": len(kept),
        "legacy_dropped": 0 if keep_legacy else len(legacy),
        "created": not existed,
    }


def read_row(fact_sheet: Path, model_id: str) -> dict[str, str] | None:
    with fact_sheet.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            if record.get("model_id") == model_id:
                return record
    return None
