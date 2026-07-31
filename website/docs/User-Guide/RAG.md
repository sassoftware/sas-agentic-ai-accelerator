---
sidebar_position: 4
title: RAG
---

Retrieval-augmented generation turns a folder of documents into something a
decision can quote. This guide builds a working pipeline with the shipped
custom steps, then asks it questions. For deployment and the environment
prerequisites, see the
[RAG administration guide](../Administration-Guide/RAG-Ingestion-and-Retrieval.md).

## The idea in one minute

Documents are crawled, their text extracted, cut into chunks, embedded through
a governed SCR container and loaded into a vector store. A question is embedded
the same way, and the closest chunks come back as context. Everything in
between is bookkeeping — but the bookkeeping is what makes it operable:

- a **ledger** remembers what was ingested, so a second run only processes what
  changed;
- chunks carry **lineage**, so a collection can be read as it stood last month
  and a bad run rolled back;
- every chunk knows **where it came from**, so an answer can be traced to a
  passage in a file.

## Build the pipeline

Create a Studio Flow and chain five steps. Each passes a document inventory to
the next; the bulk data travels through CAS tables named after your project.

```
RAG - List Documents → Extract Text → Chunk Documents → Embed Chunks → Load Vector Store
```

**RAG - List Documents** is where you name things. Pick the folder holding your
documents — a compute-server path *or* a SAS Content folder — and give the
project a short name. Every later step inherits the project and caslib from the
inventory, which is why they do not ask again.

**RAG - Extract Text** turns files into text. It picks an extractor by file
extension: Markdown, HTML, plain text, CSV/JSON, PDF, email (`.eml`) and the
Office family. Leave the extractor on *Automatic* unless you have a reason.

**RAG - Chunk Documents** cuts the text to fit the embedding model's window.
The default recursive chunker splits on paragraphs, then lines, then sentences,
and overlaps consecutive chunks slightly so a sentence cut in half is still
findable. The token window must match your embedding model.

**RAG - Embed Chunks** calls the SCR container. Re-running is cheap: chunks
whose content has not changed keep their vectors, so a second run embeds only
what is new.

**RAG - Load Vector Store** writes to the collection and finalises the ledger.
This is where you choose the backend, the collection name and the connection —
the password comes from the credential domain, never from the flow.

Run the flow. Each step prints a readable summary to the log, and its output
table lands in your project's caslib.

## Ask it something

Add **RAG - Retrieve Context** — anywhere, it needs no wiring. Type a question
(several, one per line), point it at the collection, and run. You get one row
per matching chunk with its rank, distance, source file, heading and character
span, plus the corpus version that produced it.

This is the same retrieval a deployed decision performs, which makes it the
fastest way to answer "what would this collection actually say?" — and to
compare two configurations against the same questions.

:::tip Use the same embedding model
The question must be embedded with the model the corpus was built with. A
different model does not error — it places the question in a different vector
space and returns confident nonsense.
:::

## Publish it

**RAG - Register Setup** turns a finished ingestion into something consumable.
It manifests the retrieval model for *your* collection, registers it in SAS
Model Manager as a scoreable Python model, and writes the governance artifacts
next to it: `pipeline.yaml`, the ingestion manifest (which documents, at which
content hash, from which run), the collection manifest and the store DDL a DBA
can review.

Point it at your flow as well and it generates the **scheduled ingestion job**
from that flow, so the visual pipeline and the scheduled one cannot drift
apart. Everything is idempotent — re-registering updates rather than
duplicating.

## Keeping the corpus current

Re-run the flow. Documents are fingerprinted, so unchanged ones are skipped
entirely; changed ones are re-chunked and their previous chunks **retired**
rather than deleted. Retired chunks never reach a retrieval, but they stay in
the collection so you can read it as of an earlier date, or roll a run back.

To force everything to be re-processed — after changing the chunker or the
embedding model — bump the **pipeline version**. That is the sanctioned way to
change how chunks are made: the drift guard refuses a changed configuration on
an unchanged version, because a corpus half-processed each way retrieves
unpredictably.

## Removing documents

| You want to… | Use |
| --- | --- |
| Let vanished documents fade into history | Nothing — this is the default |
| Remove vanished documents for good | *When a document disappears* → **Remove its chunks** on the Load step |
| Erase named documents now | **RAG - Purge Documents** |
| Stop history growing forever | *Keep retired generations for N days* on the Load step |

**RAG - Purge Documents** takes documents the way you know them — a file name,
a full path, or a doc id — and resolves them against the ledger. It runs in
**Preview** mode by default and reports exactly what would go before anything
does.

:::danger Purging cannot be undone
It removes retired generations too, so an as-of read can no longer reach the
content and a rollback cannot bring it back. And if the document still exists
at its source, the next run adds it back — erasure has to happen there too.
:::

## Seeing what happened

Every run is recorded. Two CAS tables appear next to your pipeline tables:

- `<project>_RUNS` — one row per run: what it **found** (new, changed,
  unchanged, deleted) and what it **achieved** (ingested, failed), plus chunk
  counts and what the embedding actually cost in calls, tokens and seconds.
- `<project>_DOC_EVENTS` — the change log: one row per document per run *where
  something happened*, with the content hash and chunk count before and after.

Point Visual Analytics at them for corpus size over time, which documents keep
failing, or what last month's ingestion cost.

## Things worth knowing

**Two runs cannot collide.** The first step takes a lock on the ledger. A
second run refuses to start while the first holds it, and a failed run hands
its lock back so you can retry immediately. A stale lock expires after 30
minutes.

**A bad document does not fail the run.** Extraction and embedding failures are
recorded against that document's ledger row; everything else proceeds. Failed
documents re-enter the pipeline on the next run until they succeed or
disappear.

**A step that fails does not abort the session.** The rest of the flow skips
and forwards, which is what makes the same flow safe to schedule as a job.

**Citations may lack a location.** A chunk whose position in its document
cannot be established carries no span rather than a guessed one — a wrong
citation is worse than an absent one.
