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
mdb builders-import [<file>...] [--options <file>] [--content-root <path> [--create]] [--no-reports] [--compute-context <name>] [--report-caslib <lib>] [--dry-run]
                                                                                         # bring the package to a site
mdb content-deploy [--content-root <path>] [--create] [--only <path> [--to <path, id or URI> [--force]]] [--compute-context <name>] [--dry-run]
                                                                                         # bring a content folder to the state of the checkout
mdb package-export [--content-root <path>] [--out <file> [--keep-user-ids]] [--allow-differences]
                                                                                         # export the content folder to the one package
mdb package-check [<file>...] [--release] [--fix] [--allow-missing]                      # compare the package with the checkout
```

The commands ran on SAS Viya 2026.09 in four passes, twice on 2026-10-12 and twice on 2026-10-13, each as they were then, in test content roots below the folder of a user; each paragraph below says what was measured. What the first pass found changed them, and the second pass measured the import in place, the write of a file in place, the settings of a site, `--to` and the two forms of the gate. The third pass measured what was built after the second: `release.json` written last, `--create`, `--force`, the `If-Match` of the compared read and a write that is refused, the commit check of `--release`, the user ids an export replaces, the deletes an export sends, and the paths a package may hold. The fourth pass, the second of 2026-10-13, measured what was built after the third: the refusal of a part that names `release.json`, the content root of a folder that goes somewhere else, the rule for a reference of the name of an entry, the line for a folder that is created, the whole answer of a refused write, the command a failed import names, a custom step registered without the user of its `.step` file, `userName` among the fields an export replaces, and the commit id in full. What that pass and two reviews of the same code found was changed once more, and those changes have **not run against SAS Viya**: the sign-in - one for an answer that says `Bad credentials`, the one line for every 401, a port in `SAS_VIYA_URL` -, the refusal of a part in front of the sign-in, a reference of another kind, the hints of a `FAILED` line, the two commands for two packages of which one is refused its reports, the answer of a refused request whole and on one line in every message, the line of the export for a test folder, and the gate for an id that is no commit; [What was measured, and what has not run](./Setup-Additional-UIs.md#the-package-on-sas-viya) has the list. The package file of release 2.3.0 is exported after the commit that describes it here; until it is in the repository `mdb package-check` says that the checkout holds no package and ends with exit code 1, or 0 with `--allow-missing`, and seven tests of `mdb` are skipped with the reason *the package is exported from the owner's system after this round*.

**`mdb builders-import`** is the command of a site: [First install and upgrade](./Setup-Additional-UIs.md#first-install-and-upgrade) describes it. It brings the package in two ways: the reports, with the files that are children of a report and the folders they sit in, through the SAS Viya transfer service, and every file, custom step and job definition in place, by the requests `mdb content-deploy` sends, with the package as the source of the content - [What an upgrade does to what a site has](./Setup-Additional-UIs.md#an-upgrade-keeps-ids). Three things it says rather than does:

- **A package from before 2.3.0** that somebody names. Such a file still holds the report `RAG Builder` and the job definition `RAG-Builder-UI`, the standalone RAG UI that is now part of the Prompt Builder. The command names it as predating 2.3.0, because importing it brings that UI back and `mdb` does not configure its report.
- **A report named `RAG Builder` that is still on the site.** It is retired, keeps serving the old UI with its own options, and is neither written to nor deleted; the message says how to remove it. See [the retired RAG Builder report](./Setup-Additional-UIs.md#the-retired-rag-builder-report).
- **A run that did not do everything.** The exit code is 1 and the run ends with the command to repeat for its case, with the files and flags it was given, on a line of its own that can be selected and pasted into PowerShell or bash: paths are spelled with forward slashes, and an argument a shell would read something into is quoted. The line is built from every option of the run but `--dry-run`. Where the reports were refused for the content root and no package's reports went to the transfer service, it carries `--no-reports` and says that it was added; where the reports of one package went through and those of another were refused, it is two commands: the run without that package, and that package with `--no-reports`; only the line with the package the release table came from loads the table again. Where the package imported and only the options could not be written, that command is `mdb options-restore --file <the kept file>`, which writes the options and imports nothing.

**`mdb content-deploy`** makes a content folder hold what the checkout holds, without a package. It creates the folders, uploads the files, registers the custom steps and updates a step that exists in place (`PUT /dataFlows/steps/{id}`), so that the step keeps its id and saved flows keep working, and creates the job definitions or updates them in place - the program as `code`, the page as the property `form`, the parameters. It writes two files itself: `Data/ACCELERATOR_RELEASES.csv`, the release table as `mdb load-releases --csv` writes it, and `release.json`. It does not deploy the two reports, which are authored in SAS Visual Analytics and reach a site with the package.

- One line per entry says `equal`, `updated`, `created`, or `FAILED` with the reason. `--dry-run` writes nothing and says what a run would do. Where the folder the content goes into is created, the first line says so - `created folder <path> (the folder the content goes into ...)`, in a dry run too; it is no entry and is not counted with them.
- **Everything is written in place.** A file that is missing is uploaded; one whose bytes differ gets its content replaced (`PUT /files/files/{id}/content` under the ETag of the content that was compared, so that a file somebody changed after the comparison is refused and left as it is) and stays the file it was - same id, same place. A custom step and a job definition are updated under the id they have. Nothing is deleted. What sits in a folder of the list and is not listed is named at the end and left alone.
- **The run stops at the first write that is refused or gets no answer.** That entry is `FAILED`, every entry behind it is `not attempted`, and the line `Stopped at the first write that failed: ...` says what was written before it and that nothing was removed. Where the update of a file is refused, the file is left as it is. An entry that fails before anything was sent - two objects of its name in the folder, a reference of its name to an object of its kind elsewhere - alone, or beside the object itself -, an object that cannot be read - is reported and the run goes on: each object and each reference of the name is named on the line, and none is written. A reference of that name to an object of another kind fails nothing and is listed at the end as `<path> (reference to a <kind> - not the <kind of the entry> that is listed under this name)`. A folder that FAILED this way takes what is in it with it - nothing is written into it, in a dry run as in a run -, and a folder that cannot be created stops the run like a failed write. Either way the exit code is 1, and a second run leaves what is equal by then as it is. The line of a refused request shows `message`, `remediation` and `errorCode` of the answer whole, on one line.
- **`release.json` is written last**, only by a run that covers every entry of the manifest, and only when every entry in front of it was written: behind a stop or a failure its line says `not attempted` and why, and the folder keeps the `release.json` of an earlier run, or has none. A run of a part that names it - `--only release.json` - is refused with one line before the command signs in.
- **A folder that is not there** is created only where the manifest's own content root is the target; for another `--content-root`, and for the folder form of `--to`, `--create` creates it with every folder above it, and without it nothing is written: `there is no folder P, and nothing was created: pass --create to create it ...`.
- A repository file is uploaded with LF line ends whatever the checkout holds, so that the same file has the same hash from every checkout.
- A custom step is compared in its program, its form and its ports; a change of its description alone is not deployed.
- **Site settings.** A parameter the manifest marks `siteSetting` is a value of your site: `_contextName` and `contentRoot`, and of `Start Here` and the loader the CAS library and server, the credential domain, the names of the repository and the projects, the compute contexts to look for and the release table. A new job definition gets the default, and so does one that has no such parameter; a value a job definition on the site holds stays, and the deploy does not count it as a difference. `--compute-context <name>` writes a compute context into every job definition that has `_contextName`; `contentRoot` is always written with the content root the content goes to - for a folder that goes somewhere else see [One part, somewhere else](#one-part-somewhere-else); one object named with `--to` keeps the one it has. The other parameters of a job definition - the address of the guides, `_action`, `_output_type` - are written as the manifest has them.
- `--only RAG` deploys the RAG runtime alone; with `--to` a part goes somewhere else, see [One part, somewhere else](#one-part-somewhere-else).

Measured on SAS Viya 2026.09 on 2026-10-12, with the command as it was then, in a test content root:

- **The dry run** listed the 72 entries of the manifest - 61 objects and their eleven folders - as `70 created, 2 not deployed`, the two reports, and wrote nothing: the folder did not exist afterwards.
- **The run** created the 70. Read back from the folder, every repository file had the bytes of the repository with LF line ends, every custom step and job definition equalled its source in each compared part, the release table CSV was byte for byte the file `mdb load-releases --csv` writes (626,101 bytes, 707 rows), and `release.json` held the release, the commit and 72 entries, with the hash of a downloaded file equal to its entry.
- **A second run** said `70 equal, 2 not deployed`; a listing before and after it differed in no id and no modified time.
- **An update in place.** A file, a custom step and a job definition were changed by hand: the dry run named the three as `updated` and wrote nothing, the run updated them, and the step and the job definition had the ids they had before. The file had a new id - the command of that day deleted a file that differed and uploaded it again, which is what the write in place replaced.
- **What is not in the manifest** - a file, a folder with a file in it, a registered step and a job definition that were put into the folder - kept its ids and modified times through every run. Four of the five were listed under `Not in the manifest and left alone`: of a folder that is not listed, the folder is named and not what it holds.
- **Against the content folder of the test system** the dry run said `16 equal, 34 updated, 20 created, 2 not deployed` and wrote nothing.

Measured in the second pass of 2026-10-12, with the command as it was then, in a new test content root: the same counts for the dry run, the run and a second run; a file changed by hand `updated ... (content replaced in place; id <its id> kept)` by one `PUT /files/files/{id}/content` with `If-Match`, answered 200, without a DELETE, the id and the creation time of the file kept and its bytes those of the checkout, the file and its content answering with the same ETag; a custom step and a job definition changed by hand updated under their ids; a `_contextName` set by hand on `Start Here` left alone and the next run all equal; `--compute-context` and `--content-root` written.

Measured in the third pass, on 2026-10-13, with the command as it was then, in two new test content roots:

- **`--create`.** For a content root that was not there the command ended with `there is no folder <root>, and nothing was created: pass --create to create it (with every folder above it that is missing)`, exit code 1 and GET requests only. With `--create`: `72 entries: 70 created, 2 not deployed.`, exit code 0, `release.json` the last line of an object and the last write, and the new root in `contentRoot` of the two job definitions that have it. The folder of the root was created without a line for it, in the dry run and in the run; the line is new since then.
- **A reference at a path of the manifest.** With a file deleted and a folder member of type reference of its name in its place, the dry run and the run said `FAILED` for the entry (`a reference of that name is in the folder ...`) and `not attempted` for `release.json` (`not written: 1 entry in front of it FAILED ...`): `72 entries: 68 equal, 2 not deployed, 1 not attempted, 1 FAILED.`, exit code 1, GET requests only, and `release.json` with the time stamp, the bytes and the ETag it had. The same path was also listed under `Not in the manifest and left alone`, which it no longer is. With the reference removed the file was `created` and `release.json` `equal`.
- **The ETag of the compared read.** A file changed by hand was `updated ... (content replaced in place; id <its id> kept)`; the `If-Match` of the PUT was the ETag of the `GET .../content` in front of it, and the answer was 200.
- **A write that SAS Viya refuses.** A file that another writer changed between the read and the write of the command - staged by the harness of the pass - was refused with HTTP 412: `FAILED ... (the file was left as it is ...)`, 30 entries `not attempted`, the usage report `not deployed ... (not looked up after the stop)`, `release.json` not attempted and unchanged, `39 equal, 2 not deployed, 30 not attempted, 1 FAILED`, `Stopped at the first write that failed`, exit code 1. The content of the other writer stayed, and the next run updated the file in place. The line of that day ended in the middle of the JSON the service answered (`"remediation":"Get the latest re))`); it shows the answer whole since then.

