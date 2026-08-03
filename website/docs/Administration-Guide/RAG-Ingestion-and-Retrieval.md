---
sidebar_position: 11.8
---

# RAG Ingestion and Retrieval

The accelerator ingests a document corpus into a vector store and serves it
back to a decision. This page is the operations view: what to deploy, what the
environment must provide, which knobs matter, and what the product does with
your data. For building a pipeline, see the
[RAG user guide](../User-Guide/RAG.md).

## What gets deployed

Everything lives in one governed SAS Content folder, by default
`/SAS Agentic AI Accelerator/RAG`:

| Folder | Contents |
| --- | --- |
| `rag_core/` | The Python runtime every step and job imports at run time |
| `steps/` | The nine custom steps, registered as dataFlows resources |
| `jobs/` | `Ingest-Documents.sas`, the standalone schedulable job |
| `models/` | The retrieval model template, manifested per setup |
| `generated/` | Default destination for per-setup artifacts — the ingestion job and the Studio flow the Builder generates, and what **RAG - Register Setup** writes. The Builder lets an author choose a different folder per setup |

Deploy with the shipped script, which reads your local checkout and
authenticates through the `sas-viya` CLI session:

```powershell
./SAS-Viya-Integrations/Other/deploy-rag-content.ps1
```

```bash
./SAS-Viya-Integrations/Other/deploy-rag-content.sh
```

Re-run it after every change. **Nothing is ever fetched from the internet at
run time**, so the same script works in an air-gapped deployment: the SAS
Content folder is the distribution channel, not GitHub.

:::note Why the script and not a manual upload
A `.step` file uploaded as a plain Content file renders as an empty step
editor — steps are dataFlows *service* resources. The script also **updates**
an already-registered step in place rather than replacing it. Re-registering a
step mints a new id, and every saved flow referencing the old id breaks.
:::

## Serving the RAG Builder

The Builder is a single-file app served through SAS Job Execution, the same
way as the Prompt Builder. Build it with `npm run build:rag` (or take the
prebuilt file) and create a job definition whose **form** is that HTML:

| | |
| --- | --- |
| Job definition | `<content root>/RAG-Builder-UI` |
| `code` | empty — this job never runs SAS |
| `properties` entry named `form` | the contents of `dist-rag/rag.html` |
| URL | `https://<host>/SASJobExecution/?_program=<path>&_action=form` |

:::note The HTML is the FORM, not the code — and the form is a *property*
`&_action=form` serves the job's **form**. Two ways to get this wrong, both
of which look like something else:

- HTML in `code` produces *"Parameter Error — HTML form file was not found"*,
  which reads like a missing file rather than the wrong field.
- The form is an entry **inside the definition's `properties` list**, named
  `form` — not a top-level `form` key. A PUT carrying a top-level key returns
  **200 and silently drops it**, so the deploy looks successful while the old
  app keeps serving. Verify by reading the property back, not by trusting the
  status code.

The Prompt Builder is served the same way, from
`/SAS Agentic AI Accelerator/Prompt Builder/SAS-LLM-Prompt-Builder-UI`.
:::

The Content Security Policy directives in
[Deploying the Builder UIs](./Setup-Additional-UIs.md) apply here too — the
build base64-encodes its inline scripts so the Go template engine cannot
corrupt them, and the CSP has to allow the decoded bundle to run.

Configure the Builder from its **Options** pane in Visual Analytics: the
Model Manager repository, the **embedding model project**, SCR endpoint,
credential domain, content root, CAS server, the ingestion compute context, a
checkbox per vector database the deployment offers, and the operational policy
(TLS, deleted-document handling, history retention, run-history recording,
embedding replicas, table persistence). Those policy values are recorded onto
each setup as it is created, so a setup keeps what it was built with.

