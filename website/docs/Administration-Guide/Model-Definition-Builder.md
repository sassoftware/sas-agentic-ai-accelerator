---
sidebar_position: 13
title: Model Definition Builder
---

The Model Definition Builder (`mdb`) generates complete LLM model definitions from a single manifest file per model, replacing the manual 10-step copy-and-edit process described in [LLM Definitions](./LLM-Definitions.md). You pick a provider and a model, answer a few questions, and every asset the framework needs is generated consistently: the score script, `inputVar.json`/`outputVar.json`, `modelConfiguration.json`, `options.json`, `requirements.json`, the documentation files and the fact-sheet row.

Generated definitions work with the established registration flow — continue with [Register & Publish LLMs](./Register-&-Publish-LLMs.md) exactly as before.

## Installation

```bash
cd Model-Definition-Builder/cli
python -m venv .venv
.venv/Scripts/activate        # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e ".[viya]"     # keep the quotes; the [viya] extra adds sasctl for the Viya commands
```

Requires Python 3.10 or newer. Without the `[viya]` extra `mdb` still adds, generates, validates and tests definitions; `setup`, `register`, `publish`, `ship`, `load-facts`, `options-*` and `credentials-*` need it. For air-gapped environments, download the dependency wheels on a connected machine and install with `pip install --no-index --find-links <wheel-dir> sas-mdb`.

## Adding a model

New to `mdb`? The [Model Definition Builder user guide](../User-Guide/Model-Definition-Builder.md) is a step-by-step walkthrough of creating, checking, iterating on and registering a model (including embeddings and self-hosted Ollama/vLLM servers). This section is the reference summary.

Run `mdb add` from anywhere inside the repository for the interactive wizard, or use the non-interactive form in scripts and pipelines:

```bash
# Interactive: pick provider and model from a live (or bundled) catalog
mdb add

# Non-interactive examples
mdb add openrouter deepseek/deepseek-v3.1 --yes
mdb add anthropic claude-sonnet-4-5-20250929 --id claude_sonnet_4_5 --yes
mdb add azure-foundry --resource myres --deployment my-gpt41 --id gpt_41_az --yes
mdb add hf-selfhosted --repo Qwen/Qwen2.5-0.5B-Instruct --id qwen_25_05b --params-billions 0.5 --yes
```

Supported providers: OpenRouter, OpenAI, Azure AI Foundry (any host flavor; GA v1 or legacy api-version endpoint, key auth), Mistral, Anthropic, AWS Bedrock (Converse API - Bedrock API key by default, `--auth-variant sigv4` for boto3/IAM shops), Google Gemini, Voyage AI, self-hosted Hugging Face models (`transformers` for LLMs, `sentence-transformers` for embeddings) and self-hosted OpenAI-compatible servers (**Ollama**, **vLLM**).

Before anything is written, the wizard shows the **catalog-derived values** — option defaults, metadata (description, context length, dates) and token pricing — for you to **confirm or adjust**: these steer scoring behavior and cost monitoring, so they should be accepted consciously. Adjusting also lets you **rename or drop options** (`max_tokens=max_completion_tokens` renames, `-top_p` drops) — useful when a model's provider contract differs from the classic parameter names; where the catalog's `supported_parameters` reveal the right token-limit name, the wizard picks it automatically. `--accept-defaults` skips the review (and `--yes` implies it); unknown token pricing is always asked about (or warned about in non-interactive runs).

**Embedding definitions** work exactly like LLM definitions: the wizard picks the kind from the model you select (or the runtime you choose for self-hosted models), the folder lands in `Embedding-Definitions/`, the row goes to `embedding_fact_sheet.csv`, and registration continues with `mdb register`, exactly like an LLM. The generated embedding scorers return all three declared outputs (embedding, run_time, tokens) and embed the full vector — two long-standing bugs in several hand-written definitions that the templates fix centrally. The bundled open-source embedding models run on CPU: `all_minilm_l6_v2`, the BGE family, `embedding_gemma_300m` and the RTEB-leaderboard IBM Granite models `granite_embedding_small_r2` (47M, 384-dim) and `granite_embedding_r2` (149M, 768-dim), all Apache-2.0/MIT ModernBERT or MiniLM bi-encoders.

