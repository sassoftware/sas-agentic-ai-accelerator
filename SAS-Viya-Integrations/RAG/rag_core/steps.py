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
status (new|changed|unchanged|deleted|skipped|failed|ingested), error_text,
pipeline_version, config_hash, chunk_count, run_id, updated_at.

`skipped` and `failed` are deliberately different states. Skipped means the
pipeline decided not to ingest this document and nothing is wrong: an image,
a source file with code ingestion off, a scanned PDF with no text layer.
Failed means something broke that a person can fix. Collapsing the two - which
is what this pipeline did until 2026-08-01 - makes every run look broken and
hides the rows that are.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from .chunkers import (CHUNKERS, document_text, element_pages, locate,
                       page_at)
from .extractors.builtin import CODE_SUFFIXES
from .schema import Chunk, link_neighbors
from .sources import FileSystemSource
from .tokens import token_budget

LEDGER_COLUMNS = ["doc_id", "source_uri", "source_kind", "content_hash", "mtime",
                  "status", "error_text", "pipeline_version", "config_hash",
                  "chunk_count", "run_id", "updated_at"]

# What travels between the steps. The ledger persists LEDGER_COLUMNS only;
# the extra three ride along the flow so every step after List Documents
# knows which extractor ran and WHERE the pipeline tables live - nobody
# should have to retype the project name into every step.
INVENTORY_COLUMNS = LEDGER_COLUMNS + ["extractor", "rag_project",
                                      "tables_caslib", "config_json",
                                      "embed_usage", "enrich_usage"]

ELEMENT_COLUMNS = ["doc_id", "type", "text", "level", "page", "heading_path"]

CHUNK_COLUMNS = ["chunk_id", "doc_id", "source_uri", "chunk_index", "content",
                 "content_hash", "extractor", "pipeline_version", "ingested_at",
                 "span", "heading_path", "tags", "prev_id", "next_id",
                 "context_header", "attributes", "enrich_version",
                 "entities", "relations", "embedding"]

# Every column the accelerator ships carries a label: the steps put them on
# the SAS output tables and on the promoted CAS tables, so a table opened in
# SAS Studio or Visual Analytics reads without the schema at hand.
COLUMN_LABELS = {
    # ledger / inventory
    "doc_id": "Document ID",
    "source_uri": "Source location",
    "source_kind": "Source type",
    "content_hash": "Content fingerprint",
    "mtime": "Source last modified",
    "status": "Ingestion status",
    "error_text": "Failure reason",
    "pipeline_version": "Pipeline version",
    "config_hash": "Configuration fingerprint",
    "chunk_count": "Chunks produced",
    "run_id": "Ingestion run ID",
    "updated_at": "Row updated (UTC)",
    "extractor": "Text extractor used",
    "rag_project": "RAG project",
    "tables_caslib": "Pipeline tables caslib",
    "config_json": "Pipeline configuration (JSON)",
    "embed_usage": "Embedding usage (JSON)",
    "enrich_usage": "Enrichment usage (JSON)",
    # elements
    "type": "Element type",
    "text": "Element text",
    "level": "Heading level",
    "page": "Page number",
    "heading_path": "Heading path",
    # chunks
    "chunk_id": "Chunk ID",
    "chunk_index": "Chunk number in document",
    "content": "Chunk text",
    "ingested_at": "Ingested (UTC)",
    "span": "Source span (JSON)",
    "tags": "Tags (JSON)",
    "prev_id": "Previous chunk ID",
    "next_id": "Next chunk ID",
    "context_header": "Context header",
    "attributes": "Extracted attributes (JSON)",
    "enrich_version": "Enriched by (prompt@version)",
    "entities": "Entities (JSON)",
    "relations": "Relations (JSON)",
    "embedding": "Embedding vector (JSON)",
    # registration report
    "registered": "Registered item",
    "name": "Name",
    "location": "Location",
    "detail": "Detail",
    # load report
    "chunks_loaded": "Chunks written to the vector store",
    "chunks_deleted": "Stale chunks removed",
    "collection": "Vector store collection",
    "load_status": "Load status",
    # retrieval results
    "question": "Question",
    "rank": "Rank (1 = closest)",
    "filename": "Source file",
    "span_start": "Start offset in document",
    "span_end": "End offset in document",
    "ingestion_timestamp": "Chunk ingested (UTC)",
    "corpus_run_id": "Corpus version (ingestion run)",
    "distance": "Distance (lower = closer)",
    "score": "Score (higher = better)",
    # purge report
    "chunks_removed": "Chunk rows removed (live and retired)",
    "ledger_removed": "Ledger entry removed",
    "outcome": "Outcome",
}