:::warning These options live inside the report
Importing a newer Builder report from a transfer package replaces every value
above with whatever the package was built against — silently, since the report
still opens and the app still runs. Save them with `mdb options-save` before
importing and restore them after: see
[Preserving builder options across a report import](./Setup-Additional-UIs.md#preserving-builder-options-across-a-report-import).
:::

Served standalone rather than embedded, the same settings are query
parameters:

```
.../SASJobExecution/?_program=<path>&_action=form&embeddingProjectID=<uuid>&modelRepositoryID=<uuid>
```

:::note What the Builder never asks a user
Three things a person building a corpus should not have to know are resolved
rather than typed:

| | Comes from |
| --- | --- |
| Vector store host, port, database | The credential domain — see below |
| Embedding model | Listed from the embedding model project |
| Embedding dimensions | The chosen model's fact sheet |
| Maximum chunk size | Capped at the chosen model's published token window |

A typed model name has nothing behind it until a container is published, and
the failure surfaces as an HTTP 404 at the first embed call — after the crawl
and the chunking have already run. A vector column is created at the model's
width and cannot be widened afterwards. And text beyond a model's token window
is dropped by the model silently rather than rejected, so an oversized chunk
is embedded from its opening only — retrieval then matches on text the answer
never sees, with nothing in the run reporting a problem. The Builder asks for
the model first for that reason: the ceiling has to be known before anyone
picks a chunk size under it.
:::

### What the Builder's buttons create — and what they clean up

| Button | What it leaves in your environment |
| --- | --- |
| **Save setup** | The RAG Setup model in SAS Model Manager |
| **Manifest setup** | Saves, then the ingestion job definition and the Studio flow in the *Generated artifacts* folder, `retrieve_context.py` as the setup's score code, and a new minor model version |
| **Launch ingestion** | A job run, the collection, and the pipeline tables in the chosen caslib |
| **Browse ledger** | Nothing — reads the promoted ledger table |
| **Test retrieval** | **Nothing.** See below |

*Test retrieval* asks the live collection a question and shows the chunks that
come back, with what the probe cost. It is deliberately built to leave no
trace: the job definition it runs is created **without a parent folder**, so it
never appears anywhere in SAS Content, and it is deleted when the run ends —
including when the run fails or is abandoned. The hits travel back in the job
**log** rather than in a CAS table, because an output table would itself be an
artifact of a test meant to have none. The finished job's own record and log
remain in SAS Job Execution, as for any job run.

It exercises the **collection**, not the registered score code: it queries
through `rag_core` exactly as the ingestion does, so a broken
`retrieve_context.py` would still pass. Verifying that needs the published
model.

Both *Launch ingestion* and *Test retrieval* need the **ingestion compute
context** option to be set — SAS Job Execution rejects a job that does not name
one, and the rejection carries no log. The Builder checks first and names the
missing setting.

## Enrichment: an LLM call per chunk

Off by default. A chunk that reads perfectly in place is often meaningless on
its own — *"revenue grew 12%"* says nothing about whose revenue, or when — and
the published remedy is to prepend a short LLM-written statement situating it
in its document, then embed the two together. The Builder's **Enrichment**
section is that slot, and it is general: the same call can classify a chunk,
pull out an effective date, or flag personal data. Which of those it does is
decided by the **prompt**, not by the pipeline.

**The prompt is a Prompt Builder artifact, not a setting here.** Build and
evaluate it in the Prompt Builder, manifest it, and point the RAG setup at that
Model Manager model. The prompt then has its own documentation, versions and
permissions, and improving it does not mean editing a RAG setup.

It must be manifested with the **integrated LLM call**. That option makes the
score code perform the call and return the answer, which is what the ingestion
reads. The other form returns `llmBody`/`llmURL` for the Call LLM node of SAS
Intelligent Decisioning — the request to make rather than its result — and the
Builder and the ingestion both refuse it by name.

| You choose | What it does |
| --- | --- |
| **Prompt project** and **prompt** | Which manifested prompt is called, once per chunk |
| **Prompt version** | *Latest* follows the prompt; a pinned version freezes exactly the prompt that version carried |
| A chunk field for each **prompt input** | What the prompt is given: the chunk, the whole document (capped at 20,000 characters), the neighbouring chunks, the heading path, the file name, the full source location, or the position (*"chunk 3 of 42"*) |
| The output stored as the **context header** | Prepended to the chunk and embedded **with** it — this is the part that changes retrieval |
| Outputs stored as **columns** | Each becomes its own typed column on the chunk table, so it can be selected, filtered and aggregated |

Nothing is guessed for a prompt input whose name is unfamiliar: a silently
wrong mapping produces a whole corpus of confident nonsense, so the setup will
not save until every input is mapped.

### What the extracted values become

A stored output is a **real column**, named after the output and typed from the
prompt's registered output variables — a `string` becomes text, a `decimal`
becomes a double, so a confidence score can be averaged rather than parsed out
of a JSON blob. Retrieval returns them alongside the chunk.

Three rules govern the schema, and all three are reported in the run log rather
than applied quietly:

- **A new column is not backfilled.** Add an output to the prompt and the
  column appears on the next run, holding null for every chunk written before
  it. That is indistinguishable from an LLM that had nothing to say, so the log
  states it explicitly; only a full re-ingestion (bump the pipeline version)
  fills it in.
- **A column you stop producing is not dropped.** It keeps whatever the prompt
  that wrote it said. Removing data is never a side effect of editing a prompt.
- **A name the table already uses is refused**, by name, in the Builder and in
  the ingestion. `score`, `content`, `page` and `rank` are all plausible things
  to ask an LLM for and all belong to the chunk schema — rename the output in
  the prompt, or don't store it.

### Latest, or pinned

*Latest* re-reads the prompt on every run, so improving it in the Prompt
Builder changes what the next ingestion writes — **without** re-processing
anything already stored. Pinning freezes a version, which is what you want once
a corpus is in production and you want a prompt change to be a deliberate act.

Either way, every enriched chunk records **which prompt at which version wrote
it** in `enrich_version`, so a collection that legitimately holds work from two
prompts can be read that way rather than merely being that way.

Version labels (`1.0`, `1.1`) are **not unique** — the Builder shows the date
beside each and stores the version's id.

:::warning A pinned version can lose its files
A version's snapshot does not always keep its score code — an older model on
our own environment returned its file listing but no content. Both the Builder
and the ingestion refuse an empty prompt rather than running one, naming the
version. If a pin stops working, pin a different version or return to *Latest*.
:::

### What it costs, and what that buys

**One LLM call per chunk, repeated on every re-chunk** — this is normally the
largest line in an ingestion, an order of magnitude above the embedding. It is
priced in the run log while the run is happening, and recorded per run in
`rag_runs` (`enrich_calls`, `enrich_input_tokens`, `enrich_output_tokens`,
`enrich_seconds`, `enrich_failed`) so `RAG_RUN_COST` reports embedding and
enrichment side by side with a total.

A prompt manifested **without** the `prompt_length` and `output_length` outputs
returns no token counts, and a token-priced model then has nothing to multiply.
That reads as *unknown* rather than as zero — select those outputs when you
manifest the prompt if you want the cost.

On the benefit: the widely cited 35–49% reduction in retrieval failures comes
from one vendor on one corpus, with a Claude-class model generating the
headers, and the affordable price in that write-up depends on prompt caching
over the parent document, which a self-hosted container does not have. Treat it
as a technique to measure on your own corpus, not as a number to expect. The
cheapest first check is *Test retrieval*, which shows the stored header beside
each chunk once a collection has been enriched.

### Consequences worth knowing before you turn it on

- **Enrichment is deliberately NOT part of the drift fingerprint.** Changing
  the prompt, the mapping or the stored outputs applies from the next run
  onward and never demands a re-ingest. The trade is explicit: a collection can
  hold headers written by two different prompt versions, and `enrich_version`
  on each chunk is what makes that visible. The drift guard still covers the
  chunker, the token window and the embedding model — the settings that change
  the vectors themselves and would make a collection unreadable.
- **A hallucinated header is permanent and invisible at query time.** It is
  baked into the vector and into the stored chunk. Read a few through *Test
  retrieval* before building a large corpus on a new prompt.
- **Failure is per chunk.** A call that fails, or a response that was not the
  JSON the prompt asked for, leaves that chunk **without** a header and it is
  embedded plain rather than failing the run. A parsed output is never stored
  from the prompt author's default value — a plausible answer no model
  generated is worse than none. The count is named in the run log and in the
  run summary, because a corpus where a tenth of the chunks have no header
  retrieves differently from one where all of them do.
- **The prompt's score code is executed** in the ingestion compute session,
  exactly as SAS Container Runtime would execute it. That is what scoring a
  model is; the control is Model Manager permissions on the prompt project.
  Do not point a setup at a model you would not run.
- **A prompt calling a hosted LLM needs that provider's key** in the same
  credential domain as the store credentials, under the provider's name
  (`OpenAI`, `Anthropic`, …). A prompt whose LLM runs as a local container
  takes no API key and needs nothing. The mapping from model to provider lives
  in `rag_core/providers.py`; a model missing from it fails at load with that
  said, rather than sending a wrong key on its behalf.

In a SAS Studio flow the stage is the **RAG - Enrich Chunks** step, wired
between *Chunk Documents* and *Embed Chunks*. The Builder adds it to the
generated flow only for a setup that enriches.

## Two prerequisites that are not optional

Both of these produce confusing failures if missed, so check them first.

### 1. A compute context that runs as the requesting user

The steps create a CAS session in SAS and reuse it from Python, because the
staged table has to be visible to the promote that follows. CAS only lets an
identity reconnect to its **own** session.

The stock **SAS Job Execution compute context** runs the server as a service
account (`sas-be-sa` on a default deployment) while the CAS session belongs to
the user who launched the job. The two do not match, and the first step fails.
The step detects this and says so, but the fix is environmental:

> Run RAG flows and jobs in a context that runs as the **requesting user**.
> The **SAS Studio compute context** does; a full five-step ingestion is
> verified running there as a job.

Submit with that context explicitly:

```json
{ "arguments": { "_contextName": "SAS Studio compute context" } }
```

The same service-account mismatch also prevents credential-domain lookups, so
one context change fixes both.

### 2. A credential domain holding the store credentials

Vector-store credentials come from a SAS Viya credential domain (default
`agentic-ai-keys`), never from the flow. Entries are **backend-prefixed** so
one domain can serve several stores:

```
PGVECTOR_RAG_USER      PGVECTOR_RAG_PW
SINGLESTORE_RAG_USER   SINGLESTORE_RAG_PW
```

The same domain also carries **where** each store lives. These are not
secrets, but the domain is the one place every identity can already read, so
the Builder resolves the connection instead of asking users for a hostname
they should never have to hold:

```
PGVECTOR_HOST     PGVECTOR_PORT     PGVECTOR_DB     PGVECTOR_SSLMODE
SINGLESTORE_HOST  SINGLESTORE_PORT  SINGLESTORE_DB  SINGLESTORE_SSLMODE
RAGSTORE_HOST     RAGSTORE_PORT     RAGSTORE_DB     RAGSTORE_SSLMODE
```

`RAGSTORE_*` is the unprefixed fallback for any backend, the same precedence
the runtime already applies to a `.env`. A value passed explicitly — a host
typed into a step — still wins over the domain, so the steps are unaffected.
Without `<BACKEND>_HOST` and `<BACKEND>_DB` the Builder shows the store as
unresolved and refuses to save a setup that could not ingest.

Author them with `create-credential-domain.ps1`/`.sh`, which reads your
git-ignored `.env`. See [Managing Credentials](./Managing-Credentials.md).
Equip **every identity that will run a flow or job** — including the service
account, if you are not using a run-as-requesting-user context.

## Python packages

`rag_core` cannot install anything: `sas-pyconfig` environments are read-only.
The compute context's Python needs:

| Package | Needed for |
| --- | --- |
| `requests`, `pandas`, `swat` | Always |
| `psycopg2-binary` | The pgvector backend |
| `singlestoredb` | The SingleStore backend (already present on many builds) |
| `pypdfium2` | PDF text extraction |
| `markitdown` | `.docx`, `.xlsx`, `.pptx`, `.epub`, `.rtf`, `.msg` |

`.eml` needs nothing beyond the standard library. A missing package disables
only the extractor that needs it — the registry reports it rather than failing
the run.

**Source-code files are skipped by default.** `.py`, `.sas`, `.r`, `.js`,
`.ts`, `.sql` and around thirty more are not ingested unless a setup ticks
*Ingest source-code files as plain text*, because a documents folder that sits
inside a project would otherwise fill the collection with build scripts. They
are listed as skipped with the reason, never dropped silently.

## Choosing a vector store

Both backends carry the same feature set: chunk history, as-of reads,
rollback, the portable filter grammar and the governance DDL.

| | pgvector | SingleStore |
| --- | --- | --- |
| Live-row marker | `valid_to IS NULL` | sentinel `9999-12-31` |
| Cosine | native `<=>` | normalized vectors, dot product |
| ANN index | HNSW, **on by default** | **opt-in** — see below |
| Index covers only live rows | yes (partial index) | no |
| Cutover | transactional rename | rename, not transactional |

:::warning The SingleStore ANN index is opt-in for a reason
Measured on a live cluster, an HNSW index there **loses rows**: the same query
returned 1, 2, 1, 0, 1 and 1 rows for `LIMIT` 1 to 9 on a six-row collection,
where exact search returned the correct 4 every time. Neither `SEARCH_OPTIONS`
nor `OPTIMIZE TABLE` changed it. Approximate search may return an imperfect
*order*; returning fewer chunks than exist is a different thing, and for
retrieval that feeds an answer it fails silently. Exact search is therefore the
default. If a collection is large enough to need the index, enable it with
`schema={"ann": True}` and **validate recall on your own data first**.
pgvector was checked for the same defect and does not have it.
:::

## Retention and erasure

Three different operations, deliberately separate:

| Operation | Where | Reversible |
| --- | --- | --- |
| Retire (default) | Automatic on re-ingestion | Yes — `restore()` |
| Deleted-document policy | **RAG - Load Vector Store** | No |
| Erasure | **RAG - Purge Documents** | No |
| History retention | `retainDays` on the Load step | Live rows untouched |

Retiring keeps previous chunk generations so a collection can be read as it
stood on an earlier date and a bad run can be rolled back. Purging removes
chunk rows **including retired generations** and drops the ledger entries.

:::danger Erasure is not deletion from the source
If a purged document still exists at its source, the next ingestion run adds
it again. Erasure has to happen at the source as well.
:::

On SingleStore, retention matters more than on pgvector: a vector index there
cannot be limited to live rows, so retained history is index the search walks.

## Run history

Each run is recorded in three tables **in the vector store's own database**,
beside the chunks they describe:

| Table | Contents |
| --- | --- |
| `rag_runs` | One row per run: document counts, chunk counts, embedding cost, and the enrichment cost when the setup has an Enrich stage |
| `rag_doc_events` | Append-only; one row per document per run *where something happened* |
| `rag_configs` | The parameters behind a `config_id`, so the hash has an inverse |

They are not in CAS because a CAS table here is overwrite-in-place by
construction, has no transactional append and no constraints, and an empty run
deletes the saved file outright. The cost of that choice — history invisible to
Visual Analytics — is paid explicitly: the Load step **publishes `rag_runs` and
`rag_doc_events` to CAS** after each run as `<project>_RUNS` and
`<project>_DOC_EVENTS`. The database copy is authoritative; the CAS copy is
disposable and rebuildable.

History never fails a load. If the store refuses the write, the ingestion still
stands and the log says so.

### Skipped is not failed

A document the pipeline decided not to ingest — no extractor for its format, a
source file with code ingestion off, a scanned PDF with no text layer — is
recorded as `skipped` with the reason. `failed` is reserved for something a
person can fix. Collapsing the two made every run look broken and hid the rows
that were. Both are counted separately in `rag_runs` (`docs_skipped`,
`docs_failed`), and every skipped document is **named in the run log**, grouped
by reason.

`docs_skipped` is added to an existing `rag_runs` table by an additive
migration on the next run, so an upgraded deployment needs no manual DDL. The
same applies to the six enrichment columns: a collection built before the
Enrich stage existed keeps answering, and its runs simply record no LLM cost.

The run log also states **what the embedding and the enrichment cost** while
the run is still on screen, and spells out where the chunk token budget came
from — it is the setup's chunk window minus the estimator's safety margin, not
a limit the model imposed.

## Security posture

**Secrets.** Store credentials live only in the credential domain, resolved
per identity at run time. They are never written to a flow, a CAS table, a
WORK file or a log. The `.env` file is git-ignored and is read only by the
admin scripts on your workstation. On SCR and MAS destinations — which have no
SAS Viya session — the same names are supplied as environment variables.

**What crosses which boundary.** Document text leaves the source only to reach
the extractor, the embedding container and the vector store, all inside your
deployment. The embedding model runs as a governed SCR container you publish.

Two settings can change that, and both are explicit choices:

- an **embedding model that forwards to a hosted API** (the fact sheet's
  `deployment_type` says which) sends the chunk text to that provider;
- an **Enrich stage whose prompt calls a hosted LLM** sends the chunk, and
  whatever else the mapping gives it — including up to 20,000 characters of the
  surrounding document — to that provider, once per chunk.

Both are visible on the setup, and neither happens by default: the shipped
embedding default and every enrichment prompt built on a locally served model
stay inside the deployment.

**Identity.** Everything runs as the identity that launched it, which is why
the run-as-requesting-user context matters: it keeps the audit trail truthful
as well as making the steps work.

**Error text.** Database error messages are stored in the ledger's
`error_text` and shown in step summaries. Some stores put internal cluster
hostnames in their own error messages, so that column can contain
infrastructure detail. Treat the ledger table as internal, or restrict the
caslib holding it.

**What is not encrypted by the accelerator.** Chunk text and embeddings sit in
your vector store in the clear. Encryption at rest is the database's
responsibility. TLS to the store is configurable per backend (`sslmode` for
PostgreSQL, on/off for SingleStore) and defaults to enabled.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| *"cannot run the RAG steps … different identities"* | Wrong compute context — see above |
| *"no credential resolved from domain"* | The running identity has no credential, or a service account is running the job |
| A step renders as an empty editor | The `.step` file was uploaded as a plain file instead of registered |
| A saved flow reports a missing step | A step was re-registered instead of updated, minting a new id |
| Code generation returns HTTP 500 | The `.flw` was uploaded as a file, or a node declares an input port nothing is wired into |
| Retrieval returns fewer chunks than exist | A SingleStore ANN index is enabled — see the warning above |
| The run refuses to start | Another run holds the ledger lock; it expires after 30 minutes |
| The Builder says the store is unresolved | The domain has no `<BACKEND>_HOST` / `_DB` — rerun `create-credential-domain` after adding them to your `.env` |
| The embedding model has to be typed | No **embedding model project** is configured, or the user cannot read it |
| Documents are missing from the collection | Read the run log's `rag skipped:` lines — they name every document and why. Code files are skipped unless the setup opts in |
| The Builder points at the wrong environment after an upgrade | A report import replaced its Options — restore them with `mdb options-restore` |
| Browsing the ledger shows nothing after a run | The ledger is written by the run's final step; a large corpus takes minutes. The run panel's clock shows whether it is still going |
