---
sidebar_position: 11
---

# Deploying the Builder UIs

The Prompt Builder is not required, but it is the no-code tool that puts an otherwise code-only workflow in front of the people who own the work. It is **one application** that builds up to three kinds of things, chosen on its Setup card:

- a **prompt template for LLMs** lets prompt engineers test new prompts across LLMs, compare the results, version their experiments, and turn the best prompt into a model for further consumption in the platform (for example in SAS Intelligent Decisioning);
- a **decision template for decision models** does the same for the typed questions a decision model answers — see [Decision Definitions](./Decision-Definitions.md);
- a **RAG setup** lets a data or decision engineer configure a document pipeline — sources, chunking, embedding model, vector store — save it as a governed RAG setup, and generate the ingestion job and SAS Studio flow that run it. This part is the **RAG Builder**.

One single-file build, one Job Execution definition and one Data-Driven Content object serve all three. Which of them a deployment offers is decided by three switches among the object's options (see [Configuration](#configuration-properties-panel)): decision templates are on by default, prompt optimization and RAG are off until an administrator switches them on. RAG additionally needs its runtime in SAS Content — see [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md).

:::note Standalone build
The Prompt Builder is shipped as a **standalone application** in the [`LLM-Prompt-Builder`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/LLM-Prompt-Builder) directory of this repository. It has no dependency on the SAS Portal Framework for SAS Viya — it is a single self-contained HTML file that you embed directly in a SAS Visual Analytics report through SAS Job Execution. Up to release 2.2 the RAG Builder was a second application with a build, a job definition and a report of its own; all three are retired, and a site that still has them follows the upgrade steps of the 2.3.0 [changelog](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CHANGELOG.md).
:::

## Install or upgrade with the transfer package {#the-transfer-package}

The content folder `/SAS Agentic AI Accelerator` ships as **one transfer package**, `SAS-Agentic-AI-Accelerator.json` in the folder [`SAS-Viya-Integrations`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations) of the repository. It holds the Prompt Builder ready to run — the **Visual Analytics report** that hosts the object, the **SAS Job Execution** definition that serves the single-file app and the optimization job — and with it the RAG runtime, the logging programs with their two custom steps and the usage report, the files the tables of the reports are loaded from, and a first page with an installation check. Importing it takes the place of building the app and wiring it up by hand (steps 1–2 below).

:::warning The package file of release 2.3.0 is not in the repository yet
The package is exported from the content folder of the maintainers' system **after** the commit that describes it on this page. Until the file is in `SAS-Viya-Integrations/`:

- `mdb builders-import` says that the checkout holds no package and ends with exit code 2;
- `mdb package-check` says the same and ends with exit code 1;
- seven tests of `mdb` are skipped with the reason *the package is exported from the owner's system after this round* — the six that read the shipped package and the one that wants the four packages of earlier releases gone;
- those four packages are still in the repository. They are older than this page and are not what it describes.

