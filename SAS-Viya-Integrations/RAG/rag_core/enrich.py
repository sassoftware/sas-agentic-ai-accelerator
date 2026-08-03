# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Enrich stage: an LLM call per chunk, between chunking and embedding.

`schema.Chunk` has reserved `context_header` from the start and `run_embed`
has always prepended it to the text before embedding. The consumer existed;
this module is the producer.

WHAT IT IS FOR. A chunk that reads perfectly in place is often meaningless on
its own - "revenue grew 12%" says nothing about whose revenue, or when. The
published remedy (Anthropic's contextual retrieval) is to prepend a short
LLM-written statement situating the chunk in its document, and to embed the
two together. That is the headline use of this slot, but not the only one:
the same call can classify a chunk, pull out a date or a department, or flag
personal data. Which of those it does is decided by the PROMPT, not by this
module, so the accelerator gains a general enrichment slot rather than one
hard-coded technique.

WHERE THE PROMPT COMES FROM (owner decision OQ14). Not a prompt literal in
this file: the user builds and evaluates the prompt in the Prompt Builder,
manifests it as a Model Manager model, and the RAG setup points at that model.
So the prompt is a governed artifact with its own documentation, versions and
permissions, and improving it does not mean editing rag_core.

WHAT A MANIFESTED PROMPT LOOKS LIKE. The Prompt Builder writes a score module
whose shape is fixed and is the whole contract this module needs:

    def scoreModel(customer, amount):
        "Output: response, output_length, sentiment, score, parse_status"

The parameters are the prompt's variables; the docstring names what the
function returns, in tuple order. With the "integrated LLM call" option the
function performs the call itself and returns the response - which is the
form this module requires, because it has no other way to reach the LLM. The
alternative form returns `llmBody`/`llmURL` for the Call LLM node of SAS
Intelligent Decisioning to POST, and is refused here with that explanation.

THE SCORE CODE IS EXECUTED. It is downloaded from Model Manager and exec'd in
this process, exactly as SAS Container Runtime would execute it. That is
arbitrary code execution by design - it is what scoring a model IS - and the
control is Model Manager permissions on the prompt project. The administration
guide says so; do not point a setup at a model you would not run.

FAILURE CONTRACT. Per chunk, like every other stage: a chunk whose enrichment
fails keeps `context_header = None` and is embedded plain rather than failing
the run. Two failure modes are detected on purpose, because both would
otherwise be stored as though they were an answer:

* the score code reports a failed call through its `response` output rather
  than raising ("LLM call failed: ..."), which embedded verbatim would put an
  error message into the vector;
* `parse_status = 0` means the response was not valid JSON, so any output
  variable still holds the DEFAULT the prompt author gave it - a plausible
  value that was never generated.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

#: How much of a document may be handed to the prompt as `document` context.
#:
#: Whole documents are what the published recipe passes, and for a 300-page
#: PDF that is neither affordable nor within any context window. The cap is a
#: character count rather than tokens because it only has to be safe, not
#: exact, and it is applied per chunk-of-the-same-document once.
DOCUMENT_CONTEXT_CHARS = 20_000

#: A generated header longer than this is truncated before it is stored.
#:
#: The header is prepended to the chunk and embedded WITH it, so an essay in
#: response to "write one sentence" would push the chunk itself out of the
#: model's token window - the text would still be in the collection and no
#: longer in its own vector.
MAX_HEADER_CHARS = 1_000

#: Outputs every manifested prompt with the integrated call can return.
#: Everything else in an "Output:" list is a variable parsed out of the LLM's
#: JSON response, and therefore only trustworthy when parse_status is 1.
LLM_NATIVE_OUTPUTS = frozenset(
    ("response", "run_time", "prompt_length", "output_length", "parse_status"))

#: What the score code says when the call did not happen. Its own wording -
#: see the manifested template in the Prompt Builder.
CALL_FAILED_PREFIX = "LLM call failed"

#: Column names a stored output may NOT take.
#:
#: An output is stored as a REAL column on the chunk table, so its name has to
#: be one the table does not already use - and `score`, `content`, `page` and
#: `rank` are all plausible things to ask an LLM for. The collision is refused
#: by name at validation rather than silently prefixed: a column that is not
#: called what the prompt calls it is a trap for whoever reads the table later.
#: Covers the canonical chunk schema, the lineage columns the adapters add, and
#: the names retrieval computes.
RESERVED_COLUMNS = frozenset((
    "id", "chunk_id", "doc_id", "source_uri", "chunk_index", "content",
    "content_hash", "extractor", "pipeline_version", "ingested_at", "span",
    "heading_path", "tags", "prev_id", "next_id", "context_header",
    "entities", "relations", "embedding", "tsv",
    "run_id", "config_id", "embed_model", "embed_dims",
    "valid_from", "valid_to", "retired_in_run",
    "enrich_version",
    "distance", "score", "rank", "question", "filename", "error_text",
))

#: A stored output's column name must be a plain SQL identifier.
_COLUMN_OK = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: The two types a Prompt Builder output variable can have.
ATTRIBUTE_TYPES = {"decimal": "decimal", "string": "string"}

_LLM_LITERAL = re.compile(r'^\s*llm\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_OUTPUT_DOC = re.compile(r"^\s*Output\s*:\s*(.+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# What a prompt can be given about a chunk
# ---------------------------------------------------------------------------
#: field key -> what it means, for the Builder's tooltips and the docs.
#:
#: A closed vocabulary on purpose. Every entry is cheap and bounded, and a
#: prompt input can only be filled from one of them - so no setup can ask for
#: something the pipeline would have to go and fetch per chunk.
CHUNK_FIELDS = {
    "chunk": "The chunk text itself",
    "document": (
        "The surrounding document, rebuilt from its chunks and capped at "
        f"{DOCUMENT_CONTEXT_CHARS} characters"),
    "neighbours": "The previous and next chunk of the same document",
    "heading": "The heading path the chunk sits under",
    "filename": "The document's file name",
    "source": "The document's full source location",
    "position": "Which chunk this is, as 'chunk 3 of 42'",
}


def document_context(chunks: list, max_chars: int = DOCUMENT_CONTEXT_CHARS) -> dict:
    """Per-document context, computed once for all of that document's chunks.

    Rebuilding the document from its own chunks - rather than carrying the
    original text into this stage - keeps the Enrich step working off exactly
    what the chunker produced, and means the step needs no access to the
    document source at all.
    """
    ordered: dict = {}
    for chunk in chunks:
        ordered.setdefault(chunk.get("doc_id", ""), []).append(chunk)
    context: dict = {}
    for doc_id, doc_chunks in ordered.items():
        doc_chunks = sorted(doc_chunks, key=lambda c: int(c.get("chunk_index") or 0))
        text = "\n\n".join(str(c.get("content") or "") for c in doc_chunks)
        context[doc_id] = {
            "document": text[:max_chars],
            "truncated": len(text) > max_chars,
            "count": len(doc_chunks),
            "by_index": {int(c.get("chunk_index") or 0): str(c.get("content") or "")
                         for c in doc_chunks},
        }
    return context


def field_values(chunk: dict, context: dict) -> dict:
    """Every CHUNK_FIELDS value for one chunk."""
    doc = context.get(chunk.get("doc_id", ""), {})
    index = int(chunk.get("chunk_index") or 0)
    by_index = doc.get("by_index", {})
    neighbours = [by_index.get(index - 1), by_index.get(index + 1)]
    uri = str(chunk.get("source_uri") or "")
    return {
        "chunk": str(chunk.get("content") or ""),
        "document": str(doc.get("document") or ""),
        "neighbours": "\n\n".join(text for text in neighbours if text),
        "heading": str(chunk.get("heading_path") or ""),
        "filename": uri.replace("\\", "/").rsplit("/", 1)[-1],
        "source": uri,
        "position": f"chunk {index + 1} of {doc.get('count', 0)}",
    }


def parse_mapping(raw: str) -> dict:
    """`input=field;input=field` -> {input: field}.

    Deliberately not JSON: this value travels as a SAS macro variable through
    a job parameter, and a format with no quotes or braces is one fewer thing
    that can arrive mangled. Unknown field names are left as they are and
    rejected later by name, where the message can say which ones.
    """
    mapping: dict = {}
    for pair in str(raw or "").replace("\n", ";").split(";"):
        name, _, field = pair.partition("=")
        if name.strip() and field.strip():
            mapping[name.strip()] = field.strip()
    return mapping


def render_mapping(mapping: dict) -> str:
    """The inverse of parse_mapping, in a stable order."""
    return ";".join(f"{name}={mapping[name]}" for name in sorted(mapping))


def parse_names(raw) -> list:
    """A comma-separated output list, as the job parameter carries it."""
    if isinstance(raw, (list, tuple)):
        values = [str(item).strip() for item in raw]
    else:
        values = [item.strip() for item in str(raw or "").split(",")]
    return [value for value in values if value]


# ---------------------------------------------------------------------------
# The manifested prompt
# ---------------------------------------------------------------------------
class PromptModel:
    """A manifested Prompt Builder model, loaded and callable.

    Everything this class knows it reads off the score code itself, because
    the score code is what actually runs: a model whose registered variable
    definitions disagree with its function signature would otherwise fail one
    chunk at a time, deep inside a run.
    """

    def __init__(self, code: str, name: str = "", model_id: str = ""):
        self.name = name
        self.model_id = model_id
        # what every refusal below calls this prompt. A model can arrive with
        # neither a name nor an id (a file read straight off disk), and
        # "the prompt model '' was manifested for..." reads like a bug.
        label = name or model_id or "this model"
        self.code = code
        self.code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        namespace: dict = {}
        try:
            exec(compile(code, f"<prompt {label}>", "exec"), namespace)
        except Exception as exc:
            raise RuntimeError(
                f"the prompt model {label!r} could not be loaded: "
                f"{type(exc).__name__}: {exc}") from exc
        score = namespace.get("scoreModel")
        if not callable(score):
            raise RuntimeError(
                f"the prompt model {label!r} has no scoreModel "
                "function - it is not a manifested prompt")
        self._score = score
        import inspect

        self.inputs = list(inspect.signature(score).parameters)
        self.outputs = _outputs_of(score, label)
        if "llmBody" in self.outputs or "llmURL" in self.outputs:
            raise RuntimeError(
                f"the prompt model {label!r} was manifested for the "
                "Call LLM node of SAS Intelligent Decisioning: it returns the "
                "prepared request instead of making it, so there is nothing for "
                "the ingestion to read. Re-manifest it in the Prompt Builder "
                "with 'integrated LLM call' ticked")
        self.llm = _llm_of(code)
        self.needs_api_key = "API_KEY" in self.inputs
        #: output name -> "string" | "decimal", from the model's outputVar.json.
        #: What makes a stored output a TYPED column instead of text.
        self.output_types: dict = {}
        #: '' when the setup follows the model rather than pinning a version.
        self.version_id = ""
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                      "run_time": 0.0, "failed": 0}
        self._meter = threading.Lock()

    @property
    def stamp(self) -> str:
        """What identifies the prompt that wrote a chunk's enrichment.

        Stored on every enriched chunk. Since a prompt may now change without
        forcing a re-ingest, a collection can legitimately hold work from two
        prompts at once - and this is what makes that visible rather than
        merely true.
        """
        return f"{self.model_id or self.name}@{self.version_id or self.code_hash}"

    @property
    def variables(self) -> list:
        """The inputs a setup has to map - API_KEY is resolved, not mapped."""
        return [name for name in self.inputs if name != "API_KEY"]

    def describe(self) -> str:
        return (f"{self.name or self.model_id} (llm {self.llm or 'unknown'}, "
                f"code {self.code_hash}), inputs {', '.join(self.inputs) or 'none'}"
                f" -> outputs {', '.join(self.outputs) or 'none'}")

    def call(self, values: dict) -> dict:
        """One scoring call. Returns {output name: value}.

        Raises only when the CALL COULD NOT BE MADE (a missing input, a broken
        module). A call that was made and failed comes back as a normal result
        whose `response` carries the reason, because that is what the score
        code does and what a decision flow would see.
        """
        try:
            arguments = [values[name] for name in self.inputs]
        except KeyError as missing:
            raise RuntimeError(
                f"the prompt input {missing.args[0]!r} has no value - map it "
                "on the RAG setup") from None
        returned = self._score(*arguments)
        if not isinstance(returned, tuple):
            returned = (returned,)
        result = dict(zip(self.outputs, returned))
        with self._meter:
            self.usage["calls"] += 1
            self.usage["input_tokens"] += _as_int(result.get("prompt_length"))
            self.usage["output_tokens"] += _as_int(result.get("output_length"))
            self.usage["run_time"] += _as_float(result.get("run_time"))
        return result

    def note_failure(self) -> None:
        with self._meter:
            self.usage["failed"] += 1

    # -- loading -------------------------------------------------------------
    @classmethod
    def from_model_manager(cls, base: str, token: str, model_id: str,
                           version_id: str = "", verify=True,
                           timeout: float = 60.0, session=None) -> "PromptModel":
        """Load a manifested prompt from SAS Model Manager.

        `version_id` empty follows the model — whatever the prompt author last
        manifested. Given, it pins the setup to one version, which is read from
        a DIFFERENT endpoint: a version's contents live under the model's
        `history`, not under the version's own id.

        Two shapes of nothing are refused rather than accepted (both seen live
        on 2026-08-02, on a model an older tool wrote):

        * `GET /models/{versionId}/contents` — the path that looks right —
          answers **200 with an empty collection**, which reads like a prompt
          that has no score code rather than like the wrong URL;
        * a version whose snapshot lost the file body answers **200 with zero
          bytes**, which would otherwise load as an empty prompt module.
        """
        import requests

        http = session or requests
        root = base.rstrip("/")
        headers = {"Authorization": "Bearer " + str(token or ""),
                   "Accept": "application/json"}
        where = (f"/modelRepository/models/{model_id}/history/{version_id}/contents"
                 if version_id else
                 f"/modelRepository/models/{model_id}/contents")
        listing = http.get(root + where, params={"limit": 100}, headers=headers,
                           verify=verify, timeout=timeout)
        if listing.status_code == 404:
            raise RuntimeError(
                (f"model {model_id} has no version {version_id}" if version_id
                 else f"no Model Manager model with id {model_id}")
                + " - the prompt this setup enriches with has been deleted, "
                  "moved, or its pinned version removed")
        listing.raise_for_status()
        items = listing.json().get("items") or []
        pinned = f" version {version_id}" if version_id else ""
        if not items:
            raise RuntimeError(
                f"the prompt model {model_id}{pinned} lists no content at all. "
                "A version whose snapshot did not keep its files cannot be "
                "used - pin a different version, or follow the latest")
        score = _score_content(items)
        if not score:
            raise RuntimeError(
                f"the model {model_id}{pinned} carries no score code - manifest "
                "the prompt in the Prompt Builder before enriching with it")
        code = _content_of(http, root, token, score, verify, timeout)
        if not code.strip():
            raise RuntimeError(
                f"the score code of prompt model {model_id}{pinned} came back "
                "EMPTY. Its file body is not in the version snapshot, so there "
                "is nothing to run - pin a different version, or follow the "
                "latest")
        name = ""
        detail = http.get(f"{root}/modelRepository/models/{model_id}",
                          headers=headers, verify=verify, timeout=timeout)
        if detail.status_code == 200:
            name = str(detail.json().get("name") or "")
        prompt = cls(code, name=name, model_id=model_id)
        prompt.version_id = version_id
        # The registered output variables carry each output's TYPE, which is
        # what lets a stored output become a typed column rather than text.
        # Absent (an older manifest), everything falls back to text - readable,
        # just not summable.
        variables = _named_content(items, "outputvariables", "outputVar.json")
        if variables:
            try:
                declared = json.loads(
                    _content_of(http, root, token, variables, verify, timeout))
                for entry in declared if isinstance(declared, list) else []:
                    if entry.get("name"):
                        prompt.output_types[str(entry["name"])] = (
                            "decimal" if str(entry.get("type")) == "decimal"
                            else "string")
            except (ValueError, TypeError):
                pass          # unreadable declarations never fail a run
        return prompt


def list_versions(base: str, token: str, model_id: str, verify=True,
                  timeout: float = 60.0, session=None) -> list:
    """A prompt's versions, oldest first: [{"id", "label", "created"}].

    `modelVersionName` is the major.minor a person recognises, but it is NOT
    unique - two versions labelled 1.0 were seen on one live model - so the id
    is the identity and the label is only for display.
    """
    import requests

    http = session or requests
    response = http.get(
        f"{base.rstrip('/')}/modelRepository/models/{model_id}/modelVersions",
        params={"limit": 100},
        headers={"Authorization": "Bearer " + str(token or ""),
                 "Accept": "application/json"},
        verify=verify, timeout=timeout)
    if response.status_code != 200:
        return []
    versions = [
        {"id": str(item.get("id") or ""),
         "label": str(item.get("modelVersionName") or ""),
         "created": str(item.get("creationTimeStamp") or "")}
        for item in (response.json().get("items") or []) if item.get("id")
    ]
    return sorted(versions, key=lambda v: v["created"])


def _content_of(http, root: str, token: str, item: dict, verify, timeout) -> str:
    """The bytes behind a content item, by whichever reference it carries.

    A version's item advertises its own `content` link and may have an EMPTY
    fileUri, while a current model's item has the fileUri and no such link -
    so both are tried rather than assuming either.
    """
    for link in item.get("links") or []:
        if str(link.get("rel")) == "content" and link.get("uri"):
            response = http.get(root + str(link["uri"]),
                                headers={"Authorization": "Bearer " + str(token or "")},
                                verify=verify, timeout=timeout)
            if response.status_code == 200 and response.content:
                return response.content.decode("utf-8", "replace")
    file_uri = str(item.get("fileUri") or "").rstrip("/")
    if file_uri and not file_uri.endswith("/files/files"):
        response = http.get(root + file_uri + "/content",
                            headers={"Authorization": "Bearer " + str(token or "")},
                            verify=verify, timeout=timeout)
        if response.status_code == 200:
            return response.content.decode("utf-8", "replace")
    return ""


def _named_content(items: list, role: str, filename: str) -> dict:
    for item in items:
        if str(item.get("role") or "").lower() == role:
            return item
    for item in items:
        if str(item.get("name") or "") == filename:
            return item
    return {}


def _score_content(items: list) -> dict:
    """The score file among a model's contents.

    Role first, because that is what the role is for; a .py file is the
    fallback for a model whose content was uploaded without one.
    """
    for item in items:
        if str(item.get("role") or "").lower() == "score":
            return item
    for item in items:
        if str(item.get("name") or "").lower().endswith(".py"):
            return item
    return {}


def _outputs_of(score, label: str) -> list:
    """The output names from the `"Output: a, b, c"` docstring."""
    for line in str(score.__doc__ or "").splitlines():
        found = _OUTPUT_DOC.match(line)
        if found:
            return [name.strip() for name in found.group(1).split(",") if name.strip()]
    raise RuntimeError(
        f"the prompt model {label!r} does not declare its outputs - a "
        "manifested prompt's scoreModel carries an \"Output: ...\" docstring "
        "naming what it returns")


def _llm_of(code: str) -> str:
    """The model the prompt calls, for the cost line. Best effort."""
    found = _LLM_LITERAL.search(code)
    return found.group(1) if found else ""


def _as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# RAG - Enrich Chunks
# ---------------------------------------------------------------------------
def attribute_schema(prompt: PromptModel, outputs) -> dict:
    """column name -> "string" | "decimal" for the outputs a setup stores.

    The column IS the output name: an output called `department` becomes a
    `department` column, so the table reads the way the prompt does. Types
    come from the prompt's registered output variables; anything undeclared
    is text, which is readable but not summable.
    """
    schema: dict = {}
    for name in outputs or []:
        schema[str(name)] = ("decimal"
                             if prompt.output_types.get(name) == "decimal"
                             else "string")
    return schema


def validate_selection(prompt: PromptModel, mapping: dict, header_output: str,
                       column_outputs: list) -> list:
    """Everything wrong with a setup's use of this prompt, by name.

    Called BEFORE the corpus is crawled - a mapping that names an input the
    prompt does not have is a five-second failure, not a twenty-minute one.
    """
    problems: list = []
    unmapped = [name for name in prompt.variables if not mapping.get(name)]
    if unmapped:
        problems.append("no chunk field is mapped onto the prompt input(s) "
                        + ", ".join(unmapped))
    unknown_inputs = [name for name in mapping if name not in prompt.variables]
    if unknown_inputs:
        problems.append("the prompt has no input(s) named "
                        + ", ".join(unknown_inputs)
                        + " - it takes " + (", ".join(prompt.variables) or "none"))
    unknown_fields = sorted({field for field in mapping.values()
                             if field not in CHUNK_FIELDS})
    if unknown_fields:
        problems.append("no such chunk field(s): " + ", ".join(unknown_fields)
                        + " - available: " + ", ".join(sorted(CHUNK_FIELDS)))
    columns = list(column_outputs or [])
    wanted = ([header_output] if header_output else []) + columns
    missing = [name for name in wanted if name not in prompt.outputs]
    if missing:
        problems.append("the prompt does not return " + ", ".join(missing)
                        + " - it returns " + (", ".join(prompt.outputs) or "nothing"))
    # A stored output becomes a real column, so its name has to be one the
    # chunk table can take. Refused by name rather than renamed for the user:
    # a column that is not called what the prompt calls it is a trap for
    # whoever reads the table six months later.
    taken = sorted({name for name in columns if name.lower() in RESERVED_COLUMNS})
    if taken:
        problems.append("these output(s) cannot be stored as columns because "
                        "the chunk table already uses those names: "
                        + ", ".join(taken)
                        + " - rename them in the prompt, or do not store them")
    malformed = sorted({name for name in columns
                        if name.lower() not in RESERVED_COLUMNS
                        and not _COLUMN_OK.match(name.lower())})
    if malformed:
        problems.append("these output name(s) cannot be a column name: "
                        + ", ".join(malformed)
                        + " - use letters, digits and underscores, starting "
                          "with a letter")
    if not header_output and not columns:
        problems.append("nothing would be stored: choose the output that "
                        "becomes the context header, or at least one output to "
                        "store as a column")
    return problems


def run_enrich(chunks: list, prompt: PromptModel, mapping: dict,
               header_output: str = "", column_outputs=(), api_key: str = "",
               max_workers: int = 4, already_enriched: set = frozenset(),
               document_chars: int = DOCUMENT_CONTEXT_CHARS, log=print) -> tuple:
    """Enrich every chunk through the prompt. Returns (chunks, failures).

    The returned list is the SAME chunks, with `context_header` and an
    `attributes` map filled in where the call succeeded. The Embed step
    prepends the header before embedding; the Load step writes each attribute
    into its own COLUMN on the chunk table, so what the LLM extracted is
    queryable and filterable rather than buried in a JSON blob.

    Every enriched chunk also records `enrich_version` - which prompt, at
    which version, wrote it. A prompt may now change without forcing a
    re-ingest, so a collection can hold work from two prompts at once, and
    this is what makes that visible rather than merely true.

    `already_enriched` is the checkpoint set the Embed step has too: a chunk
    whose vector is being reused was enriched by the run that embedded it, and
    calling the LLM again for a header nobody will store is pure cost.
    """
    column_outputs = list(column_outputs or [])
    problems = validate_selection(prompt, mapping, header_output, column_outputs)
    if problems:
        raise ValueError("this setup cannot enrich with "
                         f"{prompt.name or prompt.model_id!r}: "
                         + "; ".join(problems))
    todo = [chunk for chunk in chunks if chunk["chunk_id"] not in already_enriched]
    reused = len(chunks) - len(todo)
    if reused:
        log(f"rag enrich: {reused} chunks already embedded with a header "
            f"(checkpoint), {len(todo)} to enrich")
    if not todo:
        return chunks, []

    # Built from EVERY chunk, not just the ones being enriched: a document
    # half of whose chunks came from the checkpoint would otherwise be
    # described to the prompt as half a document.
    context = document_context(chunks, document_chars)
    truncated_docs = sum(1 for doc in context.values() if doc.get("truncated"))
    if truncated_docs:
        log(f"rag enrich: {truncated_docs} document(s) longer than "
            f"{document_chars} characters are passed to the prompt truncated")
    # Only the parsed outputs depend on the response being valid JSON; asking
    # for none of them means parse_status is irrelevant to this setup.
    parsed_wanted = [name for name in ([header_output] if header_output else [])
                     + column_outputs if name not in LLM_NATIVE_OUTPUTS]
    failures: list = []
    truncated_headers = 0

    def work(chunk):
        values = field_values(chunk, context)
        arguments = {name: values[field] for name, field in mapping.items()}
        if prompt.needs_api_key:
            arguments["API_KEY"] = api_key
        return chunk, prompt.call(arguments)

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        submitted = [(chunk, pool.submit(work, chunk)) for chunk in todo]
        for chunk, future in submitted:
            try:
                _, result = future.result()
            except Exception as exc:
                prompt.note_failure()
                failures.append((chunk["chunk_id"], str(exc)[:300]))
                continue
            reason = _rejected(result, parsed_wanted)
            if reason:
                prompt.note_failure()
                failures.append((chunk["chunk_id"], reason[:300]))
                continue
            if header_output:
                header = str(result.get(header_output) or "").strip()
                if len(header) > MAX_HEADER_CHARS:
                    header = header[:MAX_HEADER_CHARS]
                    truncated_headers += 1
                chunk["context_header"] = header or None
            if column_outputs:
                attributes = dict(chunk.get("attributes") or {})
                for name in column_outputs:
                    value = result.get(name)
                    # A decimal column must not receive the empty string; a
                    # missing number is NULL, which is what "the prompt did
                    # not say" means in a column somebody will average.
                    if prompt.output_types.get(name) == "decimal":
                        attributes[name] = _as_float(value) if value not in (None, "") else None
                    else:
                        attributes[name] = None if value is None else str(value)
                chunk["attributes"] = attributes
            # which prompt, at which version, wrote this chunk's enrichment
            chunk["enrich_version"] = prompt.stamp

    if truncated_headers:
        log(f"rag enrich: {truncated_headers} header(s) longer than "
            f"{MAX_HEADER_CHARS} characters were cut to fit - the prompt is "
            "asking for more text than a header can carry")
    enriched = len(todo) - len(failures)
    log(f"rag enrich: {enriched} chunks enriched, {len(failures)} failed, "
        f"usage {prompt.usage}")
    if failures:
        # Named, not just counted: a collection where a tenth of the chunks
        # have no header retrieves differently from one where all of them do,
        # and the run log is where that becomes visible.
        log(f"rag enrich: {len(failures)} chunk(s) were embedded WITHOUT a "
            f"header - first reason: {failures[0][1]}")
    return chunks, failures


def _rejected(result: dict, parsed_wanted: list) -> str:
    """Why this result must not be stored, or "" when it may be.

    Both cases exist because the score code does not raise: it reports a
    failed call through `response`, and a response that was not JSON leaves
    every parsed variable holding the prompt author's default. Storing either
    would put something into the collection that no model generated.
    """
    response = result.get("response")
    if isinstance(response, str) and response.startswith(CALL_FAILED_PREFIX):
        return response
    if parsed_wanted and "parse_status" in result:
        if not _as_int(result.get("parse_status")):
            return ("the LLM response was not the JSON the prompt asks for, so "
                    + ", ".join(parsed_wanted) + " would be the prompt's default "
                    "values rather than anything generated")
    return ""


def usage_json(usage: dict) -> str:
    """The tally as the inventory carries it between steps."""
    return json.dumps(usage or {}, sort_keys=True, separators=(",", ":"))
