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
| `jobs/` | `Ingest-Documents.sas`, the standalone schedulable job, and `Test-Retrieval.sas`, the probe behind the Builder's *Test retrieval* |
| `models/` | The retrieval model template, manifested per setup |
| `generated/` | Default destination for per-setup artifacts — the ingestion job and the Studio flow the Builder generates, and what **RAG - Register Setup** writes. The Builder lets an author choose a different folder per setup |

This is the **RAG runtime**. It holds no user interface: the RAG Builder that
writes setups against it is part of the Prompt Builder — see
[Serving the RAG Builder](#serving-the-rag-builder) below.

Two ways put the runtime there:

1. **The transfer package.** `mdb builders-import` imports the content folder,
   and the runtime is the folder `RAG` of it — see
   [Install or upgrade with the transfer package](./Setup-Additional-UIs.md#the-transfer-package).
   The package is compared with the repository before it ships, file by file
   and step by step, so a site that imports the package of a release has the
   runtime the documentation and the changelog of that release describe, and
   needs no script.
2. **From a checkout.** The shipped script publishes the runtime of your
   checkout — for a fix between two packages, or for a site that does not
   import the package:

```powershell
./SAS-Viya-Integrations/Other/deploy-rag-content.ps1
```

```bash
./SAS-Viya-Integrations/Other/deploy-rag-content.sh
```

The script calls `mdb content-deploy --only RAG --to <the RAG content root>`
(see [One part, somewhere else](./Model-Definition-Builder.md#one-part-somewhere-else)).
It needs the Model Definition Builder installed and a sign-in it can use — a
token, a password grant or the login of the SAS Viya CLI. The folder it deploys
into is `/SAS Agentic AI Accelerator/RAG`; `-ContentRoot` (PowerShell) or `-r`
(bash) names another one: the RAG content root itself, a folder of any name,
which is what the option **RAG: content root** of the Prompt Builder holds.
A site with such a content root deploys the runtime there after an import,
because the import brings it into the folder `RAG` of the content folder.
`-DryRun` / `-n` lists what would change and writes nothing. A folder that is
not there is created only with `-Create` / `-c` (with every folder above it),
so a mistyped path does not become a new folder. A warning `mdb` writes to the
error stream - `InsecureRequestWarning` where TLS verification is off - does
not stop the PowerShell script, also with its streams redirected, and both
scripts end with the exit code of `mdb`. The command they call, `mdb
content-deploy --only RAG --to <folder>`, created the 41 entries of the runtime
directly in a test folder on SAS Viya 2026.09 on 2026-10-12. On 2026-10-13
the PowerShell script ran as a dry run for a test folder, with every stream
redirected into a file and its command line saved beside it: the file held
the warning and `41 entries: 41 equal.`, and the request log GET requests
only; the exit code of the script was not saved. **Not run against SAS
Viya**: the bash script, a run without `-DryRun` / `-n`, and `-Create` / `-c`.

**Nothing is ever fetched from the internet at run time**, so both ways work
in an air-gapped deployment: the SAS Content folder is the distribution
channel, not GitHub.

If a deploy ends with a line that says `FAILED`, read the line
`Stopped at the first write that failed: ...` under it and run the deploy
again: it stops at the first write that is refused or gets no answer, lists
what it did not attempt, and removes nothing, so the folder holds the file
it had or the new one. Up to release 2.2 a file was replaced by deleting it
and uploading the new copy. Measured on 2026-10-11 on SAS Viya 2026.09 with
`deploy-rag-content.ps1` as it was then — a script that uploaded the files
itself —, against a content folder that held the runtime of release 2.0:
the first run stopped after four files with `(409) Conflict` on the upload
of a file (`POST /files/files`) whose old copy it had just deleted, and left
that file missing. The same upload was answered 201 a minute later, and a
second run of the script completed with *Deployed 35 files*. All nine steps
had kept their ids, and every file equalled the checkout afterwards. A third
run later the same day went through at the first attempt.
`mdb content-deploy` deletes no file: it replaces the content of a file that
differs in place, under the id the file has. That update has **not run
against SAS Viya**.

:::note Why a deploy and not a manual upload
A `.step` file uploaded as a plain Content file renders as an empty step
editor — steps are dataFlows *service* resources. The deploy also **updates**
an already-registered step in place rather than replacing it. Re-registering a
step mints a new id, and every saved flow referencing the old id breaks.
:::

### After an upgrade: manifest every setup once {#after-an-upgrade}

A new runtime in the folder does not reach what a site made from the earlier
one. After the package of a new release was imported, or its runtime deployed
from a checkout:

- **Open every RAG setup in the Prompt Builder and click Manifest setup.** A
  retrieval model holds the score code it was manifested with, and the job
  definition of a setup holds a copy of `Ingest-Documents.sas` from the day it
  was manifested; the manifest writes the template and the program of the
  release into them. For release 2.3.0 that is what brings the endpoint a
  setup embeds at ([Where a setup embeds](#where-a-setup-embeds)), the TLS
  setting of the report, the lengths of the variables
  ([The lengths of the variables](#the-lengths-of-the-variables)), the saved
  ledger that is loaded back
  ([A saved ledger is loaded back](#a-saved-ledger-is-loaded-back)), the run
  lock that follows the column types of the ledger, and a failed run that ends
  as a failed job.
- **Run the ingestion of each setup once.** From release 2.3.0 on the job
  publishes `<prefix>_RUNS` and `<prefix>_DOC_EVENTS` beside the ledger, and
  that one run publishes every run the vector store holds for the collection
  ([Run history](#run-history)). A job definition that was not manifested
  again holds the program of the day it was made and publishes neither table.
- **Publish the decisions that use the retrieval model again**, so that the
  destinations run the new score code. No published decision has to be
  repaired for the lengths of the variables.
- **A setup that was registered from a flow** gets the new score code when the
  step *RAG - Register Setup* runs again
  ([the checkbox of the step](#tls-register-setup-step)). A job that the step
  generated from a flow is not written by **Manifest setup**: run the step
  again with the flow named under *Studio Flow to generate the scheduled job
  from*. A job that SAS Studio itself deploys from the flow does not have the
  statements that report a failed run as failed.
- **A schedule, a monitor or a script that reads the state of the ingestion
  job** has to expect a new state from release 2.3.0 on: a run that failed
  ends as `failed`. Up to 2.2.x, and with a job definition that was not
  manifested again, it ends as `completed` with *ERROR: RAG ingestion
  failed: …* as the last message of the program in its log.

## Serving the RAG Builder

The RAG Builder is not an application of its own. It is the **RAG setup** kind
of the Prompt Builder: the same single-file app, the same job definition
(`/SAS Agentic AI Accelerator/Prompt Builder/SAS-LLM-Prompt-Builder-UI`) and
the same Data-Driven Content object in the **Prompt Builder** report. There is
nothing to build and no job definition to create for it — deploy the Prompt
Builder as described in
[Deploying the Builder UIs](./Setup-Additional-UIs.md), then switch RAG on:

1. Import the package or deploy the runtime (above), so the content root holds `rag_core`, the
   steps, the jobs and the model template.
2. Open the Prompt Builder report, select the object and open its **Options**
   pane. The release table `Public.ACCELERATOR_RELEASES` has to be assigned to
   the object, as for every use of it — see
   [step 5](./Setup-Additional-UIs.md#5-add-the-object-to-a-visual-analytics-report).
3. Tick **Enable RAG setups**. The options that start with `RAG:` appear
   below the checkbox.
4. Fill them in: the **embedding model project**, the content root, the CAS
   server, the **ingestion compute context**, a checkbox per vector database
   the deployment offers, and the operational policy (TLS, deleted-document
   handling, history retention, run-history recording, embedding replicas,
   table persistence). The Model Manager repository, the SCR endpoint, the
   deployment type and the credential domain are the ones the Prompt Builder
   already has; they are set once and used by every kind. The whole list, with
   defaults, is in
   [Configuration](./Setup-Additional-UIs.md#configuration-properties-panel).

The Setup card of the Prompt Builder then offers *RAG setup for a document
collection* beside the template kinds, and choosing it shows the Builder's own
four steps. While the switch is not ticked — the default — the kind is not
offered, nothing of the Builder is loaded and nothing RAG is requested from
SAS Viya. With it enabled, the first request for RAG is made when a user
chooses the kind.

The policy values are recorded onto each setup as it is created, so a setup
keeps what it was built with.

:::note Three settings worth checking when RAG does not behave as expected
- **The credential domain must not be `none`.** Prompt and decision templates
  read `none` as "look no key up". RAG has no such mode — the ingestion and the
  retrieval take the store's user, password, host and database from the
  domain — so the RAG card is shown disabled and says why.
- **The Model Manager repository ID must be set.** Every kind needs it; without
  it the kind is not offered.
- **The ingestion compute context is not the optimization compute context.**
  The option is `ragComputeContext` (**RAG: ingestion compute context**); the
  option `computeContext` belongs to the prompt optimization job and is never
  read for an ingestion. A blank `ragComputeContext` does not hide the kind:
  it blocks *Launch ingestion* and *Test retrieval* with a message that names
  the setting.
:::

:::warning These options live inside the report
Importing a newer Prompt Builder report from a transfer package replaces every
value above with whatever the package was built against — silently, since the
report still opens and the app still runs, with RAG switched off. Save them
with `mdb options-save` before importing and restore them after: see
[Preserving builder options across a report import](./Setup-Additional-UIs.md#preserving-builder-options-across-a-report-import).
That section also covers a site that still has the retired report named
**RAG Builder** from release 2.2 or earlier.
:::

Served standalone rather than embedded, the same settings are query
parameters of the Prompt Builder job — the switch included, because it is off
by default:

```
.../SASJobExecution/?_program=/SAS Agentic AI Accelerator/Prompt Builder/SAS-LLM-Prompt-Builder-UI&_action=form&enableRag=true&modelRepositoryID=<uuid>&embeddingProjectID=<uuid>&ragComputeContext=<context name>
```

(The browser encodes the blanks.) Without an LLM project in the URL the page
shows the RAG Builder alone; add `llmProjectID` and `SCREndpoint` to get the
template kinds beside it. A URL written for the job definition
`RAG-Builder-UI` of release 2.2 or earlier has to be rewritten this way: that
definition is retired, its context parameter was called `computeContext`, and
it needed no `enableRag`. `id` is no longer read from the URL. For your own builds: `npm run build:rag` is gone, and `npm run build` produces the one `dist/index.html` that holds all three kinds.

:::note If you replace the app by hand: the HTML is the FORM, and the form is a *property*
`mdb builders-import` and the manual steps in
[Deploying the Builder UIs](./Setup-Additional-UIs.md#2-create-a-sas-job-execution-definition)
put the built `dist/index.html` into the job definition for you. Updating the
definition through the REST API instead has two ways to go wrong, both of
which look like something else:

- `&_action=form` serves the job's **form**. HTML in `code` produces
  *"Parameter Error — HTML form file was not found"*, which reads like a
  missing file rather than the wrong field.
- The form is an entry **inside the definition's `properties` list**, named
  `form` — not a top-level `form` key. A PUT carrying a top-level key returns
  **200 and silently drops it**, so the deploy looks successful while the old
  app keeps serving. Verify by reading the property back, not by trusting the
  status code.
:::

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

### One ledger for the job and the flow {#one-ledger-for-the-job-and-the-flow}

**Manifest setup** generates two things that ingest the same setup: the
ingestion job, which is the one program `Ingest-Documents.sas`, and the SAS
Studio flow, which is made of the RAG custom steps. Both keep the same
ledger, `<prefix>_LEDGER`, and both take the same run lock on it: a row
that the start of a run appends to the table. They do not store the ledger
alike:

| The ledger was last written by | Its text columns are |
| --- | --- |
| the ingestion job | `CHAR`, with fixed lengths |
| the flow (*RAG - Load Vector Store*), or *RAG - Purge Documents* | `VARCHAR` |

CAS does not append a row whose column is `VARCHAR` where the table has
`CHAR`, or the other way round. Up to release 2.2 each of the two declared
its lock row the one way it stores the ledger itself. Seen on 2026-10-11 on
SAS Viya 2026.09: a setup ingested with **Launch ingestion**, then its
generated flow run in SAS Studio, stopped in the first step with

```
ERROR: The variable doc_id has been specified with incompatible data types.
ERROR: The action stopped due to errors.
ERROR: RAG - List Documents failed: the run lock could not be written to <caslib>.<prefix>_LEDGER - refusing to ingest unguarded
```

and left the ledger as it was. The append the other way round — a `CHAR`
row to a `VARCHAR` table, which is the job on a ledger a flow wrote — was
refused with the same text in a scratch caslib. The job itself was not run
on a ledger a flow had written before the change; by its code it did not
look whether its lock had been written, so it would have gone on without
one.

From release 2.3.0 the job, *RAG - List Documents* and *RAG - Purge
Documents* ask the table for its column types (`table.columnInfo`) before
they take the lock, and declare the lock row the way the table holds its
text. The log of the run says which it found: *NOTE: the ledger … holds its
text columns as char - the run lock is written the same way*, or *as
varchar*. The steps that read the ledger compare its values without the
blanks a `CHAR` column is padded with. Nothing has to be migrated: a ledger
of either type is used as it is, and it still changes its type with the
writer that last completed a run. The two were not made to store the ledger
alike; that would have changed how the last step of a flow, or the job,
writes the table, and neither change could be measured.

The new lock is in the steps and in the job, which reach a site with
the transfer package, or from a checkout with `deploy-rag-content`. The job definition of a setup holds a copy of
`Ingest-Documents.sas` as it was when the setup was manifested, so manifest
the setup again afterwards; whether a flow generated earlier runs the
redeployed steps without that was not checked.

**Measured on 2026-10-11 on SAS Viya 2026.09**, with the ingestion job and
the generated flow of one setup, in both directions:

- **The job on the ledger it had written** (`CHAR`). The log said *NOTE: the
  ledger Public.…_LEDGER holds its text columns as char - the run
  lock is written the same way*, the append added one row, and the run
  completed with 3 documents unchanged.
- **The generated flow on that `CHAR` ledger** — the case that had failed
  with *incompatible data types*. All five steps ended green. Afterwards the
  ledger was `VARCHAR`, with 3 rows of status *unchanged* and no lock row.
- **The job on the `VARCHAR` ledger the flow had left.** The log said *…
  holds its text columns as varchar - the run lock is written the same way*,
  the append added one row, and the run completed with 3 unchanged.

**Measured again on 2026-10-11 on the same system, with pgvector and with
SingleStore 9.0.40.** The job and the flows ran as SAS Job Execution jobs
in the *SAS Studio compute context*, the flows as the code SAS Studio
generates for them and not clicked in SAS Studio, on ledgers in `CASUSER`:

- **A project without a ledger, then the job twice.** The first run found
  3 documents new, and the ledger it wrote was `CHAR`. The second run
  logged *as char*, appended the lock and completed with 3 unchanged.
  The same on SingleStore.
- **The step *RAG - Purge Documents* in the mode purge**, on a `CHAR` and on a
  `VARCHAR` ledger (pgvector), and on a `CHAR` ledger (SingleStore). The
  step logged the type it found and appended its lock, its log said
  *removed 1 chunk rows for 1 documents*, and the ledger held 2 documents
  afterwards and no lock row. The ingestion that followed — the flow in
  two of the three cases, the job in one — found the purged document as
  new and brought it back. On pgvector the removed chunk row is the
  step's own log line; on SingleStore the collection was read and held 2
  chunks.
- **A first step that fails and hands the lock back**, on both types
  (pgvector). *RAG - List Documents* with a documents folder that does
  not exist appended its lock, failed, and wrote *NOTE: RAG - List
  Documents: the run lock was released so the run can be retried*. The
  ledger kept its 3 rows and its type and held no lock row, and the next
  run, less than three minutes later, was not refused.
- **The job reads its lock back** (see below). Every job run on a ledger
  that existed showed two `PROC JSON` steps before the Python program and
  completed.

Not measured in those runs: a lock that CAS refuses, which is what the
read-back of the job is there for; a first run of the flow without a
ledger with these steps (a flow on a ledger of its own ran green earlier
the same day, before the change); the fallback for a table whose column
types cannot be read; two runs at the same time, and with them a run that
the lock of another run refuses; and a lock older than 30 minutes. What
the job does with its lock when its run fails was not measured either. By
the code of that day it left its lock row in the ledger; it now takes its
own row out again. All of these but the first and the third were measured
later the same day, with the job and the steps as release 2.3.0 ships
them — see *Measured with the runtime of release 2.3.0* at the end of
this section.

**The job reads its lock back.** *RAG - List Documents* reads the lock it
appended and refuses to go on without one. The job did not: by its code a
lock that CAS had refused went unnoticed there, and the job went on without
one. It now exports the ledger a second time, after the append, and looks
for a lock row the first export did not hold. Without one it ends with
*RAG ingestion failed: RuntimeError: the run lock could not be written to
the ledger … - refusing to ingest unguarded*, and the
ledger stays as it was. The check sees only that the row is not there, so
the message names where to look and not one cause: the SAS log above it,
for an ERROR of the append or of the export after it. The passing case
was measured, as said above; the refusal was not provoked. To see it on a
site: run the job of a setup whose ledger exists (**Launch ingestion**
after `deploy-rag-content` and **Manifest setup**) — the run has to
complete as before, and its log then shows two `PROC JSON` steps before
the Python program where it showed one.

What follows in this section was written after those measurements. It
is in the steps and in the job of release 2.3.0 and was run on the same
system later that day: *Measured with the runtime of release 2.3.0*, at
the end of this section, says what was seen and what was not run. The
test suite of `rag_core` holds the text of the macros and runs the Python
of the job.

**Who hands a lock back.** A step that fails hands the run lock back by
rewriting the ledger without that row — copy, drop, promote, save — and
then writes a NOTE that begins with its name and says *the run lock was
released so the run can be retried*. The steps measured above did that
whether the run held a lock or not. Seen in those runs: a purge that had gone through, a preview of a
purge and a *RAG - Register Setup* that failed after *RAG - Load Vector
Store* had already written the ledger each rewrote the ledger once more
and wrote the NOTE. A run that the lock of another run had refused did
the same by the code, and saved the ledger with the lock row of the
other run in it. Now only a failing run whose own lock row stands in the
ledger rewrites it and writes the NOTE. A run that another run's lock
refused and a step that fails after the ledger was written leave the
ledger alone, a purge that went through has written the ledger without
its lock row and does not write it a second time, and none of them says
that a lock was released. A preview of *RAG - Purge Documents* takes no
lock at all ([retention and erasure](#retention-and-erasure)).

**The job hands its lock back.** Up to this change the job appended its
lock row before its Python program looked for the lock of another run,
and only a run that went through took the row out again, by storing the
ledger. A run that failed left a fresh lock row behind, and a run that
another lock had refused left a second one. The next run of the job or
of the flow was then refused for 30 minutes with nothing running:
*another ingestion run appears active for this ledger (run lock held) -
retry later*. The row carried no run id, so nothing could tell it from
the lock of another run. The job now makes its run id in SAS, as *RAG -
List Documents* does, and writes it into the lock row. It looks for a
fresh lock of another run before it appends; a run that is refused
appends nothing, exports the ledger once and leaves the table and its
saved file as they are. SAS refuses such a run before the Python program
looks, so the message ends the run as it stands above, without
*RuntimeError:* in front of it. A run that failed after it took the lock
copies the ledger without the row that carries its own id, drops the
table, promotes and saves the copy, and writes *NOTE: RAG ingestion: the
run lock was released so the run can be retried*. The ledger keeps its
column types. A lock row with an empty run id — written by a job
definition that was not manifested again — is the lock of another run to
the job and to the steps: it refuses while it is less than 30 minutes
old, is ignored afterwards, and stays in the ledger until a run that
goes through writes it.

One limit, read from the code and not measured. A lock row carries the
time of the SAS clock, and the Python programs of the job and of the
steps compare it with their own clock, which is UTC. In a compute
session whose SAS clock is not UTC — the option `TIMEZONE` — the two
differ by the offset. East of UTC the Python program of the job then
still takes for fresh a lock row that SAS took for stale: such a run has
appended its own lock, hands it back, and its message does carry
*RuntimeError:*. The sessions of the test system run in UTC.

**More than one lock row.** A run that crashes leaves its lock row
behind; 30 minutes later the next run ignores it and appends its own
beside it, and the stale row stays until a run that goes through writes
the ledger. The job and the steps *RAG - List Documents*, *RAG - Load
Vector Store* and *RAG - Purge Documents* take the newest lock row to
decide whether another run holds the lock. They took one row, whichever
CAS returned first or last, so a stale row could hide the lock of a run
that was at work. No such ledger had come about on the test system by
itself; the measurement below made one.

**The job ends as failed when the run failed.** `Ingest-Documents.sas`
never raises a SAS error, so up to release 2.2 SAS Job Execution reported
every run of it as completed and the outcome was in the log only. The
program now sets `SYSCC` as its last statement when the run failed, and
SAS Job Execution then reports the job as failed. It still runs to its
last statement; nothing aborts. That statement was measured for the job
*RAG - Register Setup* generates from a flow, which ended as `failed`
([the job generated from a flow](#tls-register-setup-step)), and later
the same day for `Ingest-Documents.sas` (below). Two ways out of the
program used to leave no
milestone for the Prompt Builder either: a saved ledger that cannot be
loaded, and a compute context without the `requests` package. Both now
write the milestone *Ingestion failed: …* that every other failure
writes, so **Launch ingestion** shows *The ingestion run did not
succeed* where it showed *Run finished. The ingestion ledger is below.*

**The job stores a ledger only after a run that went through.** The job
stored `rag_ledger_out.json` whenever that file existed in WORK. In a SAS
session that had run the job before, a later run that stopped early found
the file of the earlier run and stored that ledger again, over the saved
file. A job started by SAS Job Execution has a WORK of its own and was
not affected. The two files the job reads the ledger and its lock from,
`rag_ledger_in.json` and `rag_ledger_lock.json`, stayed in WORK as well:
in a SAS session that had run the job before, a run that found no ledger
in memory exported none and read the two files of the earlier run — that
ledger as its own, and its lock as the lock that was written. The job
now deletes both before it exports anything and says so (*NOTE: RAG
ingestion: rag_ledger_in.json of an earlier run of this SAS session was
deleted from WORK*); a file that is there and cannot be deleted stops
the run. The line is written for each of the two files that is there:
for both after a run that took a lock, for `rag_ledger_in.json` alone
after a run that was refused, and for none after a run that had no
ledger.

**The run id.** A run is named `run-<epoch>-<ten digits>`, and `rag_runs`
is keyed on that name and shared by every project in the database. The
digits were `&sysjobid` in the steps and the process id in the job, and
neither told two sessions apart: twelve flow runs of the measurements
above, each in a compute server of its own, ended in `-344`, the four
other runs with an id a step had made in `-343` or `-345`, and the job
runs in `-400` to `-408`. Two runs that started in the same second could
therefore get the same id; no such collision was seen. The steps and the
job now take the ten digits from a UUID, in SAS (`&sysjobid` only where
`UUIDGEN` returns nothing); the lock row of the job carries the id. The
Python program of the job makes an id with ten random digits only where
SAS hands it none. Ids written before keep their form and are read as
before. The epoch in the id is the time of the SAS clock — in the steps
as before, and now in the job, whose Python program made the id from its
own clock up to release 2.2. What that means for `started_at` is said
under [run history](#run-history).

**Measured with the runtime of release 2.3.0**, on 2026-10-11 on SAS Viya
2026.09 with pgvector, in the *SAS Studio compute context*. The job ran
in SAS Job Execution and, where one SAS session had to hold several
runs, as a program in a compute session. The flows ran as the code SAS
Studio generates for them and were not clicked in SAS Studio. The lock
of another run was that of a flow that really ran; the old and the stale
lock rows were appended by hand.

- **A job run that fails after it took the lock**, on a `CHAR` and on a
  `VARCHAR` ledger, with one parameter of the configuration changed. The
  log held, in this order: *NOTE: the ledger … holds its text columns as
  char - the run lock is written the same way* (or *as varchar*); the
  append of one row; *NOTE: Python-Subprocess - RAGINGEST Ingestion
  failed: RuntimeError: configuration drift: …*; a table `<ledger>_R`
  with the 4 rows the ledger had before the run, its promote and its
  save; *NOTE: RAG ingestion: the run lock was released so the run can be
  retried*; and *ERROR: RAG ingestion failed: RuntimeError: configuration
  drift: …*. The ledger then held the same 4 document rows, no lock row
  and the column type it had. The job run again five seconds later was
  not refused and completed.
- **A job launched while the flow of the same ledger runs.** No append
  and one `PROC JSON` step; the milestone *Ingestion failed: another
  ingestion run appears active for this ledger (run lock held) - retry
  later* and the same text behind *ERROR: RAG ingestion failed:*, without
  *RuntimeError:*; no `<ledger>_R` and no line about a released lock. The
  ledger held one lock row, the flow's, and its saved file kept its size
  and time. The flow completed, and the job run again five seconds after
  it was not refused.
- **A flow started while another flow of the same ledger runs** ended in
  *RAG - List Documents* with *ERROR: RAG - List Documents failed: another
  ingestion run holds the lock on this ledger - wait for it to finish, or
  retry in 30 minutes if it crashed*. Its log held no rewrite of the
  ledger and no line about a released lock, the ledger kept the lock row
  of the first flow, and that flow completed.
- **The lock row carries the run id.** Read from another session while a
  job ran, the ledger held one row `__run_lock__` with the status `lock`
  and the `run_id` of the milestone *RAGINGEST run run-…: ledger has 4
  documents, config …*, which is also the id of the run in `rag_runs`.
- **A lock row with an empty run id**, as a job definition from before
  release 2.3.0 writes it. 60 seconds old, it refused the job and the
  flow, and it was still the only lock row afterwards. Older than 30
  minutes, it was ignored: the job logged *RAGINGEST stale run lock
  ignored (held > 30 minutes)*, the steps *NOTE: RAG - List Documents: a
  stale run lock (held longer than 30 minutes) is ignored* and *RAGLIST
  stale run lock ignored (held longer than 30 minutes)*. A job run that
  failed kept it — `<ledger>_R` held 5 rows, the 4 documents and that
  row — and a run that went through left a ledger without lock rows. A
  job run that fails at the configuration drift does not write the line
  about the stale lock: the drift is looked at first.
- **Two lock rows**, a stale one and a fresh one with the id of another
  run, appended in either order: the job and *RAG - List Documents* were
  refused both ways and left both rows. With a flow that ran beside a
  stale row, the job and a second flow launched while it ran were
  refused. CAS does not fix the order of the rows of a table; what was
  varied is the order of the two appends.
- **A step hands a lock back only when it holds one.** A failing *RAG -
  List Documents* took its own row out and wrote the NOTE, on both column
  types, and the run after it was not refused. A *RAG - Register Setup*
  that failed after *RAG - Load Vector Store* and a purge that went
  through did not rewrite the ledger a second time and wrote no such
  NOTE.
- **The job ends as failed.** The nine job runs that failed ended as
  `failed` in SAS Job Execution after 16.5 to 20 seconds, the eight that
  went through as `completed`. The record of a failed job holds the error
  code 5713 of SAS Job Execution, *A procedure in the SAS program provided
  for the job completed execution with at least one error.*, and inside
  it an error with the code 1012 and the text of the program's ERROR
  line. That line is the last message of the program, not the last line
  of the log: the timing lines of its DATA step and the echoed source of
  the last statement follow it.
- **The job stores no ledger after a run that was refused**, in a SAS
  session that had run it to success before: behind the Python program
  the log held no promote and no save, and the ledger, its saved file and
  the lock row of the running flow were as before.
- **The exports of an earlier run.** In one SAS session, a run for a
  second setup that had no ledger wrote the two NOTE lines, ran no `PROC
  JSON` step, logged *ledger has 0 documents* and found its documents
  new. No job started by SAS Job Execution wrote the lines. With
  `rag_ledger_in.json` replaced by a directory that held a file, the run
  ended with *… could not be deleted from WORK - stopped, because the run
  would read the ledger of that run as its own*. Above the milestone of
  that run stood three ERROR messages of `PROC JSON`, on six log lines:
  the export still runs after the delete failed (see
  [troubleshooting](#troubleshooting)).
- **The run id.** Every id ended in ten digits — 28 ids in the logs of
  the flows, the purges and the job of one pass, 15 of the job in
  another — and no two endings were alike. The ledger held the 25
  characters of the id, and `rag_runs.started_at` was the time the id
  names.
- **Launched from the Prompt Builder page of this release.** **Launch
  ingestion** of a setup on this runtime ended as completed, with run ids
  of the form `run-<epoch>-<ten digits>` and the run history recorded.
  Three runs of one setup showed 6 documents new, then 6 changed, then 6
  unchanged and 1 new.

**Not run**, and as the code and the tests have it: a job run that fails
because a saved ledger cannot be loaded, with its milestone (see the
next section); the lock that CAS refuses, which the read-back of the job
is there for; the fallback for a table whose column types cannot be
read; the texts the Prompt Builder shows for a run that failed — the
page was not opened on one; a run that slips in between the steps of a
flow that is running; a lock row left by a job definition of an earlier
release that really failed; an export of an earlier run that cannot be
deleted for another reason than being a directory; and a compute session
whose SAS clock is not UTC.

### A saved ledger is loaded back {#a-saved-ledger-is-loaded-back}

The pipeline promotes and saves `<project>_LEDGER` and
`<project>_EMBEDDED`. Up to release 2.2 every reader looked only at the
table in memory and nothing loaded the saved file. After a restart of the
CAS server — or for any ledger that was never loaded again — *RAG - List
Documents* therefore found no ledger, started one that held only its run
lock and classified the whole corpus as new: every chunk went through the
enrichment prompts and the embedding model again, and the saved ledger was
overwritten with the result.

From release 2.3.0 the saved table is loaded before it is read, by one
macro that is worded the same in all seven programs:

| Program | Loads back |
| --- | --- |
| *RAG - List Documents*, *RAG - Purge Documents*, *RAG - Register Setup*, `Ingest-Documents.sas` | `<project>_LEDGER` |
| *RAG - Embed Chunks*, *RAG - Enrich Chunks* | `<project>_EMBEDDED` |
| *RAG - Load Vector Store* | both |

The log then says `NOTE: RAG: <caslib>.<table> was saved but not in memory
and has been loaded from its saved file.` The `.sashdat` is matched by
name without regard to case. With the run lock above, the order is: load
the saved table back, ask it for its column types, append the lock, read
the lock back. What the macro finds decides how the run goes on:

| The macro finds | The run |
| --- | --- |
| the table in memory | goes on with it; a table in memory is never replaced by its saved copy |
| no table in memory and no saved file | starts as before, without a previous run |
| a saved file, and loads it | goes on with the loaded table and writes the NOTE above. A load that a concurrent run got to first is not an error |
| a saved file that cannot be loaded | fails, before it takes a lock or starts a ledger: *the saved table … could not be loaded into memory (CAS messages above) - stopped, because an empty table would re-embed the corpus and overwrite the saved file*. In the job the Python program writes the milestone *Ingestion failed: the saved table …* and returns, and the job ends as failed |
| no table in memory, and the files of the caslib cannot be listed | goes on **without the table**, with two WARNING lines |

The last row is the case the reload is there for, without the reload:
where the ledger is not in memory, every document counts as new, the
corpus is embedded again and the saved file is overwritten. The run does
not stop there, because a caslib that cannot be listed says nothing about
whether a saved copy exists; it says what it does:

```text
WARNING: RAG: <caslib>.<table> is not in memory, and the files of the caslib <caslib> could not be listed (CAS messages above) - a saved copy could not be looked for.
WARNING: RAG: the run continues without the table and saves over a copy that may be there. A ledger that is not in memory means that every document counts as new and is embedded again.
```

When `PROC CAS` ends before it says what it found, not even that much is
known, and the first line reads *WARNING: RAG: could not look for … -
PROC CAS ended before it said whether the table is in memory or saved
(messages above). The run continues with what is in memory.* Up to this
change both cases wrote one NOTE, *could not look for a saved copy of …*,
which no longer exists. The two WARNING lines, the milestone of the job
and the failed state of the job were written after the first
measurements below. The wording of the two WARNING lines and the failed
state of the job were measured later that day; a saved file that cannot
be loaded, with the milestone of the job for it, was **not run in SAS**
— see the end of this section.

**Measured on 2026-10-11 on SAS Viya 2026.09, with pgvector**, with the
saved tables dropped from memory while their `.sashdat` files stayed —
the CAS server was not restarted:

- **The job**, on a `CHAR` and on a `VARCHAR` ledger: the log held the
  NOTE for `<project>_LEDGER`, the lock was appended with the type of the
  loaded table, and the run found 3 documents unchanged and embedded
  nothing.
- **The flow**, on a `CHAR` and on a `VARCHAR` ledger: *RAG - List
  Documents* loaded the ledger back, appended its lock and listed 3
  documents as unchanged; *RAG - Embed Chunks* loaded
  `<project>_EMBEDDED` back and found nothing to embed.

**Measured again later that day, with the runtime of release 2.3.0**
(pgvector, the saved ledger dropped from memory): the job and *RAG - List
Documents* wrote the NOTE, loaded the ledger and found every document
unchanged. And for the last row of the table, in *RAG - List Documents*
with the ledger in a caslib of the CAS session whose files CAS could not
list: the log held the two WARNING messages above, word for word, on
four log lines below the CAS errors. That run did not reach its end. The step went on to its
run lock, and a caslib of a session cannot hold the promoted table the
lock is appended to, so it stopped with *the run lock could not be
written to … - refusing to ingest unguarded*. Whether a run in a global
caslib whose files cannot be listed ends, and what it saves, is not
measured: the user of the test may add no path caslib.

**Not run: a saved file that cannot be loaded.** No file that CAS cannot
load could be put into a caslib of the test. The compute server does not
see the directory of the caslib, CAS refuses to save another format under
a `.sashdat` name (`ERROR: Save exportoptions file type and name
extension do not match.`), and the caslibs whose directories the compute
server does see belong to other people. So the CAS errors of such a
load, the ERROR of the step, the milestone *Ingestion failed: the saved
table …* of the job and the untouched file are as the code and the tests
have them. What a run of the job leaves behind that stops before it
takes a lock was measured for a run that another lock refused: no lock
row, no store, the saved file untouched
([one ledger for the job and the flow](#one-ledger-for-the-job-and-the-flow)).

Not measured: a restart of the CAS server; a saved file that cannot be
loaded; a global caslib whose files cannot be listed; the reload in *RAG -
Purge Documents*, *RAG - Register Setup* and *RAG - Enrich Chunks*, which
did not run in that state (*RAG - Enrich Chunks* did not run at all); the
reload of *RAG - Load Vector Store* itself, because both tables were in
memory again by the time it ran; and SingleStore. The *About* tabs of
those four steps name the reload and claim no measurement for it. The
test suite of `rag_core` holds the text of the macro, its place in every
program and the order with the run lock.

A job definition holds a copy of `Ingest-Documents.sas` from the day the
setup was manifested, so the job of a setup loads its ledger back only
once **Manifest setup** has run again after `deploy-rag-content`. Anything
that reads the ledger outside the steps and the job — the ledger view of
the Builder, for one — still sees no ledger after a restart until a run
has loaded it, and `<project>_ELEMENTS` and `<project>_CHUNKS` are saved
and not loaded back.

### Where a setup embeds {#where-a-setup-embeds}

The documents of a collection and the question asked of it have to be
embedded by the same container: a question embedded somewhere else searches
the collection with the wrong vectors, and nothing reports it. The Builder
therefore resolves the embedding endpoint of a setup once — the **SCR
endpoint** option of the Prompt Builder object, and `<SAS Viya host>/llm`
where that option is blank, the host being the **SAS Viya host** option or
the address the page is opened at — and gives the same value to everything
it generates and starts. (A stored setup that names an endpoint of its own,
`embedding.scrEndpoint`, keeps it ahead of the option; the Builder itself
never writes one.)

| | Receives it as |
| --- | --- |
| The ingestion job, at **Manifest setup** and at **Launch ingestion** | the parameter `scrEndpoint` |
| The Embed step of the generated SAS Studio flow | the step option `_rgem_scrEndpoint` |
| **Test retrieval** | the parameter `scrEndpoint` |
| The retrieval model | the constant `EMBED_ENDPOINT` in its MANIFEST block |

With a blank option, that address has to be reachable from the compute
session of the ingestion and from the destination the retrieval model is
published to. Where the page is opened under a name that is not — a
port-forward, an alias of a reverse proxy — set the **SCR endpoint** option.

The value is written at **Manifest setup**. A job definition, a flow and a
retrieval model generated earlier keep what they were generated with until
the setup is manifested again; a launch from the page always passes the
current value, a scheduled run of the job and a run of the flow in SAS
Studio do not.

The retrieval model looks for the endpoint in this order and takes the first
that is not blank:

1. `scr_endpoint` in the `options` input of the call,
2. the environment variable `RAGEMBED_ENDPOINT` on the destination,
3. the manifested `EMBED_ENDPOINT`,
4. `SAS_SERVICES_URL` + `/llm`, where the variable is set — a compute
   session sets it.

Up to release 2.2 the fourth came before the third, so in a compute session
the question was embedded at `<SAS Viya host>/llm` whatever the setup had
been manifested for. The order is part of `retrieve_context.py`: a model
has it once `deploy-rag-content` has published the current template and the
setup has been manifested again.

### TLS verification in the retrieval model

The retrieval model — registered by **Manifest setup** of the Builder, or by
the custom step *RAG - Register Setup* in SAS Studio — makes two HTTPS calls
itself: to the embedding container, to embed the question, and — only in a
compute session, where a session token exists — to the credentials service.
It verifies both against the CA bundle of the deployment, and two environment
variables per call decide how:

| Call | CA bundle | Verification on or off |
| --- | --- | --- |
| Embedding container | `RAGEMBED_CABUNDLE` | `RAGEMBED_SSLVERIFY` |
| Credentials service | `RAGSTORE_CABUNDLE` | `RAGSTORE_SSLVERIFY` |

- `<prefix>_CABUNDLE` names the bundle and defaults to
  `/security/trustedcerts.pem`. When that file does not exist where the model
  runs, the model verifies against the certificates Python trusts by default,
  and a certificate signed by the deployment's own CA is refused.
- `<prefix>_SSLVERIFY` set to `false`, `no` or `0` switches verification off
  on that destination; set to `true`, `yes` or `1` it switches verification
  on. The variable wins in both directions.
- When the variable is not set, or holds any other word, the constant
  `SSL_VERIFY` in the MANIFEST block of the model decides. The Builder writes
  it from the option **Verify TLS certificates in manifested models**
  (`sslVerify`) of the Prompt Builder object: `True` while the option is
  ticked, which is the default, and `False` while it is not. The custom
  step *RAG - Register Setup* writes it from a checkbox of its own, in the
  same way — see [the checkbox of the custom step](#tls-register-setup-step).

So the option is a default written when the setup is manifested. A retrieval
model that is already registered keeps its constant until **Manifest setup**
runs again, and `RAGEMBED_SSLVERIFY=true` and `RAGSTORE_SSLVERIFY=true` on a
destination turn verification back on for a model manifested without it.
While the option is not ticked, the step shows a warning at **Manifest
setup** before the click, and the model it registers carries the tag
`TLS-Verification-Off` in SAS Model Manager —
[Deployment of Decisions](./Deployment-of-Decisions.md#finding-the-models-that-do-not-verify)
shows how to list such models. Manifesting the setup again with the option
ticked writes `SSL_VERIFY = True` and removes the tag. The tag follows the
score code the model holds: when SAS Model Manager refuses
`retrieve_context.py`, the manifest stops at that stage with the HTTP status
and leaves the score code and the tags as they were, and a tag that could
not be written or removed is named in the outcome beside the button. One
case the page cannot decide: when the upload of `retrieve_context.py` gets
no answer at all — the connection drops, or an ingress answers with its
error page — SAS Model Manager may have stored the file all the same. The
manifest stops at that stage too, says that *it is not known whether SAS
Model Manager stored retrieve_context.py*, and leaves the tag as it was;
until **Manifest setup** has run through again, the tag may not match the
`SSL_VERIFY` line the model holds.

What the option does not touch:

- **The ingestion job**, the generated flow and *Test retrieval*. They run in
  a compute session, where the bundle is mounted, and verify as before.
- **The connection to the vector store.** Its TLS is the option
  **RAG: vector store TLS** (`storeSslmode`) and the `sslmode` of the store.
- **A model registered with the custom step *RAG - Register Setup*.** The
  step does not read the options of the Prompt Builder object. It has a
  checkbox of its own for the same setting — see
  [the checkbox of the custom step](#tls-register-setup-step).
- **A template deployed before release 2.3.0.** The `retrieve_context.py`
  under `<content root>/models` then has no `SSL_VERIFY` constant. **Manifest
  setup** succeeds with it, and the model verifies whatever the option says.
  With the option not ticked, the outcome beside the button says that
  verification is still ON and that the template has to be deployed again,
  and the model is not tagged. Run `deploy-rag-content` to publish the
  current `retrieve_context.py`: the next **Manifest setup** writes
  `SSL_VERIFY = False` and tags the model.

Switching verification off is for a destination that runs Python where the
bundle is not mounted — the case found with SAS Micro Analytic Service, which
[Deployment of Decisions](./Deployment-of-Decisions.md#mas-certificate-bundle)
describes together with the mount that keeps verification on. That mount is
the fix for production; verification off is for a test system on a closed
cluster network. The mount was measured there with a decision template,
and with a prompt template with the included LLM call. A retrieval model
was published there after the mount as well. Without the credentials of the
vector store it answered *no vector-store credentials resolved*; with them
passed in `options` it returned chunks, with `SSL_VERIFY = True` in its
score code — see
[the retrieval model in a decision](#the-retrieval-model-in-a-decision).
The call to the credentials service is not made when the credentials come
with the call or from the environment — the two cases that were run — so
the verification of that one call in SAS Micro Analytic Service is not
measured.

Both *Launch ingestion* and *Test retrieval* need the **ingestion compute
context** option (`ragComputeContext`) to be set — SAS Job Execution rejects a
job that does not name one, and the rejection carries no log. The Builder
checks first and names the missing setting. The optimization compute context
of the same Options pane does not count: it is a different option, for a
different job.

### The checkbox of the custom step RAG - Register Setup {#tls-register-setup-step}

The custom step *RAG - Register Setup* registers the same
`retrieve_context.py` from SAS Studio. It does not read the options of the
Prompt Builder object; its *Runtime* section has the checkbox
**Verify TLS certificates in the registered retrieval model**
(`_rgrg_sslVerify`), ticked by default:

| The checkbox | The registered model holds | The tag `TLS-Verification-Off` | What the step says |
| --- | --- | --- | --- |
| ticked | `SSL_VERIFY = True` | not set, and removed from a model that carried it | *TLS certificate verification on* in the summary of its log and in the registration report |
| not ticked | `SSL_VERIFY = False` | set | WARNING lines at the end of its log, and *TLS certificate verification OFF* in the registration report |

- **The warning.** With the box not ticked, the log of the step ends with
  WARNING lines: the retrieval model does not verify the certificates of
  its two HTTPS calls — to the embedding container and to the credentials
  service — and `RAGEMBED_SSLVERIFY=true` and `RAGSTORE_SSLVERIFY=true` on
  the publishing destination turn verification back on. The registration
  report, the output table of the step, shows the state in the *Detail*
  column of the row of the model.
- **The tag follows the score code that is stored.** It is set or removed
  after `retrieve_context.py` has been stored on the model, so a
  registration that fails at that upload leaves the tag as it was — also
  when the model could not be read before the upload: the tag is then
  kept as the listing of the models shows it. Where the listing does not
  show the tags of the model either, nothing says whether it carries the
  tag, and the step writes it: a tag too many is put right by the next
  registration that goes through, a model that does not verify and is not
  tagged is found by nobody. When the upload then fails, the model carries
  a tag this run put there, and the log says so in WARNING lines of its
  own (*the model could not be read before this run updated it …*); that
  case was checked with the test suite and not run in SAS, and neither
  were the WARNING lines for a tag that could not be written or removed
  and for an upload without a usable answer. When the tag itself cannot be
  updated, the registration still goes through, and a WARNING says which
  way the tag is wrong: it could not be written to a model that does not
  verify, or could not be removed from one that does.
- **A run that fails after the score code is stored.** The step does more
  after the upload: it attaches the other artifacts and, when a flow is
  named under *Studio Flow to generate the scheduled job from*, generates
  the job. When one of these fails — the flow does not exist, say — the
  step ends with its ERROR, and the model in SAS Model Manager holds the
  new score code all the same. The log of the failing run then says so
  below the ERROR line, *the step failed after the score code of the
  retrieval model was stored*, followed by the same WARNING lines as a run
  that went through: that the model does not verify, and whether it
  carries the tag.
- **An upload of the score code without a usable answer.** When the
  connection drops, the request times out, or an ingress answers with a
  page of its own, the step cannot know whether SAS Model Manager stored
  the file. It reads nothing back. It ends with its ERROR and two WARNING
  lines: *it is not known whether SAS Model Manager stored the score code
  retrieve_context.py*, and *the tag TLS-Verification-Off was not updated
  and may not say what the stored score code does. Run this step again.*
  The Builder words the same case the same way beside **Manifest setup**.
  On a model whose tag the run wrote before the upload without knowing
  whether the model carried it (the bullet above), the second line says
  *was not updated after the upload*, as `rag_core` does, and the three
  lines about the tag written unseen follow; up to this change the step
  said there that the tag was not updated and, two lines on, that the run
  had written it. That wording was not run in SAS.
  An upload SAS Model Manager refuses with an answer of its own is
  certain: nothing was stored, and the model keeps the score code it had.
  It keeps the tag it had as well, with the one exception of the bullet
  above: a model that could not be read and whose tags the listing did
  not show got the tag before the upload, and carries it.
- **The job generated from a flow.** With a flow named under *Studio Flow
  to generate the scheduled job from*, the step creates the job definition
  `RAG Ingest - <setup>` from the code SAS Studio generates for the flow.
  Up to release 2.2 the job was that code unchanged, so a failing step ran
  `abort 42` as it does in an interactive flow — which in a job ends the
  session before SAS Job Execution gets its completion handshake. The job
  now begins with `%let RAG_ABORT_ON_ERROR = 0;` and `%let RAG_JOB_FAILED
  = 0;` and ends with a check that writes *ERROR: RAG ingestion failed - a
  step of this run reported an error (its ERROR line is above) and the
  steps after it were skipped.* and sets `SYSCC` when a step failed. The
  check reads `RAG_JOB_FAILED`, which every failing RAG step sets and no
  step clears; `RAG_PIPELINE_FAILED` is no use for it, because *RAG - List
  Documents*, *RAG - Purge Documents* and *RAG - Retrieve Context* clear
  it when they start. Both parts are added by the step and are not part of
  the flow: a job that SAS Studio itself deploys from the flow does not
  have them, and running the step again puts them back. **Measured on
  2026-10-11 on SAS Viya 2026.09**, with two jobs the step generated from
  a flow of List, Extract, Chunk, Embed and Load (pgvector), run in SAS
  Job Execution: the job in which *RAG - List Documents* failed ended as
  `failed` after 31 seconds, with the ERROR line of the step and the line
  of the check in its log and the four steps after it skipped; the job
  without a failure ended as `completed`. Not measured: the job that does
  not end, which the change is made to prevent, was not reproduced; a
  step other than *RAG - List Documents* that fails; a flow with a second
  chain or with a *RAG - Retrieve Context* node, and with it that
  `RAG_JOB_FAILED` rather than `RAG_PIPELINE_FAILED` is what fails the
  job — both flags were set in the measured flow. The *About* tabs of the
  eight other steps name the flag and claim no measurement for it.
- **A template deployed before release 2.3.0.** `rag_core` writes the line
  under the rule of the Builder: only where the MANIFEST block of the
  template has `SSL_VERIFY`. With an older template and the box not
  ticked, the step registers a model that still verifies, does not tag
  it, and warns that *TLS certificate verification is still ON* and that
  the RAG content has to be deployed again. Run `deploy-rag-content`, then
  the step once more.
- **A flow that hands the step no value for the checkbox** registers with
  verification on: a missing or blank value is read as ticked. Not ticked
  is a value that reads as the number zero — `0`, which is what SAS Studio
  hands over for a box that is not ticked, and equally `0.0`, `00` or
  `-0` — or the word `false` or `no`, in any case. Every other value, an
  unknown word included, is read as ticked.

The checkbox changes the registered model and nothing else. The step
itself, the other eight RAG steps and the ingestion job run in a compute
session and verify the HTTPS calls they make themselves — to SAS Viya and
to the embedding container — with the CA bundle of that session
(`SSLCALISTLOC`, else `CAS_CLIENT_SSL_CA_LIST`, else the certificates
Python trusts); none of them has a control or reads an environment
variable that switches that off. A prompt that *RAG - Enrich Chunks* runs
is a manifested model with the default it was manifested with. The
connection to the vector store is a database connection: its TLS is the
*SSL mode* (PostgreSQL) or *TLS* (SingleStore) control of the steps that
connect to it, and for the registered model the *SSL mode* of this step.

The step and `rag_core` reach a site with the transfer package, or from a checkout with `deploy-rag-content`. The step
was run in SAS Studio on 2026-10-11, on SAS Viya 2026.09, as the last of a
flow of six steps — List, Extract, Chunk, Embed, Load, Register Setup. The
flow was created without a value for the checkbox, which is what a flow
saved before the control existed hands the step:

- The checkbox shows in the *Runtime* section, ticked.
- Run that way, the step ended green and its log said *retrieval model: TLS
  certificate verification on*. The score code of the model held
  `SSL_VERIFY = True`, and the model carried the tags `LLM`, `RAG`,
  `all_minilm_l6_v2` and `pgvector`.
- With the box not ticked, the node of the step showed a warning badge in
  the flow. The log said *retrieval model: TLS certificate verification OFF
  (tag TLS-Verification-Off)* and held the WARNING lines, the score code
  held `SSL_VERIFY = False`, and the model carried `TLS-Verification-Off`
  beside the four tags.
- With the box ticked again, the score code held `SSL_VERIFY = True`, the
  tag was gone and the four tags were kept.
- With a `retrieve_context.py` without the `SSL_VERIFY` line under
  `<content root>/models` and the box not ticked, the log held three
  WARNING lines (verification still ON, the content predates the setting,
  deploy the RAG content again), the *Detail* of the report said *TLS
  certificate verification still ON - the retrieval model template predates
  the setting …*, the model was not tagged, and the score code held no
  `SSL_VERIFY` line.

Not measured: a job generated from a flow that contains the step, and a
retrieval model registered by the step running inside a decision or in SAS
Micro Analytic Service.

The WARNING lines of a run that fails after the upload were added after
these runs and measured later the same day, with the step run as the
code SAS Studio generates for a flow, in a compute session of the *SAS
Studio compute context* and not clicked in SAS Studio:

- **Box ticked, no flow named.** The run ended with condition code 0 and
  no WARNING line. Its log said *retrieval model: TLS certificate
  verification on* and *no flow selected, so no scheduled job was
  generated*; the model held `SSL_VERIFY = True`, no tag
  `TLS-Verification-Off`, and its nine variables with the lengths of
  [the table below](#the-lengths-of-the-variables).
- **Box not ticked, and a flow that does not exist.** The log held, in
  this order: the ERROR line of the step (*no flow named …*), the
  WARNING that the step failed after the score code of the retrieval
  model was stored, the three WARNING lines about a model that does not
  verify, the WARNING that the model carries the tag
  `TLS-Verification-Off`, and the ERROR of the `abort`. The same model
  then held `SSL_VERIFY = False` and the tag. The log held no *Apparent
  symbolic reference* line and no NOTE about a quoted string.

Not run in SAS: the WARNING lines of an upload without a usable answer,
of a tag that could not be written or removed, and of a tag written to a
model that could not be read. Each needs a fault of SAS Model Manager —
a model it does not return while its listing shows no tags, an upload it
does not answer — and not a state a model of the test can be put in. The
runs of 2026-10-11 with the steps of release 2.3.0 could not stage them
either, and the logs of the two runs of the step made that day hold none
of these lines. The test suite of `rag_core` runs the Python of the step
for these cases and pins the SAS text.

Two more things came out of those runs. Both are changed in this release,
and both were measured afterwards on the same system:

- **A SAS Model Manager project that does not exist yet.** The step names
  the project the model is registered in (*RAG Engineering* unless
  changed) and creates it when it is missing. With a new name it stopped
  with *POST /modelRepository/projects returned 400 … You must specify a
  value for the "folderId" parameter*: the service places a project in a
  folder, and the request named the repository only. `rag_core` now
  creates the project in the **default repository** of SAS Model Manager —
  the one marked as default among the first 100 it lists, else the first
  of them — and sends the folder of that repository with the request; the
  same request with the folder added was answered 201 on that system. A
  repository that names no folder ends in a message that says so. To have
  the project in another repository, create it there in SAS Model Manager
  first: a project that exists is used as it is. Measured with a project
  name that did not exist: the step ended green, the project was created
  in the repository marked as default (*Public* on that system) with the
  folder of that repository and the tags `LLM` and `RAG-Engineering`, and
  the retrieval model was registered in it.
- **A NOTE above the summary.** Every run wrote *NOTE: The quoted string
  currently being processed has become more than 262 bytes long. You might
  have unbalanced quotation marks.* twice, above the summary of its log.
  The cause as it was read from the code: the summary was written into
  the program inside two quoted strings, and SAS writes that NOTE for a
  quoted string of more than 262 bytes. The same two strings stood in all
  nine RAG steps since release 2.0.0. The steps now read the summary with
  `symget`. The file of the step held one more pair of strings of that
  length, two docstrings of its Python program; nothing in the code
  suggests that SAS counts those, and they were shortened as well.
  Measured for *RAG - Register Setup*: the log of a run of the new step
  goes from `%_rgrg_status;` straight to *NOTE: ----- RAG - Register Setup
  -----*, with 0 errors and 0 warnings, and the two NOTE lines every
  earlier run had are gone. Which of the two changes removed them was not
  separated. The other eight steps got the same change. Measured later
  the same day with SingleStore: the log of a flow of List, Extract,
  Chunk, Embed and Load held no such NOTE. The logs of *RAG - Enrich
  Chunks*, *RAG - Purge Documents* and *RAG - Retrieve Context* were not
  read for it.
- **The same NOTE above an ERROR line.** A step that fails wrote its
  message, up to 500 characters, into one quoted string as well. Seen on
  2026-10-11: a failing *RAG - List Documents* wrote the NOTE directly
  above *ERROR: RAG - List Documents failed: …*. The closing line of
  `Ingest-Documents.sas` was written the same way, and its message for a
  run lock that was not read back is longer than 262 bytes with any
  table name. The nine steps and the job now put that line together in a
  variable, its text read with `symget`, as the summary is. Measured on
  2026-10-11 with a *RAG - List Documents* whose documents folder does
  not exist, run as a job and in a compute session: the line above the
  ERROR line was not the NOTE, and no log of that day's runs of the
  steps and of the job held the NOTE. The ERROR line had 311 characters.
  The log of that system is 132 characters wide, and this line, which
  the step writes with `PUT`, was cut there, inside a word and with no
  prefix on what follows:
  `… { version :2, httpSta`, then `tusCode :404, …`. A long NOTE
  milestone and the WARNING lines of the steps were broken at a blank
  before that column in the same logs. All three log lines
  of the message were of the type error, and so were the four of a
  413-character ERROR line of the job. SAS Studio was not opened: that
  it shows such a line as an error is read from the type of the lines.
- **A part of the summary longer than the log line.** *RAG - Register
  Setup* printed the list of the artifacts it wrote as an empty `NOTE:`
  line followed by the text without the prefix (seen on 2026-10-11). A
  part of a summary is now written as one value with its `NOTE:` prefix,
  in all nine steps. Measured on 2026-10-11 for *RAG - Register Setup*
  without a flow: the line began `NOTE:   artifacts in …` and went on in
  the next log line — the break stood inside a word, `ingestio` and
  `n-manifest.json` — with no empty `NOTE:` line before it.

### The retrieval model in a decision {#the-retrieval-model-in-a-decision}

A decision calls the retrieval model like any other Python model: inputs
`question`, `k`, `filter_json`, `retrieval_mode` and `options`, outputs
`context_dg`, `context_envelope`, `retrieval_status` and `run_time`. The
model never raises. When it cannot retrieve, `retrieval_status` says why
and the datagrid is empty — so a decision that uses the context should
look at `retrieval_status`.

Where a decision runs — the test of SAS Intelligent Decisioning, SAS Micro
Analytic Service, a SAS Container Runtime container — there is no SAS Viya
session, so the credential domain that serves the ingestion cannot be read
there ([Managing Credentials](./Managing-Credentials.md#where-the-domain-applies--and-where-it-cannot)
has the boundary). The model then needs the user and the password of the
vector store from one of two places:

| From | Names |
| --- | --- |
| Environment variables of the runtime the Python runs in | `<BACKEND>_RAG_USER` and `<BACKEND>_RAG_PW` — `PGVECTOR_RAG_USER` and `PGVECTOR_RAG_PW` for pgvector, `SINGLESTORE_RAG_USER` and `SINGLESTORE_RAG_PW` for SingleStore |
| The `options` input of the call, a JSON object | `user` and `password` — for a caller that manages its own secrets; a decision should not store them |

Host, port, database and SSL mode of the store, the embedding endpoint and
the embedding model are in the model already, as they were manifested. A
destination can override them with `<BACKEND>_HOST`, `_PORT`, `_DB` and
`_SSLMODE` (or `RAGSTORE_HOST`, `RAGSTORE_PORT`, `RAGSTORE_DB`,
`RAGSTORE_SSLMODE` for any backend), `RAGEMBED_ENDPOINT` and
`RAGEMBED_MODEL`. An embedding model that calls a hosted API also needs its
key: `RAGEMBED_API_KEY`, or `API_KEY` in `options`. The variables that
decide TLS verification are in
[TLS verification in the retrieval model](#tls-verification-in-the-retrieval-model).

Where the variables are set depends on the destination. For SAS Container
Runtime they are part of the `env` of the container. For SAS Micro
Analytic Service they belong to the container that runs the Python, which
can be a separate runner —
[the variable and the Python runner](./Deployment-of-Decisions.md#the-variable-and-the-python-runner)
shows the patch, with *LLMCONTAINERPATH* as its example. Measured for SAS
Micro Analytic Service on 2026-10-11, with the two variables in a
Kubernetes secret and set on every container of the deployment:

```bash
kubectl -n <namespace> set env deployment/sas-microanalytic-score --containers=* --from=secret/<name>
```

The pod restarts, and a call of the published decision with `{}` as the
`options` of the retrieval model then returned `retrieval_status` *ok* and
three hits. Which of the containers needs the variables was not separated.
The command changes the running deployment only: it is not part of the
manifests the deployment is applied from. Not measured: the same variables
for the test of SAS Intelligent Decisioning, where it was not established
where they have to be set, and for a SAS Container Runtime container. The
Python packages the model imports have to be there too: `requests`, and
`psycopg2` or `singlestoredb` for the store.

**What was measured, on 2026-10-11 on SAS Viya 2026.09.** One decision with
three model nodes, all manifested in the Prompt Builder — the retrieval
model of a RAG setup on pgvector, a prompt template with the included LLM
call that is given the `context_envelope` of the retrieval, and a decision
template — was tested in SAS Intelligent Decisioning and published to the
SAS Micro Analytic Service destination `maslocal`. TLS verification was on
in the score code of all three models.

*Without credentials for the vector store.* All three nodes ran in both
places; the publishing validation ended on three rows with HTTP status 201
and an empty error column. The prompt and the decision template answered.
The retrieval model answered, in both places and with no chunk:

```
retrieval failed: RuntimeError: no vector-store credentials resolved: pass user/password in options, grant a agentic-ai-keys credential holding PGVECTOR_RAG_USER/PGVECTOR_RAG_PW, or set those environment variables on the destination
```

Neither runtime had the two variables, so this is the boundary above and
the degradation the model is built for. What was measured is this status
text; which requests the model made before it was not looked at.

*With the user and the password in `options`.* Every row passed
`{"user": …, "password": …}` in the `options` input of the retrieval model.

- **In the test of SAS Intelligent Decisioning**: 3 rows,
  `retrieval_status` *ok* after 0.2 to 0.7 seconds, three hits per row with
  the right document first, and envelopes of about 2,000 characters. The
  LLM answered from the context, and the decision template routed. An empty
  `filter_json` was accepted.
- **Published to `maslocal`**, called one row at a time: six single calls
  one after another each returned HTTP status 201, `retrieval_status`
  exactly *ok*, three hits, an envelope of 1969 characters and the answer
  of the LLM from the context. A whole call took 6.5 to 9 seconds.
- **Published to `maslocal`**, in a publishing validation with the same
  three rows: runs of the same validation gave different results. In one,
  all 3 rows returned `retrieval_status` *ok*, the envelopes, the answers
  of the LLM from the context (*Last shuttle to the railway station departs
  at 19:10.*) and the routes. In others, rows came back with HTTP status
  201, an empty error column and **every output of all three nodes
  empty** — all three rows in one run, two of the three in the next run of
  the same module with the same table. SAS Micro Analytic Service had ended
  those executions after 30 seconds: the validation sends its rows at the
  same time, and the LLM and the decision model, both self-hosted on one
  CPU node, then took longer than that.
  [Several rows at once](#several-rows-at-once) has it.

Direct calls of the published module without credentials, and with made-up
ones, completed normally. To the made-up ones the database answered
*password authentication failed*, so the PostgreSQL driver is there where
SAS Micro Analytic Service runs the model on that system.

*With the user and the password as environment variables.* With
`PGVECTOR_RAG_USER` and `PGVECTOR_RAG_PW` set on the containers of the
deployment `sas-microanalytic-score` (the command is above) and `{}` as
`options`, a call returned `retrieval_status` *ok* and three hits.

Not measured: the two environment variables for the test of SAS
Intelligent Decisioning and for a SAS Container Runtime container;
SingleStore; outputs longer than 32000 characters; and a retrieval model
registered with *RAG - Register Setup* inside a decision.

One more thing showed in the first of these tests. A decision hands a
character input to a model padded with blanks to the length the variable
is declared with: a question declared with 1000 characters arrived with
about 900 trailing blanks in the test of SAS Intelligent Decisioning, and
with one in SAS Micro Analytic Service. Up to release 2.2 the model
returned the padded text as `query` in `context_envelope` — 1134 characters
for a short question — and sent it to the embedding model as it was. From
release 2.3.0 the model drops the blanks around the question before it
embeds it and before it returns it. It treats the other inputs the same
way: a `filter_json` of blanks only is no filter, a `retrieval_mode` of
blanks only is the default mode, and a `k` of blanks only is the default
number of chunks. A `k` that is no number at all falls back to the default
as well and says so in `retrieval_status` — *ok (k … is not a number - used
4)* — where it failed the call before; a missing number and an infinity,
also as the texts `nan` and `inf`, are the default without that note. A
question of blanks only is still answered with *question is empty*.

Measured with the model of release 2.3.0, on 2026-10-11: a question
followed by 200 blanks arrived as 56 characters in `query` in SAS Micro
Analytic Service, and in the test of SAS Intelligent Decisioning the three
questions arrived with 103, 91 and 56 characters. A blank `retrieval_mode`,
a `k` of 0, a missing `k` and a blank `filter_json` each gave
`retrieval_status` exactly *ok*, in single calls of SAS Micro Analytic
Service and in the test of SAS Intelligent Decisioning. The collection has
three chunks, so these runs do not show that the default number of chunks
was used: it cannot be told from 3. Not measured: a `k` that is no number.

### Several rows at once {#several-rows-at-once}

A decision that chains a retrieval model, an LLM and a decision model is
as slow as its models are when they are called together, and SAS Micro
Analytic Service ended an execution that took longer than 30 seconds
(measured on 2026-10-11 on SAS Viya 2026.09). What comes back then does
not say so: HTTP status 201, an empty error column and every output of
every node empty. A direct REST call of the module answers
`"executionState": "timedOut"` with `"outputs": []`.

| | One call at a time | Three rows at once (publishing validation) |
| --- | --- | --- |
| Retrieval node | 0.2 s | 0.2 to 2.2 s |
| LLM node | 1.3 to 4.2 s | 5 to 6 s for the first row, more than 29 s for the two others |
| Decision model node | 1.7 to 4.1 s | 24 to 25 s while the LLM container was busy |
| A whole call | 6.5 to 9 s | all three rows empty in one run; one row complete after 29.65 s and two empty in the next |

The retrieval itself is the smallest part of it. What it adds is the
prompt: with the retrieved context the prompt of the LLM was about 900
characters instead of about 50, and the LLM and the decision model were
self-hosted on the same CPU node. The
[Troubleshooting Guide](./Troubleshooting-Guide.md#empty-outputs-published-decision)
has how to recognise the case in the log of the service, the rest of what
was measured and what a site can do: fewer calls at once, a faster model
or models that do not share a node, a shorter prompt — a smaller `k` or
smaller chunks. The lengths of the variables below have no part in it.

### The lengths of the variables {#the-lengths-of-the-variables}

SAS Model Manager keeps a length for every variable of a model, and SAS
Intelligent Decisioning declares the model in the code it generates for a
decision with those lengths. The Prompt Builder writes every variable of
the retrieval model with a length, and the custom step *RAG - Register
Setup* registers the same ones:

| Variable | Role | Length |
| --- | --- | --- |
| `question` | input | 32000 |
| `k` | input | 8 (a number) |
| `filter_json` | input | 4000 |
| `retrieval_mode` | input | 32 |
| `options` | input | 4000 |
| `context_dg` | output | 32000 |
| `context_envelope` | output | 32000 |
| `retrieval_status` | output | 512 |
| `run_time` | output | 8 (a number) |

Up to release 2.2 the two did not agree. The Prompt Builder wrote
`context_dg` and `context_envelope` without a length, for which SAS Model
Manager stored 99, and the code of a decision then declared

```
in_out varchar(100) "context_dg", in_out varchar(100) "context_envelope"
```

The step registered the two context outputs with 32000 and its inputs
shorter than the Builder's (4096, 1024, 16 and 2048). The lists are now
the same, so that a model is the same model whichever of the two wrote its
variables last, and a test of the repository compares them entry by entry.

**A value that is longer than its declared length arrives complete.** That
was measured on 2026-10-11 on SAS Viya 2026.09, as a control: with
`context_dg` and `context_envelope` set back to 99 on the model — the
generated code of the decision then declared `in_out varchar(100)` for
both — and the decision published again, single calls of the published
module in SAS Micro Analytic Service returned HTTP status 201,
`retrieval_status` *ok*, the correct answer and `context_envelope`
complete, with its 1969 characters. The test of SAS Intelligent
Decisioning had returned the longer values complete with the length 99 as
well. So a longer value neither failed the execution nor was it cut there,
and the lengths above describe the model; nothing that was measured
depends on them. The empty outputs of a publishing validation, which were
first seen with a model that held 99, are another matter: the execution
had taken longer than 30 seconds
([several rows at once](#several-rows-at-once)).

**What a site does.** Nothing has to be repaired. A setup gets the lengths
of this release the next time it is saved or manifested in the Prompt
Builder of this release, which the upgrade asks for anyway; a published
decision keeps the declaration it was published with until it is
published again, and by the measurement above it runs the same with either.

Measured on 2026-10-11 with the Prompt Builder page of this release, the
bundle that ships: **Manifest setup** wrote `question` 32000, `k` 8,
`filter_json` 4000, `retrieval_mode` 32, `options` 4000, `context_dg`
32000, `context_envelope` 32000, `retrieval_status` 512 and `run_time` 8.
Measured earlier that day with the page as it was before eleven of its
German texts were reworded: the same nine lengths, and the code SAS
Intelligent Decisioning then generated for a decision declared
`varchar(32000)` for both context outputs. Measured the same day for the
step: a model that *RAG - Register Setup* registered held the same nine
lengths in SAS Model Manager. The verify suite of the page
checks the same against a stand-in for SAS Viya: a save writes the nine
lengths, every update of the model during a manifest carries 32000 for
both context outputs, and a manifest of a setup that held 99 leaves 32000.

Not measured: a datagrid or an envelope longer than 32000 characters. Both
hold the text of every hit, so their length grows with `k` and with the
size of the chunks; three hits of the measured setup made 1969 characters.
Also not measured: a value longer than its declared length for a model of
another kind, in a SAS Container Runtime container, and a retrieval model
registered with *RAG - Register Setup* with these lengths inside a
decision.

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

**Only prompts manifested with output parsing are offered.** Those are the ones
whose answer is a set of named variables, which is exactly what this stage
stores — the Prompt Builder tags them `Output-Parsing` when you manifest them
that way. The project list is built from those prompts, so a prompt project
holding none of them never appears rather than appearing and then being empty.
If a prompt you expect is missing, re-manifest it with output parsing.

### Several prompts, one after the other

The stage is a **list**. Situating a chunk and extracting fields from it are
different jobs, and one prompt asked to do both is worse at each — so *Add
another prompt* runs a second, a third, over the same chunks, in the order
shown. Each sees the chunk as the chunker produced it, not as the previous
prompt left it.

Two rules hold across the chain, and both are refused rather than resolved
silently, in the Builder and again in the ingestion:

- **Only one prompt may write the context header.** A chunk has one, so a
  second would overwrite the first — after you had already paid for it, one LLM
  call per chunk.
- **Two prompts may not store the same column**, for the same reason and with
  no way to tell afterwards which prompt wrote what.

`enrich_version` holds **every** prompt that contributed to a chunk, comma
separated, so a chunk can say that its header came from one prompt and its
`clause_type` from another.

The cost is the obvious one: **each prompt is its own LLM call per chunk**, so
two prompts cost twice as much as one. The run log prices each separately and
`rag_runs` records the total.

| You choose, per prompt | What it does |
| --- | --- |
| **Prompt project** and **prompt** | Which manifested prompt is called, once per chunk |
| **Prompt version** | *Latest* follows the prompt; a pinned version freezes exactly the prompt that version carried |
| A chunk field for each **prompt input** | What the prompt is given: the chunk, the whole document (capped at 20,000 characters), the neighbouring chunks, the heading path, the file name, the full source location, or the position (*"chunk 3 of 42"*) |
| The output stored as the **context header** | Prepended to the chunk and embedded **with** it — this is the part that changes retrieval |
| Outputs stored as **columns** | Each becomes its own typed column on the chunk table, so it can be selected, filtered and aggregated |
| **Parallel calls** | How many chunks this prompt enriches at once — per prompt, because two prompts may call two different LLMs |

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
- **The column is the output name in lower case.** Both pgvector and
  SingleStore fold an unquoted identifier, so an output called `Department`
  becomes a `department` column. Nothing else about the name changes — a
  stored output is never prefixed or abbreviated.

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
between *Chunk Documents* and *Embed Chunks*. The Builder adds **one step per
prompt** to the generated flow, in order, and adds none at all for a setup that
does not enrich. Chaining the step by hand does the same thing: each run adds
to what the last one wrote rather than replacing it.

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

For jobs launched from the Builder, that context is the option
**RAG: ingestion compute context** (`ragComputeContext`) of the Prompt Builder
object. It is deliberately not the option **Optimization: compute context**
(`computeContext`): the prompt optimization job is commonly run in the stock
context, which is exactly the one that does not work here.

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

**A preview only reads.** *RAG - Purge Documents* in its default mode,
*Preview*, says what a purge would remove and ends its summary with
*PREVIEW - nothing was changed*. Measured on 2026-10-11 on SAS Viya
2026.09 with SingleStore, with the step as it was before the change
described here: the preview said *would remove 1 chunk rows for 1
documents*, and the rows of the ledger were the same afterwards — but
the step had appended a run lock, promoted and saved the ledger again
and written *the run lock was released so the run can be retried*. A
preview now takes no run lock and neither promotes nor saves the ledger.
It is still refused while another run holds a lock that is less than 30
minutes old, and an ingestion can start while a preview reads. A preview
of a project that has no ledger fails with *no ledger table for this RAG
project - check the project name and caslib* and no longer leaves a
ledger behind. Measured on 2026-10-11 on SAS Viya 2026.09 with pgvector
and the step of release 2.3.0, run as the code SAS Studio generates for
a flow: a preview ended with *PREVIEW - nothing was changed* and *1
documents named, 1 chunk rows would be removed (live and retired)*,
appended no lock, promoted and saved nothing, and the saved file of the
ledger kept its size and time. Under the lock of a flow that was
running, and under a fresh lock row appended by hand, it ended with
*ERROR: RAG - Purge Documents failed: another ingestion run holds the
lock on this ledger - wait for it to finish, or retry in 30 minutes if it
crashed*. On a project without a ledger it ended with *ERROR: RAG - Purge
Documents failed: RuntimeError: no ledger table for this RAG project -
check the project name and caslib*, and no ledger was in memory or saved
afterwards. A purge in the mode purge promoted and saved the ledger
once and wrote no line about a released lock. Not measured: an ingestion
that starts while a preview reads.

**A purge is not in the run history.** *RAG - Purge Documents* writes no
row to `rag_runs` or `rag_doc_events`. Measured on 2026-10-11 with
SingleStore: both tables held the same number of rows before and after a
purge, 13 and 50. The purged document shows in the history only when a
later run brings it back, as a second `new` event.

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
Visual Analytics — is paid explicitly: both ingestion paths **publish `rag_runs`
and `rag_doc_events` to CAS** after each run as `<project>_RUNS` and
`<project>_DOC_EVENTS`. The database copy is authoritative; the CAS copy is
disposable and rebuildable.

*RAG - Load Vector Store*, the step of the flow, and `Ingest-Documents.sas` —
**Launch ingestion**, or the ingestion job of a setup — publish the same two
tables: every run the store holds for the collection (the newest 500) and
their document events (the newest 5,000), not only the run that publishes,
with the 35 and 10 columns, the measures as numbers and everything else as
text, promoted and saved in the caslib of the pipeline tables. For the job
`<project>` is its ledger table without the ending `_LEDGER`, which is the
table prefix of a setup. A run that finds nothing to do publishes too, so one
more run of a setup brings the runs before it into CAS, as far as the store
recorded them.

The job publishes since release 2.3.0; before, only the step did. Measured on
2026-10-11 on SAS Viya 2026.09, with the job as it was: `<project>_RUNS` still
had 3 rows after a job run on pgvector, and on SingleStore the store held four
runs of a collection while the CAS copy held three. On 2026-10-11 and on
2026-10-12 the pull of the log chain found the ledger of a setup that the
Prompt Builder had ingested in memory and its `<project>_RUNS` not.

The job as it publishes ran on the test system later on 2026-10-12, in six
SAS Job Execution jobs with a content folder, a table prefix and a collection
of their own; every job ended as completed:

- **A first run** of three documents, with one file excluded: `<project>_RUNS`
  with 1 row and 35 columns - 13 VARCHAR and 22 DOUBLE - and
  `<project>_DOC_EVENTS` with 4 rows and 10 columns, both promoted and saved,
  with the NOTE below above the last line of the log. Set against a run
  table the step had published on 2026-10-11, no column differed in name,
  type or label; two differed in length, and one of the document events.
- **A run that found nothing to do:** 2 rows and 5 events. **A run with one
  changed document:** 3 rows and 7 events.
- **With the run history switched off** the log had no line about the
  history, and both tables and their saved files were as the run before had
  left them.
- **Both tables dropped and their files deleted, then a run:** 4 rows and 8
  events - every run the store held. The run with the history switched off
  is not among them: it is not in the store.
- **With the `rag_core` of before this change** in the content folder the job
  wrote the milestone *rag history: not read for CAS (ImportError: …) - the
  ingestion itself is unaffected* and the WARNING, left both tables as they
  were and ended as completed.

The tables were read back in a compute session, not in jobs. The pull and the
link of the log chain then found those runs - see
[The Third Run](./Logging-&-Monitoring.md#the-third-run). Not provoked: an
upload that fails, a row count that differs, a promote or a save that is
refused. Changed after those runs and **not run in SAS**: the reason of a
history that could not be read reaches SAS through the function of the
logging programs, so in the WARNING and in the milestone a quote shows as a
backtick, `%` as `pct`, `&` as `+` and a semicolon as a comma, a comment mark
is split, and the reason ends after 200 characters.

The two differ in three points. With the history switched off, and for a table
the store holds no rows for, the step drops the CAS table and deletes its saved
file (read from its program); the job leaves the table as it is. The job puts
an upload in the place of a table only when it holds every row that was read.
And a history the job cannot read or publish is a WARNING in its log —
`WARNING: RAG ingestion: the run history ... was not published ...` — while the
run stays one that went through; the next run publishes the history again. A
run that published writes this line above its last line:

```text
NOTE: RAG ingestion: the run history was published to CAS, promoted and saved - <caslib>.<project>_RUNS with <n> rows and <caslib>.<project>_DOC_EVENTS with <m> rows.
```

The job definition `RAG Ingest - <setup>` holds a copy of `Ingest-Documents.sas`
from the time the setup was manifested. After the RAG content is deployed
again, **Manifest setup** has to run once for a setup before its job
publishes. *RAG - Purge Documents* writes no row to either table
([retention and erasure](#retention-and-erasure)).

`started_at` of a run is the epoch in its run id, read as UTC, and the
steps and the job take that epoch from the SAS clock of the compute
session. On the test system, whose sessions run in UTC, `started_at`
was the start of each run (measured on 2026-10-11, for 20 runs of one
collection). In a compute session whose SAS clock is not UTC — the
option `TIMEZONE` — it is off by the offset to UTC, while `finished_at`,
which Python writes from its own clock, is not. For a run of the job
that is new with release 2.3.0: up to release 2.2 the Python program of
the job made the id from its own clock. Read from the code, not
measured.

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

The summary of *RAG - Extract Text* counts such a document as skipped as
well. A new document in which the extractor finds no text — an empty file,
a PDF that holds only scans — was counted there under *documents extracted*
and under *extractors used*, although `rag_core` hands it back as skipped.
It is now counted as skipped, with the reason in the line
`rag skipped: 1 - <extractor> found no text (empty or scanned-only document)`
above the summary: three unchanged documents and one empty file give
`0 documents extracted, 0 failed, 3 skipped as unchanged, 1 skipped` and no
line *extractors used*. Measured on 2026-10-11 on SAS Viya 2026.09 with an
empty `.txt` file: the step logged `rag extract: 0 extracted, 1 skipped, 0
failed` and `rag skipped: 1 - plaintext found no text (empty or
scanned-only document)` with the path of the file, and no line
*extractors used*. The summary of that run read `0 documents extracted, 0
failed, 3 skipped as unchanged, 1 deleted, 1 skipped`, because a file had
been taken out of the folder before it. With one more new document it
read `1 documents extracted, 0 failed, 3 skipped as unchanged, 1 skipped`,
followed by `extractors used: plaintext=1`. Measured the same day for an
excluded code file beside three unchanged documents: `0 documents
extracted, 0 failed, 3 skipped as unchanged, 1 excluded`.

`docs_skipped` is added to an existing `rag_runs` table by an additive
migration on the next run, and so are `docs_excluded` — the documents the
deployment excludes, a source file with code ingestion off — and the six
enrichment columns: a collection built before the Enrich stage existed keeps
answering, and its runs simply record no LLM cost. A run reads which columns
`rag_runs` has and adds only the missing ones, so an upgraded deployment
needs no manual DDL **where the store user of the ingestion may alter the
table** — on PostgreSQL that is its owner. Where it may not, the Load step
logs `rag history: not recorded (… must be owner of table rag_runs`, nothing
is written for that run and the ingestion itself is not affected, until the
owner runs an ingestion or a DBA runs
`ALTER TABLE rag_runs ADD COLUMN docs_excluded integer DEFAULT 0` once.

Before release 2.3.0 a run wrote `docs_excluded` to a table that had no such
column, so by the code every write to `rag_runs` failed and no run was
recorded; the runs of that time are not backfilled.

Measured on 2026-10-11 on SAS Viya 2026.09, with pgvector and with
SingleStore 9.0.40:

- **Every run wrote its row.** The logs said *rag history: run … recorded*,
  and *rag history: not recorded* stood in none of them. On pgvector the
  CAS copy `<project>_RUNS` held 35 columns, `docs_excluded` among them,
  and grew with the runs of the flow, to 19 rows.
- **The column is added to a table that exists.** On SingleStore `rag_runs`
  had 34 columns and no `docs_excluded` before the first run and 35 with it
  after; the two runs in between added two rows to `rag_runs` and three
  events to `rag_doc_events`. That table is shared by every collection in
  the database, and the accelerator does not take the column out again:
  the first run of this release changes `rag_runs` for good.
- **The count is written.** With one source-code file in the documents
  folder and code ingestion off, the two runs that followed — the job and
  the flow, on pgvector — had `docs_excluded` 1, and the 17 runs before
  them 0.

Not measured: a store whose user may not alter `rag_runs`, and with it
the line *must be owner of table rag_runs*; a store that does not say
which columns a table has; whether an older runtime that is still
deployed elsewhere writes its rows to a `rag_runs` that has the new
column; and `docs_excluded` other than 0 on SingleStore. The tests run
the first two against a stand-in for the connection.

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

**The address of the vector store.** Host and database are configuration,
not a credential, and they stand in the log: the summary of *RAG - Load
Vector Store* names the vector store. One line of it reads
`NOTE: <backend> collection <collection> on <host>/<database>`, with the
host as it is set for the step — an IP address on the test system (seen on
2026-10-11 with SingleStore: `singlestore collection zz_verify_s2_v1 on
<host>/SASModel`). The line after it names the credential domain and the
name of the entry the user was read from (`<BACKEND>_RAG_USER`), not its
value: user and password come from the credential domain, and the summary
holds neither. Seen on 2026-10-11 with pgvector, as two lines that follow
each other:
`NOTE:   pgvector collection zz_verify_r12s_v1 on <host>/postgres` and
`NOTE:   credentials resolved from domain agentic-ai-keys (PGVECTOR_RAG_USER)`.
A search of that flow log for the words password, user name, credential,
secret and API key found that one line outside the echoed program; such
a search does not show that the name of the account stands nowhere as a
bare value. The summary is part of the log of every run that reaches
the step, so the address of the store is in the job log of every run of a
flow that is scheduled as a job, for everyone who may read that log.

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
| *RAG - List Documents* fails with *the run lock could not be written to … - refusing to ingest unguarded*, below `ERROR: The variable doc_id has been specified with incompatible data types` | The flow runs on a ledger the ingestion job wrote, with steps from before release 2.3.0 (or the job on a ledger a flow wrote) — see [one ledger for the job and the flow](#one-ledger-for-the-job-and-the-flow) |
| *RAG - Register Setup* fails with `POST /modelRepository/projects returned 400` and *You must specify a value for the "folderId" parameter* | The SAS Model Manager project named in the step does not exist, and the `rag_core` on the site is from before release 2.3.0. Import the package of the release or run `deploy-rag-content`, or create the project in SAS Model Manager first — see [the checkbox of the custom step](#tls-register-setup-step) |
| A RAG step writes *NOTE: The quoted string currently being processed has become more than 262 bytes long* above its summary or above its ERROR line | Steps from before release 2.3.0; nothing is wrong with the run. The steps of release 2.3.0 write neither the summary nor the ERROR line as a quoted string. Above the summary the NOTE was gone in *RAG - Register Setup* and in a flow of List, Extract, Chunk, Embed and Load; above the ERROR line it was gone in a failing *RAG - List Documents* (measured on 2026-10-11) — see [the checkbox of the custom step](#tls-register-setup-step) |
| A publishing validation on a SAS Micro Analytic Service destination returns HTTP status 201, an empty error column and **empty outputs** in some or all rows, for a decision that uses a retrieval model | SAS Micro Analytic Service ended those executions after 30 seconds. The validation sends its rows at the same time, and the models behind the nodes took longer when they were called together; a direct call of the module answers `"executionState": "timedOut"`. The lengths of the model's variables are not the cause — see [several rows at once](#several-rows-at-once) and the [Troubleshooting Guide](./Troubleshooting-Guide.md#empty-outputs-published-decision) |
| *RAG - Register Setup* fails, and its log holds *WARNING: … the step failed after the score code of the retrieval model was stored* | A request after the upload failed (a flow that does not exist, an artifact that could not be attached). The model holds the new score code; the WARNING lines below say whether it verifies TLS certificates and whether it is tagged — see [the checkbox of the custom step](#tls-register-setup-step) |
| *RAG - Register Setup* fails, and its log holds *WARNING: … it is not known whether SAS Model Manager stored the score code* | The upload of the score code got no usable answer. The tag `TLS-Verification-Off` was not updated; run the step again |
| The ingestion job ends with *the run lock could not be written to the ledger … - refusing to ingest unguarded* | The lock row of the job was not in the ledger when the job read it back. Look in the SAS log above the message: CAS refused the append (its ERROR is there), or the second export of the ledger failed. The refusal of the job was not provoked on the test system: no measured run of the job of release 2.3.0 wrote it. The lock row with its run id was read from another session during one of those runs and is named in the release step of the runs that failed; for the other runs it is taken from the job not refusing. *RAG - List Documents* wrote the same refusal there once, for a ledger in a caslib of the CAS session, below `ERROR: The caslib '…' is a session caslib. You cannot promote tables to global scope in session caslibs.` See [one ledger for the job and the flow](#one-ledger-for-the-job-and-the-flow) |
| The ingestion job ends with *another ingestion run appears active for this ledger (run lock held) - retry later* | The ledger holds a lock row of another run that is less than 30 minutes old: a flow or a job is ingesting, or a run crashed, or a job definition from before release 2.3.0 failed and left its row. The run took no lock and did not touch the ledger. Wait for the other run, or 30 minutes. A run of the job of release 2.3.0 that fails takes its own lock out and writes *the run lock was released so the run can be retried* — see [one ledger for the job and the flow](#one-ledger-for-the-job-and-the-flow). Measured on 2026-10-11: a job refused this way ended as `failed`, left no lock row of its own, and was not refused when it was run again after the other run had ended |
| A step fails with *the saved table … could not be loaded into memory (CAS messages above) - stopped, because an empty table would re-embed the corpus and overwrite the saved file* | The ledger or the embedded chunks of the project are saved in the caslib but not in memory, and CAS could not load the file. The CAS messages above the ERROR say why. The step stops before it takes a lock or starts a ledger, and the job stores no ledger after a run that failed, so the saved file is not touched — also in a SAS session that ran the job before, where up to this release the job stored the ledger of the earlier run over it. A saved file that cannot be loaded could not be staged on the test system and was not run in SAS; that the job stores no ledger after a run that failed was measured for a run that the lock of another run refused. See [a saved ledger is loaded back](#a-saved-ledger-is-loaded-back) |
| A run writes *WARNING: RAG: … is not in memory, and the files of the caslib … could not be listed (CAS messages above) - a saved copy could not be looked for.* and *WARNING: RAG: the run continues without the table and saves over a copy that may be there.* | The table is not in memory and CAS could not list the files of the caslib, so the run could not look for a saved copy and goes on without the table. For a ledger that means that every document counts as new and is embedded again, and that a saved ledger is overwritten. Stop the run if a saved copy should exist, and look at the CAS messages above the WARNING and at the caslib. The wording of the two messages - they stood on four log lines - was measured on 2026-10-11 in *RAG - List Documents*, for a ledger in a caslib of the CAS session; a run that goes on to its end behind them was not. See [a saved ledger is loaded back](#a-saved-ledger-is-loaded-back) |
| **Launch ingestion** shows *The ingestion run did not succeed* with the milestone *Ingestion failed: the saved table … could not be loaded into memory* | The ledger of the setup is saved in the caslib but not in memory, and CAS could not load the file. Nothing was ingested and the saved file is not touched; the CAS messages in the log of the job say why. The milestone was not run in SAS: no file that CAS cannot load could be staged on the test system. See [a saved ledger is loaded back](#a-saved-ledger-is-loaded-back) |
| The ingestion job of a setup ends as `failed` in SAS Job Execution | The run failed: the last ERROR line of its log, *ERROR: RAG ingestion failed: …*, says why, and the record of the job holds the same text. SAS Job Execution reports its own error code there, 5713 (*A procedure in the SAS program provided for the job completed execution with at least one error.*), and inside it an error with the code 1012 and the text of that line. Up to release 2.2, and with a job definition that was not manifested again, the job ends as `completed` with the same ERROR line — see [one ledger for the job and the flow](#one-ledger-for-the-job-and-the-flow). The failed state was measured for the job *RAG - Register Setup* generates from a flow and, on 2026-10-11, for `Ingest-Documents.sas`, with these two codes in the record of the job |
| The log of the ingestion job holds *NOTE: RAG ingestion: rag_ledger_in.json of an earlier run of this SAS session was deleted from WORK* | The program ran before in the same SAS session. The file is the export of that run and is removed so that this run cannot read it; nothing is wrong. A job started by SAS Job Execution has a WORK of its own and does not write the line. The line comes once for each of the two files that is there, `rag_ledger_in.json` and `rag_ledger_lock.json` (measured on 2026-10-11) |
| The ingestion job ends with *… of an earlier run of this SAS session could not be deleted from WORK - stopped, because the run would read the ledger of that run as its own* | The export of an earlier run is still in WORK and SAS could not delete it. Start a new SAS session and run the job there. The log of such a run also holds three ERROR messages of `PROC JSON` above its milestone, on six log lines - *ERROR: Invalid file, …/rag_ledger_in.json.*, *ERROR: Due to the previous error, the JSON output file is incomplete and invalid, …* and *ERROR: The JSON writer is in an error state at this point and no more items may be inserted.* They have the same cause: the export still runs after the delete failed, and writes to the file that is in the way. Measured on 2026-10-11 with a directory of that name in WORK; by the code, the last line names the lock instead of the file where another run holds a fresh lock at that moment |
| **Test retrieval** says *The probe failed: the Python environment of this compute context lacks the requests package* | The Python of the compute context named in `ragComputeContext` has no `requests` — see [Python packages](#python-packages). Up to release 2.2 the page showed a probe that returned no chunks in that case. The line the page reads - *RAGRETRIEVE FAILED the Python environment of this compute context lacks the requests package - see the RAG administration guide* - was measured on 2026-10-11 in a session in which the import of `requests` was made to fail; the text of the page was not run in SAS, because the test system has no compute context without the package |
| `deploy-rag-content` stops with *(409) Conflict* on the upload of a file | Seen on 2026-10-11 with `deploy-rag-content.ps1`, on a content folder that held an earlier runtime: the Files service refused the upload of a file whose old copy the script had just deleted, and answered the same upload with 201 a minute later. Run the script again - the second run completed. Until a run has completed, the file the script stopped at is missing from the folder. The script calls `mdb content-deploy` since release 2.3.0, which deletes no file - it replaces the content of a file that differs in place - and stops at the first write that fails; that has not run against SAS Viya — see [what gets deployed](#what-gets-deployed) |
| The Load step logs *rag history: not recorded (… must be owner of table rag_runs* | The store user of the ingestion may not alter `rag_runs`, which release 2.3.0 widens by the column `docs_excluded`. The ingestion itself is not affected. Let the owner of the table run an ingestion, or run `ALTER TABLE rag_runs ADD COLUMN docs_excluded integer DEFAULT 0` once — see [run history](#run-history) |
| A job generated by *RAG - Register Setup* ends with *ERROR: RAG ingestion failed - a step of this run reported an error (its ERROR line is above) and the steps after it were skipped.* | A step of the flow failed. The line is written by the job, not by a step: the ERROR of the step that failed is above it — see *The job generated from a flow* under [the checkbox of the custom step](#tls-register-setup-step) |
| `retrieval_status` of a retrieval model in a decision says *no vector-store credentials resolved* | The runtime the decision runs in has no `<BACKEND>_RAG_USER` and `<BACKEND>_RAG_PW` — see [the retrieval model in a decision](#the-retrieval-model-in-a-decision) |
| The Builder says the store is unresolved | The domain has no `<BACKEND>_HOST` / `_DB` — rerun `create-credential-domain` after adding them to your `.env` |
| No embedding model can be chosen, and no setup can be saved | No **embedding model project** is configured (`embeddingProjectID`), it holds no registered embedding model, or the user cannot read it |
| Documents are missing from the collection | Read the run log's `rag skipped:` lines — they name every document and why. Code files are skipped unless the setup opts in |
| The Setup card does not offer the RAG setup | **Enable RAG setups** is not ticked in the Options pane of the Prompt Builder object (the default, and what a report import without saved options leaves), or the repository ID is blank |
| The RAG setup card is shown but cannot be chosen | The credential domain option is `none` — RAG needs a domain |
| *Launch ingestion* or *Test retrieval* names a missing compute context | `ragComputeContext` is blank. The optimization `computeContext` is a different option |
| A setup shows its vector database as *not offered for new setups* | The deployment withholds that database in the Options pane. The setup keeps it and can still be saved, launched and tested |
| The Builder is switched off, or points at the wrong environment, after an upgrade | A report import replaced its Options — restore them with `mdb options-restore` |
| A published retrieval model returns `CERTIFICATE_VERIFY_FAILED ... self-signed certificate in certificate chain` in `retrieval_status` | The model runs where the CA bundle is not mounted — see [TLS verification in the retrieval model](#tls-verification-in-the-retrieval-model) |
| A report named **RAG Builder** still exists and shows an older page | It is the retired standalone UI of release 2.2 or earlier — see [the retired RAG Builder report](./Setup-Additional-UIs.md#the-retired-rag-builder-report) |
| Browsing the ledger shows nothing after a run | The ledger is written by the run's final step; a large corpus takes minutes. The run panel's clock shows whether it is still going |