The commands of this section ran on the test system on 2026-10-12, in a test content root and as they were then, and what that run found changed the import. [What was measured, and what has not run](#the-package-on-sas-viya) has both.
:::

### First install and upgrade {#first-install-and-upgrade}

With `mdb` installed and your `.env` filled in ([Setup SAS Model Manager](./Setup-SAS-Model-Manager.md#envSetup)):

```bash
# first install
mdb setup               # the repository, the projects, the release table, the authorization commands of the repository
mdb builders-import     # the content folder

# upgrade
mdb builders-import
```

Then open [Start Here](#start-here), in a compute context that runs as you:

```
https://<your SAS Viya host>/SASJobExecution/?_program=/SAS Agentic AI Accelerator/Start Here&_contextName=<a compute context that runs as you>
```

Go through the lines of its installation check, run the commands of [Access to the content folder](#access-to-the-content-folder), and — on a first install — continue at [step 3](#3-provide-the-api-keys-via-the-credential-domain) (the credential domain), [step 4](#4-sas-environment-manager-configuration) (the Content Security Policy) and the optional object options of [step 5](#5-add-the-object-to-a-visual-analytics-report).

`mdb builders-import` does in one run what an import otherwise leaves to hand work afterwards:

1. It reads the options of the Prompt Builder report that is on the site — and of the retired report `RAG Builder`, while that still exists — and keeps them in a file `builder-options-captured-<time>.json` in the current directory. Importing a report replaces the options an administrator set on the object, see [Preserving builder options across a report import](#preserving-builder-options-across-a-report-import); this is what makes an upgrade one command. A report that is on the site and cannot be read stops the command here, before anything is loaded or imported.
2. It loads the release table the Prompt Builder report binds to (`--no-releases` skips that).
3. **The reports go through the SAS Viya transfer service**: the two reports, the files that are children of a report (the pictures of the usage report) and the folders they sit in, cut out of the package. The command rewrites the **import mapping** before the job starts, so that nothing is edited after the fact: the data source of the Prompt Builder report is retargeted from the shipped `Public.ACCELERATOR_RELEASES` to `SAS_CAS_LIBRARY.SAS_RELEASES_TABLE` on your CAS server, and the placeholder host `https://your-sas-viya-host` in the Data-Driven Content URL becomes `SAS_VIYA_URL`. With `--report-caslib <library>` the other tables the reports read — `LLM_LOGS`, `LLM_FACT_SHEET`, `EMBEDDING_FACT_SHEET`, `PROMPT_EXPERIMENTS` — are pointed at the tables of the same name in that library; without it they stay bound to `Public`. It waits for the job and reads every task of it: a task that did not end `completed` is printed and ends the run with exit code 1.
4. **Every file, custom step and job definition is written in place**, by the requests `mdb content-deploy` sends, with the package as the source: an object that is at its path on your site is updated under the id it has there, and a missing one is created. One line per object says `equal`, `updated`, `created` or `FAILED` — see [What an upgrade does to what a site has](#an-upgrade-keeps-ids).
5. It writes the options back: the ones it read; or, with `--options <file>`, a file saved with `mdb options-save` — the way to change a value before the import; or, on a site that has no Prompt Builder report yet, the values `mdb setup` discovers — the repository, the LLM and the embedding project ids, the SCR endpoint and the deployment type. On such a first install the three switches keep the defaults of the package, so RAG and prompt optimization are off until you enable them in [step 5](#5-add-the-object-to-a-visual-analytics-report). Either way every placeholder host that is left in the report becomes `SAS_VIYA_URL`.
6. It writes `sas-viya-cli-content-commands.txt`, see [Access to the content folder](#access-to-the-content-folder).

`mdb builders-import --dry-run` says what a run would do and writes nothing: every request it sends, the sign-in aside, is a GET, nothing is uploaded, and its `mapping:` lines are what the package itself says about its tables and its host. A run that did not do everything ends with exit code 1 and prints the command to run next, on a line of its own; that command names the kept options file, so that the second run does not read the options again from a report the import may have replaced already. The kept files stay in the directory the command ran in. They hold option values and no key.

Where a run stops:

- **A report that is on the site and cannot be read**: `<report>: it is on this site and its content cannot be read (...)`, `Nothing was imported.` Run the command again when the report can be read, name the options with `--options <file>`, or pass `--no-options-capture` — then the imported report is configured from the deployment, and what an administrator set is gone.
- **A task of the import job that did not end `completed`**: `the import job ended completed, and n of its tasks did not:` with a line per task. The options are not written into a report the command cannot be sure was replaced; the command to repeat names the kept options file.
- **A write of the in-place part that is refused or gets no answer**: that object is `FAILED`, every object behind it is listed as `not attempted`, and `Stopped at the first write that failed: ...` says what was written before it. Nothing is removed, and a second run leaves what is equal by then as it is.

### What an upgrade does to what a site has {#an-upgrade-keeps-ids}

A custom step that a flow uses is referenced by its id, and so is a job definition behind a link or a schedule. The in-place part of the import therefore never replaces an object by a new one:

```
updated      step          RAG/steps/RAG - Embed Chunks.step  (templates; id <the id on your site> kept)
updated      file          RAG/rag_core/tokens.py  (content replaced in place; id <the id on your site> kept)
updated      jobDefinition Start Here  (code; id <the id on your site> kept)
equal        step          RAG/steps/RAG - List Documents.step  (id <the id on your site>)
created      file          Logging and Monitoring/Link-Logs-To-Models.sas  (id <a new id>)
```

- **An object at its path is updated under its own id**, whatever id the package carries for it — a site that got its content from an earlier package, from `mdb content-deploy` or from the earlier deploy script has other ids than the system the package was exported from. A file keeps its id too: its content is replaced, the file is not deleted. Nothing of the exporting system — an id, a link, a user — reaches your site with the in-place part.
- **A second import changes nothing**: every line says `equal`, and nothing is written.
- **Two objects of one name in one folder** are both named with their ids, and neither is written; the rest is brought and the run ends with exit code 1. Remove the one that is not used and run the command again.
- **What is not at its path is not touched.** A step you registered in a folder of your own — the starter package of earlier releases put *LLM - Log Parser* into the folder `Custom Steps`, and a step may live in the folder of a user — stays as it is, and the import creates the step of this release at its path beside it. Update yours in place from a checkout with `mdb content-deploy --only "<its path in the manifest>" --to "<path or id of your step>"`, see [One part, somewhere else](./Model-Definition-Builder.md#one-part-somewhere-else).
- **The settings of a site in a job definition stay.** `_contextName`, the compute context a job runs in, and `contentRoot` are values of your site: a job definition that exists keeps what it holds, a new one gets the default of the package (`SAS Studio compute context`, `/SAS Agentic AI Accelerator`). `mdb builders-import` writes a compute context into `_contextName` of `Start Here`, `Load-Accelerator-Tables` and `SAS-LLM-Prompt-Builder-UI` when it knows one: the one you pass with `--compute-context`, or the option **RAG: ingestion compute context** (`ragComputeContext`) of the options it writes back, which has to be a context that runs as the requesting user. It does not take `computeContext`, the context of the optimization job.

**Another content folder.** `--content-root <folder>` brings everything but the reports into that folder and writes it into the parameter `contentRoot` of the two job definitions that have one. The reports are imported only into the folder the package was exported from, `/SAS Agentic AI Accelerator`, and only because that folder is at the root of SAS Content: the transfer service takes no target folder, and no mapping to another folder is known to work. With another content root the command says `Reports (...): NOT imported` with the reason and ends with exit code 1; `--no-reports` says that this is meant, and the transfer service is then not called at all.

After an upgrade, reload the Prompt Builder report. On a site that had the report `RAG Builder`, the switch **Enable RAG setups** in the Options pane of the object is ticked and the `RAG:` options below it hold the values of the old report; the Setup card offers *RAG setup for a document collection* as a third kind, and an existing setup opens. If the RAG card is shown and cannot be chosen, the credential domain is `none` — see [The retired RAG Builder report](#the-retired-rag-builder-report). **Enable prompt optimization (DSPy)** shows what the report stored: ticked where optimization was enabled before.

What an import does not bring, and what it replaces:

- **No tables, no projects, no credentials, no option values.** A transfer package cannot carry CAS tables, SAS Model Manager projects, credentials or the option values of a site. `mdb setup` creates the repository and the projects, the command above writes the options, [Managing Credentials](./Managing-Credentials.md) has the credential domain, and the tables are loaded as [The tables of the reports](#load-accelerator-tables) says.
- **A job definition holds a copy of its program and of its form.** An import replaces both, and the parameters with them, except the two that are settings of a site: a compute context an administrator set in the parameter `_contextName` of `SAS-LLM-Prompt-Builder-UI` stays. The job definition of a RAG setup that a site generated is not part of the package and keeps the ingestion program of the day it was manifested — see [After an upgrade: manifest every setup once](./RAG-Ingestion-and-Retrieval.md#after-an-upgrade).
- **The logging programs are replaced as files.** Settings that were written into `Log-Parser-Code.sas` or `Get-All-Prompts.sas` are gone with the old file; from release 2.3.0 on a site sets them in a program of its own — see [Settings that stay yours](./Logging-&-Monitoring.md#settings-that-stay-yours).
- **An import removes nothing.** What a site has in the folder beside the content of the package stays, the retired report `RAG Builder` among it.
- **The reports go into one folder.** Everything else follows `--content-root`; the two reports are imported into `/SAS Agentic AI Accelerator` or not at all, see [What an upgrade does to what a site has](#an-upgrade-keeps-ids).
- **The package is left on the server.** The command uploads the reduced package for the reports and does not delete it afterwards; it is listed under the packages of the transfer service.

### The content folder {#the-content-folder}

What the package brings, as `SAS-Viya-Integrations/package-manifest.yaml` lists it — one entry per object, with the repository file it is a copy of:

| Path below `/SAS Agentic AI Accelerator` | What |
| --- | --- |
| `Start Here` | Job definition: the first page, with the links and the installation check |
| `release.json` | The release, the commit the folder was deployed from, and every object of the folder with a hash of its content |
| `Prompt Builder/` | The report `Prompt Builder`; the job definitions `SAS-LLM-Prompt-Builder-UI` (the single-file app) and `Optimize-Prompt-DSPy`; `Create-Optimization-Dataset.sas` |
| `RAG/` | `steps/` with the nine RAG custom steps, `rag_core/`, `jobs/` with `Ingest-Documents.sas` and `Test-Retrieval.sas`, `models/` with the retrieval model template |
| `Logging and Monitoring/` | The report `LLM Usage Report` with its images; the custom steps *LLM - Log Parser* and *LLM - Get All Prompts*; `Log-Parser-Code.sas`, `Get-All-Prompts.sas`, `Link-Logs-To-Models.sas`, `Build-RAG-Cost-View.sas`, `Load-Fact-Sheets.sas` |
| `Data/` | `llm_fact_sheet.csv`, `embedding_fact_sheet.csv`, `decision_fact_sheet.csv`, `ACCELERATOR_RELEASES.csv`; the job definition `Load-Accelerator-Tables` |
| `Custom Steps/` | *LLM - List Available LLMs*, *LLM - Get Options*, *LLM - Create LLM Call* and the four *MM -* steps |

61 objects: 37 files, 18 custom steps, four job definitions and two reports. The folder names are the ones sites have had; `Start Here`, `release.json` and `Data/Load-Accelerator-Tables` are new in release 2.3.0, and so is the folder `Custom Steps` for a site that never imported the starter package of earlier releases. That package held *LLM - Log Parser* in `Custom Steps`; the step of this release is in `Logging and Monitoring`.

*LLM - Get Options* ships, although its program is an earlier copy of *LLM - Create LLM Call* with the same lookup of the options and the same generated code: the repository lists it as a step of the accelerator, and a flow that holds it keeps its step after an import. *LLM - Create LLM Call* is version 1.1: with *Save the code to a filename?* ticked and no path chosen it writes the code to a temporary file and leaves the fileref `_slo_LLM` assigned, where version 1.0 held an ERROR line and an ABORT statement for that. This is read from the program and **not run in SAS**; for a model whose `options.json` holds a `values` list the step is expected to fail in PROC TRANSPOSE, which is not changed and not measured.

**What never ships.** An object that the manifest does not list is not deployed, not exported and fails the check of the package when it is found in it. And whatever the manifest says, an object is refused where a part of its path has `Showcases` or `Example` in its name, is a folder named `generated`, or starts with `ZZ_VERIFY`:

- showcase and example content — it lives outside the product, for instance in a folder `/SAS Agentic AI Accelerator Showcases` of a site, and is not in the repository;
- `RAG/generated`, where the Prompt Builder writes the ingestion job and the flow of a site's own RAG setups;
- test objects.

Not in the package either: the retired report `RAG Builder` and its job definition `RAG-Builder-UI`; model definitions, which reach a site with `mdb register`; the log collection script, which runs where `kubectl` runs; and the custom step *LLM - Get Prompt Experiments* of the repository: it is the step of August 2024 that reads the experiment tracker of one model as one table and shows the options of a run as they are stored, where *LLM - Get All Prompts* pulls the experiments of every model and leaves out the options whose names say that they hold a secret. Its file stays in `Custom-Steps/` of the repository.

A site that takes a fix from a checkout without waiting for a package uses `mdb content-deploy`, and maintainers build the package with it — see [The content folder: deploy, export, check](./Model-Definition-Builder.md#the-content-folder-deploy-export-check).

### Start Here: the first page and the installation check {#start-here}

`Start Here` is a job definition at the root of the content folder. Open it in a browser:

```
https://<your SAS Viya host>/SASJobExecution/?_program=/SAS Agentic AI Accelerator/Start Here&_contextName=<a compute context that runs as you>
```

The page shows what its SAS session may read, so it is opened in a compute context that runs as you. The parameter `_contextName` of the job definition names that context - `SAS Studio compute context` as shipped; `mdb builders-import` writes the ingestion compute context of your options there, or the one you pass with `--compute-context` -, so the address works without the parameter where that context runs as you, and `&_contextName=` names another one for one request.

Measured on SAS Viya 2026.09 on 2026-10-12, when the job definition did not have the parameter: opened without it the job ran in the `SAS Job Execution compute context`, which runs its sessions under a service account on that system. SAS Viya answered every request with 401, and the page read `13 ok, 0 missing, 15 not checked`, twelve of the fifteen lines with the advice to ask an administrator. With `&_contextName=SAS Studio compute context` the same page had 97 lines that were ok. Where every request is refused the page now has one line at its top in place of those lines: what it is, and that the page is opened with `&_contextName=`. It says that the session runs under a service account where the user of the SAS session is another one than the user SAS Job Execution started it for; neither is written to the page. The parameter in the job definition and this line are **not run in SAS**.

The job writes one HTML page: what the accelerator is, links to the Prompt Builder report and the LLM Usage Report by the address they have on your system, links to these guides, and the **installation check**. Every line of the check has one of three states — `ok`, `missing`, `not checked` — with what was found and one sentence what to do:

| Section | What is looked at |
| --- | --- |
| Release | `release.json` in the content root: release, commit and date. The file is asked for at the Files service only |
| Content | Every entry `release.json` lists: present in this content root or not. A custom step is registered - by the import of the package or by `mdb content-deploy` -, so a `.step` file that was uploaded to the folder is not the step: the step is `missing` while only that file is there, and the line of a registered step names a file of its name that lies beside it and says to delete it |
| CAS tables of the reports | `ACCELERATOR_RELEASES` (Prompt Builder) and `LLM_LOGS`, `PROMPT_EXPERIMENTS`, `LLM_FACT_SHEET`, `EMBEDDING_FACT_SHEET` (LLM Usage Report): there or not, their rows, when they were loaded |
| SAS Model Manager | The repository and the projects of the LLM, the embedding model and the decision model definitions, by their names, with the number of models of each |
| Options of the Prompt Builder report | `modelRepositoryID`, `llmProjectID` and, with RAG switched on, `embeddingProjectID`: whether each names the object that was found by its name |
| Credentials | The credential domain of the provider keys, by its name. No entry of it is asked for |
| Python packages | For each part what the Python of the compute context needs: `pandas`, `swat` and `requests` for logging; for the RAG runtime also `psycopg2-binary`, `singlestoredb`, `pypdfium2` and `markitdown`; `dspy`, `requests` and `optuna` for prompt optimization |
| Publishing destinations | Whether one is defined, and their names |
| Compute contexts | The contexts the options `computeContext` and `ragComputeContext` of the Prompt Builder name, and the ones the parameter `computeContexts` names |

What holds for the page:

- **It changes nothing.** Every request is a GET, and the one file the program writes is the page, in the WORK folder of its session.
- **A site that lacks something gets a line, not a failed job.** A service that does not answer, a right the session lacks and a folder that is not there are lines of the page: `not checked` with the HTTP status, or `missing`. Where PROC PYTHON does not run in the compute context, the page says that and nothing else.
- **It shows what its session may read.** Open it in a compute context that runs as you (`_contextName`). In a compute context that runs under a service account the session is the service account: what it may not read is `not checked` with the HTTP status, and a session that is refused everything gets one line at the top. No token and no user id is written to the page, and of an answer that was refused only the status.
- **The content root is a parameter.** The job definition checks the folder its parameter `contentRoot` names: `/SAS Agentic AI Accelerator` as shipped, and the folder a deploy or an import was given with `--content-root`. Add `&contentRoot=<folder>` to the address to check another one. `_contextName` and `contentRoot` are the two settings of a site among the parameters. The other parameters name the CAS library and the CAS server of the report tables (`caslib`, `casServer`), the credential domain, the repository and the three projects by the names `mdb setup` gives them, the compute contexts to look for and the address of the guides; `SAS-Viya-Integrations/Setup/README.md` lists them.

Measured on SAS Viya 2026.09 on 2026-10-12, with the program as it was then, from a test content root that `mdb content-deploy` had filled:

- **The page is served as a page.** Opened the way a browser opens it, with `_contextName=SAS Studio compute context`, the job answered HTTP 200 with `text/html` and no `Content-Disposition`. The page began with `<!DOCTYPE html>` and held no script, link or image element.
- **What it read.** `97 ok, 2 missing, 1 not checked`, with release 2.3.0, the commit and its date, and `70 of 72 entries of release.json are present in this content root` - the two that are missing are the reports, which a deploy does not bring. With copies of the two reports in the folder a run through the jobExecution API logged `the page holds 102 checks - 102 ok, 0 missing, 0 not checked`. Such a run has no `_webout`: the program says so in a NOTE and names the file in WORK, and no page of that run was kept.
- **What is missing gets its line.** A file that was deleted: `missing | not in its folder`. A `.step` uploaded as a file in the place of a registered step: `missing | a file of that name is there, and no registered step`. A content root that does not exist: `Content root | missing | the folder was not found`, with the other sections filled. Against the content folder of the test system: `29 ok, 1 missing` - `release.json`, which that folder did not hold yet.
- **It changed nothing and showed no secret.** A listing of the folder before and after a page differed in nothing. No page held a token, a password, a host name or an e-mail address, and the user id only inside the folder path the page was given; the publishing destinations are listed by their names.

Not measured: the page for a user without administrator rights, and the program pasted into SAS Studio. Changed after that run and **not run in SAS**: the parameter `_contextName`, the one line for a session that is refused everything, the advice for a custom step, and the check of the address of `release.json`.

### The tables of the reports {#load-accelerator-tables}

A package cannot carry a CAS table. The two reports read five:

| Table | Report | Loaded by |
| --- | --- | --- |
| `ACCELERATOR_RELEASES` | Prompt Builder | `mdb setup`, `mdb builders-import` and `mdb load-releases`; or the job `Load-Accelerator-Tables` |
| `LLM_FACT_SHEET`, `EMBEDDING_FACT_SHEET` | LLM Usage Report | `mdb load-facts`; or the job `Load-Accelerator-Tables`, which also loads `DECISION_FACT_SHEET` |
| `LLM_LOGS`, `PROMPT_EXPERIMENTS` | LLM Usage Report | The log parser and the pull of your own system, see [Logging & Monitoring](./Logging-&-Monitoring.md) |

`Data/Load-Accelerator-Tables` is a job definition for a site that imported the package without `mdb` — through SAS Environment Manager, for example — and so has neither the release table nor the fact sheets: it loads `ACCELERATOR_RELEASES` from `Data/ACCELERATOR_RELEASES.csv` and the three fact sheets from the files beside it, promoted and saved, into the CAS library of its parameter `caslib` (default `Public`). Its other parameters are `_contextName` - a compute context that runs as you -, `contentRoot` and `releaseTable`, the name of the release table.

- The fact sheets are loaded by `Load-Fact-Sheets.sas`, which the job includes from the folder `Logging and Monitoring` of the content root.
- The release table gets the eleven columns of `mdb` in their order, the three numbers as DOUBLE and the eight text columns as VARCHAR. Measured on the test system on 2026-10-12 against the table `mdb load-releases` loaded: 707 rows, the same eleven columns, types and lengths, and no difference in `PROC COMPARE`. One thing differs: the formatted length - the width CAS keeps for display - is 16 in the table of the job for the four text columns whose longest value is shorter than that (`version`, `release_date`, `change_type`, `builder_part`), and 6, 10, 8 and 8 in the table of `mdb`. A width of 16 cuts no value of these columns, and the page of the Prompt Builder reads nothing from the table; what a SAS Visual Analytics object shows with it was not measured.
- A table that exists is replaced only by an upload that holds every row of its file. A file that is not in the folder, a first line with other columns and an upload that is short leave the table as it is, with a WARNING.
- `Load-Fact-Sheets.sas` replaces each fact sheet table the same way: the upload of the sheet whose rows were counted is promoted under the name of the table and saved, and the sheet is not loaded a second time. Where the promote is refused, the table is loaded again from the file it was saved to before, with a WARNING. As first built for this release the program dropped its trial and loaded the sheet a second time, so a failure of CAS between the drop and that load left the site without the table. The swap is **not run in SAS**; the program that ran on 2026-10-12 was the one with the second load.
- In a compute context that runs under a service account the log holds one NOTE that the session does not run as the user who started the job. **Not run in SAS.**
- The job writes an ERROR line and sets `SYSCC` to 1012 when a parameter is refused, or when one of the four tables is not in the CAS library at its end.

`mdb load-releases` and `mdb load-facts` load the same tables from a checkout and stay the way to load them from a workstation.

The job ran on the test system on 2026-10-12 from a test content root, with `caslib=casuser` and a release table of another name, so that no table of the system was written:

- **As shipped, in a folder without `Load-Fact-Sheets.sas`**: the NOTE `the release table ... was loaded with 707 rows, promoted and saved`, the WARNING that `Load-Fact-Sheets.sas` was not found, then the ERROR line that names the three fact sheet tables as not in the CAS library, `SYSCC` 1012 and a failed job. A second run replaced the table. Without the CSV in the folder the table was left as it was, with the WARNING. `releaseTable=x;y` was refused with one ERROR line, and nothing was read or written.
- **With the three fact sheet names replaced by test names** - three lines of the job code and fifteen of `Load-Fact-Sheets.sas`, nothing else: completed, no WARNING, `SYSCC` 0, the release table with 707 rows and the sheets with 30, 16 and 5 rows, promoted and saved. Without `decision_fact_sheet.csv` in the folder: the WARNING for that sheet, and the two others loaded. The comparison with the table of `mdb` above is of the table of this run, which read a copy of the CSV with LF line ends; for the job as shipped, which read the deployed file, 707 rows and equal column lengths were read.
- Every job log of that run begins with three ERROR lines from the start of the SAS session about a connection to CAS on port 5570, in front of the first line of a program.

Not run: the job with its own table names in `Public`.

### Access to the content folder {#access-to-the-content-folder}

The package carries no authorization rule and no share: who may read the folder is decided on the site. The rules `mdb setup` writes cover the model repository, not this folder.

`mdb builders-import` writes a file `sas-viya-cli-content-commands.txt` into the current directory (`--out <dir>` names another one, `--no-files` leaves it out): two `sas-viya authorization create-rule` commands that give the *LLM Consumers* group — the group the commands of `mdb setup` create — Read on the content folder and on what is in it, with the id of the folder filled in. The command runs neither. Review the file, replace the group if you use another, and run the two commands with the SAS Viya CLI.

On a site that upgrades, this is also what gives the people who used the RAG Builder access to the folder `Prompt Builder`: the report and the job definition `SAS-LLM-Prompt-Builder-UI` that serve the RAG Builder now live there.

### What was measured, and what has not run {#the-package-on-sas-viya}

The commands of this section ran on SAS Viya 2026.09 on 2026-10-12, as they were that day, in a test content root below the folder of a user. The content folder of the system was only read.

**What held.**

- **`mdb content-deploy`**: the dry run, the run, a second run that found everything equal and wrote nothing, the update in place that keeps the id of a custom step and of a job definition, and objects outside the manifest that were left alone - see [The content folder: deploy, export, check](./Model-Definition-Builder.md#the-content-folder-deploy-export-check).
- **`mdb package-export` and `mdb package-check`** on exports of the test root: an export without the reports was refused, one with copies of the two reports passed the check, and every change made by hand was named.
- **Start Here** in the `SAS Studio compute context`, the job **Load-Accelerator-Tables** with its release table against the one `mdb` loads, and the **settings** of the logging programs set in front of an include ([Start Here](#start-here), [The tables of the reports](#load-accelerator-tables), [Settings that stay yours](./Logging-&-Monitoring.md#settings-that-stay-yours)).

**What did not hold, and what the commands do since.**

- **A package of a folder that is not at the root of SAS Content landed in a new folder at the root.** `mdb builders-import` of that day handed the whole package to the transfer service. The package had been exported from the test root, held no report, and was imported on the system it came from. Afterwards a new folder with the name of the test root existed at the root of SAS Content, and the five sub-folders of the test root, with everything in them, had it as their parent; the command had said `imported 71 object(s).`, `Done.` and ended with exit code 0. A copy of the package that was rewritten for a second test root arrived in a second new folder at the root, and the folder it was meant for stayed empty; the mapping the service returned for it held a version and nothing to name a folder with. This was seen for one test root, on the system the package was exported from, and no export that keeps the folders above its root was imported for comparison.
- **A file of the same name under another id was not replaced.** In that import one file was in its folder under another id than the package names. The file in the folder kept its content and its id. The file of the package existed afterwards under the id of the package in no folder; its task said `was updated but cannot be added to the intended folder` and `An item named "tokens.py" of type "File" already exists in the folder "rag_core"`. Three of the 71 tasks ended `completedWithWarnings`, the job said `completed` with `errorCount` 0, and the command showed none of it. What was on the system under the ids of the package was written in place: 18 custom steps, 35 files and three job definitions kept their ids. A custom step or a job definition of the same name under another id was not measured.
- **Since then** the command hands the transfer service only the reports and writes every file, custom step and job definition in place under the id it has on the site; it imports a report only into the folder the package was exported from, at the root of SAS Content; and it reads the tasks of the import job.
- **Start Here opened without a compute context** ran under a service account and checked nothing - see [Start Here](#start-here). Both job definitions carry `_contextName` since.
- **A sign-in the system refused** ended `mdb content-deploy` with a traceback; the same command ran a minute and a half later. `mdb` asks such a refusal again since.
- **`Build-RAG-Cost-View.sas` with two run tables** failed and still said that its table was built - see [The Third Run](./Logging-&-Monitoring.md#the-third-run).

**Not run against SAS Viya:** what was built after that run - the in-place part of `mdb builders-import` and its options `--content-root`, `--no-reports`, `--compute-context` and `--no-options-capture`; the update of the content of a file in place, which is written from the documentation of the Files service; the stop at the first write that fails; the settings of a site; `mdb content-deploy --to`; `mdb package-check --release` and the `drift:` lines; and the changes to the two job programs. **Never measured:** an import of a package that holds the reports, and with it the capture and the write-back of the options, the import mapping and the commands file - that needs a system whose Prompt Builder report may be written; an import of a package whose root folder is at the root of SAS Content, which is how every release so far was installed; and what the transfer service does with a folder or a report of the same name under another id.

### Importing without `mdb` {#importing-without-mdb}

:::warning An import without `mdb` leaves everything to the transfer service
Measured on 2026-10-12 (see [What was measured](#the-package-on-sas-viya)): the transfer service did not replace a file that was in the target folder under the same name with another id - it wrote the file of the package into no folder and reported its job as completed. An object has another id than the package names where it did not come from an import of a package of the same origin: created by `mdb content-deploy`, by the earlier deploy script, or by hand. After an import without `mdb`, read the tasks of the import job, and run `mdb content-deploy --dry-run` from a checkout of the release: it says of every file, custom step and job definition whether it equals the checkout.
:::

The one file imports like any transfer package. What `mdb builders-import` does beside the import is then hand work: the options of the Prompt Builder report are the defaults of the package afterwards — save them first, see [Preserving builder options across a report import](#preserving-builder-options-across-a-report-import) —, the tables stay bound to `Public`, the release table and the fact sheets are loaded by the job [Load-Accelerator-Tables](#load-accelerator-tables), and the access rules are yours to write.

**SAS Environment Manager.** Import the JSON from **SAS Environment Manager → Content → Import**. Then correct the host in [step 5](#5-add-the-object-to-a-visual-analytics-report): open the report and update the Data-Driven Content object's base URL to your SAS Viya server.

**SAS Viya CLI.** Use the `transfer` plugin of the `sas-viya` command-line interface. This assumes you have installed the CLI, created a connection profile, logged in and installed the plugin as described in [Introduction — SAS Viya CLI Setup](./Introduction.md#sas-viya-cli-setup). In the commands below substitute `https://viya.example.com` with your own SAS Viya URL.

1. **Point the package at your host.** The package ships the Prompt Builder object pointing at the placeholder host `https://your-sas-viya-host`. Replace it in the JSON so that the imported report loads the app from your server (the percent-encoded program path after the host is left untouched):

   ```bash
   # bash / Linux / macOS
   sed -i 's#https://your-sas-viya-host#https://viya.example.com#' SAS-Agentic-AI-Accelerator.json
   ```

   ```powershell
   # PowerShell / Windows
   (Get-Content SAS-Agentic-AI-Accelerator.json) `
     -replace 'https://your-sas-viya-host','https://viya.example.com' |
     Set-Content SAS-Agentic-AI-Accelerator.json
   ```

2. **Upload** the package — the command prints the new package's `id`:

   ```bash
   sas-viya transfer packages upload --file SAS-Agentic-AI-Accelerator.json
   ```

3. **Import** it by that id:

   ```bash
   sas-viya transfer packages import --id <package-id>
   ```

   Or do both in one go, capturing the id (requires [`jq`](https://jqlang.github.io/jq/)):

   ```bash
   pkg=$(sas-viya --output json transfer packages upload \
     --file SAS-Agentic-AI-Accelerator.json | jq -r '.id')
   sas-viya --output text transfer packages import --id "$pkg"
   ```

   (On older `transfer` plugin versions the same commands are `sas-viya transfer
   upload` / `sas-viya transfer import` — they still work, marked deprecated.)

:::note The host is a transfer substitution
The placeholder lives in the package's `substitutions` block, which is what SAS's import machinery is designed to remap. Editing the string before upload (step 1 above) is the simplest fix. Alternatively, remap it at import time with a **mapping file**: pass `--mapping mapping.json` to the upload and the CLI writes a JSON file whose `substitutions` block holds the entry with the placeholder URL — edit that value to your host, then pass the same file to the import:

```bash
sas-viya transfer packages upload --file SAS-Agentic-AI-Accelerator.json --mapping mapping.json
# edit mapping.json: replace https://your-sas-viya-host in the substitutions block
sas-viya transfer packages import --id <package-id> --mapping mapping.json
```

(`sas-viya transfer get-mapping --id <package-id>` regenerates the file for an already-uploaded package.) If you do neither, the report still imports; you then correct the object's base URL in [step 5](#5-add-the-object-to-a-visual-analytics-report).
:::

After the import, continue at [step 3](#3-provide-the-api-keys-via-the-credential-domain) — the Job Execution definition and the report already exist under **SAS Content > SAS Agentic AI Accelerator > Prompt Builder**.

:::warning A package from before 2.3.0 brings the old RAG UI back
Packages exported before release 2.3.0 hold a report named **RAG Builder** and a job definition **RAG-Builder-UI** — the standalone RAG UI that is retired. Importing such a file through SAS Environment Manager or the CLI puts both back. `mdb builders-import` names such a package before it imports it; use the package of a current checkout.
:::

## Preserving builder options across a report import

:::danger Importing a report overwrites your configuration
Every option you set on the Prompt Builder object — repository and project IDs, the SCR endpoint, the credential domain, the three switches, and for RAG the content root, the ingestion compute context and which vector stores the deployment offers — is stored **inside the Visual Analytics report**, not alongside it. A transfer package carries those values too, so importing a newer version of the report **replaces your settings with whatever the package was built against**.

This does not fail loudly. The report still opens and the app still runs; it simply shows the package's defaults — RAG switched off, no project IDs. `mdb builders-import` reads the options before it imports and writes them back; for an import made any other way, save your options first.
:::

`mdb builders-import` without `--options` does both steps below by itself and keeps what it read in `builder-options-captured-<time>.json`. The two commands are for a file to keep with your deployment records, to edit before an import, to carry to another deployment, or for a package that is imported without `mdb` (see [Model Definition Builder](./Model-Definition-Builder.md) for installing the CLI and its `[viya]` extra):

```bash
# BEFORE importing a new report package
mdb options-save --file builder-options.json

# AFTER the import
mdb options-restore --file builder-options.json --dry-run   # check what would change
mdb options-restore --file builder-options.json             # write it back
```

`options-save` discovers the Model Manager repository and the LLM and embedding projects exactly as `mdb setup` does, then overlays whatever your live **Prompt Builder** report already holds — so a deployment that has been tuned is captured as tuned, not as freshly bootstrapped. Keep the resulting file with your deployment records; it supersedes the `llm-prompt-builder.json` seed as the record of how this environment is configured.

`options-restore` writes back **only the options named in the file**. The newly imported report keeps its new layout, data items and objects, and regains your configuration. It also replaces the shipped placeholder host `your-sas-viya-host` - in the object's Data-Driven Content URL and in the `viyaHost` / `SCREndpoint` defaults - with `SAS_VIYA_URL`, so a report imported by hand needs no URL edit afterwards (`mdb builders-import` does both in one run).

Points worth knowing:

- **The report is matched by name** (`Prompt Builder`). If you have renamed the report in your deployment, rename it in the file to match, or the restore skips it.
- **An option the report does not have is reported, not inserted.** That usually means the option was renamed or removed in the newer version of the app — worth reading the message rather than ignoring it. When the missing option is one the current Prompt Builder report has, the report on the site is an older one: everything it can hold is still written, and the command ends with exit code 1 so that the gap is not taken for a finished restore — import the package of this release and restore again. It is also why the shipped report already holds every option, including the ones that only show while a switch is on.
- **A switch is saved as the word `true` or `false`**, which is what its checkbox in the Options pane stores. A file saved with 2.2.x holds the same two words for `enableOptimization`, so it restores unchanged.
- **`sslVerify` is saved and restored like every other option**, as the same two words. A file saved with 2.2.x does not name it, and nothing supplies a value for it: the imported report keeps its own default, which is ticked. A package is exported with the option ticked: on a report that would ship it switched off, `mdb package-export` ends with exit code 1 and writes nothing.
- **API keys are deliberately not saved.** This file is meant to be kept with deployment paperwork and reviewed in a diff, which is no place for a provider key. The Prompt Builder reads its keys from the credential domain at run time — see [Managing Credentials](./Managing-Credentials.md).
- **The file records which deployment it came from.** Restoring it against a different SAS Viya URL still works — moving configuration between environments is a legitimate thing to do — but the command says so first, because doing it by accident is exactly the failure this guards against.
- **Concurrent edits fail loudly.** The write is conditional on the report being unchanged since it was read, so a colleague editing the report in Visual Analytics at that moment causes an error rather than silently losing their work.

- **A sign-in that is refused can pass.** Seen on 2026-10-11 and again on 2026-10-12: a call of `mdb` ended with `401 Client Error` at the sign-in and went through when it was repeated; the sign-in service of that system refused password grants that overlapped. `mdb` asks a refused sign-in again, up to three times (`MDB_LOGIN_ATTEMPTS=1` switches that off), and a refusal that stays ends the command with one line and exit code 2: `SAS Viya at <url> refused the sign-in of <user> ... (asked 3 time(s)): HTTP 401 from /SASLogon/oauth/token.` The repeat of the 401 as that system sends it and the one line are **not run against SAS Viya**.

Measured on 2026-10-11 on SAS Viya 2026.09 with the Prompt Builder report of this release, after the report had been saved once in SAS Visual Analytics with **Enable RAG setups** ticked: in a round of `mdb options-save` and `mdb options-restore`, the dry run of the restore named six options to write and none that the report lacks, and the restore wrote them.

On a brand-new environment with no live report to capture from, run `mdb options-save` anyway: with nothing to overlay it writes the discovered values alone, which is the same content the seed file carries.

### The retired RAG Builder report

Up to release 2.2 the RAG Builder had a report of its own, named **RAG Builder**, with its own options. A site that upgrades still has it, and its options are carried into the Prompt Builder report by a **fold** that all three commands share:

- `mdb options-save` reads the live `RAG Builder` report when one exists and writes **one** `Prompt Builder` section. `mdb builders-import` without `--options` reads both reports the same way before it imports. Run `mdb options-save` **before** the import and before deleting the old report — afterwards the new report holds its own defaults, and what the Prompt Builder report holds always wins over the old report. A site that imported first still has a way back while the old report exists: `options-save` names every value of the old report that it did not carry, with what the old report held. Copy the ones you want into `builder-options.json` and run `mdb options-restore`.
- `mdb options-restore` and `mdb builders-import --options` fold a file saved with 2.2.x, which has a `RAG Builder` section, in the same way before they write. Nothing is ever written to the retired report, and nothing is deleted.

What the fold does, and prints as it does it:

| In the old report | In the Prompt Builder section |
| --- | --- |
| The RAG options (`embeddingProjectID`, `contentRoot`, `casServer`, `storeSslmode`, `deletedPolicy`, `retainDays`, `recordHistory`, `embedReplicas`, `persistElements`, `persistChunks`, `enable_pgvector`, `enable_singlestore`) | Carried under the same name with the stored value, where the Prompt Builder section has none |
| `computeContext` | Becomes `ragComputeContext`. It is never written to the Prompt Builder's `computeContext`, which is the context of the optimization job |
| `viyaHost`, `modelRepositoryID`, `SCREndpoint`, `deploymentType`, `credentialDomain` | The Prompt Builder value is kept; a different value in the old report is printed. If the credential domains differ, the RAG part now reads its secrets from the kept one — add the vector-store entries to it. If the kept value is `none`, there is no domain to add them to and RAG cannot be used: the message names the domain of the old report — set `credentialDomain` to it in the file before restoring, or in the Options pane afterwards. Models without an API key are called as before |
| `enabledBackends` | Dropped — it has been unused since the per-store switches exist |
| *(nothing)* | `enableRag` is set to `true` when the site had the report `RAG Builder` — live, or as the `RAG Builder` section of a file saved from one — and only if the Prompt Builder section does not hold an `enableRag` yet. The sign is a content root or a CAS server in the old section, which the report shipped with 2.2.x holds from the start |

The old report does not record whether anyone used it, so the fold switches RAG on at every site that had it. **A site that does not use RAG** runs `mdb options-save`, sets `"enableRag": "false"` in the `Prompt Builder` section of `builder-options.json` and imports with `mdb builders-import --options builder-options.json`, or clears **Enable RAG setups** in the Options pane afterwards. A 2.2.x file that was saved while no `RAG Builder` report existed holds only discovered values in its `RAG Builder` section; `enableRag` is then not set and RAG stays off.

While a report named `RAG Builder` is still on the site, `options-restore` and `builders-import` say so on every run: it keeps serving the old standalone UI with its own options. Once the Prompt Builder report shows your RAG setups, delete the report **RAG Builder** and the job definition **RAG-Builder-UI** next to it (SAS Environment Manager → Content, or SAS Drive), and give the people who used it Read access to the **Prompt Builder** folder if they did not have it - see [Access to the content folder](#access-to-the-content-folder). `mdb` deletes nothing; `mdb content-deploy` and the in-place part of `mdb builders-import` name the two objects - `RAG/RAG Builder (report)` and `RAG/RAG-Builder-UI (jobDefinition)` - among what is in the folder and not in the package. Afterwards bookmarks to that report and to the URL of `RAG-Builder-UI` are dead. A package that was exported before 2.3.0 still holds both objects, and importing one brings them back.

## 1. Get the single-file app

You have two options:

**Option A — use the prebuilt file (recommended).** Download the ready-to-use [`dist/index.html`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/dist/index.html) from the repository. No Node.js or build step is required — skip straight to step 2. Or copy the code from there to your clipboard and move to step 2.

**Option B — build it yourself.** With Node.js (`^20.19 || >=22.12`) installed:

```bash
git clone https://github.com/sassoftware/sas-agentic-ai-accelerator.git
cd sas-agentic-ai-accelerator/LLM-Prompt-Builder
npm install
npm run build
```

This produces the same single-file `dist/index.html`, with all three kinds in it — there is no separate build for the RAG Builder. See the [project README](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/README.md) for local development and customization details.

## 2. Create a SAS Job Execution definition

The single-file app is served to Visual Analytics through **SAS Job Execution**:

1. In **SAS Data and AI Studio**, click on *New > Job definition* to create a new **Job Definition**
2. In the *Job Definition (edit)* tab go to the right hand side bar and click on the *first icon (Associate a Form)* and change it from **Prompt designer to HTML form**.
2. Copy the full contents of `dist/index.html` and paste it into the *Job Definition (edit)* tab
3. Save the job to the place you want it in SAS Content - note that people that should be using it need read access.
4. On the right hand side bar click the *second icon (Properties)*, expand the *Details* section and copy the value under *Job URL* - it should look something like this: `https://your-viya-host/SASJobExecution/SASJobExecution/?_program=path-to-your-job-definition` after the path value add the following: `&_action=form`, as this enables us to view the HTML form without having to provide any SAS code. Save that URL as we will need it in fifth step.

:::note Why the build base64-encodes its scripts
SAS Job Execution serves HTML through a Go template engine that treats `{{ … }}` as directives. The build base64-encodes every inline `<script>` and decodes it at runtime so the minified bundle — which inevitably contains `{{`/`}}` — is not corrupted. This is automatic and requires no action.
:::

## 3. Provide the API keys via the credential domain

The Prompt Builder resolves provider API keys from a **SAS Viya credential domain** (default `agentic-ai-keys`) under the identity of the signed-in user — a user credential overrides a group credential, and models the user holds no key entry for are shown disabled with a note. RAG setups take the vector-store credentials, and where each store lives, from the same domain. Set the domain up once with the CLI-based scripts as described in [Managing Credentials](./Managing-Credentials.md); no data source, table, or report assignment is involved.

:::warning Keys stay governed
Keys live encrypted in the Credentials service — never in the report definition, the URL, or a data table. Who holds a credential in the domain (directly or via group membership) is who can run paid model calls.
:::

## 4. SAS Environment Manager configuration

To let the app run inside SAS Visual Analytics, set the following Content Security Policy directives via **SAS Environment Manager → Configuration → View Definitions**.

**SAS Visual Analytics** — `sas.commons.web.security` → *SAS Visual Analytics* → `content-security-policy`:

```
default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src * blob: data:; frame-src * blob: data: mailto:; connect-src 'self' *.sas.com login.microsoftonline.com graph.microsoft.com *.arcgis.com *.arcgisonline.com; object-src 'none'
```

**SAS Job Execution** — `sas.commons.web.security` → *SAS Job Execution* → `content-security-policy` (replace `<sas-viya-host>` with your environment's URL). The `'unsafe-inline'` and `'unsafe-eval'` in `script-src` are what allow the base64-decoded single-file bundle to run:

```
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://<sas-viya-host>; style-src 'self' 'unsafe-inline'; img-src * blob: data:; child-src 'self' blob: data: ; frame-ancestors 'self'; form-action 'self';
```

**IFrame sandbox** — verify the `sas.visualanalytics` definition's *IFrame Sandbox Attribute Value* contains at minimum `allow-same-origin allow-scripts`.

:::note
That sandbox does not include `allow-popups`, which is why the links that leave the page — "Open in SAS Model Manager", the link to a saved prompt-test, the links to a decision in the delete dialogs — copy their URL instead of forcing a new tab on a normal click (use the browser's right-click → *Open link in new tab*). Add `allow-popups` to the *IFrame Sandbox Attribute Value* to let these links open a tab directly. A link is copied only where no tab opened: with `allow-popups` a click opens the tab and leaves the clipboard alone.

The page carries the referrer policy `same-origin`, a `meta` element in its head: what it asks of another origin than its own carries no `Referer`, what it asks of its own origin keeps it. A link to another site — the user guide link of the *Optimize* step — therefore does not tell that site the address of your SAS Viya host. A tab the page opens on SAS Viya itself, such as SAS Model Manager, is told where the click came from, as before, and the calls to SAS Viya keep their `Referer`; that holds where the page is served from the SAS Viya origin, as in the setup described on this page, and not where the `viyaHost` parameter names another origin. A call to a model container on another host carries no `Referer` either, but that host still learns where the page runs: a browser names the origin of the page in the `Origin` header of every cross-origin call, whatever the referrer policy.
:::

Restart the following kubernetes pods of your SAS Viya environment if saving the changes didn't trigger a restart automatically:
- sas-job-execution
- sas-job-execution-app
- sas-logon-app
- sas-visual-analytics
- sas-visual-analytics-app

Example kubectl command:
```bash
kubectl get pods -n <your-namespace> -o name | grep -E 'pod/sas-job-execution|pod/sas-logon-app|pod/sas-visual-analytics' | xargs kubectl delete -n <your-namespace>
```

## 5. Add the object to a Visual Analytics report

If you imported the transfer package then open up the SAS Visual Analytics report under SAS Content > SAS Agentic AI Accelerator > Prompt Builder > Prompt Builder and then continue.

1. In a Visual Analytics report, add a **Data-Driven Content** object and in the **Options** pane under Web Content enter the URL from the previous step or if you imported it update the base URL to your SAS Viya server.
2. Assign the **release table** to the object: in the **Data** pane pick `Public.ACCELERATOR_RELEASES` (loaded by `mdb setup` or `mdb load-releases`; the library and name follow `SAS_CAS_LIBRARY` / `SAS_RELEASES_TABLE`) and add any of its columns to the object's role. A Data-Driven Content object does not render until it has a data assignment; the Prompt Builder itself reads nothing from it, and provider keys still come from the credential domain (step 3). The table carries the accelerator's changelog - one row per release and per change, with a `component` column - so the same report can show what changed for the Prompt Builder next to the object. A change to the Prompt Builder is one row for each part of it that it concerns, named in the column `builder_part` (`Prompt`, `Decision`, `RAG`): a page that lists the changes keeps its filter on `component` and gets a button bar on `builder_part`, with which a reader picks the part - see [The release table](./Model-Definition-Builder.md#the-release-table) for the rows and for what a page built before the column shows.
3. Open the object's **Properties** panel and set the configuration values (see below).

### Configuration (Properties panel)

The object publishes **one list of options**, in the order of the tables below: first the options every kind shares, then the three switches, each directly followed by the options that only matter while it is on. Visual Analytics writes every value into the object's URL under the **URL parameter** name and reloads the app, which is how a changed switch makes its options appear or disappear.

The five environment-specific values are exactly those captured in your `llm-prompt-builder.json` — the file produced in the [Setup SAS Model Manager](./Setup-SAS-Model-Manager.md) chapter (regenerate it by re-running `mdb setup` if you have lost it), where each stands under the name of its URL parameter: `modelRepositoryID`, `llmProjectID`, `embeddingProjectID`, `SCREndpoint` and `deploymentType`. `mdb builders-import` writes the same five into the report by itself, so copying them by hand is only needed for a report you built yourself.

**Shared by every kind** — always shown:

| Properties-panel field | URL parameter | Default | Meaning |
|---|---|---|---|
| SAS Viya host | `viyaHost` | the embedding origin | SAS Viya base URL. Leave blank to default to the embedding origin. |
| Model Manager repository ID | `modelRepositoryID` | *blank* | Model Manager repository in which new prompt projects, decision projects and RAG projects are created. **Required by every kind.** |
| LLM project ID | `llmProjectID` | *blank* | Model Manager project holding the available LLM definitions (each with an `options.json`). Required for prompt and decision templates; a page that only builds RAG setups can leave it blank. |
| SCR endpoint | `SCREndpoint` | *blank* | Base URL of the SCR endpoint hosting the LLM and decision model containers and, for RAG, the embedding containers. Required for prompt and decision templates; RAG reads a blank value as `<SAS Viya host>/llm`, the host being the *SAS Viya host* option or the address the page is opened at. For a RAG setup the one resolved value goes to the ingestion job, *Test retrieval*, the Embed step of the generated SAS Studio flow and the retrieval model, so it has to be reachable from the compute session of the ingestion and from the publishing destination — set the option where the address of the page is not. |
| Deployment type | `deploymentType` | `k8s` | `k8s` or `aca` (Azure Container Apps / Instances) — how the model containers are deployed. See [Container Deployment](./Container-Deployment.md). |
| Default judge model | `judgeModel` | *blank* | Optional. Name of an LLM in the LLM project used by default for the [LLM-as-a-Judge](../User-Guide/Prompt-Builder.md) comparison; prompt engineers can still override it in the app. Leave blank for no default. |
| Model card report URI | `modelCardReportURI` | *blank* | Optional. A SAS Visual Analytics report path (`/reports/reports/<uuid>`). When set, manifesting the best prompt embeds that report on its model card as the custom chart, hosted from the *SAS Viya host* above. Leave blank to omit. |
| Credential domain | `credentialDomain` | `agentic-ai-keys` | Credential domain that provider API keys and, for RAG, the vector-store credentials resolve from (see step 3). Enter `none` when only key-less self-hosted models are used: no credential is then looked up for prompt and decision templates. **RAG needs a domain** — with `none` its card is shown disabled with that reason. |
| Verify TLS certificates in manifested models | `sslVerify` | ticked | A checkbox. Ticked: a model manifested in the Prompt Builder verifies the certificate of the model container it calls. Not ticked: models manifested from then on do not verify it, carry the tag `TLS-Verification-Off` in SAS Model Manager, and the step that manifests them shows a warning. See [below](#verify-tls-certificates-in-manifested-models). |

#### Verify TLS certificates in manifested models {#verify-tls-certificates-in-manifested-models}

Three kinds of manifested model make an HTTPS call themselves when they are scored: a prompt template manifested with *Include the LLM call in the manifested model*, a decision template, and the retrieval model of a RAG setup. Each verifies the certificate of what it calls against the CA bundle of the SAS Viya deployment. `sslVerify` decides what is written into the score code of such a model when it is manifested:

- **It is a default written at manifest time.** Changing the option changes the models manifested from then on. A model that is already manifested keeps what it was manifested with, in SAS Model Manager and on every destination it is published to, until it is manifested again.
- **It changes nothing else.** The calls the Prompt Builder page makes in the browser, the ingestion job of a RAG setup, and a prompt manifested for the *Call LLM* node (where the node makes the call) are as before.
- **The SAS Studio custom step has a checkbox of its own.** A retrieval model registered with *RAG - Register Setup* is not manifested on this page, and the step does not read `sslVerify`. Its checkbox **Verify TLS certificates in the registered retrieval model** (*Runtime* section, ticked by default) writes the same line into the same score code and sets and removes the same tag — see [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md#tls-register-setup-step).
- **A destination can overrule it in both directions** with an environment variable: `LLMCONTAINERSSLVERIFY` for prompt and decision templates, `RAGEMBED_SSLVERIFY` and `RAGSTORE_SSLVERIFY` for the retrieval model. `true` switches verification on for a model manifested with it off, `false` the reverse — see [Deployment of Decisions](./Deployment-of-Decisions.md#tls-default-at-manifest-time).
- **Off is visible.** A model manifested while the option is not ticked carries the tag `TLS-Verification-Off`, on all three kinds, and manifesting it again with the option ticked removes the tag. The step shows a warning before the click that names this option and the variable that turns verification back on: *Finalize* for a decision template, *Finalize* for a prompt only while **Include the LLM call in the manifested model** is ticked, and *Manifest setup* for a RAG setup. [Finding the models that do not verify](./Deployment-of-Decisions.md#finding-the-models-that-do-not-verify) lists such models and names the line to look for in a score code.
- **A report saved before the option existed shows the checkbox ticked**, and so verifies. The state of the checkbox is stored with the report: it is there again after the report is saved and reopened.

Leave it ticked unless a destination cannot verify. The case it exists for is SAS Micro Analytic Service running Python in a container that does not have the CA bundle; [Publishing to SAS Micro Analytic Service: the certificate bundle](./Deployment-of-Decisions.md#mas-certificate-bundle) describes it, the fix that keeps verification on, and when switching it off is acceptable — a test system on a closed cluster network, not production.

In the URL the option reads like a switch: Visual Analytics writes `sslVerify=true` or `sslVerify=false`; in a URL written by hand `0`, `no` and `off` also switch verification off, in any case. A blank, missing or any other value keeps it on.

**The three switches** — always shown, each a checkbox:

| Properties-panel field | URL parameter | Default | Ticked | Not ticked |
|---|---|---|---|---|
| Enable decision templates | `enableDecision` | ticked | The Setup card offers decision templates for decision models, with the question designer, the **Evaluate** step and the typed manifest. | The kind is not offered and nothing is shown for it. The *Decision Model Project* is not looked up and no decision model is called. Existing decision projects and templates are not listed; they stay untouched in SAS Model Manager. |
| Enable prompt optimization (DSPy) | `enableOptimization` | not ticked | Prompt templates gain the **Optimize** step, and the three `Optimization:` options below appear. See [Enabling Prompt Optimization](./Enabling-Prompt-Optimization.md). | No Optimize step, and no request to SAS Job Execution or to the folder of the optimize job. |
| Enable RAG setups | `enableRag` | not ticked | The Setup card offers the RAG setup, and the `RAG:` options below appear. Nothing is requested for RAG until a user chooses that kind — the RAG Builder is loaded on first use. | The kind is not offered; nothing of the RAG Builder is loaded and no RAG project, embedding project, CAS library or content folder is requested. Stored RAG setups stay untouched. |

A switch that is off costs nothing at run time, which is the point of having it: a site that does not publish decision models, or has no vector store, should neither show the kind nor make requests that can only fail. With one kind left, the question *What are you building?* is not shown at all.

The report stores a switch as the word `true` or `false`. Up to release 2.2 **Enable prompt optimization (DSPy)** was a dropdown with *Enabled* and *Disabled* that stored the same two words, so a report saved then keeps its setting: a stored `true` shows as a ticked checkbox.

**Only while prompt optimization is enabled:**

| Properties-panel field | URL parameter | Default | Meaning |
|---|---|---|---|
| Optimization: compute context | `computeContext` | *blank* | SAS Compute context the optimization job runs in. Its Python needs `dspy`. Not used by RAG. |
| Optimization: job path | `optimizeJobProgram` | *blank* | SAS Content path of the optimize job definition, e.g. `/Public/Jobs/Optimize-Prompt-DSPy`. |
| Optimization: minimum samples | `minOptimizeSamples` | `30` | Minimum dataset rows before an optimization run is allowed; the Optimize panel warns below 50. |

**Only while RAG is enabled** — what each one governs is explained in [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md):

| Properties-panel field | URL parameter | Default | Meaning |
|---|---|---|---|
| RAG: embedding model project ID | `embeddingProjectID` | *blank* | Model Manager project holding the registered embedding models. The RAG Builder lists that project instead of asking for a model name; without it no embedding model can be chosen and no setup can be saved. |
| RAG: content root | `contentRoot` | `/SAS Agentic AI Accelerator/RAG` | SAS Content folder that holds the RAG runtime - the folder `RAG` of the content folder, which the transfer package brings. Generated job definitions go to its `generated` subfolder unless a setup names another. |
| RAG: CAS server | `casServer` | `cas-shared-default` | CAS server of the ingestion ledger and the pipeline tables. |
| RAG: ingestion compute context | `ragComputeContext` | *blank* | SAS Compute context the ingestion job and the retrieval test run in. It **must run as the requesting user** — *SAS Studio compute context* does, the stock *SAS Job Execution compute context* does not. Launching and testing are refused until it is set. |
| RAG: vector store TLS | `storeSslmode` | `prefer` | How the ingestion connects to the vector store: `prefer`, `require`, `verify-ca`, `verify-full`, `allow` or `disable`. Stores other than PostgreSQL read `disable` as no TLS and anything else as TLS on. |
| RAG: when a document disappears from the source | `deletedPolicy` | `retire` | `retire` keeps its chunks as history that no retrieval returns; `purge` removes them for good. |
| RAG: keep retired chunks for (days) | `retainDays` | `0` | Retired chunk generations older than this are dropped after each load. `0` keeps them forever. |
| RAG: record run history | `recordHistory` | on | A checkbox. Writes `rag_runs`, `rag_doc_events` and `rag_configs` beside the collection and publishes the first two to CAS. |
| RAG: embedding container replicas | `embedReplicas` | `1` | How many replicas of the embedding container the deployment runs; the ingestion sizes its parallel calls from it. |
| RAG: save the element table to disk | `persistElements` | `1` | `1` saves `<prefix>_ELEMENTS` to disk, `0` keeps it in memory only. The value is recorded on each setup; the SAS Studio flow that *Manifest setup* generates does not apply it yet and keeps this table in memory either way. |
| RAG: save the chunk table to disk | `persistChunks` | `1` | The same for `<prefix>_CHUNKS`; in the generated flow it reaches the enrichment step only so far. The ledger and the embedded chunks are always saved. |
| RAG: offer pgvector (PostgreSQL) | `enable_pgvector` | on | A checkbox per vector database: whether users may choose it for a new setup. A setup that already uses a withheld database keeps it and says so. |
| RAG: offer SingleStore | `enable_singlestore` | on | As above. |

:::note Two compute contexts, two options
`computeContext` is the context of the **optimization** job and `ragComputeContext` the context of the **RAG ingestion**; neither is ever read for the other. They are kept apart because they usually cannot be the same context: the optimization job is commonly run in the stock *SAS Job Execution compute context*, which runs its servers as a service account — and an ingestion cannot reuse its CAS session there (see [the two prerequisites](./RAG-Ingestion-and-Retrieval.md#two-prerequisites-that-are-not-optional)). Before release 2.3.0 the RAG Builder's own report called its context `computeContext` too; `mdb options-save` renames it when it carries that report's options over.
:::

:::info
Until the object is configured it shows a **"Configuration required"** message and does not call SAS Viya, so it never fails against placeholder IDs. The message names what is missing for each side, and completing either is enough for the page to load: prompt and decision templates need the repository ID, the LLM project ID and the SCR endpoint; RAG setups need the repository ID, **Enable RAG setups** ticked and a credential domain other than `none`. A site with no LLM project therefore gets the RAG Builder alone.

Every field can also be appended to the object's URL by hand using its **URL parameter** name above (for example `&judgeModel=...&modelCardReportURI=/reports/reports/<uuid>`), which is a reliable fallback if your Visual Analytics version does not render the options panel. For a switch, Visual Analytics writes `true` for a ticked checkbox and `false` for one that is not; in a URL written by hand a switch also reads `1`, `yes` or `on` as on and `0`, `no` or `off` as off. Anything else, and a blank or missing value, is its default.
:::