def column_labels(columns) -> dict:
    """Labels for the given columns, skipping any that have none."""
    return {column: COLUMN_LABELS[column] for column in columns
            if column in COLUMN_LABELS}


# ---------------------------------------------------------------------------
# Pipeline configuration: accumulated along the flow, hashed at the end
# ---------------------------------------------------------------------------
def merge_config(existing, additions: dict) -> str:
    """Add this step's settings to the configuration travelling in the inventory.

    No single step knows the whole pipeline configuration - the chunker lives
    in one step, the embedding model in another - so each contributes its own
    part and the Load step hashes the total. That accumulated value is what
    makes both the drift guard and per-chunk `config_id` possible.
    """
    config = {}
    if existing:
        try:
            parsed = json.loads(existing) if isinstance(existing, str) else dict(existing)
            if isinstance(parsed, dict):
                config.update(parsed)
        except Exception:
            pass                      # unreadable history never fails a run
    for key, value in additions.items():
        if value not in (None, ""):
            config[str(key)] = value
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def stamp_usage(rows: list, usage) -> list:
    """Carry the embedding cost forward to the step that records history.

    The EmbeddingClient accumulates calls, tokens and seconds in the Embed
    step, and the Load step is where a run is written to rag_runs - so the
    numbers have to travel between them. They ride in their OWN column
    rather than in config_json, because anything added there changes the
    configuration fingerprint and would make every run look like drift.
    """
    payload = json.dumps(usage or {}, sort_keys=True, separators=(",", ":"))
    for row in rows:
        row["embed_usage"] = payload
    return rows


#: The enrichment measurements that ADD UP across several prompts.
_ENRICH_TALLIES = ("calls", "input_tokens", "output_tokens", "run_time", "failed")


def stamp_enrich_usage(rows: list, usage, model: str = "",
                       attributes: dict = None) -> list:
    """The same for the Enrich stage's LLM calls, ACCUMULATED.

    Its own column for the same reason: the enrichment tally is a MEASUREMENT
    of a run, and anything written into config_json becomes part of the
    configuration fingerprint - which would make every run look like drift.
    The LLM's name rides along here rather than in the configuration because
    it is read off the prompt's score code at run time, not set on the setup.

    It MERGES with whatever is already on the rows, because a flow may chain
    several `RAG - Enrich Chunks` steps and each one stamps as it finishes.
    Overwriting would have cost more than a wrong number: the `attributes` map
    is how the Load step learns which columns to add, so the second step would
    have silently dropped the first step's columns and every value in them.
    """
    previous: dict = {}
    for row in rows:
        raw = row.get("enrich_usage")
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            parsed = {}          # unreadable history never fails a run
        if isinstance(parsed, dict) and parsed:
            previous = parsed
            break

    payload = dict(previous)
    fresh = dict(usage or {})
    for key in _ENRICH_TALLIES:
        if key not in fresh and key not in previous:
            continue
        total = _as_number(previous.get(key)) + _as_number(fresh.get(key))
        payload[key] = total if key == "run_time" else int(total)
    for key, value in fresh.items():
        payload.setdefault(key, value)
    if model:
        # every LLM this run's enrichment called, in the order it called them
        called = [name for name in str(previous.get("model") or "").split(",")
                  if name.strip()]
        if model not in called:
            called.append(model)
        payload["model"] = ",".join(called)
    if attributes:
        # The columns the Enrich step's outputs need. They travel here because
        # the LOAD step is what holds the collection open, and it is the only
        # stage that can add a column - but the ENRICH step is the only one
        # that knows which outputs a setup stores.
        columns = dict(previous.get("attributes") or {})
        columns.update(attributes)
        payload["attributes"] = columns
    for row in rows:
        row["enrich_usage"] = json.dumps(payload, sort_keys=True,
                                         separators=(",", ":"))
    return rows


