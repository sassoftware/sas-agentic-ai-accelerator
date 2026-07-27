# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Step implementations for the RAG ingestion custom steps (design §2).

The .step files are THIN SHELLS: a bootstrap that sys.path-inserts rag_core
and one call into this module. All logic lives here so sites iterate by
updating the SAS Content folder — never by re-importing steps (OQ2/OQ11).

Data crosses steps as tables; inside a step we work on pandas DataFrames
(pandas ships in the proc-python environment). Every function follows the
per-document failure contract: a document failure marks its ledger row
`failed` with `error_text` and NEVER raises out of the step.

Ledger columns: doc_id, source_uri, source_kind, content_hash, mtime,
status (new|changed|unchanged|deleted|failed), error_text, pipeline_version,
config_hash, chunk_count, run_id, updated_at.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

from .chunkers import CHUNKERS
from .schema import Chunk, link_neighbors
from .tokens import token_budget

LEDGER_COLUMNS = ["doc_id", "source_uri", "source_kind", "content_hash", "mtime",
                  "status", "error_text", "pipeline_version", "config_hash",
                  "chunk_count", "run_id", "updated_at"]

ELEMENT_COLUMNS = ["doc_id", "type", "text", "level", "page", "heading_path"]

CHUNK_COLUMNS = ["chunk_id", "doc_id", "source_uri", "chunk_index", "content",
                 "content_hash", "extractor", "pipeline_version", "ingested_at",
                 "span", "heading_path", "tags", "prev_id", "next_id",
                 "context_header", "entities", "relations", "embedding"]

_TEXT_SUFFIXES = None  # populated lazily from the registry


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stream_sha256(path: str, chunk_bytes: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_bytes), b""):
            digest.update(block)
    return digest.hexdigest()