### Self-hosted Ollama and vLLM

`mdb add ollama` and `mdb add vllm` create definitions that call a self-hosted OpenAI-compatible server, for either chat (`--kind llm`) or embeddings (`--kind embedding`). Unlike the `hf-selfhosted` path — which downloads weights into the SCR image — these keep the weights on the inference server and the container only carries the thin api-wrapper requirements. The server base URL is environment-neutral, resolving per call as option → `OLLAMA_BASE_URL` / `VLLM_BASE_URL` container environment variable → a localhost default, and an optional bearer token is read from `OLLAMA_API_KEY` / `VLLM_API_KEY` (leave blank for an open server). So one published image can point at a dev, test or production inference server without a rebuild.

```bash
# chat model served by a local Ollama, addressed by its Ollama tag
mdb add ollama llama3.1:8b --id llama31_8b_ollama --kind llm --yes
# embedding model served by vLLM, with the server URL baked as the default
mdb add vllm intfloat/e5-mistral-7b-instruct --id e5_mistral_vllm --kind embedding \
  --base-url http://vllm-svc.ml.svc:8000/v1 --yes
```

AWS Bedrock regions work like Azure resources: the region resolves per call as option → `AWS_BEDROCK_REGION` container environment variable → baked default, so one image can serve multiple regions.

### Azure across subscriptions and projects

Azure definitions are environment-neutral by default: the resource host you enter during `mdb add` is used for smoke tests, but is **not** baked into the definition unless you confirm it (or pass `--commit-resource`). A deployed container resolves its target as `AZURE_OPENAI_RESOURCE` container environment variable → the baked default — where a container sends its requests is a property of the deployment, never a scoring option, so nothing a caller passes can redirect it (an optional `AZURE_OPENAI_ENDPOINT` replaces the built URL for a gateway in front of Azure). `mdb deploy` renders the Azure Deployment YAML, with that env block, for every Azure definition. Set `AZURE_OPENAI_RESOURCE` in each deployment's YAML to serve dev, test and production (or different customer subscriptions) from the same published image — and set it in your `.env` so the wizard and smoke tests use your environment automatically. `mdb validate` reminds you when a definition carries a baked resource.

**Host flavors and API styles.** Azure hands out the same OpenAI-compatible data plane under three host suffixes, and mdb accepts any of them verbatim (a bare name still expands to the classic `*.openai.azure.com`):

| Host | Resource type |
| --- | --- |
| `<res>.openai.azure.com` | classic Azure OpenAI resource |
| `<res>.cognitiveservices.azure.com` | Azure AI Services / Foundry resource |
| `<res>.services.ai.azure.com` | Azure AI Foundry endpoint (sometimes region-qualified) |

