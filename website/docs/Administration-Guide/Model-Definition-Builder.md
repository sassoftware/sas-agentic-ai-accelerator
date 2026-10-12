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
mdb builders-import                  # import the content folder - the one transfer package:
                                     # release table, host, data-source binding and the
                                     # options of the Prompt Builder report in one run
mdb content-deploy                   # bring a SAS Content folder to the state of the checkout
mdb package-export                   # maintainers: export the content folder to the one package
mdb package-check                    # compare the package with the checkout
```

On a fresh environment, `mdb setup` creates the `LLM Repository` and the LLM/Embedding Model Projects (idempotent — existing objects are left untouched), and `mdb register` performs the same check automatically for the kind it registers, so you do not have to run setup explicitly. `mdb setup` also writes the authorization-group rules (`sas-viya-cli-commands.txt`) and one seed file, `llm-prompt-builder.json` — the repository ID, the LLM project ID, the embedding project ID, the SCR endpoint and the deployment type, each under the name of the Prompt Builder option it is the value for. It is the single entry point for bootstrapping the environment. Once the Prompt Builder is configured, `mdb options-save` supersedes the seed file: it starts from the same discovered values and overlays what the live report actually holds, so what you keep is your deployment as configured rather than as bootstrapped — see [Preserving builder options across a report import](./Setup-Additional-UIs.md#preserving-builder-options-across-a-report-import).

`--update` removes the old delete-and-re-register workaround: after `mdb generate`, one command refreshes the registered model while keeping its ID, history (a new model version is created) and project placement. Both kinds use one implementation — embedding models register into the Embedding Model Project with the same content roles and fact-sheet enrichment. Each registered model also stores its `definition.yaml` as model content, so the source of truth travels with the model.

## The content folder: deploy, export, check

The content folder `/SAS Agentic AI Accelerator` ships as one transfer package, `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json`. What it holds is listed in `SAS-Viya-Integrations/package-manifest.yaml`: one entry per object with its path below the content root, its kind - folder, file, custom step, job definition, report - and the repository file it is a copy of. [The content folder](./Setup-Additional-UIs.md#the-content-folder) has the layout and what never ships. Four commands act on that list:

```bash
mdb builders-import [<file>...] [--options <file>] [--report-caslib <lib>] [--dry-run]   # import the package into a site
mdb content-deploy [--content-root <path>] [--only <path>] [--dry-run]                   # bring a content folder to the state of the checkout
mdb package-export [--content-root <path>] [--out <file>]                                # export the content folder to the one package
mdb package-check [<file>...] [--fix] [--allow-missing]                                  # compare the package with the checkout
```

These commands were tested with a SAS Viya system that is made up - the transfer service, the Files, dataFlows and jobDefinitions services and the sign-in - and have **not run against SAS Viya**. The package file of release 2.3.0 is exported after the commit that describes it here; until it is in the repository `mdb package-check` says that the checkout holds no package and ends with exit code 1, or 0 with `--allow-missing`, and seven tests of `mdb` are skipped with the reason *the package is exported from the owner's system after this round*.

**`mdb builders-import`** is the command of a site: [First install and upgrade](./Setup-Additional-UIs.md#first-install-and-upgrade) describes it. Three things it says rather than does:

- **A package from before 2.3.0** that somebody names. Such a file still holds the report `RAG Builder` and the job definition `RAG-Builder-UI`, the standalone RAG UI that is now part of the Prompt Builder. The command names it as predating 2.3.0, because importing it brings that UI back and `mdb` does not configure its report.
- **A report named `RAG Builder` that is still on the site.** It is retired, keeps serving the old UI with its own options, and is neither written to nor deleted; the message says how to remove it. See [the retired RAG Builder report](./Setup-Additional-UIs.md#the-retired-rag-builder-report).
- **A run that did not do everything.** The exit code is 1 and the run ends with the command to repeat for its case, with the files and flags it was given, on a line of its own that can be selected and pasted into PowerShell or bash: paths are spelled with forward slashes, and an argument a shell would read something into is quoted. Where the package imported and only the options could not be written, that command is `mdb options-restore --file <the kept file>`, which writes the options and imports nothing.

**`mdb content-deploy`** makes a content folder hold what the checkout holds, without a package. It creates the folders, uploads the files, registers the custom steps and updates a step that exists in place (`PUT /dataFlows/steps/{id}`), so that the step keeps its id and saved flows keep working, and creates the job definitions or updates them in place - the program as `code`, the page as the property `form`, the parameters. It writes two files itself: `Data/ACCELERATOR_RELEASES.csv`, the release table as `mdb load-releases --csv` writes it, and `release.json`. It does not deploy the two reports, which are authored in SAS Visual Analytics and reach a site with the package.

- One line per entry says `equal`, `updated`, `created`, or `FAILED` with the reason; a run with a failure goes on with the next entry and ends with exit code 1. `--dry-run` writes nothing and says what a run would do.
- A file that differs is replaced: its old copy is deleted and the new one uploaded. Nothing else is removed. What sits in a folder of the list and is not listed is named at the end and left alone.
- An upload that is answered 409 right after that delete - measured with the earlier deploy script, see [What gets deployed](./RAG-Ingestion-and-Retrieval.md#what-gets-deployed) - is tried again up to five times, and a file that is still missing then is reported as deleted and not uploaded.
- A repository file is uploaded with LF line ends whatever the checkout holds, so that the same file has the same hash from every checkout.
- A custom step is compared in its program, its form and its ports; a change of its description alone is not deployed.
- The parameters of a job definition are written as the manifest has them. A compute context a site set in the parameter `_contextName` of `SAS-LLM-Prompt-Builder-UI` is set back to `SAS Studio compute context`, and the parameter `contentRoot` of `Start Here` and of `Load-Accelerator-Tables` stays `/SAS Agentic AI Accelerator` whatever `--content-root` names.
- `--only RAG` deploys the RAG runtime alone, which is what `deploy-rag-content.ps1` / `.sh` run.

`release.json` holds the release, the commit of the checkout the folder was deployed from with the date of that commit, and every entry of the manifest with its kind and - for a file, a custom step and a job definition - a SHA-256 hash of its content. The page [Start Here](./Setup-Additional-UIs.md#start-here) reads it.

**`mdb package-export`** is for maintainers. It exports the content folder with its dependencies and keeps what the list names: every other object of the export is dropped with its entries in the package - a generated artifact, a report somebody parked in the folder, a dependency from outside it -, and so is every authorization rule and share. The images of a listed report stay with it. In every copy of the Prompt Builder report it resets the option defaults to the neutral values - no project ids, RAG and optimization off, **Verify TLS certificates in manifested models** on -, it removes the per-user states of the reports (each is a full copy of the report with a user id) and rewrites the exporting host to `your-sas-viya-host`, also inside the compressed report content. What would be written is compared with the checkout first; the command writes nothing and ends with exit code 1 - the file from before stays byte for byte - when a difference is left, when the package still names a host, or when an object sits on a path that never ships. An export of another folder than the content root needs `--out`, and `--allow-differences`, with `--out` only, writes the export of a test folder that holds no report. (In Git Bash on Windows prefix a command that names a content folder with `MSYS_NO_PATHCONV=1`, or run it from PowerShell: the shell otherwise rewrites the `/SAS Agentic AI Accelerator/...` argument into a Windows path before `mdb` sees it.)

**`mdb package-check`** is the same comparison on the file in the repository, and a step of the workflow `verify-model-definitions.yml`. Every entry of the manifest has to be in the package and equal its source - a file its repository file, line ends aside; a generated file what `mdb` generates now, `release.json` by its entries; a custom step the `.step` file in `templates`, `ui` and `flowMetadata`; a job definition its program, its form and its parameters. Nothing else may be in the package, there may be no authorization rule and no share, and a report must name no host, carry no per-user state and pass the checks of its entry: for the Prompt Builder report every option the UI reads with its neutral default, and for both reports the CAS tables they bind. One line per difference, and exit code 1. `--fix` rewrites hosts, states and option defaults in the file; a difference in content is fixed in the folder, with `mdb content-deploy`, and by exporting again.

### Releasing the package

The package is built on the system whose content folder holds the two reports, in this order:

1. **Finish `CHANGELOG.md`**: the date of the release, and for release 2.3.0 the sentences that say the package file is not in the repository yet - they stand in the changelog, in *Deploying the Builder UIs*, on this page and in the README of `SAS-Viya-Integrations`. The release table in the package is made from the changelog, and its hash is in `release.json`: a change to the changelog after the export makes `mdb package-check` fail until the folder is deployed and exported again. Commit.
2. **`mdb content-deploy --dry-run`**, then **`mdb content-deploy`**: the folder holds the files, the custom steps and the job definitions of the checkout. Run it again; every line says `equal`.
3. **The reports.** In SAS Visual Analytics, open the Prompt Builder report and save it once if the UI of the release has an option the report does not hold yet - the export refuses a report that lacks one.
4. **`mdb package-export`** writes `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json`, or prints one line per difference and leaves the file as it was.
5. **`mdb package-check`** ends with exit code 0.
6. **Commit the one file.** The commit that adds the package of release 2.3.0 also removes the four packages of earlier releases - `SAS-Agentic-AI-Accelerator-Prompt-Builder.json`, `SAS-Agentic-AI-Accelerator-RAG-Builder.json`, `SAS Agentic AI Accelerator.json` and `Logging-Monitoring/LLM Usage Report.json` - and takes `--allow-missing` out of the workflow. A test of `mdb` fails while the one package and one of the four earlier files are in the repository together.

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
| `component` | Prompt Builder, RAG runtime, Credentials, Model Definition Builder, Definitions, Documentation or General - from the section heading when it names one, else from the entry's wording. Entries about the RAG Builder are filed under Prompt Builder, which it is part of, and no entry of a release before 2.0.0 is filed under RAG runtime. An entry whose headline is about the release table itself is filed under Model Definition Builder, and an entry that adds definitions under Definitions when its headline names them and nothing of `mdb` |
| `summary`, `detail` | the entry's bold lead (or its first sentence, of which only the beginning is kept when it is longer than 140 characters) and the rest, as plain text |
| `builder_part` | the part of the Prompt Builder an entry concerns: `Prompt`, `Decision` or `RAG`; empty for the release intro and for every component other than Prompt Builder and RAG runtime. The last column |

It exists because a SAS Visual Analytics Data-Driven Content object needs a
data assignment before it renders: the Prompt Builder object is assigned this
table, and a report author can filter it by `component` to show what changed
for the Builder next to it. `mdb load-releases --csv out.csv`
writes the table without contacting SAS Viya, for a look at the rows.
`mdb content-deploy` writes the same file into the content folder as
`Data/ACCELERATOR_RELEASES.csv`, and the job `Data/Load-Accelerator-Tables`
of that folder loads it into CAS with the columns and types of this table -
for a site that imported the package without `mdb`, see
[The tables of the reports](./Setup-Additional-UIs.md#load-accelerator-tables).

The component is read from the wording with keyword lists, in a fixed order:
the first component an entry names is the one it is filed under, the Prompt
Builder first. Three things decide before the wording of the entry. A **section
heading** that names a component files every entry under it there - the
entries of 2.0.0 under *RAG cost* are `RAG runtime` rows, those about the
setup editor of the RAG Builder included. And the **release**: `rag_core`, the
RAG steps and the vector stores came with 2.0.0, so an older entry that
"retrieves" or "enriches" something is filed under the next component it
names. And a **headline about the release table** - one that names the
release table, `ACCELERATOR_RELEASES` or `mdb load-releases`: `mdb` makes
the table, and such an entry names the Prompt Builder, the RAG Builder or
the RAG runtime as values of the column, not as what changed, so it is a
`Model Definition Builder` row whatever it names. This holds for the
headline only: an entry that names the table in its text, as what a report
object is assigned, is filed by its wording (2.0.5 *The RAG Builder object
in the shipped package has a data assignment* is a `Prompt Builder` row).
What the Prompt Builder is recognised by includes its buttons and
objects - a prompt-test, the experiment tracker, the judge, the best prompt -
and an entry that says it holds for *both builders*, the Prompt Builder and
the RAG Builder of 2.0.0 to 2.2.x.

One thing decides after the wording. `mdb` is the tool and the definitions
are what it makes, so an entry that adds definitions says what `mdb`
generated or renders, and its wording files it under `Model Definition
Builder`. It is filed under `Definitions` when its headline holds the word
*definitions* and nothing `mdb` is recognised by: 2.2.0 *Shipped
definitions*, and the open-source CPU embedding definitions of 1.1.0. A
headline that names the tool as well - *`mdb sync --rebuild` regenerates
the fact sheets from the definitions* - stays with the tool, and so does
one that names a single definition or a definition kind. These two rules
were checked on the CSV that `mdb load-releases --csv` writes from this
changelog and by the tests of `mdb`; they were not run in SAS Viya.

The line the command ends with counts the release headings of the changelog,
which is not the number of versions: `CHANGELOG.md` has the heading `[0.1.29]`
twice, for 2025-10-13 and for 2025-10-10. For the changelog of 2.3.0 the
command says 58 releases, and the table holds 58 values of `release_rank` and
57 of `version`.

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

Which parts an entry concerns is read from its wording, as the component is.
The first of these that applies decides:

1. A **section heading** that names a part: every entry under it is filed
   under the parts the heading names, whatever its own headline names. 2.0.0
   has the heading *RAG Builder: generating what a setup implies*, and its
   entry *Deleting a setup says what depends on it first*, whose text begins
   with the Prompt Builder, is `RAG` alone. The headings Added, Changed, Fixed
   and Removed name nothing.
2. The **steps of an upgrade** are written for the whole Builder, and so is an
   entry that says it holds for all of it: with the words *three kinds*,
   *every kind*, *all four create dialogs* or *both builders*.
3. The parts the **headline** names: a prompt, the judge, the optimization or
   the Call LLM node for `Prompt`; a decision template, the decision mode or a
   decision model for `Decision`; the RAG Builder, a RAG setup, retrieval,
   ingestion, the ledger or a vector store for `RAG`. A prompt-test and an
   experiment belong to the flow both kinds of template share, so they name
   `Prompt` and `Decision`, and so does a template named without its kind:
   *a prompt, a template or a RAG setup* names all three parts. A decision in
   SAS Intelligent Decisioning names no part. The headline is read whole - for
   an entry without a bold lead that is its first sentence, also where the
   column `summary` holds only the beginning of it.
4. A headline that names the Prompt Builder and no part of it - *The create
   dialogs of the Prompt Builder show the whole name* - concerns the Builder
   as a whole and is written for all three.
5. When the headline settles nothing, the parts the rest of the entry names
   decide, and an entry that names none anywhere is written for the whole
   Builder. A template without its kind names nothing here: in the text of an
   entry the word is as often the score template of a definition or the file a
   retrieval model is manifested from.

In each of them the release has a say: a part that did not exist yet cannot be
concerned. Before 2.2.0 there was no decision mode, so no entry of an older
release is filed under `Decision`, and a template without its kind names
nothing there. Before 2.0.0 the Builder was the one for prompts, so an entry
that names no part is `Prompt` alone there, `Prompt` and `RAG` from 2.0.0, and
all three from 2.2.0 - which is also what *the whole Builder* in the list
above means for a release. Up to 2.2.x the Prompt Builder and the RAG Builder
were two applications, and the name *Prompt Builder* names the prompt part in
those releases; from 2.3.0 it is the name of the whole. An entry that names
the RAG Builder is filed under `RAG` in any release - the changelog first
names it in 0.1.14.

The reading is a set of keyword lists (`PART_KEYWORDS` and its neighbours in
`mdb/core/releases.py`), not an understanding of the entry, and the headline
outweighs the text: an entry whose headline names one part is filed under that
part even where its text says that another is affected as well. The one
exception is a headline about prompts whose text names decision templates,
which is filed under both because the two share a flow. Name every part in the
headline of a new entry when it concerns more than one, and put an entry that
is not about one part under a heading that names none.

These rules were checked on the CSV that `mdb load-releases --csv` writes from
this changelog and by the tests of `mdb`. The table with `builder_part` was
**not run in SAS Viya**: it was not loaded into CAS and not shown on a report
page.

#### The Changelog page of the report

The page needs two things from the table: the **filter on `component`** it has
(`Prompt Builder`; add `RAG runtime` to it to list the changes to the steps
and `rag_core` under the RAG button as well), and a **button bar on
`builder_part`** as the control a reader picks the part with. Without the
filter on `component` the rows of the other components, which have no part,
are rows of the empty value. With `Prompt Builder` alone in the filter, the
RAG button lacks the `RAG runtime` rows, and among those are entries about the
interface of the RAG Builder: 2.0.0 lists some under its headings *RAG cost*
and *Enrichment: making a chunk say where it came from*, and those headings
file every entry under them as `RAG runtime`. The entries about the release
table itself are `Model Definition Builder` rows with an empty
`builder_part`: a page filtered on `Prompt Builder` does not list them, and
with `Model Definition Builder` added to the filter no button of the bar
shows them - read from the table as CSV, not measured in SAS Visual
Analytics.

`mdb load-releases` changes the table and not the report: the page with the
button bar comes with the transfer package a maintainer exports after
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