def _doc_id(source_uri: str) -> str:
    return hashlib.sha1(source_uri.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# RAG - List Documents
# ---------------------------------------------------------------------------
def run_list(source_path: str, ledger_rows: list, run_id: str,
             pipeline_version: str, config_hash: str,
             include_suffixes=None, log=print) -> list:
    """Crawl a filesystem path, hash streaming, diff against the ledger.

    ledger_rows: list of dicts (previous ledger state). Returns the NEW
    inventory rows (one per discovered or disappeared document).
    """
    previous = {row["doc_id"]: row for row in ledger_rows}
    seen: set = set()
    inventory: list = []
    now = _now()

    if not os.path.isdir(source_path):
        raise ValueError(f"source path is not a directory visible from this "
                         f"compute context: {source_path!r}")

    for root, _dirs, files in os.walk(source_path):
        for filename in sorted(files):
            full = os.path.join(root, filename)
            suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
            if include_suffixes and suffix not in include_suffixes:
                continue
            doc_id = _doc_id(full)
            seen.add(doc_id)
            row = {"doc_id": doc_id, "source_uri": full, "source_kind": "path",
                   "mtime": str(os.path.getmtime(full)), "error_text": "",
                   "pipeline_version": pipeline_version, "config_hash": config_hash,
                   "chunk_count": 0, "run_id": run_id, "updated_at": now}
            try:
                row["content_hash"] = _stream_sha256(full)
            except OSError as exc:
                row.update(status="failed", content_hash="",
                           error_text=f"unreadable: {exc}")
                inventory.append(row)
                continue
            old = previous.get(doc_id)
            if old is None:
                row["status"] = "new"
            elif old.get("status") == "failed":
                # failed docs re-enter the pipeline every run until they
                # succeed or disappear — "unchanged" would hide them forever
                row["status"] = "changed"
            elif old.get("content_hash") != row["content_hash"] \
                    or old.get("pipeline_version") != pipeline_version:
                row["status"] = "changed"
            else:
                row["status"] = "unchanged"
                row["chunk_count"] = old.get("chunk_count", 0)
            inventory.append(row)

    for doc_id, old in previous.items():
        if doc_id not in seen and old.get("status") != "deleted":
            gone = dict(old)
            gone.update(status="deleted", run_id=run_id, updated_at=now,
                        error_text="")
            inventory.append(gone)

    counts: dict = {}
    for row in inventory:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    log(f"rag list: {len(inventory)} documents ({counts})")
    return inventory


# ---------------------------------------------------------------------------
# RAG - Extract Text
# ---------------------------------------------------------------------------
def run_extract(inventory: list, registry, extractor_name=None, log=print) -> tuple:
    """Extract elements for every new/changed doc. Returns (elements, updated_inventory)."""
    elements: list = []
    updated: list = []
    for row in inventory:
        row = dict(row)
        if row["status"] not in ("new", "changed"):
            updated.append(row)
            continue
        try:
            if row.get("source_kind") == "path":
                with open(row["source_uri"], "rb") as fh:
                    data = fh.read()
            else:
                raise ValueError(f"unsupported source_kind {row.get('source_kind')!r} "
                                 "(content-uri fetch lands with the SAS Content source)")
            doc_elements, used = registry.extract(data, row["source_uri"],
                                                  extractor_name=extractor_name)
            for el in doc_elements:
                el = dict(el)
                el["doc_id"] = row["doc_id"]
                elements.append(el)
            row["extractor"] = used
            if not doc_elements:
                row.update(status="failed",
                           error_text="extractor returned no elements "
                                      "(empty or scanned-only document)")
        except Exception as exc:  # per-doc failure contract (§2)
            row.update(status="failed", error_text=str(exc)[:500])
            log(f"rag extract: FAILED {row['source_uri']}: {exc}")
        updated.append(row)
    done = sum(1 for r in updated if r.get("extractor") and r["status"] != "failed")
    failed = sum(1 for r in updated if r["status"] == "failed")
    log(f"rag extract: {done} extracted, {failed} failed")
    return elements, updated


# ---------------------------------------------------------------------------
# RAG - Chunk Documents
# ---------------------------------------------------------------------------
def run_chunk(elements: list, inventory: list, chunker: str, input_token_limit: int,
              pipeline_version: str, max_bytes: int = 24000,
              overlap_tokens: int = 30, margin: float = 0.85, log=print) -> list:
    """Chunk per document; returns canonical chunk dicts (embedding empty)."""
    if chunker not in CHUNKERS:
        raise ValueError(f"unknown chunker {chunker!r}; available: {sorted(CHUNKERS)}")
    chunk_fn = CHUNKERS[chunker]
    budget = token_budget(input_token_limit, margin)
    by_doc: dict = {}
    for el in elements:
        by_doc.setdefault(el["doc_id"], []).append(el)
    row_by_doc = {row["doc_id"]: row for row in inventory}

    all_chunks: list = []
    now = _now()
    for doc_id, doc_elements in by_doc.items():
        row = row_by_doc.get(doc_id, {})
        if row.get("status") == "failed":
            continue
        heading = None
        texts: list = []
        for el in doc_elements:
            if el.get("heading_path"):
                heading = el["heading_path"]
            if el.get("type") != "heading" and el.get("text"):
                texts.append(el["text"])
        kwargs = {"max_tokens": budget, "max_bytes": max_bytes}
        if chunk_fn is CHUNKERS["recursive"]:
            kwargs["overlap_tokens"] = overlap_tokens
        contents = chunk_fn(texts, **kwargs)
        chunks = [
            Chunk.build(doc_id, row.get("source_uri", ""), i, content,
                        row.get("content_hash", ""), row.get("extractor", ""),
                        pipeline_version, now,
                        heading_path=heading if len(by_doc) else None)
            for i, content in enumerate(contents)
        ]
        link_neighbors(chunks)
        row["chunk_count"] = len(chunks)
        all_chunks.extend(c.__dict__ for c in chunks)
    log(f"rag chunk: {len(all_chunks)} chunks from {len(by_doc)} documents "
        f"(budget {budget} tokens)")
    return all_chunks


# ---------------------------------------------------------------------------
# RAG - Embed Chunks
# ---------------------------------------------------------------------------
def run_embed(chunks: list, client, already_embedded: set = frozenset(),
              max_workers: int = 8, checkpoint_every: int = 1000,
              checkpoint=None, log=print) -> tuple:
    """Embed every chunk not already in the checkpoint set.

    checkpoint: optional callable(batch_of_chunks) invoked every
    `checkpoint_every` completions (the step persists to the CAS staging
    table). Returns (embedded_chunks, failed_chunk_ids).
    """
    todo = [c for c in chunks if c["chunk_id"] not in already_embedded]
    skipped = len(chunks) - len(todo)
    if skipped:
        log(f"rag embed: {skipped} chunks already embedded (checkpoint), "
            f"{len(todo)} to go")

    embedded: list = []
    failed: list = []
    pending_checkpoint: list = []

    def work(chunk):
        text = chunk["content"]
        if chunk.get("context_header"):
            text = chunk["context_header"] + "\n" + text
        return chunk, client.embed(text, mode="document")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [(chunk, pool.submit(work, chunk)) for chunk in todo]
        for chunk, future in futures:
            try:
                _, vector = future.result()
                done = dict(chunk)
                done["embedding"] = vector
                embedded.append(done)
                pending_checkpoint.append(done)
                if checkpoint and len(pending_checkpoint) >= checkpoint_every:
                    checkpoint(list(pending_checkpoint))
                    pending_checkpoint.clear()
            except Exception as exc:
                failed.append((chunk["chunk_id"], str(exc)[:300]))
    if checkpoint and pending_checkpoint:
        checkpoint(list(pending_checkpoint))
    log(f"rag embed: {len(embedded)} embedded, {len(failed)} failed, "
        f"usage {client.usage}")
    return embedded, failed


# ---------------------------------------------------------------------------
# RAG - Load Vector Store
# ---------------------------------------------------------------------------
def run_load(embedded_chunks: list, inventory: list, adapter, collection: str,
             dims: int, pipeline_version: str, sparse: bool = False,
             log=print) -> list:
    """Upsert-first-then-delete-stale per document; tombstone deleted docs.

    Returns the updated inventory (per-doc statuses -> ingested/failed).
    """
    adapter.ensure_collection(collection, dims, schema={"sparse": sparse})
    by_doc: dict = {}
    for chunk in embedded_chunks:
        by_doc.setdefault(chunk["doc_id"], []).append(chunk)

    updated: list = []
    loaded = failed = removed = 0
    for row in inventory:
        row = dict(row)
        status = row.get("status")
        try:
            if status == "deleted":
                adapter.delete(collection, filter={"doc_id": row["doc_id"]})
                removed += 1
            elif status in ("new", "changed"):
                chunks = by_doc.get(row["doc_id"], [])
                if not chunks:
                    row.update(status="failed",
                               error_text=row.get("error_text")
                               or "no embedded chunks reached the load step")
                    failed += 1
                else:
                    adapter.upsert(collection, chunks)          # new first (§2)
                    adapter.delete(collection, filter={          # stale after
                        "doc_id": row["doc_id"],
                        "pipeline_version": {"$ne": pipeline_version}})
                    stale_hash = {"doc_id": row["doc_id"],
                                  "content_hash": {"$ne": row["content_hash"]}}
                    adapter.delete(collection, filter=stale_hash)
                    row["status"] = "ingested"
                    loaded += 1
        except Exception as exc:
            row.update(status="failed", error_text=str(exc)[:500])
            failed += 1
            log(f"rag load: FAILED doc {row['doc_id']}: {exc}")
        row["updated_at"] = _now()
        updated.append(row)
    if adapter.needs_flush:
        adapter.flush(collection)
    log(f"rag load: {loaded} docs loaded, {removed} removed, {failed} failed "
        f"-> collection '{collection}' now {adapter.count(collection)} chunks")
    return updated


def merge_ledger(previous_rows: list, updated_inventory: list) -> list:
    """New ledger state: updated rows win by doc_id; untouched history survives."""
    merged = {row["doc_id"]: dict(row) for row in previous_rows}
    for row in updated_inventory:
        base = merged.get(row["doc_id"], {})
        base.update(row)
        # ingested docs carry no stale error text forward
        if base.get("status") == "ingested":
            base["error_text"] = ""
        merged[base["doc_id"]] = base
    normalized = []
    for row in merged.values():
        for column in LEDGER_COLUMNS:
            row.setdefault(column, "" if column not in ("chunk_count",) else 0)
        normalized.append(row)
    return sorted(normalized, key=lambda r: r["source_uri"])


def config_hash(pipeline_config: dict) -> str:
    """Stable hash of the pipeline parameters (the §2 drift guard)."""
    canonical = json.dumps(pipeline_config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
