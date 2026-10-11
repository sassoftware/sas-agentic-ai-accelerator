# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register Setup: what a finished ingestion leaves behind (design §5, §9).

An ingestion that nobody can consume or audit is half a feature. This module
turns a completed run into four artifacts:

* the **manifested retrieval model** - a copy of `retrieve_context.py` whose
  MANIFEST block names this setup's collection, embedding model, store and
  corpus version, so the deployed model no longer carries placeholders a
  human had to edit;
* **pipeline.yaml** - the configuration that produced the corpus, in the
  form the design treats as the setup's source of truth;
* **ingestion-manifest.json** - which documents are in the collection, at
  which content hash, from which run;
* **collection-manifest.json** plus the store DDL - what a DBA needs to
  pre-create or review the collection.

Everything here is a pure function over settings: no REST, no SAS, so it is
testable and the step and the RAG Builder can share one implementation.
"""
from __future__ import annotations

import datetime
import json
import re

MANIFEST_START = "# ---- MANIFEST: rewritten per RAG Setup"
MANIFEST_END = "# ----------------------------------------"

# order matters only for readability; the names must match retrieve_context.py
MANIFEST_STRINGS = ("BACKEND", "COLLECTION", "EMBED_MODEL", "EMBED_ENDPOINT",
                    "STORE_HOST", "STORE_PORT", "STORE_DB", "STORE_SSLMODE",
                    "CREDENTIAL_DOMAIN", "INGESTION_RUN_ID")
MANIFEST_NUMBERS = ("DEFAULT_K",)
# Written as a Python bool literal, `NAME = True`, and ONLY into a template
# whose block already holds the name. SSL_VERIFY is the default the retrieval
# model applies to its two HTTPS calls (the embedding container and the
# credentials service) when the destination sets neither RAGEMBED_SSLVERIFY
# nor RAGSTORE_SSLVERIFY. A content root deployed before the setting existed
# holds a retrieve_context.py without the constant and without the code that
# reads it; such a template still has to manifest, and it verifies. The
# Prompt Builder's rag-manifest.ts follows the same rule - keep the two in
# step (tests/test_manifest_parity.py compares them).
MANIFEST_OPTIONAL_BOOLEANS = ("SSL_VERIFY",)
REQUIRED = ("BACKEND", "COLLECTION", "EMBED_MODEL", "STORE_HOST", "STORE_DB")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifest_block(lines: list):
    """The first and the last line index of the MANIFEST block, or None."""
    start = -1
    for index, line in enumerate(lines):
        if start < 0 and line.startswith(MANIFEST_START):
            start = index
        elif start >= 0 and line.startswith(MANIFEST_END):
            return start, index
    return None


def _assigns(line: str, name: str) -> bool:
    """Whether a line of the block assigns this name (`NAME = ...`)."""
    return re.match(re.escape(name) + r"\s*=", line) is not None


def manifest_block_has(template: str, name: str) -> bool:
    """Whether the template's MANIFEST block holds a name.

    Asked for SSL_VERIFY: a template without it verifies TLS whatever the
    setting says, and the caller has to be able to say so.
    """
    lines = template.splitlines()
    found = _manifest_block(lines)
    return found is not None and any(
        _assigns(line, name) for line in lines[found[0] + 1:found[1]])


def manifested_ssl_verify(source: str) -> bool:
    """Whether a manifested retrieve_context.py verifies TLS by default.

    Read from the text that is stored, not from the setting that was asked
    for: False only when the block holds the line `SSL_VERIFY = False`, which
    is the one spelling retrieve_context.py switches verification off for. A
    copy without the line, or with any other value, verifies.
    """
    lines = source.splitlines()
    found = _manifest_block(lines)
    if found is None:
        return True
    return not any(re.match(r"SSL_VERIFY\s*=\s*False\s*(#.*)?$", line)
                   for line in lines[found[0] + 1:found[1]])


def render_retrieval_model(template: str, settings: dict) -> str:
    """Rewrite the MANIFEST block of retrieve_context.py for one setup.

    Refuses rather than guesses: a missing marker or a missing required
    setting raises, because the failure mode otherwise is a model deployed
    against `your-database-host` that only shows up when a decision runs.

    SSL_VERIFY is the one OPTIONAL name of the block: it is written when the
    template has it and left out when the template does not (see
    MANIFEST_OPTIONAL_BOOLEANS). `manifest_block_has(template, "SSL_VERIFY")`
    tells a caller which of the two happened.
    """
    missing = [key for key in REQUIRED if not str(settings.get(key) or "").strip()]
    if missing:
        raise ValueError("cannot manifest the retrieval model without "
                         + ", ".join(missing))
    lines = template.splitlines()
    found = _manifest_block(lines)
    if found is None:
        raise ValueError("the retrieval template has no MANIFEST block - it is "
                         "not the accelerator's retrieve_context.py")
    start, end = found
    template_block = lines[start + 1:end]

    block = [lines[start]]
    for key in MANIFEST_STRINGS:
        value = str(settings.get(key, "") or "")
        if '"' in value or "\n" in value:
            raise ValueError(f"{key} may not contain quotes or newlines: {value!r}")
        block.append(f'{key} = "{value}"')
    for key in MANIFEST_NUMBERS:
        block.append(f"{key} = {int(settings.get(key) or 4)}")
    for key in MANIFEST_OPTIONAL_BOOLEANS:
        # only the literal False writes False: anything else keeps the safe value
        if any(_assigns(line, key) for line in template_block):
            block.append(f"{key} = {'False' if settings.get(key) is False else 'True'}")
    block.append(lines[end])
    return "\n".join(lines[:start] + block + lines[end + 1:]) + "\n"


def pipeline_yaml(settings: dict) -> str:
    """The setup's configuration, readable and diffable.

    Hand-written rather than via a yaml library: rag_core stays
    dependency-light because it is distributed as a SAS Content folder, not
    a wheel.
    """
    def scalar(value):
        text = "" if value is None else str(value)
        return text if text and all(c not in text for c in ": #'\"\n") else json.dumps(text)

    rows = [
        "# Generated by RAG - Register Setup. The RAG Setup owns this file;",
        "# the Studio Flow and the scheduled job are generated FROM it.",
        f"generated_at: {scalar(_now())}",
        f"setup: {scalar(settings.get('setup'))}",
        "source:",
        f"  kind: {scalar(settings.get('source_kind'))}",
        f"  location: {scalar(settings.get('source'))}",
        "tables:",
        f"  project: {scalar(settings.get('rag_project'))}",
        f"  caslib: {scalar(settings.get('tables_caslib'))}",
        "chunking:",
        f"  chunker: {scalar(settings.get('chunker'))}",
        f"  input_token_limit: {scalar(settings.get('input_token_limit'))}",
        f"  overlap_tokens: {scalar(settings.get('overlap_tokens'))}",
        "embedding:",
        f"  model: {scalar(settings.get('embed_model'))}",
        f"  endpoint: {scalar(settings.get('EMBED_ENDPOINT'))}",
        f"  dimensions: {scalar(settings.get('dims'))}",
        "store:",
        f"  backend: {scalar(settings.get('BACKEND'))}",
        f"  collection: {scalar(settings.get('COLLECTION'))}",
        f"  host: {scalar(settings.get('STORE_HOST'))}",
        f"  port: {scalar(settings.get('STORE_PORT'))}",
        f"  database: {scalar(settings.get('STORE_DB'))}",
        f"  sslmode: {scalar(settings.get('STORE_SSLMODE'))}",
        f"  credential_domain: {scalar(settings.get('CREDENTIAL_DOMAIN'))}",
        "run:",
        f"  pipeline_version: {scalar(settings.get('pipeline_version'))}",
        f"  configuration: {scalar(settings.get('config_id'))}",
        f"  ingestion_run_id: {scalar(settings.get('INGESTION_RUN_ID'))}",
        "retrieval:",
        f"  default_k: {scalar(settings.get('DEFAULT_K') or 4)}",
    ]
    return "\n".join(rows) + "\n"


def ingestion_manifest(settings: dict, ledger_rows: list) -> str:
    """Which documents this collection contains, and from which run."""
    documents = []
    counts: dict = {}
    for row in ledger_rows:
        if row.get("doc_id") == "__run_lock__":
            continue
        status = str(row.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
        documents.append({
            "doc_id": row.get("doc_id"),
            "source_uri": row.get("source_uri"),
            "source_kind": row.get("source_kind"),
            "content_hash": row.get("content_hash"),
            "chunk_count": int(float(row.get("chunk_count") or 0)),
            "status": status,
            "run_id": row.get("run_id"),
            "updated_at": row.get("updated_at"),
        })
    return json.dumps({
        "generated_at": _now(),
        "setup": settings.get("setup"),
        "collection": settings.get("COLLECTION"),
        "pipeline_version": settings.get("pipeline_version"),
        "configuration": settings.get("config_id"),
        "ingestion_run_id": settings.get("INGESTION_RUN_ID"),
        "document_counts": counts,
        "documents": sorted(documents, key=lambda d: str(d["source_uri"])),
    }, indent=2) + "\n"


def collection_manifest(settings: dict, ddl: str = "") -> str:
    """What a DBA needs to pre-create or review the collection."""
    return json.dumps({
        "generated_at": _now(),
        "setup": settings.get("setup"),
        "backend": settings.get("BACKEND"),
        "collection": settings.get("COLLECTION"),
        "dimensions": int(settings.get("dims") or 0),
        "metric": settings.get("metric") or "cosine",
        "embedding_model": settings.get("EMBED_MODEL"),
        "host": settings.get("STORE_HOST"),
        "database": settings.get("STORE_DB"),
        "credential_domain": settings.get("CREDENTIAL_DOMAIN"),
        "history": {
            "retires_previous_generations": True,
            "readable_as_of_a_past_date": True,
            "note": "chunk rows carry valid_from/valid_to; retrieval reads the "
                    "live slice only",
        },
        "ddl": ddl,
    }, indent=2) + "\n"


def settings_from_inventory(rows: list, extra: dict) -> dict:
    """Assemble Register Setup's settings from what the flow already carries.

    The inventory knows the project, caslib, run id and the accumulated
    configuration (chunker, model, backend, collection); only the store
    connection and the retrieval defaults have to be supplied. Anything in
    `extra` wins, so a caller can override.
    """
    settings: dict = {}
    for row in rows:
        if row.get("doc_id") == "__run_lock__":
            continue
        config = {}
        if row.get("config_json"):
            try:
                config = json.loads(row["config_json"])
            except Exception:
                config = {}
        settings.update({
            "rag_project": row.get("rag_project"),
            "tables_caslib": row.get("tables_caslib"),
            "pipeline_version": row.get("pipeline_version"),
            "config_id": row.get("config_hash"),
            "INGESTION_RUN_ID": row.get("run_id"),
            "source_kind": config.get("source_kind"),
            "chunker": config.get("chunker"),
            "input_token_limit": config.get("input_token_limit"),
            "overlap_tokens": config.get("overlap_tokens"),
            "embed_model": config.get("embed_model"),
            "EMBED_MODEL": config.get("embed_model"),
            "BACKEND": config.get("backend"),
            "COLLECTION": config.get("collection"),
        })
        break
    settings.update({key: value for key, value in extra.items()
                     if value not in (None, "")})
    return settings