By default mdb calls the **GA v1 endpoint** (`/openai/v1/chat/completions`, deployment name in the body — Microsoft's recommended surface). If your resource or org policy still requires the **legacy deployment-scoped route** — recognizable by URLs like `…/openai/deployments/<name>/chat/completions?api-version=2025-01-01-preview` — answer the wizard's *API version* question (or set the `AZURE_OPENAI_API_VERSION` container environment variable) and the calls switch to that route. The reverse works too: a definition that bakes a version is forced back onto the GA route by setting `AZURE_OPENAI_API_VERSION` to an *empty* value in the container — a v1-only resource answers the legacy route with a bare 401 and no body, which is the symptom to look for. Embedding deployments work the same way: `mdb add azure-foundry --kind embedding --deployment <name>` builds on the `emb_azure_openai_v1` template (`/openai/v1/embeddings` or the legacy route, `api-key` header, `dimensions` supported).

**Not just OpenAI models.** The whole Foundry Models catalog is served through this same OpenAI-compatible surface — a DeepSeek, Llama, Mistral, Phi or Grok deployment on a Foundry resource works exactly like a GPT one (mdb addresses the *deployment name*, not the vendor); Microsoft's older separate Model Inference API (`/models/chat/completions`) is deprecated in favor of it. The boundaries are API-surface ones: the deployment must serve **chat completions** — embedding, audio/image/realtime, and Responses-API-only models (`/openai/v1/responses`, a different request/response shape, e.g. computer-use previews) are outside the chat-completions score contract — and some non-OpenAI models reject specific OpenAI options (e.g. `temperature` on certain reasoning models), which you control per definition anyway.

API keys are read from environment variables or the `.env` file at the repository root (for example `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`). The wizard tells you which variable it used; keys are never written into any generated file.

### Keeping your definitions in your own repository

By default `mdb` reads and writes definitions in the accelerator clone's `LLM-Definitions/` and `Embedding-Definitions/` folders. If you would rather **commit your definitions to your own git repository** (instead of carrying them in a fork of the accelerator), point `MDB_DEFINITIONS` at your repo in its `.env`:

```bash
# .env in your own repository
MDB_REPO=/path/to/sas-agentic-ai-accelerator   # supplies the definition-core templates
MDB_DEFINITIONS=/path/to/your-repo             # mdb keeps its layout under this root
```

`mdb` loads the `.env` from your current working directory (and its parents), so running it from inside your repo picks these up automatically. Under that root, mdb creates the familiar layout **as needed** — `LLM-Definitions/`, `Embedding-Definitions/`, and the `mdb retire` archive `_archive/` (add that one to your `.gitignore`) — and every command (`add`, `apply`, `generate`, `validate`, `register`, `list`, `retire`, …) operates there, including the fact sheets (`llm_fact_sheet.csv` / `embedding_fact_sheet.csv`) inside the definition folders. The accelerator clone is still required for the templates, which is what `MDB_REPO` points at.

After adding a model, work through the pipeline in order — each step catches a different class of problem before the next one can be blamed for it:

```bash
mdb validate <model_id> --live     # coherence rules + one real provider call through the adapter
mdb test <model_id>                # run the GENERATED scoreModel() locally - exactly what SCR will execute
mdb register <model_id>            # register in SAS Model Manager
mdb publish <model_id>             # publish to the SCR destination (or mdb ship for register + publish)
```

`mdb validate --live` proves the provider, endpoint and key work; `mdb test` additionally proves the *generated score code* works — options parsing, request body, response extraction — so a template or option problem surfaces on your machine, not in a published container.

## Register, update and publish from the CLI

`mdb` now owns the full Viya lifecycle for managed definitions (install the extra: `pip install -e "Model-Definition-Builder/cli[viya]"`):

```bash
mdb setup                            # create the LLM Repository + LLM/Embedding
                                     # projects if missing (idempotent; register
                                     # runs this automatically for its kind)
mdb register <model_id>              # create in SAS Model Manager (skips if it exists)
mdb register <model_id> --update     # replace a registered model IN PLACE: new minor
                                     # version + content replacement + refreshed attributes
mdb publish <model_id> --wait        # publish to SCR and poll until the image build finishes
mdb ship <model_id>                  # validate --live -> register --update -> publish --wait
mdb unregister <model_id>            # delete a registered model from Model Manager
mdb endpoints --json                 # SCR endpoint manifest for CI and testing
mdb options-save                     # save this deployment's Prompt Builder options to a file
mdb options-restore                  # write them back after importing a report package
mdb builders-import [pkg...]         # import the Prompt Builder and RAG runtime packages:
                                     # release table, host, data-source binding and saved
                                     # options in one run
mdb package-export --folder <path>   # export a SAS Content folder into the repository, host removed.
                                     # Without --out only for the two Builder packages; any
                                     # other folder needs --out <file>
```

On a fresh environment, `mdb setup` creates the `LLM Repository` and the LLM/Embedding Model Projects (idempotent — existing objects are left untouched), and `mdb register` performs the same check automatically for the kind it registers, so you do not have to run setup explicitly. `mdb setup` also writes the authorization-group rules (`sas-viya-cli-commands.txt`) and one seed file, `llm-prompt-builder.json` — the repository ID, the LLM project ID, the embedding project ID, the SCR endpoint and the deployment type, each under the name of the Prompt Builder option it is the value for. It is the single entry point for bootstrapping the environment. Once the Prompt Builder is configured, `mdb options-save` supersedes the seed file: it starts from the same discovered values and overlays what the live report actually holds, so what you keep is your deployment as configured rather than as bootstrapped — see [Preserving builder options across a report import](./Setup-Additional-UIs.md#preserving-builder-options-across-a-report-import).

`--update` removes the old delete-and-re-register workaround: after `mdb generate`, one command refreshes the registered model while keeping its ID, history (a new model version is created) and project placement. Both kinds use one implementation — embedding models register into the Embedding Model Project with the same content roles and fact-sheet enrichment. Each registered model also stores its `definition.yaml` as model content, so the source of truth travels with the model.

## Importing and exporting the Builder packages

`mdb builders-import` imports the two transfer packages shipped under `SAS-Viya-Integrations/` (or the files you name) into the deployment in `.env`: first the **Prompt Builder** package - the UI for all three kinds, the optimization job and the report - then the **RAG runtime** package - the RAG custom steps, `rag_core`, the jobs and the retrieval model, and no UI. It does in one run what an import otherwise leaves to hand work afterwards:

1. loads the release table (`mdb load-releases`; `--no-releases` skips it),
2. uploads each package and rewrites its **import mapping** before the job starts: the report's data-source connector is retargeted from the shipped `Public.ACCELERATOR_RELEASES` to `SAS_CAS_LIBRARY.SAS_RELEASES_TABLE` on your CAS server, and the placeholder host in the Data-Driven Content URL becomes `SAS_VIYA_URL` - nothing is edited after the fact,
3. imports and waits for the job, naming the failing task if there is one,
4. configures the imported report: with `--options builder-options.json` the site's saved values are written back (see [Preserving builder options across a report import](./Setup-Additional-UIs.md#preserving-builder-options-across-a-report-import)); without it the values `mdb setup` discovers - the repository, the LLM and the embedding project ids, `SAS_SCR_ENDPOINT`, `SAS_DEPLOYMENT_TYPE` - are; and either way every placeholder host left in the report (the Data-Driven Content URL, the `viyaHost` and `SCREndpoint` defaults) becomes `SAS_VIYA_URL`. A first install needs no edits in Visual Analytics beyond the optional settings - the credential domain, the judge model, and the switches for decision templates, prompt optimization and RAG, which keep the package's defaults, so RAG is off until it is enabled.

`--dry-run` lists the objects and the mapping changes and imports nothing; because nothing is imported it compares the saved options with the report as it is on the site now, and sums up in one line the options that only arrive with the new report. Other data sources a package binds are left as exported and reported: retargeting a report at a table with different columns breaks its data items.

Three things the command says rather than does:

- **A package from before 2.3.0.** Such a file still holds the report `RAG Builder` and the job definition `RAG-Builder-UI`, the standalone RAG UI that is now part of the Prompt Builder. The command names it as predating 2.3.0, because importing it brings that UI back and `mdb` does not configure its report.
- **A report named `RAG Builder` that is still on the site.** It is retired, keeps serving the old UI with its own options, and is neither written to nor deleted; the message says how to remove it. See [the retired RAG Builder report](./Setup-Additional-UIs.md#the-retired-rag-builder-report).
- **A package that failed to import.** The options are still written back when the package that carries the report imported, and the exit code stays 1. Once the cause is fixed, run `mdb builders-import` again with the same `--options` file: it imports the package and restores. `mdb options-restore --file ...` writes the options and imports nothing, so it is the command to repeat only when every package imported and the report could not be written. The run ends with the command to repeat for its case, with the packages and flags it was given, on a line of its own that can be selected and pasted into PowerShell or bash: paths are spelled with forward slashes, and an argument a shell would read something into is quoted.

`mdb package-export --folder "/SAS Agentic AI Accelerator/Prompt Builder"` is the other direction, for maintainers: it exports the folder (dependencies and rules included, as the shipped packages are), downloads the package, rewrites the exporting environment's hostname to the placeholder inside the compressed report content, removes the report's per-user states (each is a full copy of the report with a user id), checks the result and writes `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json`. The runtime package is exported from the content root under its own name: `mdb package-export --folder "/SAS Agentic AI Accelerator/RAG" --name "RAG Runtime"` writes `SAS-Agentic-AI-Accelerator-RAG-Runtime.json`. `--name`, `--out`, `--keep` (leave the package on the server) and `--exclude <object name>` (leave out something that lives in the folder but must not ship, such as a demo report) adjust that. Without `--out` the command works only for these two targets, the files `mdb builders-import` reads: for any other folder or name it stops with exit code 2 before anything is exported, and `--out <file>` is how such a folder is exported. An export that is not fit to ship is judged before it is written: the command ends with exit code 1 and writes nothing — the file from before stays byte for byte as it was, with `--out` as well — when the package still names a host after the rewrite, when it still carries the retired RAG UI, or when its Prompt Builder report lacks an option of the current UI or holds a default that is not the neutral one — a judge model, a model card report, or **Verify TLS certificates in manifested models** switched off. Each finding is named with the option, the value found and the expected one; reset it in the Options pane, save the report and export again (`--keep` leaves the exported package on the server to look at). A value that sits only in a per-user state of the report is not judged, because the state is removed and does not reach the file. It replaces the three manual steps - export in SAS Environment Manager, copy the file, `mdb package-check --fix`. (In Git Bash on Windows prefix the command with `MSYS_NO_PATHCONV=1`, or run it from PowerShell: the shell otherwise rewrites the `/SAS Agentic AI Accelerator/...` argument into a Windows path before `mdb` sees it.)

The two packages are re-exported after every change to what they hold - the Prompt Builder package after a change to the UI bundle, the report or its options, the runtime package after a change to a RAG step, a job or `rag_core` - and the `mdb` test suite compares them with the repository: the report must hold every option the UI reads, the option defaults must be neutral (no project ids, RAG and optimization off), the job definition must hold the committed `dist/index.html`, and the runtime package must equal the repository files and carry no UI object. `mdb package-check` looks for leftover hostnames and warns about per-user report states; `--fix` removes both.

## Deployment YAML and CI pipelines

`mdb deploy <model_id> --registry myregistry.azurecr.io` renders ready-to-apply Kubernetes YAML from the `SCR-LLM-Deployment-YAML` templates with every placeholder filled (resource names, image, ingress host and path) — the persistent-volume variant is selected automatically for self-hosted models. `Model-Definition-Builder/ci-recipes/` contains thin GitHub Actions pipelines (a `workflow_dispatch` model-lifecycle job and a weekly deprecation radar) where every step is just an `mdb` verb.

## Watching for provider retirements

`mdb radar --all` checks every managed model against its provider's live catalog; `--probe` sends one 1-token call per model for ground truth (catalog listing is not an availability guarantee — retired models can stay listed). `mdb retire <model_id>` tags a definition `deprecated` (hiding it from the Prompt Builder) and regenerates it.

## Keeping definitions consistent

`definition.yaml` inside the model folder is the only file you edit. After changing it:

```bash
mdb generate <model_id>       # re-render the generated files
mdb sync <model_id>           # update the fact-sheet row
mdb validate <model_id>       # cross-file coherence rules with fix-it hints
```

The `*_fact_sheet.csv` files (`LLM-Definitions/llm_fact_sheet.csv`,
`Embedding-Definitions/embedding_fact_sheet.csv`) are a **generated artifact** —
you never edit them by hand. `mdb sync <model_id>` refreshes a single row;
`mdb sync --rebuild` regenerates each sheet in full from every managed
definition (sorted by `model_id`), creating the file if it does not exist yet:

```bash
mdb sync --rebuild            # rebuild both sheets from the definitions
mdb sync --rebuild --prune    # also drop rows for models with no definition folder
```

Rebuilding is idempotent (an unchanged fleet produces a byte-identical sheet) and
preserves any hand-maintained rows that have no definition folder unless you pass
`--prune`. This is the quickest way to bring the fact sheets in line after a bulk
migration instead of syncing each model one by one.

To make the sheets available to the SAS Visual Analytics monitoring report,
`mdb load-facts` uploads them to CAS — the Python equivalent of
`Load-Fact-Sheets.sas`. Each sheet is loaded with **global scope** (promoted, so
every session sees it) and **saved to the caslib's data source on disk** (so it
survives a CAS restart), as the tables `LLM_FACT_SHEET` and `EMBEDDING_FACT_SHEET`.
An existing table of the same name is unloaded first and its saved copy replaced:

```bash
mdb load-facts                       # load into the Public library (default)
mdb load-facts --caslib MyLib        # a different CAS library (env: SAS_CAS_LIBRARY)
mdb load-facts --rebuild             # regenerate the sheets from the definitions, then load
```

The CAS server defaults to `cas-shared-default` (auto-detected; override with
`--server` or `SAS_CAS_SERVER`). This uses the casManagement REST API over the
same session the register/publish commands use — no separate CAS connection.
The save step writes a `.sashdat`, so target a **path-based** caslib (like
`Public`); a database-backed caslib would reject the save.

### The release table

`mdb load-releases` turns the repository's `CHANGELOG.md` into the CAS table
`ACCELERATOR_RELEASES` (same caslib and server options as `load-facts`; the
name comes from `--table` or `SAS_RELEASES_TABLE`) and `mdb setup` loads it by
default (`--no-releases` skips it). It has one row per release - the release
intro, `change_type` = `Release` - and one per changelog entry, or, for an
entry of the Prompt Builder, one per part of the Builder it concerns, with:

| Column | Content |
| --- | --- |
| `version`, `release_date` | from the `## [x.y.z] - date` heading; an *Unreleased* section is listed but never current |
| `is_current` | 1 for the newest dated release |
| `release_rank`, `item_rank` | 1 = newest release; the entry's position within it, so a report can order without dates |
| `section`, `change_type` | the heading as written and its normalised kind: Added, Changed, Fixed, Removed, Breaking, Upgrade, Release |
| `component` | Prompt Builder, RAG runtime, Credentials, Model Definition Builder, Definitions, Documentation or General - from the section heading when it names one, else from the entry's wording. Entries about the RAG Builder are filed under Prompt Builder, which it is part of |
| `summary`, `detail` | the entry's bold lead (or first sentence) and the rest, as plain text |
| `builder_part` | the part of the Prompt Builder an entry concerns: `Prompt`, `Decision` or `RAG`; empty for the release intro and for every component other than Prompt Builder and RAG runtime. The last column |

It exists because a SAS Visual Analytics Data-Driven Content object needs a
data assignment before it renders: the Prompt Builder object is assigned this
table, and a report author can filter it by `component` to show what changed
for the Builder next to it. `mdb load-releases --csv out.csv`
writes the table without contacting SAS Viya, for a look at the rows.

#### One row per part of the Builder

The Prompt Builder holds three parts in one report - prompt templates,
decision templates and RAG setups - and `builder_part` is the column for a
button bar that lets a reader of the Changelog page pick one of them. A button
bar shows the rows of the value that is picked, so an entry of the component
`Prompt Builder` is written **once for each part it concerns**: the rows are
equal in every column but `builder_part`, in the order Prompt, Decision, RAG.
A row of the component `RAG runtime` carries `RAG`, once. Nothing else has a
part. `release_rank` and `item_rank` are those of the entry on each of its
rows, so a change is still counted once by that pair (not by `version` alone:
the changelog used 0.1.29 for two releases); `mdb load-releases` prints the
rows per part beside the number of entries they stand for.

Which parts an entry concerns is read from its wording, as the component is:

- The parts its **headline** names: a prompt, the judge, the optimization or
  the Call LLM node for `Prompt`; a decision template, the decision mode or a
  decision model for `Decision`; the RAG Builder, a RAG setup, retrieval,
  ingestion, the ledger or a vector store for `RAG`. A prompt-test and an
  experiment belong to the flow both kinds of template share, so they name
  `Prompt` and `Decision`. A decision in SAS Intelligent Decisioning names no
  part.
- A headline that names the Prompt Builder and no part of it - *The create
  dialogs of the Prompt Builder show the whole name* - concerns the Builder as
  a whole and is written for all three. So are the steps of an upgrade, and an
  entry that says it applies to all three kinds.
- When the headline settles nothing, the parts the rest of the entry names
  decide, and an entry that names none anywhere is written for the whole
  Builder.
- By release: a part that did not exist yet cannot be concerned. Before 2.2.0
  there was no decision mode, so no entry of an older release is filed under
  `Decision`. Before 2.0.0 the Builder was the one for prompts, so an entry
  that names no part is `Prompt` alone there, `Prompt` and `RAG` from 2.0.0,
  and all three from 2.2.0. Up to 2.2.x the Prompt Builder and the RAG Builder
  were two applications, and the name *Prompt Builder* names the prompt part
  in those releases; from 2.3.0 it is the name of the whole. An entry that
  names the RAG Builder is filed under `RAG` in any release - the changelog
  first names it in 0.1.14.

The reading is a set of keyword lists (`PART_KEYWORDS` and its neighbours in
`mdb/core/releases.py`), not an understanding of the entry, and the headline
outweighs the text: an entry whose headline names one part is filed under that
part even where its text says that another is affected as well. The one
exception is a headline about prompts whose text names decision templates,
which is filed under both because the two share a flow. Name every part in the
headline of a new entry when it concerns more than one.

#### The Changelog page of the report

The page needs two things from the table: the **filter on `component`** it has
(`Prompt Builder`; add `RAG runtime` to it to list the changes to the steps
and `rag_core` under the RAG button as well), and a **button bar on
`builder_part`** as the control a reader picks the part with. Without the
filter on `component` the rows of the other components, which have no part,
are rows of the empty value.

`mdb load-releases` changes the table and not the report: the page with the
button bar comes with the Prompt Builder package a maintainer exports after
building it, and until that report is imported a site has the new table under
the page it had. On that page a `Prompt Builder` entry that concerns two or
three parts is two or three rows of the table, equal in everything the page
shows. What the page as shipped up to now makes of them was read from the
package and **not measured in SAS Visual Analytics**: its list table is an
aggregated one (`detail="false"` in the report content) over Version,
Component, Summary and Detail, and an aggregated list table has one line per
combination of its columns, so such an entry should still be one line there;
its button bar on Change Type and its filter on `component` are not affected.
A list table switched to detail data shows the entry once per part, and
anything that counts rows - a Frequency, a key value, a bar chart of changes
per release - counts it once per part as well. To count entries instead of
rows, count under one button of the bar, or take the distinct count of an item
that joins `release_rank` and `item_rank`.

`mdb generate --all --check` verifies that every generated file matches its manifest and is intended as a CI gate. Files you edited by hand are never overwritten silently — the command tells you to either fold the change into the manifest, declare the file as hand-maintained under `generation.overrides`, or pass `--force`.

## Adopting existing definitions

Existing hand-written folders keep working unchanged and are never touched. To migrate one onto a manifest:

```bash
mdb import <model_id>             # writes definition.yaml, reports what would change
mdb import <model_id> --apply     # converges the folder onto the generated files
```

The import reports every intended normalization (canonical options parser, provider usage-based token counting, requirements standardization) so you can review the diff — re-test and re-publish affected models afterwards.

## Restricted networks

- `--offline` (or `MDB_OFFLINE=true`): no outbound calls; model catalogs come from the bundled snapshots and manual entry
- `HTTPS_PROXY` / `NO_PROXY` and `REQUESTS_CA_BUNDLE` are honored for all provider calls
- `--no-verify-ssl` / `MDB_VERIFY_SSL=false` corresponds to the `-k` option of the existing Python scripts

## Custom options

You can declare options that are not part of the standardized vocabulary — useful for provider-specific parameters. Give them an inline `type` (and a `description`) in `definition.yaml` and they are passed to the provider as-is under their own name. `mdb generate` and `mdb validate` warn you (rule V010) about what such an option gives up: UIs show it with its raw name and your description instead of a standardized label, and it gets no cross-provider value translation. That is often perfectly fine — the warning just makes it a conscious choice. To standardize an option instead, add it to `definition-core/static/option-vocabulary.json` with a label, type and per-family mapping. Custom options with `informational: true` appear in `options.json` for documentation but are never sent to the provider.

## Scoring-time options

Options are defined once in the manifest and flow into the score script defaults, `options.json` and the fact sheet together. Beyond `temperature`, `top_p`, `top_k` and `max_tokens`, the typed option vocabulary covers `seed`, frequency/presence penalties, `reasoning_effort` for reasoning models (which reject temperature/top_p — the generator handles this), `max_completion_tokens` and `thinking_budget` for extended-thinking models. The full vocabulary lives in `Model-Definition-Builder/definition-core/static/option-vocabulary.json`.