`release.json` holds the release, the commit of the checkout the folder was deployed from with the date of that commit, and every entry of the manifest with its kind and - for a file, a custom step and a job definition - a SHA-256 hash of its content. The page [Start Here](./Setup-Additional-UIs.md#start-here) reads it.

**`mdb package-export`** is for maintainers. It exports the content folder with its dependencies and keeps what the list names: every other object of the export is dropped with its entries in the package - a generated artifact, a report somebody parked in the folder, a dependency from outside it -, and so is every authorization rule and share. Of the files a report brings along, those stay that are children of the report and that its manifest entry lists under `files:` with their SHA-256 - the four pictures of the *LLM Usage Report*; a picture the report shows from a folder of its own is left out, and the report is then a difference. In every copy of the Prompt Builder report it resets the option defaults to the neutral values - no project ids, RAG and optimization off, **Verify TLS certificates in manifested models** on -, it removes the per-user states of the reports (each is a full copy of the report with a user id) and rewrites the exporting host to `your-sas-viya-host`, also inside the compressed report content. What would be written is compared with the checkout first; the command writes nothing and ends with exit code 1 - the file from before stays byte for byte - when a difference is left, when the package still names a host, or when an object sits on a path that never ships. An export of another folder than the content root needs `--out`, and `--allow-differences`, with `--out` only, writes the export of a test folder that holds no report; its last line says to keep that file out of the repository and names the command that brings it into a test folder - `mdb builders-import <file> --content-root "<folder>" --no-releases --no-reports --no-files`: the files, custom steps and job definitions, and no report, no release table and no commands file. The export replaces the user ids in `createdBy`, `modifiedBy` and `userName` - of the package, the summaries, the bodies (`userName` stands in the body of a folder) and the report wrappers - with `sas-agentic-ai-accelerator` and prints whose ids it replaced; `--keep-user-ids`, with `--out` only, leaves them and says so. `contentSourceLocation`, the path of the exported folder, stays, and `mdb` reads it to refuse a package of a nested folder. For the content root of the release it names no one; the export of a folder below `/Users/<user id>` carries that id in these paths and in the `contentRoot` of its job definitions, so the release file is written from the content root of the release only, also when `--out` names it. The export deletes its package and the record of its export job on the server; if a delete is refused or gets no answer, the file is still written and checked, a line names what is left and how to delete it, and the command ends with exit code 3. What the transfer service does with neutral user fields on an import of the reports has not been measured. (In Git Bash on Windows prefix a command that names a content folder with `MSYS_NO_PATHCONV=1`, or run it from PowerShell: the shell otherwise rewrites the `/SAS Agentic AI Accelerator/...` argument into a Windows path before `mdb` sees it.)

Measured on 2026-10-12 on the test content root: without the two reports in the folder the export ended with exit code 1 and wrote no file; with `--allow-differences` it wrote 71 objects and left nine out - the five objects of the folder the manifest does not list and the four folders above the content root, each as `(not below the content root)` -, and no rule came with the export of that folder. The file held nothing outside the manifest, no host, no rule and no share, and the user id of the exporter 215 times. With copies of the two reports of the test system in the folder the export went through without `--allow-differences`: nine option defaults reset, the host replaced in four places, 77 objects, four of them the pictures of the usage report. In the second pass of 2026-10-12 the export of a test root without the reports ended the same way, and with `--allow-differences` wrote 71 objects and named the user id of the exporter in 298 places; it deleted its package (HTTP 204) and left the record of the export job, which it deletes since. In the third pass, on 2026-10-13, the export of a test root with `--allow-differences` ended with exit code 0, the DELETE of its package and of the record of its export job were each answered 204, and the counts of both on the server were what they were before it. All 143 `createdBy` and 143 `modifiedBy` of the file were `sas-agentic-ai-accelerator`. The 12 `userName` of the folder bodies still held the id of the exporter: the export printed `left as the export wrote them (--keep-user-ids)` without that option and no `Replaced the user id(s)` line, and `mdb package-check` failed the file in both forms with `names the user id(s) ...`. `userName` is one of the fields the export replaces since then, which has not run against SAS Viya. Not measured: an export of the content folder itself, whether it brings rules, a delete that is refused, and exit code 3.

**`mdb package-check`** is the same comparison on the file in the repository, and a step of the workflow `verify-model-definitions.yml`. Every entry of the manifest has to be in the package, once, and equal its source - a file its repository file, line ends aside; a generated file what `mdb` generates now, `release.json` by its kind, its release, its commit and its entries; a custom step the `.step` file in `templates`, `ui` and `flowMetadata`; a job definition its program, its form and its parameters. Nothing else may be in the package, there may be no authorization rule and no share, and a report must name no host, carry no per-user state and pass the checks of its entry: for the Prompt Builder report every option the UI reads with its neutral default, for both reports the CAS tables they bind, and the files that are its children by name and hash. One line per difference. `--fix` rewrites hosts, states, option defaults and the user ids in `createdBy`, `modifiedBy` and `userName` in the file; a difference in content is fixed in the folder, with `mdb content-deploy`, and by exporting again. Not compared, by decision: a description and the fields the service derives for display, ids, links, users and time stamps, and a line end.

Measured on 2026-10-12 on packages exported from the test content root: for the export without the reports the command printed the two lines that say so and ended with exit code 1; for the export with copies of the two reports, `every entry of the manifest is in the package and equals the checkout`, exit code 0; and for an export after a file, a custom step and a job definition were changed by hand it named six differences - the `ui` of the step, the file with its two sizes, `code` and `parameters` of the job definition, and the two reports. The package that passed held the four pictures of the usage report, which the command of that day did not look at. In the second pass of 2026-10-12 the lenient form printed the parameters of the two job definitions of an export without reports as `drift:` lines and the two reports as errors (exit code 1), `--release` printed the four without `drift:` (exit code 1), and a file changed by hand in the root was `drift: ... (76 vs 1020 bytes)` in the lenient form and an error with `--release`. In the third pass, on 2026-10-13, both forms were run on the export of a test root and on copies of it in which only the commit and the date of `release.json` were changed: the commit of the checkout and an ancestor with its own date gave no line; a commit of another line of history gave `not in the history of this checkout (the clone holds it on another line of history)` and 40 digits of no commit `which this clone does not hold`, each a `drift:` line in the lenient form and an error with `--release`; a text that is no commit id and a date that is not the commit's were errors in both forms. Every one of these runs ended with exit code 1: the file named the user id in `userName` (above) and held no report.

### One part, somewhere else {#one-part-somewhere-else}

`mdb content-deploy --only <path of the manifest> --to <target>` deploys one part of the manifest to another place than its own in the content root. Measured in the second pass of 2026-10-12 with the command as it was then: a step of a test folder changed by hand was `updated ... (ui; id <its id> kept)` in the dry run (GET only) and in the run (one PUT), `--to <its id>` then said `equal`, a `--to` that names nothing ended with `Nothing was updated.` and exit code 1, and `--only RAG --to <folder>` created the 41 entries directly in that folder. Measured in the third pass, on 2026-10-13: `--to <id of a step of another name>` ended with `--to <id> names the registered step '<its name>' in <its folder>, not '<the name of the entry>': nothing was written - pass --force ...`, `Nothing was updated.` and exit code 1, with GET requests only - `GET /folders/ancestors` was answered 200 and named the folder; with `--force` the step was `updated` with its id kept, and the line named the step and its folder; `--to <path>` of a step of the entry's name said `equal ... - the registered step '<name>' in <folder>`; `--only RAG --to <a folder that is not there>` ended with `there is no folder ...: pass --create`, exit code 1 and no write, and with `--create` - for a folder of another name - `41 entries: 41 created.` `--only Data --to <root>/Data --create` wrote the content root of the manifest into the job definition of that folder. Measured in the second pass of 2026-10-13 with the command as it is since then (below): `--only Data --to <root>/Data --create` wrote `<root>` there, `--to <root>/zz_loader/Data --create` wrote `<root>/zz_loader`, and `--to <root>/zz_tables` ended with exit code 2 without a sign-in.

- **A custom step, a file or a job definition a site has somewhere else.** A site whose flows use a step it registered in a folder of its own has that step under an id the flows reference, and neither `mdb builders-import` nor a deploy of the content folder touches it. `--to` takes the SAS Content path, the id or the URI of that object and updates it in place from the checkout:

  ```bash
  mdb content-deploy --only "Logging and Monitoring/LLM - Log Parser.step" --to "/SAS Agentic AI Accelerator/Custom Steps/LLM - Log Parser.step" --dry-run
  mdb content-deploy --only "Logging and Monitoring/LLM - Log Parser.step" --to "/SAS Agentic AI Accelerator/Custom Steps/LLM - Log Parser.step"
  ```

  The object keeps its id and its name. It is updated and never created, no folder is made, and the content root is not looked at; where the target is not there the command says `Nothing was updated.` and ends with exit code 1. A report is refused. With an id or a URI the object is read for its name and its folder is asked for (`GET /folders/ancestors`), and the line ends with `- the registered step '<name>' in <folder>`. An object whose name is not the entry's is refused unless `--force`: `--to X names the registered step 'Y' in /F, not 'E': nothing was written - pass --force to update it all the same`. A path that names the object and a reference of its name is refused: name the object by its id. `--only release.json --to` is refused: `release.json` is the record of a whole folder.
- **A folder of the manifest, into a folder of another name.** `mdb content-deploy --only RAG --to "<your RAG content root>"` deploys the entries below `RAG` directly into that folder - `rag_core/`, `steps/`, `jobs/`, `models/` -, for a site whose option **RAG: content root** is not `/SAS Agentic AI Accelerator/RAG`. That folder is created only with `--create`. `deploy-rag-content.ps1 -ContentRoot` / `.sh -r` run this command, and `-Create` / `-c` pass `--create` on. A folder with a job definition that reads the content root - `Data`, with `Load-Accelerator-Tables` - goes to that folder of a content root: `--only Data --to "<root>/Data"` writes `<root>` into `contentRoot`, and a target that does not end with `/Data` ends the command with exit code 2 before it signs in.

### The gate between two releases, and for a release {#the-content-gate}

The package is exported from a system, so between two exports it holds the content of an earlier commit: a changelog entry, a fact sheet row or a fix to a program makes it differ from the checkout until a maintainer exports again. The gate therefore has two forms:

- **`mdb package-check`**, which the workflow runs on a branch and for a pull request. A difference in content against the checkout - a file, a custom step, a job definition, the release table, `release.json` - is printed as `drift: <file>: <what differs>` and does not fail. What a package must never be fails: an object the manifest does not list or that is in the package twice, an authorization rule, a share, a host in a report or in what an object says about itself (its summary, description or links), a connector other than the `parentFolder` of a file, step, job definition or folder, a substitution, a user id in `createdBy`, `modifiedBy` or `userName` (`--fix` rewrites it), a per-user state, a report default that is not neutral, a `release.json` whose commit is no commit id in full (40 hexadecimal digits, 64 in a SHA-256 repository; an abbreviation is none, and neither is an id with a blank or a line break around it) or is the id of a tag, a tree or the content of a file in the clone, an entry of the manifest that is missing, a path that never ships - a folder whose name holds `generated` among them -, a property or a field nothing in the checkout gives it. A host inside a form, a program or a file is content and a `drift:` line; the workflow therefore runs `--release` as well on a branch whose change replaces the package file. `--allow-missing` is honoured only while a package file of an earlier release is still in `SAS-Viya-Integrations/`.
- **`mdb package-check --release`**, which the release procedure runs after the export and the workflow runs for a version tag. Every difference is an error, also a release, a commit or a hash in `release.json` that is not the one of the checkout; `--allow-missing` has no effect with it, so a release cannot be tagged without the package. The commit of `release.json` passes only where it is HEAD or an ancestor of it. A commit the clone holds on another line of history (`is not in the history of this checkout (the clone holds it on another line of history)`), one it does not hold (`which this clone does not hold - a commit of a branch that was squash-merged or deleted after the deploy ...`) and an answer git cannot give are drift in the lenient form and errors here; a text that is no commit id in full, and a date that is not the commit's, fail both forms.

A contributor who changes a file the package holds a copy of sees `drift:` lines in the log of the workflow and a check that passes, and does nothing about them; a contributor who adds, moves or removes an object of the content folder lists it in the manifest, and the check then fails with `... is in the manifest but not in the package` until a maintainer has exported again. [`CONTRIBUTING.md`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CONTRIBUTING.md) has this for contributors.

Lines of the gate that are new with these checks: `... is in the package 2 times - one path holds one object`; `file '<name>' belongs to report '<path>' and is not among the files its manifest entry lists`; `file '<name>' of report '<path>' is not the file its manifest entry lists (SHA-256 ...)`; `job definition '<path>' carries 2 'form' properties ...`; `step '<path>' carries 'properties' the .step file does not have`; `file 'release.json' names release '<v>', this checkout is <v>`; `... names the commit <12 hex>, which is not in the history of this checkout`. Neither form has run in the workflow. `--release` ran on two exports of a test root in the second pass of 2026-10-12 and on one in the third, not on an export of the content folder.

### Releasing the package

The package is built on the system whose content folder holds the two reports. Two commits matter, and both are commits of `main`: the one the folder is deployed from, which `release.json` names, and the one the tag names. `mdb package-check --release` - on your machine, for the pull request of the package and for the tag - accepts only a `release.json` whose commit is the checked-out commit or one it descends from. Where `main` only takes squash-merged pull requests, this is the order:

1. **Squash-merge the release work with its final texts, then check out the commit that merge made on `main`**: `git fetch origin`, `git switch --detach origin/main`. Final means `CHANGELOG.md` with the date of the release - and for release 2.3.0 without the sentences that say the package file is not in the repository yet - and everything the manifest names as a source. The release table in the package is made from the changelog, so a change to it after the export makes the gate fail until the folder is deployed and exported again. Do not deploy from a commit of a branch, also not from the branch of this pull request before it is merged: the squash makes a new commit on `main`, your clone holds the commit of the branch on another line of history (`is not in the history of this checkout (the clone holds it on another line of history)`), and the fresh clone of the workflow of the tag does not hold it at all (`which this clone does not hold - ...`).
2. **`mdb content-deploy --dry-run`**, then **`mdb content-deploy`**: the folder holds the files, the custom steps and the job definitions of the checkout, each under the id it had. Read the list under `Not in the manifest and left alone` - it is what the export will drop. Run it again; every line says `equal`. A run that stops at a write that failed says what it wrote; nothing was removed, and the next run goes on from there.
3. **The reports.** In SAS Visual Analytics, open the Prompt Builder report and save it once if the UI of the release has an option the report does not hold yet - the export refuses a report that lacks one. The pictures of the *LLM Usage Report* are listed in the manifest with their hashes: if one was replaced, the export says `file '<name>' of report ... is not the file its manifest entry lists (SHA-256 <hash>)` - put that hash into the entry, bring that change to `main` the way of step 1, and start again at step 2, because `release.json` names the commit.
4. **`mdb package-export`** writes `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json`, or prints one line per difference and leaves the file as it was. The package of a release holds the defaults of the two settings of a site: a job definition of the folder whose `_contextName` is not `SAS Studio compute context` is named as a difference - set it with `mdb content-deploy --compute-context "SAS Studio compute context"` for the export. The command replaces the user ids in `createdBy`, `modifiedBy` and `userName` and prints whose ids it replaced; its last line says what the commit of the file holds and which commit is tagged.
5. **`mdb package-check --release`** ends with exit code 0.
6. **The package file goes into a second pull request**, from a branch that starts at the commit of step 1 (`git switch -c release/package`). It changes neither `CHANGELOG.md` nor a source the manifest names. For release 2.3.0 the same commit removes the four packages of earlier releases - `SAS-Agentic-AI-Accelerator-Prompt-Builder.json`, `SAS-Agentic-AI-Accelerator-RAG-Builder.json`, `SAS Agentic AI Accelerator.json` and `Logging-Monitoring/LLM Usage Report.json` - and takes `--allow-missing` out of the workflow; the sentences outside the changelog that say the package is not there yet (*Deploying the Builder UIs*, this page, the README of `SAS-Viya-Integrations`) can go with it, since none of them is a source of the manifest. The workflow runs `mdb package-check --release` on this pull request. Squash-merge it. A test of `mdb` fails while the one package and one of the four earlier files are in the repository together.
7. **Tag the commit that merge made on `main`** - `git fetch origin`, then `git tag 2.3.0 origin/main` while nothing else was merged - not the commit of your branch that added the file: after a squash merge that commit is not on `main`. A pull request merged between step 1 and the tag has to leave the changelog and every source of the manifest alone. For a version tag the workflow first asks git whether the commit of the tag is in the history of `main` (`git merge-base --is-ancestor HEAD origin/main`) and fails if it is not; then it runs `mdb package-check --release`, where a missing package fails as well; `mdb` no longer honours `--allow-missing` once the four earlier files are gone.

A maintainer who commits to `main` directly follows the same order with commits in place of the two pull requests.

None of this has run on a real content folder: the deploy, the export and the gate were measured on test content roots, and steps 2, 4 and 5 run code that was written after the last pass. The order of the commits was followed in scratch git repositories with the gate at each place the workflow runs it - the pull request of the package, `main` after its merge, a fresh clone of the tag -, and the question the workflow asks git for a tag was asked there as a command; no GitHub workflow has run with it.

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
loaded into CAS on the test system on 2026-10-12, by `mdb load-releases` and
by the job `Load-Accelerator-Tables`, each under a test name: 707 rows and
eleven columns, `builder_part` empty in 341 rows, `Prompt` in 134, `Decision`
in 54 and `RAG` in 178. It was **not shown on a report page**.

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