def _as_number(value) -> float:
    """A tally that arrived as text, or as nothing at all."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def stamp_config(rows: list, additions: dict) -> list:
    """merge_config over every inventory row, in place."""
    for row in rows:
        row["config_json"] = merge_config(row.get("config_json"), additions)
    return rows


def check_config_drift(ledger_rows: list, config_id: str,
                       pipeline_version: str) -> str:
    """Refuse to mix two pipeline configurations into one collection.

    Changing the chunker or the embedding model changes what a chunk IS, so a
    corpus half-processed each way retrieves unpredictably. Bumping the
    pipeline version is the sanctioned way to make that change: it re-ingests
    everything, so the collection ends up consistent. Returns an empty string
    when the run may proceed, otherwise the reason it may not.
    """
    previous = {(row.get("config_hash"), row.get("pipeline_version"))
                for row in ledger_rows
                if row.get("config_hash") and row.get("doc_id") != "__run_lock__"}
    if not previous:
        return ""
    if any(existing == config_id for existing, _ in previous):
        return ""
    if all(str(version) != str(pipeline_version) for _, version in previous):
        return ""                     # the version was bumped: re-ingest is intended
    was = sorted({version for _, version in previous})
    return ("the pipeline configuration changed since the last ingestion of "
            "this ledger, but the pipeline version is still "
            + str(pipeline_version) + " (previously " + ", ".join(map(str, was))
            + "). Bump the pipeline version to re-ingest the corpus with the "
              "new configuration, or restore the previous settings")


_TEXT_SUFFIXES = None  # populated lazily from the registry


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _doc_id(source_uri: str) -> str:
    return hashlib.sha1(source_uri.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# RAG - List Documents
# ---------------------------------------------------------------------------
def run_list(source, ledger_rows: list, run_id: str,
             pipeline_version: str, config_hash: str,
             include_suffixes=None, include_code: bool = False, log=print) -> list:
    """Crawl a document source, fingerprint it, diff against the ledger.

    `source` is a source object from rag_core.sources (filesystem or SAS
    Content); a plain string is taken as a filesystem path. ledger_rows is
    the previous ledger state. Returns the NEW inventory rows (one per
    discovered or disappeared document).

    `include_code` opts the setup into source files (CODE_SUFFIXES). They are
    skipped by default: a documents folder that happens to sit inside a
    project would otherwise ingest its own build scripts. Skipped documents
    are still LISTED, with the reason, because a document silently missing
    from a collection is the failure mode that costs the most to diagnose.
    """
    if isinstance(source, str):
        source = FileSystemSource(source)
    previous = {row["doc_id"]: row for row in ledger_rows}
    seen: set = set()
    inventory: list = []
    now = _now()

    for entry in source.entries(include_suffixes):
        full = entry["source_uri"]
        doc_id = _doc_id(full)
        seen.add(doc_id)
        row = {"doc_id": doc_id, "source_uri": full,
               "source_kind": entry.get("source_kind", source.kind),
               "mtime": entry.get("mtime", ""), "error_text": "",
               "pipeline_version": pipeline_version, "config_hash": config_hash,
               "chunk_count": 0, "run_id": run_id, "updated_at": now}
        suffix = ("." + full.rsplit(".", 1)[-1].lower()) if "." in full else ""
        if not include_code and suffix in CODE_SUFFIXES:
            # decided before fingerprinting: there is no reason to read a file
            # this run will not ingest
            row.update(status="skipped", content_hash="",
                       error_text=f"code file ({suffix}) - enable 'include code "
                                  "files' on the setup to ingest it as text")
            inventory.append(row)
            continue
        try:
            row["content_hash"] = source.fingerprint(entry)
        except Exception as exc:  # unreadable file, files-service error, ...
            row.update(status="failed", content_hash="",
                       error_text=f"unreadable: {exc}"[:500])
            inventory.append(row)
            continue
        old = previous.get(doc_id)
        if old is None:
            row["status"] = "new"
        elif old.get("status") in ("failed", "skipped"):
            # failed docs re-enter the pipeline every run until they
            # succeed or disappear — "unchanged" would hide them forever.
            # Skipped ones re-enter for the same reason: the skip was a
            # decision of the last run's configuration, not a property of
            # the document, so installing an extractor or ticking "include
            # code files" must be enough to pick it up.
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
    log_skipped(inventory, log)
    return inventory


def log_skipped(rows: list, log=print, limit: int = 25) -> None:
    """Name every skipped document, grouped by reason.

    A count alone ("2 skipped") tells someone that their corpus is incomplete
    without telling them which part - the one question they will then ask. So
    the reasons are grouped and the files listed under each, capped only so a
    folder of ten thousand images cannot bury the rest of the log.
    """
    by_reason: dict = {}
    for row in rows:
        if row.get("status") == "skipped":
            by_reason.setdefault(row.get("error_text") or "skipped",
                                 []).append(row.get("source_uri", ""))
    for reason, uris in by_reason.items():
        log(f"rag skipped: {len(uris)} - {reason}")
        for uri in uris[:limit]:
            log(f"    {uri}")
        if len(uris) > limit:
            log(f"    ... and {len(uris) - limit} more (see the ledger)")


def split_oversized_elements(elements: list, max_bytes: int = 24000) -> list:
    """Split element texts that would not fit a table column (design §2a).

    Compute-session character columns cap at 32767 bytes; elements travel
    between steps as table rows, so an oversized text (e.g. a 1 MB plaintext
    file extracting to ONE element) is split at paragraph/line boundaries
    into multiple elements. The chunker rejoins texts anyway, so the split
    only inserts an extra join boundary.
    """
    result: list = []
    for element in elements:
        text = element.get("text") or ""
        if len(text.encode("utf-8")) <= max_bytes:
            result.append(element)
            continue
        remaining = text
        while remaining:
            piece = remaining
            while len(piece.encode("utf-8")) > max_bytes:
                cut = max(piece.rfind("\n\n", 0, max_bytes // 2),
                          piece.rfind("\n", 0, max_bytes // 2),
                          piece.rfind(" ", 0, max_bytes // 2))
                piece = piece[:cut] if cut > 0 else piece[: max_bytes // 4]
            part = dict(element)
            part["text"] = piece
            result.append(part)
            remaining = remaining[len(piece):].lstrip("\n")
    return result


# ---------------------------------------------------------------------------
# RAG - Extract Text
# ---------------------------------------------------------------------------
def run_extract(inventory: list, registry, extractor_name=None, source=None,
                log=print) -> tuple:
    """Extract elements for every new/changed doc. Returns (elements, updated_inventory).

    `source` is the same source object the List step crawled; it reads the
    bytes for its own `source_kind`. Filesystem rows are readable without
    one, so the ingestion job can stay as it is.
    """
    elements: list = []
    updated: list = []
    newly_skipped: list = []     # listed here; List already listed its own
    for row in inventory:
        row = dict(row)
        if row["status"] not in ("new", "changed"):
            updated.append(row)
            continue
        try:
            if source is not None and row.get("source_kind") == getattr(source, "kind", None):
                data = source.read(row["source_uri"])
            elif row.get("source_kind") == "path":
                with open(row["source_uri"], "rb") as fh:
                    data = fh.read()
            else:
                raise ValueError(
                    f"the {row.get('source_kind')!r} source is not available in "
                    "this step - point its document source at the same location "
                    "the List Documents step used")
            if not registry.chain_for(row["source_uri"]) and not extractor_name:
                # Nothing can read this format. That is a property of the
                # corpus, not a fault of the run: an images folder next to the
                # documents is normal. Calling it "failed" trains the user to
                # ignore the word, so the row that actually broke stops being
                # visible.
                suffix = ("." + row["source_uri"].rsplit(".", 1)[-1].lower()
                          ) if "." in row["source_uri"] else "(no extension)"
                row.update(status="skipped",
                           error_text=f"no extractor for {suffix} files")
                updated.append(row)
                newly_skipped.append(row)
                continue
            doc_elements, used = registry.extract(data, row["source_uri"],
                                                  extractor_name=extractor_name)
            for el in split_oversized_elements(doc_elements):
                el = dict(el)
                el["doc_id"] = row["doc_id"]
                elements.append(el)
            row["extractor"] = used
            if not doc_elements:
                # the extractor ran and found nothing to index - an empty file
                # or a scanned-only PDF. Also not a failure: there is nothing
                # to fix, and the text-density note is the actionable part.
                row.update(status="skipped",
                           error_text=f"{used} found no text "
                                      "(empty or scanned-only document)")
                newly_skipped.append(row)
        except Exception as exc:  # per-doc failure contract (§2)
            row.update(status="failed", error_text=str(exc)[:500])
            log(f"rag extract: FAILED {row['source_uri']}: {exc}")
        updated.append(row)
    done = sum(1 for r in updated if r.get("extractor") and r["status"] not in ("failed", "skipped"))
    skipped = sum(1 for r in updated if r["status"] == "skipped")
    failed = sum(1 for r in updated if r["status"] == "failed")
    log(f"rag extract: {done} extracted, {skipped} skipped, {failed} failed")
    log_skipped(newly_skipped, log)      # List already named the ones it skipped
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
        if row.get("status") in ("failed", "skipped"):
            continue
        heading = None
        texts: list = []
        pages: list = []
        for el in doc_elements:
            if el.get("heading_path"):
                heading = el["heading_path"]
            if el.get("type") != "heading" and el.get("text"):
                texts.append(el["text"])
                pages.append(el.get("page"))
        # the chunkers drop blank elements before joining; the page map and
        # the text spans are located against are filtered the SAME way, or
        # they describe a different string than the one that was chunked
        kept = [(text, page) for text, page in zip(texts, pages)
                if text and text.strip()]
        texts = [text for text, _ in kept]
        pages = [page for _, page in kept]
        kwargs = {"max_tokens": budget, "max_bytes": max_bytes}
        if chunk_fn is CHUNKERS["recursive"]:
            kwargs["overlap_tokens"] = overlap_tokens
        contents = chunk_fn(texts, **kwargs)
        # where each chunk came from, so a citation can be opened there
        joined = document_text(texts)
        spans = locate(joined, contents)
        offsets = element_pages(texts, pages)
        chunks = [
            Chunk.build(doc_id, row.get("source_uri", ""), i, content,
                        row.get("content_hash", ""), row.get("extractor", ""),
                        pipeline_version, now,
                        heading_path=heading if len(by_doc) else None)
            for i, content in enumerate(contents)
        ]
        for chunk, span in zip(chunks, spans):
            if span is None:
                continue
            start, end = span
            located = {"start": start, "end": end}
            page = page_at(offsets, start)
            if page is not None:
                located["page"] = page
            chunk.span = located
        link_neighbors(chunks)
        row["chunk_count"] = len(chunks)
        all_chunks.extend(c.__dict__ for c in chunks)
    # Say where the budget came from. "budget 435" on its own reads like a
    # limit the pipeline imposed; it is the chunk window the SETUP asked for,
    # minus the estimator's safety margin, and it is unrelated to how many
    # tokens the embedding model could accept.
    log(f"rag chunk: {len(all_chunks)} chunks from {len(by_doc)} documents "
        f"(budget {budget} tokens = the setup's {input_token_limit}-token "
        f"chunk window x {margin:.0%} safety margin)")
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
             run_id: str = "", config_id: str = "", embed_model: str = "",
             embed_dims: int = 0, deleted_policy: str = "retire",
             retain_days: int = 0, attributes: dict = None,
             stats: dict = None, log=print) -> list:
    """Upsert-first-then-RETIRE-stale per document; tombstone deleted docs.

    Retiring rather than deleting is what makes the collection answerable
    about the past and rollback-able after a bad run (§2b). Every chunk row
    is stamped with the run and configuration that produced it, and with the
    embedding model, so a later re-embed is visible rather than silent.

    Two policies let a deployment choose otherwise:

    * ``deleted_policy="purge"`` — a document that disappeared from the
      source has its chunks REMOVED rather than tombstoned, history and all.
      For a corpus where "gone from the source" must mean "gone", at the
      price of not being able to read the collection as it stood before.
    * ``retain_days`` — after loading, retired generations older than this
      are dropped. 0 keeps history forever. This is retention housekeeping,
      not erasure: live rows are never touched.

    Returns the updated inventory (per-doc statuses -> ingested/failed).
    """
    if deleted_policy not in ("retire", "purge"):
        raise ValueError(f"unknown deleted_policy {deleted_policy!r} "
                         "(expected 'retire' or 'purge')")
    adapter.ensure_collection(collection, dims, schema={"sparse": sparse})
    # The Enrich stage's outputs are real columns. They are added here rather
    # than in the Enrich step because the collection is what has a schema, and
    # this is the only stage that holds it open.
    if attributes:
        delta = adapter.sync_attributes(collection, attributes)
        if delta.get("added"):
            # Said plainly, because it is the one thing a person will get
            # wrong: the column exists from now on and is EMPTY for every
            # chunk written before it, which looks exactly like an LLM that
            # had nothing to say. Only a full re-ingestion fills it.
            log("rag load: added column(s) " + ", ".join(delta["added"])
                + " to '" + collection + "'. Existing chunks are NOT "
                "backfilled - they keep a null there until the corpus is "
                "re-ingested (bump the pipeline version to force that)")
        if delta.get("kept"):
            log("rag load: column(s) " + ", ".join(delta["kept"])
                + " are no longer produced by this setup. They are KEPT, not "
                "dropped, and hold whatever the prompt that wrote them said")
    by_doc: dict = {}
    for chunk in embedded_chunks:
        chunk = dict(chunk)
        # each extracted attribute is its own column, so it travels as its own
        # key rather than as a nested map the adapter would have to know about
        for name, value in (chunk.pop("attributes", None) or {}).items():
            chunk[name] = value
        chunk.setdefault("run_id", run_id)
        chunk.setdefault("config_id", config_id)
        chunk.setdefault("embed_model", embed_model)
        chunk.setdefault("embed_dims", embed_dims or dims)
        by_doc.setdefault(chunk["doc_id"], []).append(chunk)

    updated: list = []
    loaded = failed = removed = 0
    written = retired = 0
    for row in inventory:
        row = dict(row)
        status = row.get("status")
        try:
            if status == "deleted":
                if deleted_policy == "purge":
                    retired += adapter.delete(
                        collection, filter={"doc_id": row["doc_id"]}) or 0
                else:
                    retired += adapter.retire(
                        collection, filter={"doc_id": row["doc_id"]},
                        run_id=run_id) or 0
                removed += 1
            elif status in ("new", "changed"):
                chunks = by_doc.get(row["doc_id"], [])
                if not chunks:
                    row.update(status="failed",
                               error_text=row.get("error_text")
                               or "no embedded chunks reached the load step")
                    failed += 1
                else:
                    written += adapter.upsert(collection, chunks) or 0
                    # retire what this document's previous generation left
                    # behind: anything live for the doc that is not one of
                    # the chunk ids we just wrote
                    retired += adapter.retire(
                        collection, filter={"doc_id": row["doc_id"]},
                        keep_ids=[c["chunk_id"] for c in chunks],
                        run_id=run_id) or 0
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
    if retain_days:
        # housekeeping, and never allowed to fail a run that loaded fine
        try:
            pruned = adapter.prune_history(collection, _cutoff(retain_days))
            log(f"rag load: pruned {pruned} retired chunk rows older than "
                f"{retain_days} days")
        except Exception as exc:
            log(f"rag load: history pruning skipped: {exc}")
    if stats is not None:
        stats["chunks_written"] = written
        stats["chunks_retired"] = retired
    log(f"rag load: {loaded} docs loaded, {removed} removed "
        f"({deleted_policy}), {failed} failed "
        f"-> collection '{collection}' now {adapter.count(collection)} chunks")
    return updated


def _cutoff(days: int) -> str:
    """An ISO timestamp `days` in the past — the retention boundary."""
    moment = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=int(days)))
    return moment.strftime("%Y-%m-%d %H:%M:%S")


RETRIEVE_COLUMNS = ["question", "rank", "doc_id", "chunk_id", "filename",
                    "source_uri", "heading_path", "context_header", "page",
                    "span_start", "span_end", "ingestion_timestamp",
                    "corpus_run_id", "distance", "score", "content",
                    "error_text"]


def run_retrieve(questions: list, embedder, adapter, collection: str,
                 k: int = 4, filter=None, log=print) -> list:
    """Ask the collection a list of questions; one row per hit.

    The same retrieval the manifested model performs, but landing in a table
    instead of a decision - so a corpus can be examined, compared across
    configurations, or turned into an evaluation set, without deploying
    anything.

    A question is isolated like a document is in ingestion: one that fails to
    embed or search produces a row carrying its error and the rest still run.
    A question that simply matches nothing produces a rank-0 row rather than
    vanishing, because "no answer" is a result worth seeing in the output.
    """
    rows: list = []
    asked = matched = failed = 0
    for raw in questions:
        question = str(raw or "").strip()
        if not question:
            continue
        asked += 1
        try:
            vector = embedder.embed(question, mode="query")
            hits = adapter.search(collection, vector, k=int(k), filter=filter)
        except Exception as exc:
            failed += 1
            rows.append({"question": question, "rank": 0, "distance": 0.0,
                         "score": 0.0, "error_text": str(exc)[:500]})
            log(f"rag retrieve: FAILED {question[:60]!r}: {exc}")
            continue
        if not hits:
            rows.append({"question": question, "rank": 0, "distance": 0.0,
                         "score": 0.0,
                         "error_text": "no chunks matched this question"})
            continue
        matched += 1
        for position, hit in enumerate(hits, start=1):
            record = hit.record
            span = record.get("span") if isinstance(record.get("span"), dict) else {}
            uri = str(record.get("source_uri") or "")
            rows.append({
                "question": question,
                "rank": position,
                "doc_id": record.get("doc_id", ""),
                "chunk_id": record.get("chunk_id", ""),
                "filename": uri.replace("\\", "/").rsplit("/", 1)[-1],
                "source_uri": uri,
                "heading_path": record.get("heading_path") or "",
                # what the Enrich stage wrote, and what was embedded WITH the
                # chunk - retrieval that cannot show it leaves the one thing
                # the stage changed invisible to the person judging it
                "context_header": record.get("context_header") or "",
                "page": span.get("page") or 0,
                "span_start": span.get("start") or 0,
                "span_end": span.get("end") or 0,
                "ingestion_timestamp": str(record.get("ingested_at") or ""),
                "corpus_run_id": record.get("run_id") or "",
                "distance": float(record.get("distance", 1.0 - hit.score)),
                "score": float(hit.score),
                "content": record.get("content", ""),
                "error_text": "",
            })
    log(f"rag retrieve: {asked} questions, {matched} with matches, "
        f"{failed} failed -> {len(rows)} rows from '{collection}'")
    return rows


def _embedding_usage(rows: list, column: str = "embed_usage") -> dict:
    """A usage tally a step stamped onto the inventory, by column."""
    for row in rows or []:
        raw = row.get(column)
        if not raw:
            continue
        try:
            usage = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(usage, dict) and usage:
            return usage
    return {}


def record_history(adapter, inventory: list, previous_ledger: list,
                   run_id: str, collection: str, config_id: str = "",
                   settings=None, metrics=None, status: str = "completed",
                   discovery: list = None, log=print) -> dict:
    """Write this run into the history tables beside the chunks (design §6).

    `inventory` is the post-load state, `discovery` the same documents as
    List Documents classified them. Both are needed: run_load overwrites
    every successful row's status with "ingested", so counting the post-load
    inventory alone reports 0 new, 0 changed and 0 unchanged for a run that
    plainly did work. A run describes what it FOUND (new/changed/unchanged/
    deleted) and what it ACHIEVED (ingested/failed); those are different
    questions and the ledger can only answer them together.

    Never fails the run. History is a record of what happened, and losing the
    record is not a reason to lose the ingestion that already succeeded - so
    a store that refuses the write is reported and the load still stands.

    Returns {"runs": n, "events": n} for the caller to publish to CAS.
    """
    from .history import History, events_from_inventory, status_counts

    written = {"runs": 0, "events": 0}
    try:
        history = History(adapter.raw_connection(),
                          getattr(adapter, "HISTORY_DIALECT", "postgres"))
        history.ensure_tables()
        previous = {row["doc_id"]: row for row in (previous_ledger or [])
                    if row.get("doc_id")}
        found = {row["doc_id"]: row.get("status", "")
                 for row in (discovery or []) if row.get("doc_id")}
        events = events_from_inventory(inventory, previous)
        for event in events:
            # what the document DID, unless the run failed on it - that
            # outcome is the more important fact
            if event["status"] not in ("failed", "skipped") \
                    and found.get(event["doc_id"]):
                event["status"] = found[event["doc_id"]]
        written["events"] = history.record_events(run_id, events)
        if config_id and settings:
            history.record_config(config_id, settings)
        counts = status_counts(discovery or inventory)     # what it found
        for name in ("ingested", "skipped", "failed"):     # what it achieved
            counts[name] = status_counts(inventory)[name]
        recorded = dict(metrics or {})
        # the configuration columns are convenience copies of what is already
        # in `settings`; without them every rag_runs row read null for the
        # chunker and its token budget
        if isinstance(settings, dict):
            for column, key in (("chunker", "chunker"),
                                ("input_token_limit", "input_token_limit"),
                                ("overlap_tokens", "overlap_tokens"),
                                ("pipeline_version", "pipeline_version"),
                                ("embed_model", "embed_model")):
                if settings.get(key) not in (None, "") and column not in recorded:
                    recorded[column] = settings[key]
        # embedding cost, carried from the Embed step in its own column
        usage = _embedding_usage(inventory) or _embedding_usage(discovery or [])
        if usage:
            recorded.setdefault("embed_calls", int(usage.get("calls") or 0))
            recorded.setdefault("embed_tokens", int(usage.get("tokens") or 0))
            recorded.setdefault("embed_seconds",
                                float(usage.get("run_time") or 0.0))
        # what the Enrich stage spent, when the setup has one
        enrichment = (_embedding_usage(inventory, "enrich_usage")
                      or _embedding_usage(discovery or [], "enrich_usage"))
        if enrichment:
            recorded.setdefault("enrich_model", str(enrichment.get("model") or ""))
            recorded.setdefault("enrich_calls", int(enrichment.get("calls") or 0))
            recorded.setdefault("enrich_input_tokens",
                                int(enrichment.get("input_tokens") or 0))
            recorded.setdefault("enrich_output_tokens",
                                int(enrichment.get("output_tokens") or 0))
            recorded.setdefault("enrich_seconds",
                                float(enrichment.get("run_time") or 0.0))
            recorded.setdefault("enrich_failed", int(enrichment.get("failed") or 0))
        history.close_run(run_id, status=status, counts=counts,
                          collection=collection, config_id=config_id,
                          **{k: v for k, v in recorded.items()})
        written["runs"] = 1
        log(f"rag history: run {run_id} recorded, {written['events']} document "
            f"events")
    except Exception as exc:
        log(f"rag history: not recorded ({type(exc).__name__}: "
            f"{str(exc)[:160]}) - the load itself is unaffected")
    return written


def resolve_doc_ids(selectors, ledger: list) -> tuple:
    """Turn what a person would type into doc ids. Returns (ids, unmatched).

    Nobody knows a doc_id — it is a hash. The ledger is what maps the things
    people do know onto it: the full source URI, or just the file name. An
    exact doc_id is accepted too, for the report-driven path.

    A selector matching several documents (the same file name in two folders)
    resolves to ALL of them, which is why the step previews before it acts.
    """
    ids: list = []
    unmatched: list = []
    for raw in selectors:
        wanted = str(raw or "").strip()
        if not wanted:
            continue
        found = []
        for row in ledger:
            uri = str(row.get("source_uri") or "")
            name = uri.replace("\\", "/").rsplit("/", 1)[-1]
            if wanted in (row.get("doc_id"), uri, name):
                found.append(row["doc_id"])
        if found:
            ids.extend(found)
        else:
            unmatched.append(wanted)
    seen: set = set()
    unique = [i for i in ids if not (i in seen or seen.add(i))]
    return unique, unmatched


def run_purge(doc_ids: list, ledger: list, adapter, collection: str,
              dry_run: bool = True, log=print) -> tuple:
    """Erase named documents from the collection and forget them.

    This is the deliberate, irreversible operation - not what an ingestion
    run does. Two things have to happen together, or the state is worse than
    before:

    * every chunk row for the document goes, LIVE AND RETIRED, because
      erasure that leaves the previous generation behind has erased nothing;
    * the document's LEDGER row goes too. Leaving it would mean the
      incremental diff still considers the document ingested and unchanged,
      so a document that is still present at the source would never come
      back - an invisible hole rather than a deletion.

    Which also means: purging a document that still exists at the source
    removes it until the next run, and then it returns. Erasure has to happen
    at the source as well; the step says so.

    Returns (report rows, remaining ledger).
    """
    wanted = {str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()}
    if not wanted:
        raise ValueError("no documents selected - refusing to purge a whole "
                         "collection implicitly")
    by_doc = {row["doc_id"]: row for row in ledger}
    report: list = []
    purged: set = set()
    for doc_id in sorted(wanted):
        known = by_doc.get(doc_id)
        try:
            chunks = adapter.count(collection, filter={"doc_id": doc_id},
                                   include_retired=True)
            if not dry_run:
                adapter.delete(collection, filter={"doc_id": doc_id})
                purged.add(doc_id)
            report.append({
                "doc_id": doc_id,
                "source_uri": (known or {}).get("source_uri", ""),
                "chunks_removed": chunks,
                "ledger_removed": "yes" if known and not dry_run else
                                  ("would" if known else "no"),
                "outcome": "would purge" if dry_run else "purged",
                "error_text": "" if known else
                              "not in the ledger - chunks removed by doc_id only",
            })
        except Exception as exc:
            report.append({"doc_id": doc_id,
                           "source_uri": (known or {}).get("source_uri", ""),
                           "chunks_removed": 0, "ledger_removed": "no",
                           "outcome": "failed", "error_text": str(exc)[:500]})
            log(f"rag purge: FAILED doc {doc_id}: {exc}")
    # only a document whose chunks actually went loses its ledger row: a
    # failed erasure that was forgotten anyway would leave the chunks
    # retrievable with nothing left to say the document is there
    remaining = [row for row in ledger if row["doc_id"] not in purged]
    total = sum(row["chunks_removed"] for row in report)
    log(f"rag purge: {'would remove' if dry_run else 'removed'} {total} chunk "
        f"rows for {len(report)} documents from '{collection}'"
        + (" (DRY RUN - nothing was changed)" if dry_run else ""))
    return report, remaining


PURGE_COLUMNS = ["doc_id", "source_uri", "chunks_removed", "ledger_removed",
                 "outcome", "error_text"]


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
