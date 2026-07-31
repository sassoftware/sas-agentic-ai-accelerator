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
| `steps/` | The eight custom steps, registered as dataFlows resources |
| `jobs/` | `Ingest-Documents.sas`, the standalone schedulable job |
| `models/` | The retrieval model template, manifested per setup |
| `generated/` | Per-setup artifacts written by **RAG - Register Setup** |

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
| `form` property | the contents of `dist-rag/rag.html` |
| URL | `https://<host>/SASJobExecution/?_program=<path>&_action=form` |

:::note The HTML is the FORM, not the code
`&_action=form` serves the job's **form**. Putting the HTML in `code`
produces *"Parameter Error — HTML form file was not found"*, which reads like
a missing file rather than the wrong field.
:::

The Content Security Policy directives in
[Setup Additional UIs](./Setup-Additional-UIs.md) apply here too — the
build base64-encodes its inline scripts so the Go template engine cannot
corrupt them, and the CSP has to allow the decoded bundle to run.

Configure the Builder from its **Options** pane in Visual Analytics: the
Model Manager repository, SCR endpoint, credential domain, content root, CAS
server, the ingestion compute context, a Yes/No per vector database the deployment offers, and the operational policy (TLS, deleted-document handling, history
retention, run-history recording, embedding replicas, table persistence).
Those policy values are recorded onto each setup as it is created, so a
setup keeps what it was built with.

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
| `rag_runs` | One row per run: document counts, chunk counts, embedding cost |
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

## Security posture

**Secrets.** Store credentials live only in the credential domain, resolved
per identity at run time. They are never written to a flow, a CAS table, a
WORK file or a log. The `.env` file is git-ignored and is read only by the
admin scripts on your workstation. On SCR and MAS destinations — which have no
SAS Viya session — the same names are supplied as environment variables.

**What crosses which boundary.** Document text leaves the source only to reach
the extractor, the embedding container and the vector store, all inside your
deployment. Nothing is sent to an external service. The embedding model runs
as a governed SCR container you publish.

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
