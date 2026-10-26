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

Requires Python 3.11 or newer. Without the `[viya]` extra `mdb` still adds, generates, validates and tests definitions; `setup`, `register`, `publish`, `ship`, `load-facts`, `options-*`, `credentials-apply` and `credentials-report` need it; `credentials-init`, `credentials-add` and `credentials-remove` edit a file and need neither the extra nor a sign-in. For air-gapped environments, download the dependency wheels on a connected machine and install with `pip install --no-index --find-links <wheel-dir> sas-mdb`.

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

`mdb add ollama` and `mdb add vllm` create definitions that call a self-hosted OpenAI-compatible server, for either chat (`--kind llm`) or embeddings (`--kind embedding`). Unlike the `hf-selfhosted` path — which downloads weights into the SCR image — these keep the weights on the inference server and the container only carries the thin api-wrapper requirements. The server base URL is environment-neutral, resolving as `OLLAMA_BASE_URL` / `VLLM_BASE_URL` container environment variable → the default of the definition (a localhost one unless `--base-url` names another); like the Azure resource, it is never a scoring option, so no caller can redirect it and the Prompt Builder shows no field for it. An optional bearer token is read from `OLLAMA_API_KEY` / `VLLM_API_KEY` (leave it out for an open server). `mdb deploy` writes both into the Deployment: the base URL as a value (your shell's variable, else the definition's default) and the token as an optional reference to the key Secret, so the pod starts without it. One published image can point at a dev, test or production inference server without a rebuild. For Ollama, `mdb add` reads the model from the server's `/api/show`: a model that thinks gets `reasoning_effort` and `max_tokens` 4000 with the reasoning timeout, an embedding model its vector and context length. vLLM reports the context length only; `mdb validate --live` refuses an `Embedding_Length` the server does not return.

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

### The provider key from the container's environment (`--auth env`) {#auth-env}

By default a hosted model takes its key per call, in the `API_KEY` scoring option: `options.json` names a credential-domain entry (`provider.auth.mode: api_key` with `key_name`), and the caller - the Prompt Builder, the optimization job, a decision - resolves it. A call that carries no `API_KEY` option, or a blank one, fails before any request with `No API key: pass the provider key in the API_KEY option of the call.` (`partials/_option_api_key.py.j2`). The SCR container answers such a call with HTTP 422 and the source line of the `raise` as its `errorMessage`, not the text of the exception, so every message of this kind is written out on that one line: the caller reads `raise RuntimeError("No API key: pass the provider key in the API_KEY option of the call.")` behind `422 Unprocessable Entity:`. Up to this release such a call failed with a `KeyError` and the `errorMessage` held only the marker line of the traceback (`~~~~~~~^^^^^^^^^^^`); the containers of 2026-10-20 answered so. The new score code has not been published to a container yet. The name of the entry (`key_name`, and the `API_KEY` default of an `options.json`) has letters, digits, `.`, `_` and `-` only: the SAS code of an LLM call writes it into `%let API_KEY=...;` with `COMPRESS`, so `Azure OpenAI` would be looked up as `AzureOpenAI`, and into a Perl expression that a `/` ends - `mdb` refuses any other name with a line that names the characters, at `generate`, `validate`, `import` and `register`. A definition with `provider.auth.mode: none` and a score template that sends the key of the `API_KEY` option is refused the same way: its `options.json` would have no `API_KEY`, and every call would fail with the line above - set `api_key` with `key_name`, or `env`. A score code kept by hand (`generation.overrides`, such as the self-hosted Llama and Phi folders) is not refused, and `mdb import` of a folder of such a template whose `options.json` has no `API_KEY` is refused naming `options.json`. With `--auth env` the **SCR container holds the key** instead, in an environment variable filled from a Kubernetes Secret:

```bash
mdb add openai gpt-6-luna --auth env --yes
mdb add anthropic claude-sonnet-5-5 --auth env --key-env TEAM_ANTHROPIC_KEY --yes
mdb add openrouter typesafe/jev-1.13 --auth env --yes          # a decision model
```

```yaml
# definition.yaml
provider:
  auth:
    mode: env
    env_var: OPENAI_API_KEY   # optional - the adapter's default when absent
```

- **The variable.** `--key-env NAME` (`auth.env_var`, upper-case letters, digits and `_`) names it; without it the provider's own name is used - the names of `.env.example`: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY` (google), `MISTRAL_API_KEY`, `VOYAGE_API_KEY`, `AWS_BEDROCK_API_KEY` (bedrock), `AZURE_OPENAI_API_KEY` (every azure adapter) and `TYPESAFE_API_KEY`. An adapter without a default needs `--key-env`. `key_name` is not used in this mode, and a manifest that sets it is refused.
- **What is generated.** `options.json` has **no `API_KEY` entry**. The score code reads the key from the variable (`partials/_env_api_key.py.j2`, shared by every template that sends a key) - only its *name* is written into the file. A caller that passes a non-empty `API_KEY` option still wins, so a credential-domain caller keeps working; a blank one counts as not set. With neither, the call fails before any request with `No API key: set the <VAR> environment variable on this container (or pass an API_KEY option).` The README and model card say where the key comes from, and the description the fact sheet, `modelConfiguration.json` and the model card show ends with *The key comes from the container environment variable &lt;VAR&gt;.* That sentence is not written into `definition.yaml`: every `mdb generate` and `mdb sync` derives it from `auth`, so it follows when you switch a definition to `env` by hand, name another variable or go back to `api_key`. A manifest in this mode that declares an option named `API_KEY` is refused - its default would take the place of the container's key in every call. `mdb import` of a folder generated in this mode (also `mdb pull --import`) reads the variable from the score code and keeps the mode. In `env` mode the env partial takes the place of the `API_KEY` option partial; nothing else of the score code changes.
- **Where it applies.** Templates that send a provider key: OpenAI-compatible chat and embeddings (OpenAI, OpenRouter, Mistral), Anthropic, Gemini, Bedrock (bearer key), Azure AI Foundry, Voyage and System One decision models. A template that takes no key - self-hosted weights, Ollama/vLLM (their own token variable), Bedrock SigV4, `azure-foundry-env` (already environment-keyed) - refuses `--auth env` with one line.
- **No credential-domain entry.** A model whose `options.json` has no `API_KEY` is offered in the Prompt Builder without a key - run, judged, used in a council, optimized and manifested - and is never shown disabled for a missing entry; `mdb credentials-*` need no entry for it either. **Remove an `API_KEY` option from a flow or a manifested model** that was built while the definition still took one: a non-empty option wins over the container's key. The same holds for **SAS code generated by `Create-LLM-Call.sas` or the custom step *LLM - Create LLM Call*** (and *LLM - Get Options*) from the `options.json` of before: it sets `%let API_KEY=<entry name>;` - the name of the credential-domain entry, such as `OpenAI`, not a key - and sends a key in its options. Code of *LLM - Create LLM Call* 1.6 and 1.7, *LLM - Get Options* 1.5 and 1.6 and the program of this release reads the key of that entry from the credential domain and sends it, in the place of the container's key; code of earlier versions sends `API_KEY:&API_KEY.`, the entry name, so the provider answers 401. Generate that code again from the new `options.json`, or delete the `%let API_KEY=...;` line and the `API_KEY:&API_KEY.` part of its options string. RAG ingestion looks a model's entry up in the static maps of `rag_core/providers.py`: regenerate them after the switch (`python -m rag_core.providers --from ../../Embedding-Definitions/embedding_fact_sheet.csv` and `--llm-from ../../LLM-Definitions/llm_fact_sheet.csv`, see [Managing Credentials](./Managing-Credentials.md#where-the-domain-applies--and-where-it-cannot)), or ingestion still asks the domain for the entry.
- **Who can call it.** The Ingress `mdb deploy` renders has no authentication: anyone who can reach *host/llm/&lt;model&gt;* calls the provider with the organisation's key, without any credential-domain check - the same holds for `azure-foundry-env`. Restrict who can reach the path - an allowlist of source addresses on the Ingress (with ingress-nginx the annotation `nginx.ingress.kubernetes.io/whitelist-source-range`), a NetworkPolicy, or no Ingress at all where only callers inside the cluster use the model - and set a spending limit for the key at the provider.
- **On the workstation.** `mdb test` lets the score code read the variable from the environment or `.env`, as the container does, and stops with one line when it is not set; `mdb validate --live`, `ship` and `radar` read the definition's variable first, then the adapter's.
- **Deploying.** `mdb deploy` adds the variable to the Deployment from a Secret (`secretKeyRef`, the Secret key named like the variable; `--key-secret`, default `llm-api-keys`) and prints the `kubectl create secret generic` line for every variable it rendered - names only, each value filled in by your shell (see [Deployment YAML](#deployment-yaml-and-ci-pipelines)).

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
mdb decision-node-migrate            # give the site's Call LLM node the id of the package and
                                     # move every decision that uses it (dry run; --apply)
```

On a fresh environment, `mdb setup` creates the `LLM Repository` and the LLM/Embedding Model Projects (idempotent — existing objects are left untouched), and `mdb register` performs the same check automatically for the kind it registers, so you do not have to run setup explicitly. `mdb setup` also writes the authorization-group rules (`sas-viya-cli-commands.txt`) and one seed file, `llm-prompt-builder.json` — the repository ID, the LLM project ID, the embedding project ID, the SCR endpoint and the deployment type, each under the name of the Prompt Builder option it is the value for. It is the single entry point for bootstrapping the environment. Once the Prompt Builder is configured, `mdb options-save` supersedes the seed file: it starts from the same discovered values and overlays what the live report actually holds, so what you keep is your deployment as configured rather than as bootstrapped — see [Preserving builder options across a report import](./Setup-Additional-UIs.md#preserving-builder-options-across-a-report-import).

`--update` removes the old delete-and-re-register workaround: after `mdb generate`, one command refreshes the registered model while keeping its ID, history (a new model version is created) and project placement. Both kinds use one implementation — embedding models register into the Embedding Model Project with the same content roles and fact-sheet enrichment. Each registered model also stores its `definition.yaml` as model content, so the source of truth travels with the model.

## The content folder: deploy, export, check

The content folder `/SAS Agentic AI Accelerator` ships as one transfer package, `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json`. What it holds is listed in `SAS-Viya-Integrations/package-manifest.yaml`: one entry per object with its path below the content root, its kind - folder, file, custom step, job definition, report, decision - and the repository file it is a copy of. [The content folder](./Setup-Additional-UIs.md#the-content-folder) has the layout and what never ships. Four commands act on that list, and a fifth prepares a site for the decision it holds:

```bash
mdb builders-import [<file>...] [--options <file>] [--content-root <path> [--create]] [--no-reports] [--no-decisions] [--compute-context <name>] [--report-caslib <lib>] [--dry-run]
                                                                                         # bring the package to a site
mdb content-deploy [--content-root <path>] [--create] [--only <path> [--to <path, id or URI> [--force]]] [--compute-context <name>] [--dry-run]
                                                                                         # bring a content folder to the state of the checkout
mdb package-export [--content-root <path>] [--out <file> [--keep-user-ids]] [--allow-differences]
                                                                                         # export the content folder to the one package
mdb package-check [<file>...] [--release [--base <commit>]] [--fix] [--allow-missing]    # compare the package with the checkout
mdb decision-node-migrate [--node-name <name>] [--target-id <id>] [--from-id <id>] [--apply [--delete-old]]
                                                                                         # move the Call LLM node to the id of the package
```

The commands ran on SAS Viya 2026.09 in eleven passes, twice on 2026-10-12, three times on 2026-10-13, three times on 2026-10-14, twice on 2026-10-15 and once on 2026-10-16, each as they were then, in test content roots below the folder of a user; each paragraph below says what was measured. What the first pass found changed them, and the second pass measured the import in place, the write of a file in place, the settings of a site, `--to` and the two forms of the gate. The third pass measured what was built after the second: `release.json` written last, `--create`, `--force`, the `If-Match` of the compared read and a write that is refused, the commit check of `--release`, the user ids an export replaces, the deletes an export sends, and the paths a package may hold. The fourth pass, the second of 2026-10-13, measured what was built after the third: the refusal of a part that names `release.json`, the content root of a folder that goes somewhere else, the rule for a reference of the name of an entry, the line for a folder that is created, the whole answer of a refused write, the command a failed import names, a custom step registered without the user of its `.step` file, `userName` among the fields an export replaces, and the commit id in full. What that pass and two reviews of the same code found was changed once more, and the fifth pass, the third of 2026-10-13, measured of it: a port in `SAS_VIYA_URL`, a reference of another kind in the list at the end of a run, the hint of a `FAILED` line for a reference alone, run as printed, the line of a refused write on one line, and the command the export printed for a test folder, which brought the 70 entries of an export into a second test root in place; on the workstation, without a request, the refusal of a part that names `release.json` in front of the sign-in; and at a stand-in on the workstation, not at SAS Logon, one sign-in for an answer that says `Bad credentials` and the one line for other 401 answers. What the fifth pass and the reviews beside it found was changed again, and the sixth pass, on 2026-10-14, measured it: a value printed as it is, the hints of a `FAILED` line in a run of a part and for the folder `Data`, each run as printed, the words of the list at the end of a run, the refusal of an empty `--only` or `--content-root` and of an address that cannot be used, without a request, and `--create` in the command the export prints for a test folder, which created a second test root that was not there and the 70 entries in it. What that pass and the reviews beside it found was changed once more, and the seventh pass, later on 2026-10-14, measured of it: the refusal of an empty `--manifest`, of `--content-root /` and of the address forms refused since then, without a request, and a deploy of the whole content folder into a new test root, whose export the command it printed brought into a second new root in place. What that pass and the reviews beside it found was changed once more, and the eighth pass, late on 2026-10-14, measured of it the lines of `mdb package-export` for a test folder, and again a deploy of the whole content folder into a new test root and the import in place of its export into a second one. The ninth pass, on 2026-10-15, measured of what was changed after the eighth the `HEAD` of a sign-in with a token, which the test system answered with 302, and again the deploy, the export and the import in place. The tenth pass, late on 2026-10-15, measured once more the deploy, the export and the import in place, with the commands as they were then. The eleventh pass, on 2026-10-16, measured them once more, and the credentials commands for a group and a user: `credentials-init`, `credentials-add`, `credentials-apply` after its dry run, `credentials-report`, `credentials-remove`, and `credentials-apply` and `credentials-report` again, after which no credential was left. The rest of those changes has **not run against SAS Viya**: the refusal of every empty value and of the options a manifest makes useless, and the one line of each outcome of the sign-in; nor has what was changed after the eighth pass but the `HEAD` of a token: the `HEAD` of a CLI login, and the refusal of an identity id or a credential domain outside printable ASCII, which ends before a request; nor the traceback of an exception that no command caught, which is printed without control characters since a change made after the ninth pass, with the text of each exception on the one line that names it since a later one, and the refusal of an empty port in `SAS_VIYA_URL`. Not run either: the quotes of a value, the answers of the token address that end a command since the sixth pass, the line for another status of the token address, which went to a stand-in on the workstation, the two commands for two packages of which one is refused its reports, and the gate for an id that is no commit. [What was measured, and what has not run](./Setup-Additional-UIs.md#the-package-on-sas-viya) has the list.

**`mdb builders-import`** is the command of a site: [First install and upgrade](./Setup-Additional-UIs.md#first-install-and-upgrade) describes it. It brings the package in two ways: the reports, with the files that are children of a report and the folders they sit in, through the SAS Viya transfer service, and every file, custom step and job definition in place, by the requests `mdb content-deploy` sends, with the package as the source of the content - [What an upgrade does to what a site has](./Setup-Additional-UIs.md#an-upgrade-keeps-ids). Three things it says rather than does:

- **A package from before 2.3.0** that somebody names. Such a file still holds the report `RAG Builder` and the job definition `RAG-Builder-UI`, the standalone RAG UI that is now part of the Prompt Builder. The command names it as predating 2.3.0, because importing it brings that UI back and `mdb` does not configure its report.
- **A report named `RAG Builder` that is still on the site.** It is retired, keeps serving the old UI with its own options, and is neither written to nor deleted; the message says how to remove it. See [the retired RAG Builder report](./Setup-Additional-UIs.md#the-retired-rag-builder-report).
- **A run that did not do everything.** The exit code is 1 and the run ends with the command to repeat for its case, with the files and flags it was given, on a line of its own that can be selected and pasted into PowerShell or bash: paths are spelled with forward slashes, and an argument a shell would read something into is quoted. The line is built from every option of the run but `--dry-run`. Where the reports were refused for the content root and no package's reports went to the transfer service, it carries `--no-reports` and says that it was added; where the reports of one package went through and those of another were refused, it is two commands: the run without that package, and that package with `--no-reports`; only the line with the package the release table came from loads the table again. Where the package imported and only the options could not be written, that command is `mdb options-restore --file <the kept file>`, which writes the options and imports nothing.

**`mdb content-deploy`** makes a content folder hold what the checkout holds, without a package. It creates the folders, uploads the files, registers the custom steps and updates a step that exists in place (`PUT /dataFlows/steps/{id}`), so that the step keeps its id and saved flows keep working, and creates the job definitions or updates them in place - the program as `code`, the page as the property `form`, the parameters. It writes two files itself: `Data/ACCELERATOR_RELEASES.csv`, the release table as `mdb load-releases --csv` writes it, and `release.json`. It does not deploy the two reports, which are authored in SAS Visual Analytics and reach a site with the package.

- One line per entry says `equal`, `updated`, `created`, or `FAILED` with the reason. `--dry-run` writes nothing and says what a run would do. Where the folder the content goes into is created, the first line says so - `created folder <path> (the folder the content goes into ...)`, in a dry run too; it is no entry and is not counted with them.
- **Everything is written in place.** A file that is missing is uploaded; one whose bytes differ gets its content replaced (`PUT /files/files/{id}/content` under the ETag of the content that was compared, so that a file somebody changed after the comparison is refused and left as it is) and stays the file it was - same id, same place. A custom step and a job definition are updated under the id they have. Nothing is deleted. What sits in a folder of the list and is not listed is named at the end and left alone.
- **The run stops at the first write that is refused or gets no answer.** That entry is `FAILED`, every entry behind it is `not attempted`, and the line `Stopped at the first write that failed: ...` says what was written before it and that nothing was removed. Where the update of a file is refused, the file is left as it is. An entry that fails before anything was sent - two objects of its name in the folder, a reference of its name to an object of its kind elsewhere - alone, or beside the object itself -, an object that cannot be read - is reported and the run goes on: each object and each reference of the name is named on the line, and none is written. A reference of that name to an object of another kind fails nothing and is listed at the end as `<path> (reference to a <kind> - not the <kind of the entry> that is listed under this name)`. The kinds are spelled as the other lines spell them: file, folder, report, registered step, job definition; an object of no kind of the manifest is called by the content type the Folders service gives it, or by its address. A folder that FAILED this way takes what is in it with it - nothing is written into it, in a dry run as in a run -, and a folder that cannot be created stops the run like a failed write. Either way the exit code is 1, and a second run leaves what is equal by then as it is. The line of a refused request shows `message`, `remediation` and `errorCode` of the answer whole, on one line. The line of a `FAILED` entry names the command that updates one object - `mdb content-deploy --only "<path in the manifest>" --to <its URI>`, `--to <id>`, or for a folder `--to <the SAS Content path of that folder>` - with the path the manifest lists, also in a run of a part. For a folder with a job definition that reads the content root it sits in (`Data`) the line says that the path has to end with that folder; for `release.json` it names no command, because `release.json` is written only by a run of the whole manifest.
- **`release.json` is written last**, only by a run that covers every entry of the manifest, and only when every entry in front of it was written: behind a stop or a failure its line says `not attempted` and why, and the folder keeps the `release.json` of an earlier run, or has none. A run of a part that names it - `--only release.json` - is refused with one line before the command signs in.
- **A folder that is not there** is created only where the manifest's own content root is the target; for another `--content-root`, and for the folder form of `--to`, `--create` creates it with every folder above it, and without it nothing is written: `there is no folder P, and nothing was created: pass --create to create it ...`.
- A repository file is uploaded with LF line ends whatever the checkout holds, so that the same file has the same hash from every checkout.
- A custom step is compared in its program, its form and its ports; a change of its description alone is not deployed.
- **Site settings.** A parameter the manifest marks `siteSetting` is a value of your site: `_contextName` and `contentRoot`, and of `Start Here` and the loader the CAS library and server, the credential domain, the names of the repository and the projects, the compute contexts to look for and the release table. A new job definition gets the default, and so does one that has no such parameter; a value a job definition on the site holds stays, and the deploy does not count it as a difference. `--compute-context <name>` writes a compute context into every job definition that has `_contextName`; `contentRoot` is always written with the content root the content goes to - for a folder that goes somewhere else see [One part, somewhere else](#one-part-somewhere-else); one object named with `--to` keeps the one it has. The other parameters of a job definition - the address of the guides, `_action`, `_output_type` - are written as the manifest has them.
- `--only RAG` deploys the RAG runtime alone; with `--to` a part goes somewhere else, see [One part, somewhere else](#one-part-somewhere-else). An empty `--only` - what `--only "$PART"` gives where the variable is not set - or one that is nothing but slashes ends the command with exit code 2; so does an empty `--content-root`, here and in `builders-import`, `package-export`, `options-save` and `options-restore`.

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

**`mdb package-export`** is for maintainers. It exports the content folder with its dependencies and keeps what the list names: every other object of the export is dropped with its entries in the package - a generated artifact, a report somebody parked in the folder, a dependency from outside it -, and so is every authorization rule and share. Of the files a report brings along, those stay that are children of the report and that its manifest entry lists under `files:` with their SHA-256 - the four pictures of the *LLM Usage Report*; a picture the report shows from a folder of its own is left out, and the report is then a difference. In every copy of the Prompt Builder report it resets the option defaults to the neutral values - no project ids, RAG and optimization off, **Verify TLS certificates in manifested models** on -, it removes the per-user states of the reports (each is a full copy of the report with a user id) and rewrites the exporting host to `your-sas-viya-host`, also inside the compressed report content. What would be written is compared with the checkout first; the command writes nothing and ends with exit code 1 - the file from before stays byte for byte - when a difference is left, when the package still names a host, or when an object sits on a path that never ships. An export of another folder than the content root needs `--out`, and `--allow-differences`, with `--out` only, writes the export of a test folder that holds no report; in such a folder `mdb content-deploy` writes that folder into the `contentRoot` of the job definitions and brings no report, so that `contentRoot` and the reports that are not there stay differences however often it runs, and the line of the differences says so; the last line of such an export says to keep that file out of the repository and names the command that brings it into a test folder - `mdb builders-import <file> --content-root "<folder>" --no-releases --no-reports --no-decisions --create --no-files`: the files, custom steps and job definitions into a test folder, which it creates where it is not there, and no report, no decision, no release table and no commands file. The export replaces the user ids in `createdBy`, `modifiedBy` and `userName` - of the package, the summaries, the bodies (`userName` stands in the body of a folder) and the report wrappers - with `sas-agentic-ai-accelerator` and prints whose ids it replaced; `--keep-user-ids`, with `--out` only, leaves them and says so. `contentSourceLocation`, the path of the exported folder, and the `contentRoot` of the job definitions stay, and the summary line names both; `mdb` reads `contentSourceLocation` to refuse a package of a nested folder. For the content root of the release it names no one; the export of a folder below `/Users/<user id>` carries that id in these paths and in the `contentRoot` of its job definitions, so the release file is written from the content root of the release only, also when `--out` names it. The export deletes its package and the record of its export job on the server; if a delete is refused or gets no answer, the file is still written and checked, a line names what is left and how to delete it, and the command ends with exit code 3. What the transfer service does with neutral user fields on an import of the reports has not been measured. (In Git Bash on Windows prefix a command that names a content folder with `MSYS_NO_PATHCONV=1`, or run it from PowerShell: the shell otherwise rewrites the `/SAS Agentic AI Accelerator/...` argument into a Windows path before `mdb` sees it.)

Measured on 2026-10-12 on the test content root: without the two reports in the folder the export ended with exit code 1 and wrote no file; with `--allow-differences` it wrote 71 objects and left nine out - the five objects of the folder the manifest does not list and the four folders above the content root, each as `(not below the content root)` -, and no rule came with the export of that folder. The file held nothing outside the manifest, no host, no rule and no share, and the user id of the exporter 215 times. With copies of the two reports of the test system in the folder the export went through without `--allow-differences`: nine option defaults reset, the host replaced in four places, 77 objects, four of them the pictures of the usage report. In the second pass of 2026-10-12 the export of a test root without the reports ended the same way, and with `--allow-differences` wrote 71 objects and named the user id of the exporter in 298 places; it deleted its package (HTTP 204) and left the record of the export job, which it deletes since. In the third pass, on 2026-10-13, the export of a test root with `--allow-differences` ended with exit code 0, the DELETE of its package and of the record of its export job were each answered 204, and the counts of both on the server were what they were before it. All 143 `createdBy` and 143 `modifiedBy` of the file were `sas-agentic-ai-accelerator`. The 12 `userName` of the folder bodies still held the id of the exporter: the export printed `left as the export wrote them (--keep-user-ids)` without that option and no `Replaced the user id(s)` line, and `mdb package-check` failed the file in both forms with `names the user id(s) ...`. `userName` is one of the fields the export replaces since then: in the second and in the third pass of 2026-10-13 the export of a test root printed `Replaced the user id(s) <id> in createdBy / modifiedBy / userName with 'sas-agentic-ai-accelerator' (298 place(s))`, and its file held that text in all 143 `createdBy`, 143 `modifiedBy` and 12 `userName`. In the third pass the command the export printed for a test folder - without `--create` then - created the 70 entries of that file in a second test root, in place and without a request to the transfer service. Not measured: an export of the content folder itself, whether it brings rules, a delete that is refused, and exit code 3.

**`mdb package-check`** is the same comparison on the file in the repository, and a step of the workflow `verify-model-definitions.yml`. Every entry of the manifest has to be in the package, once, and equal its source - a file its repository file, line ends aside; a generated file what `mdb` generates now, `release.json` by its kind, its release, its commit and its entries; a custom step the `.step` file in `templates`, `ui` and `flowMetadata`; a job definition its program, its form and its parameters. Nothing else may be in the package, there may be no authorization rule and no share, and a report must name no host, carry no per-user state and pass the checks of its entry: for the Prompt Builder report every option the UI reads with its neutral default, for both reports the CAS tables they bind, and the files that are its children by name and hash. One line per difference. `--fix` rewrites hosts, states, option defaults and the user ids in `createdBy`, `modifiedBy` and `userName` in the file; a difference in content is fixed in the folder, with `mdb content-deploy`, and by exporting again. Not compared, by decision: a description and the fields the service derives for display, ids, links, users and time stamps, and a line end.

Measured on 2026-10-12 on packages exported from the test content root: for the export without the reports the command printed the two lines that say so and ended with exit code 1; for the export with copies of the two reports, `every entry of the manifest is in the package and equals the checkout`, exit code 0; and for an export after a file, a custom step and a job definition were changed by hand it named six differences - the `ui` of the step, the file with its two sizes, `code` and `parameters` of the job definition, and the two reports. The package that passed held the four pictures of the usage report, which the command of that day did not look at. In the second pass of 2026-10-12 the lenient form printed the parameters of the two job definitions of an export without reports as `drift:` lines and the two reports as errors (exit code 1), `--release` printed the four without `drift:` (exit code 1), and a file changed by hand in the root was `drift: ... (76 vs 1020 bytes)` in the lenient form and an error with `--release`. In the third pass, on 2026-10-13, both forms were run on the export of a test root and on copies of it in which only the commit and the date of `release.json` were changed: the commit of the checkout and an ancestor with its own date gave no line; a commit of another line of history gave `not in the history of this checkout (the clone holds it on another line of history)` and 40 digits of no commit `which this clone does not hold`, each a `drift:` line in the lenient form and an error with `--release`; a text that is no commit id and a date that is not the commit's were errors in both forms. Every one of these runs ended with exit code 1: the file named the user id in `userName` (above) and held no report.

### The decision that carries the Call LLM node {#the-carrier-decision}

The content folder holds one decision, `Decisions/Call LLM Node`. It holds nothing but the *Call LLM* node and is not meant to be published: it is how the package brings that custom node type to a site under one id, `f9efcbb7-f57b-4a62-b56d-1a89595e2039` - [The Call LLM node and its id](./Deployment-of-Decisions.md#call-llm-node-uuid) says why. Its manifest entry is of the kind `decision` and lists the node types it uses with their ids (`nodeTypes:`), and for the *Call LLM* node the program that writes its code and its terms (`content:`, the Update program); like a report, the decision has no source in the repository.

- **`mdb package-export`** keeps the decision and the node type the export carries as its dependency, when the id is one the entry lists, and drops the authorization rule on the node type. **`mdb package-check`** fails a package whose decision uses a node type its entry does not list, whose node type is missing or has another id or name, or no code, or code and terms other than those of the program its entry names - the import would give every site that code -, or whose step names no node type (a node type that was deleted where the package was exported).
- **`mdb content-deploy`** does not deploy the decision. Its line says `not deployed ... (on the site)` or `(not on the site - it comes with the package)`, as for a report.
- **`mdb builders-import`** imports the decisions and the node types they use through the transfer service, in an import job of their own after the reports. It asks the site first. A node type of that id on the site is **overwritten in place**: local edits of its code and its terms are replaced, and the line says so. One that is not there is created under the id. Where a *Call LLM* node is there under another id, the command uploads nothing of the decisions, names `mdb decision-node-migrate` and ends with exit code 1 - unless `--no-decisions` leaves the decisions out. With another `--content-root` the decisions are refused like the reports, and the command it names to run again adds `--no-decisions`. Decisions that are not imported get no folder in place either. A site without SAS Intelligent Decisioning gets a line and no failure. The tasks of the job are read, and a decision or node type without a completed task counts as not imported.

### Moving the Call LLM node to its id {#decision-node-migrate}

`mdb decision-node-migrate` is the migration that [The Call LLM node and its id](./Deployment-of-Decisions.md#call-llm-node-uuid) describes - what it does to a site, and why a node that a decision uses is never deleted. What the command takes and says:

- Without `--apply` it only reads: it finds the node type by its name (`--node-name`, default *Call LLM*), refuses where another node type has the id (`--target-id`, default the shipped one), and lists every decision version that calls the node type, locked versions included - each version of each decision is read, which takes about two minutes for 250 decisions.
- Every run also lists each node type like the one of the id with the versions that call it. A **copy** has its code - line ends aside - or the `(before f9efcbb7)` name a run gives the old node type; a run with `--apply` that leaves decisions on a copy ends with exit code 1. A node type whose name alone starts with the name of the node, in any case, and whose code is another - a node renamed by hand from an older release, or another node such as *Call LLM Streaming* - is a warning only. Where the node type of the id is there, `--from-id <id>` moves the decisions of one of them to it and creates and renames nothing; it warns where that node type is no copy.
- Versions whose node lost its address - a node type deleted earlier - are a warning of the dry run and of the run: they cannot run, and no mapping moves them.
- `--delete-old`, with `--apply`, deletes the old node type at the end, and only when a fresh look finds no version that calls it. A `--from-id` node type that is no copy is kept: its decisions are moved, the line says why it is not deleted, and you delete it in SAS Intelligent Decisioning once you are sure.
- SAS Intelligent Decisioning does not say how many node types there are. The command reads up to 1000, and warns that the list may be incomplete when all 1000 come back. So does `mdb builders-import`.
- A site without SAS Intelligent Decisioning gets one line and exit code 0. A refusal or a failure ends with exit code 1; an id that is no UUID, and a `--from-id` that is the `--target-id`, end with exit code 2 before the sign-in.

A run that stopped is run again: what was done is found done. Measured on SAS Viya 2026.09 on 2026-10-20 with a throwaway node type and a decision with a locked version 1.0 and a current 2.0: the dry run listed both versions and changed nothing; `--apply` renamed, created and moved, and both versions then called the new node type and ran with its code; a second run found nothing to do; `--delete-old` deleted the old node type, and both versions still ran. Measured on 2026-10-21 as a dry run on the test system, where the node type of the id is there: it read 245 decisions, found no node type like it, named the two versions whose node type had been deleted, and sent GET requests only. Measured on 2026-10-21 on the test system with two throwaway node types, each called by a decision with a locked version 1.0 and a current 2.0: one with the code of the *Call LLM* node under another name, and one named like it with other code. The dry run listed the first as a copy and the second as a name alone, each with its decision and both versions. `--from-id --apply --delete-old` moved both versions of each decision to the node type of the id. It deleted the copy and kept the other node type, with the line that says so.

### One part, somewhere else {#one-part-somewhere-else}

`mdb content-deploy --only <path of the manifest> --to <target>` deploys one part of the manifest to another place than its own in the content root. Measured in the second pass of 2026-10-12 with the command as it was then: a step of a test folder changed by hand was `updated ... (ui; id <its id> kept)` in the dry run (GET only) and in the run (one PUT), `--to <its id>` then said `equal`, a `--to` that names nothing ended with `Nothing was updated.` and exit code 1, and `--only RAG --to <folder>` created the 41 entries directly in that folder. Measured in the third pass, on 2026-10-13: `--to <id of a step of another name>` ended with `--to <id> names the registered step '<its name>' in <its folder>, not '<the name of the entry>': nothing was written - pass --force ...`, `Nothing was updated.` and exit code 1, with GET requests only - `GET /folders/ancestors` was answered 200 and named the folder; with `--force` the step was `updated` with its id kept, and the line named the step and its folder; `--to <path>` of a step of the entry's name said `equal ... - the registered step '<name>' in <folder>`; `--only RAG --to <a folder that is not there>` ended with `there is no folder ...: pass --create`, exit code 1 and no write, and with `--create` - for a folder of another name - `41 entries: 41 created.` `--only Data --to <root>/Data --create` wrote the content root of the manifest into the job definition of that folder. Measured in the second pass of 2026-10-13 with the command as it is since then (below): `--only Data --to <root>/Data --create` wrote `<root>` there, `--to <root>/zz_loader/Data --create` wrote `<root>/zz_loader`, and `--to <root>/zz_tables` ended with exit code 2 without a sign-in. Measured in the third pass of 2026-10-13: two custom steps that had been registered in a folder of their own from the files of the build before were `updated ... (templates, ui; id <its id> kept ...)` with `--only "Custom Steps/<step>" --to <path>`, one PUT each, and a dry run behind it said `equal`.

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
- **`mdb package-check --release`**, which the release procedure runs after the export and the workflow runs for a version tag. Every difference is an error, also a release, a commit or a hash in `release.json` that is not the one of the checkout; `--allow-missing` has no effect with it, so a release cannot be tagged without the package. The commit of `release.json` passes only where it is HEAD or an ancestor of it. A commit the clone holds on another line of history (`is not in the history of this checkout (the clone holds it on another line of history)`), one it does not hold (`which this clone does not hold - a commit of a branch that was squash-merged or deleted after the deploy ...`) and an answer git cannot give are drift in the lenient form and errors here; a text that is no commit id in full, and a date that is not the commit's, fail both forms. `--base <commit>` asks the same of the history of that commit, which the workflow gives for a pull request that replaces the package: it is checked on the merge of its branch, which still holds the commits of the branch, so a package deployed from one of them passes there without `--base` and fails on `main` after the squash merge (`names the commit ..., which is not in the history of <base> ... - a commit of the branch of this pull request, which the squash merge does not keep ...`). Without `--release`, and for a name the clone holds no commit of, `--base` ends with exit code 2 before anything is checked.

A contributor who changes a file the package holds a copy of sees `drift:` lines in the log of the workflow and a check that passes, and does nothing about them; a contributor who adds, moves or removes an object of the content folder lists it in the manifest, and the check then fails with `... is in the manifest but not in the package` until a maintainer has exported again. [`CONTRIBUTING.md`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CONTRIBUTING.md) has this for contributors.

Lines of the gate that are new with these checks: `... is in the package 2 times - one path holds one object`; `file '<name>' belongs to report '<path>' and is not among the files its manifest entry lists`; `file '<name>' of report '<path>' is not the file its manifest entry lists (SHA-256 ...)`; `job definition '<path>' carries 2 'form' properties ...`; `step '<path>' carries 'properties' the .step file does not have`; `file 'release.json' names release '<v>', this checkout is <v>`; `... names the commit <12 hex>, which is not in the history of this checkout`. Neither form has run in the workflow. `--release` ran on two exports of a test root in the second pass of 2026-10-12 and on one in the third, not on an export of the content folder.

### Releasing the package

The package is built on the system whose content folder holds the two reports. Two commits matter, and both are commits of `main`: the one the folder is deployed from, which `release.json` names, and the one the tag names. `mdb package-check --release` - on your machine, for the pull request of the package and for the tag - accepts only a `release.json` whose commit is the checked-out commit or one it descends from. Where `main` only takes squash-merged pull requests, this is the order:

1. **Squash-merge the release work with its final texts, then check out the commit that merge made on `main`**: `git fetch origin`, `git switch --detach origin/main`. Final means `CHANGELOG.md` with the date of the release - and for release 2.3.0 without the sentences that say the package file is not in the repository yet - and everything the manifest names as a source. The release table in the package is made from the changelog, so a change to it after the export makes the gate fail until the folder is deployed and exported again. Do not deploy from a commit of a branch, also not from the branch of this pull request before it is merged: the squash makes a new commit on `main`, your clone holds the commit of the branch on another line of history (`is not in the history of this checkout (the clone holds it on another line of history)`), and the fresh clone of the workflow of the tag does not hold it at all (`which this clone does not hold - ...`).
2. **`mdb content-deploy --dry-run`**, then **`mdb content-deploy`**: the folder holds the files, the custom steps and the job definitions of the checkout, each under the id it had. Read the list under `Not in the manifest and left alone` - it is what the export will drop. Run it again; every line says `equal`. A run that stops at a write that failed says what it wrote; nothing was removed, and the next run goes on from there.
3. **The reports.** In SAS Visual Analytics, open the Prompt Builder report and save it once if the UI of the release has an option the report does not hold yet - the export refuses a report that lacks one. The pictures of the *LLM Usage Report* are listed in the manifest with their hashes: if one was replaced, the export says `file '<name>' of report ... is not the file its manifest entry lists (SHA-256 <hash>)` - put that hash into the entry, bring that change to `main` the way of step 1, and start again at step 2, because `release.json` names the commit.
4. **`mdb package-export`** writes `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json`, or prints one line per difference and leaves the file as it was. The package of a release holds the defaults of the two settings of a site: a job definition of the folder whose `_contextName` is not `SAS Studio compute context` is named as a difference - set it with `mdb content-deploy --compute-context "SAS Studio compute context"` for the export. The command replaces the user ids in `createdBy`, `modifiedBy` and `userName` and prints whose ids it replaced; its last line says what the commit of the file holds and which commit is tagged.
5. **`mdb package-check --release`** ends with exit code 0.
6. **The package file goes into a second pull request**, from a branch that starts at the commit of step 1 (`git switch -c release/package`). It changes neither `CHANGELOG.md` nor a source the manifest names. For release 2.3.0 the same commit removes the four packages of earlier releases - `SAS-Agentic-AI-Accelerator-Prompt-Builder.json`, `SAS-Agentic-AI-Accelerator-RAG-Builder.json`, `SAS Agentic AI Accelerator.json` and `Logging-Monitoring/LLM Usage Report.json` - and takes `--allow-missing` out of the workflow; the sentences outside the changelog that say the package is not there yet (*Deploying the Builder UIs*, this page, the README of `SAS-Viya-Integrations`) can go with it, since none of them is a source of the manifest. The workflow runs `mdb package-check --release --base <the base of the pull request>` on this pull request. Squash-merge it. A test of `mdb` fails while the one package and one of the four earlier files are in the repository together.
7. **Tag the commit that merge made on `main`** - `git fetch origin`, then `git tag 2.3.0 origin/main` while nothing else was merged - not the commit of your branch that added the file: after a squash merge that commit is not on `main`. A pull request merged between step 1 and the tag has to leave the changelog and every source of the manifest alone. For a version tag the workflow first asks git whether the commit of the tag is in the history of `main` (`git merge-base --is-ancestor HEAD origin/main`) and fails if it is not; then it runs `mdb package-check --release`, where a missing package fails as well; `mdb` no longer honours `--allow-missing` once the four earlier files are gone.

A maintainer who commits to `main` directly follows the same order with commits in place of the two pull requests.

None of this has run on a real content folder: the deploy, the export and the gate were measured on test content roots, and steps 2, 4 and 5 run code that was written after the last pass. The order of the commits was followed in scratch git repositories with the gate at each place the workflow runs it - the pull request of the package, `main` after its merge, a fresh clone of the tag -, and the question the workflow asks git for a tag was asked there as a command; no GitHub workflow has run with it.

## Deployment YAML and CI pipelines

`mdb deploy <model_id> --registry myregistry.azurecr.io` renders ready-to-apply Kubernetes YAML from the `SCR-LLM-Deployment-YAML` templates with every placeholder filled (resource names, image, ingress host and path) — the persistent-volume variant is selected automatically for self-hosted models. `--namespace` (default `llm`) sets the namespace of the Deployment, Service and Ingress, and `--tls-secret` (default `llm-tls-certs`) the TLS secret the Ingress serves its host with. For a definition whose key comes from the container's environment ([`--auth env`](#auth-env)) the Deployment reads that variable from a Kubernetes Secret - `--key-secret` (default `llm-api-keys`), one entry per variable name - and the command prints how to create it:

```bash
mdb deploy --all --registry registry.example.com/llm --host viya.example.com \
  --tls-secret viya-tls --key-secret llm-api-keys
# Create the key Secret once per namespace - ...
#   kubectl create secret generic llm-api-keys --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY" -n llm
#   Where the Secret exists already, add or replace these keys - every other key it holds stays:
#   kubectl patch secret llm-api-keys -n llm --type merge -p '{"stringData":{"OPENAI_API_KEY":"'"$OPENAI_API_KEY"'"}}'
```

The command line holds no key: the shell fills in each value from its own environment (for example after `set -a; . ./.env; set +a`). To add a key to a Secret that exists, use the `kubectl patch` line the command prints, not `kubectl create ... --dry-run=client -o yaml | kubectl apply -f -`: `apply` removes every key it applied before that the new manifest does not list, and a `mdb deploy <model_id>` lists only the variables of that model - the keys of the other models would go, and their pods would fail with `CreateContainerConfigError` at their next start.

The TLS Secret has to exist in the namespace of `--namespace` (default `llm`): an Ingress reads no Secret of another namespace, and the command says so when `--tls-secret` names another one. To serve the models with the certificate of the SAS Viya ingress, copy it into that namespace and renew the copy when the certificate is renewed:

```bash
# The certificate of the SAS Viya ingress, copied into the namespace of the models (bash)
kubectl create secret tls llm-tls-certs -n llm \
  --cert=<(kubectl get secret sas-ingress-certificate -n <viya-namespace> -o jsonpath='{.data.tls\.crt}' | base64 -d) \
  --key=<(kubectl get secret sas-ingress-certificate -n <viya-namespace> -o jsonpath='{.data.tls\.key}' | base64 -d)
```

### A cluster with Contour (`--ingress contour`) {#ingress-contour}

The Python SCR runtime answers only `POST /<model_id>`. The Ingress of each rendered model rewrites the public path `/llm/<model_id>/<model_id>` to it with an annotation of the NGINX Ingress controller. Contour, the ingress controller of current SAS Viya deployments, has no rewrite on a plain Ingress, so these Ingresses reach no model there. With `--ingress contour` the command renders the models without an Ingress and writes one router for all of them, `llm-router.yaml`, from `SCR-LLM-Deployment-YAML/llm-router-contour-template.yaml`:

```bash
mdb deploy --all --ingress contour --registry registry.example.com/llm --host viya.example.com \
  --tls-secret llm-tls-certs --dns "$(kubectl get service kube-dns -n kube-system -o jsonpath='{.spec.clusterIP}')"
kubectl apply -f deploy-yaml/                      # the models' Deployments and Services, and the router
curl https://viya.example.com/llm/healthz          # ok
```

An Ingress that an earlier `mdb deploy` without `--ingress contour` created for a model stays until you delete it (`kubectl delete ingress <model-name> -n llm`); the command reminds you of it.

- **What the router is.** A ConfigMap with an NGINX server block that has one `location = /llm/<model_id>/<model_id>` per rendered model and forwards it to `http://<service>.<namespace>.svc.cluster.local:<port>/<model_id>`; a Deployment of `nginxinc/nginx-unprivileged:1.27-alpine` (port 8080, readiness on `/llm/healthz`, requests 20m CPU and 32Mi, limit 128Mi) and its Service; and one Contour Ingress for the path `/llm` (`Prefix`) of the host, with the TLS secret of `--tls-secret` and `projectcontour.io/response-timeout: 300s`. Contour merges an Ingress for the same host, so the routes of SAS Viya stay as they are. Calls keep their URL: *host/llm/model_name/model_name*.
- **Render every model at once.** The router routes exactly the models of one render. `mdb deploy <model_id> --ingress contour` warns that applying its `llm-router.yaml` removes the routes of every other model: render with `--all` whenever a model is added. The Deployment of the router carries the list of routed models as an annotation, so a changed list restarts it - NGINX reads its configuration only at start.
- **The resolver.** NGINX looks each Service up per request through the cluster DNS (`resolver <dns> valid=30s ipv6=off`), so the router starts before the models and follows a Service that is created again. `--dns` gives the address of the cluster DNS; the default `10.0.0.10` is that of kube-dns on AKS, and a render without `--dns` prints it with the `kubectl` line that reads the address of your cluster. A wrong address fails no apply: the router starts, and every request answers 502. Other limits of the server block: request bodies up to 64 MB and 300-second timeouts.
- **Placing the pods.** `--node <name>` pins the models and the router to one node (`nodeSelector` on `kubernetes.io/hostname`); the `app=llm` node affinity of the templates still applies unless `--node-class` is given. `--node-class <class>` replaces that affinity and its tolerations with one toleration of the SAS node taint `workload.sas.com/class=<class>:NoSchedule`, so the models can run on a node pool of SAS Viya. `--requests CPU,MEMORY` (default `1000m,4000Mi`), `--limit-memory` and `--pull-policy` (`Always`, `IfNotPresent` or `Never`) change the model container only; the router keeps its own small requests. These options work with `--ingress nginx` as well. A value the command cannot use ends it with exit code 2 before anything is written.
- **Small requests are for API wrappers.** `--requests` and `--limit-memory` apply to every model of the render. Values such as `100m,384Mi` with a `1Gi` limit suit the models that only call a provider's API; a model that runs its own weights in the container - the `hf_transformers` LLMs (Qwen, SmolLM), the `emb_sentence_transformers` embedding models and `von_1_2` - needs the defaults or more, and with a `1Gi` limit it is ended for lack of memory. Because the router routes exactly the models of one render, render twice and keep the router of the `--all` run:

  ```bash
  mdb deploy --all --ingress contour ... --requests 100m,384Mi --limit-memory 1Gi                  # deploy-yaml/, with llm-router.yaml
  mdb deploy von_1_2 qwen_25_7b bge_large_en_v15 ... --ingress contour ... --out deploy-self-hosted   # the default requests
  cp deploy-self-hosted/deploy-*.yaml deploy-yaml/  # the Deployments only - not that run's llm-router.yaml, which routes only those models
  kubectl apply -f deploy-yaml/
  ```

  Measured on 2026-10-19 without a cluster: the `--all` run wrote 57 Deployments with the small values and a router with 57 routes; the second run, for the 14 self-hosted definitions, wrote them with `1000m` and `4000Mi` and no limit, and a router with 14 routes, with the warning that applying it removes every other route. The Deployments of the two runs differ only in `resources`.
- **No authentication.** Like the Ingress of each model, the router answers anyone who can reach *host/llm/*; restrict who can reach the path as described under [`--auth env`](#auth-env).

Without `--ingress contour` the rendered files are those of earlier releases, byte for byte. The output of `--ingress contour` was compared with `kubectl diff` against a cluster that ran a hand-made router of the same design (SAS Viya Stable 2026.09 on AKS, 2026-10-19), without applying it: the router was identical; the model Deployments differed in a label, in `SAS_SCR_LOG_LEVEL_SCR_IO` (`TRACE` from the templates, `INFO` on that cluster - see [Container Deployment](./Container-Deployment.md)) and in a readiness probe on `/internal/ready` (port 8080) that the hand-made Deployments had and the templates do not. Applying the rendered files there would restart each model pod once.

`Model-Definition-Builder/ci-recipes/` contains thin GitHub Actions pipelines (a `workflow_dispatch` model-lifecycle job and a weekly deprecation radar) where every step is just an `mdb` verb.

## Watching for provider retirements

`mdb radar --all` checks every managed model against its provider's live catalog; `--probe` sends one 1-token call per model for ground truth (catalog listing is not an availability guarantee — retired models can stay listed). `mdb retire <model_id>` tags a definition `deprecated` (hiding it from the Prompt Builder) and regenerates it. Where it cannot reach SAS Viya to retire the registered model, it says why - `The registered model's status was not updated: <the reason>` - and keeps the definition tagged.

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

RAG ingestion does not read the sheets: `rag_core` holds a copy of two of their
columns, the credential-domain entry of each hosted model
(`rag_core/providers.py`) and the prices of the cost line of the run log
(`rag_core/pricing.py`). After a definition is added, its auth mode switched or
a price changed, regenerate both from the sheets and replace the maps in the
modules. Until then a new hosted model is sent no key - an enrichment with it
stops before the first call and names `rag_core/providers.py`, an embedding
model fails at its first call - and the run log says `cost unknown` for it:

```bash
cd SAS-Viya-Integrations/RAG
python -m rag_core.providers --from ../../Embedding-Definitions/embedding_fact_sheet.csv
python -m rag_core.providers --llm-from ../../LLM-Definitions/llm_fact_sheet.csv
python -m rag_core.pricing --from ../../Embedding-Definitions/embedding_fact_sheet.csv
python -m rag_core.pricing --llm-from ../../LLM-Definitions/llm_fact_sheet.csv
```

The RAG test suite (`SAS-Viya-Integrations/RAG/tests`) fails while a map
differs from its sheet.

To make the sheets available to the SAS Visual Analytics monitoring report,
`mdb load-facts` uploads them to CAS — the Python equivalent of
`Load-Fact-Sheets.sas`. Each sheet is loaded with **global scope** (promoted, so
every session sees it) and **saved to the caslib's data source on disk** (so it
survives a CAS restart), as the tables `LLM_FACT_SHEET`, `EMBEDDING_FACT_SHEET`
and `DECISION_FACT_SHEET`. A kind the site has no definition of - no folder in
`Embedding-Definitions`, say - is loaded as an empty table with its sheet's
columns, and the command says so in one line: the LLM Usage Report reads the LLM
and the embedding table, and the page *Start Here* looks for all three. A kind
that has definitions but no sheet is skipped with the hint to run
`mdb sync --rebuild` first.
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
| `summary`, `detail` | the entry's bold lead (or its first sentence, cut at a word and ended with `...` when it is longer than 140 characters) and the rest, as plain text; numbered steps keep their numbers, so that "step 2" can be found |
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

The import reports every intended normalization (canonical options parser, provider usage-based token counting, requirements standardization) so you can review the diff — re-test and re-publish affected models afterwards. The second command converges the folder onto the `definition.yaml` the first one wrote, with the changes you made to it: a folder whose `definition.yaml` has no `.mdb-lock.json` beside it is not converged yet, and `mdb import` reads that `definition.yaml` as it is - without `--apply` it lists again what would change. A folder with both files is managed, and `mdb import` leaves it (*already has a definition.yaml - nothing imported.*). `mdb import <model_id> --apply` on a folder without `definition.yaml` does both steps at once, and `mdb pull <model_id> --import` writes the `definition.yaml` of the folder it pulled as `mdb import <model_id>` does.

Where the import cannot render the files, nothing is written and the line names what to repair: for an option whose name or default breaks the [rule of `options.json`](#custom-options), the `options.json` of the folder; for an option outside the vocabulary that declares no type, taking it out of `options.json` or adding it to the vocabulary - to keep it outside the vocabulary, run the import without it and declare it inline (`type` and `description`) in the `definition.yaml` it writes before `mdb import <model_id> --apply`; for a `definition.yaml` that is not converged yet, that `definition.yaml`. `mdb pull --import` refuses a folder in the same way and ends with exit code 1; the pulled files stay.

## Restricted networks

- `--offline` (or `MDB_OFFLINE=true`): no outbound calls; model catalogs come from the bundled snapshots and manual entry
- `HTTPS_PROXY` / `NO_PROXY` and `REQUESTS_CA_BUNDLE` are honored for all provider calls
- `--no-verify-ssl` / `MDB_VERIFY_SSL=false` corresponds to the `-k` option of the existing Python scripts

## Custom options

You can declare options that are not part of the standardized vocabulary — useful for provider-specific parameters. Give them an inline `type` (and a `description`) in `definition.yaml` and they are passed to the provider as-is under their own name. `mdb generate` and `mdb validate` warn you (rule V010) about what such an option gives up: UIs show it with its raw name and your description instead of a standardized label, and it gets no cross-provider value translation. That is often perfectly fine — the warning just makes it a conscious choice. To standardize an option instead, add it to `definition-core/static/option-vocabulary.json` with a label, type and per-family mapping. Custom options with `informational: true` appear in `options.json` for documentation but are never sent to the provider.

Every option `mdb` writes into `options.json` keeps the rule the Prompt Builder reads it by:

- the name of an option holds no comma and no colon, and neither the `default` of an option other than `API_KEY` nor a value of an `enum` is a text with a comma - the Prompt Builder sends the options of a run as one text, `{name:value,name:value}`;
- the name of an option has no blank and no quote at its ends, and a text the run sends - the `default` of an option other than `API_KEY`, a value of an `enum` - is one the score code reads as written: no blank and no quote at its ends, not `true` or `false` in any case, and no number (`"5"`, `"0.5"`, `"TRUE"`, `" a "` and `"'a'"` are not);
- a number the run sends is one the score code reads as a number: not one the Prompt Builder writes as `1e-7` or `1e+21` - a number below 0.000001 or from 10^21 on with one significant digit;
- an `enum` option has a non-empty list of texts as its `values`, and its `default` is one of them;
- the `default` of a `bool` option is `true` or `false`;
- the default of `API_KEY` is the name of the provider key, a non-empty text;
- the default of any other option is a text, a number, `true` or `false` - not null, a list or an object;
- `type`, `label` and `description` are texts where given;
- no option holds NaN or Infinity.

The second and the third item follow from the parser `mdb` generates into the score code (`_options_parser.py.j2`): it takes the blanks and then the quotes off the ends of a name and of a value, reads `true` and `false` in any case as true and false, and reads a value as a number where `float()` reads one that holds a point or `int()` one that does not. An informational ` temperature` was read as `temperature` and replaced the temperature of the model; a default `TRUE` was read as true and `5` as a number.

`mdb generate` - and `add`, `retire` and `validate` (rule V003) - refuses a manifest that breaks the rule with one line naming the option, and writes nothing. For example: `my_model: Option 'mode' is an enum option whose values are null - an enum option needs a non-empty list of texts as its values.` or `my_model: Option 'stop' has the default " a " - the score code would read it as "a", not as that text.` A self-hosted model whose `provider.params.base_url` is null, a list or an object is refused the same way, for its option `base_url`. `mdb import` and `mdb pull --import` refuse a hand-maintained folder in the same way when the name or the default of one of its options, or the key name of its `API_KEY`, breaks the rule - an `API_KEY` whose default is no text or an empty one (`0`, `false`, `""`, null), that has no default or that is no object is refused like one whose default is `5`; it was taken as no key, the definition became `auth.mode: none`, and the regenerated `options.json` lost the `API_KEY` the score code still read. A `definition.yaml` whose option holds a default that is null, a list or an object, or another field the schema of `definition.yaml` does not take, is refused by that schema before the rule is reached: `generate` and `register` print the schema's message on one line, `validate` reports it under rule V001, and `retire` ends with a traceback. Nothing is written. `mdb register` refuses a manifest that breaks the rule the same way, and also refuses an `options.json` it would upload that breaks the rule (one listed under `generation.overrides` too), before it writes anything to SAS Model Manager. Each refusal is printed on one line, also where the output goes to a pipe or a file, and so are the warning V010 and the note of `mdb generate` about an option outside the vocabulary, also for a name that holds a line break.

The Prompt Builder leaves an empty option of type `string` out of a run, and the score code then uses its own default. The rule of `mdb` and its refusals ran on the workstation only, not against SAS Viya.

## Scoring-time options

Options are defined once in the manifest and flow into the score script defaults, `options.json` and the fact sheet together. Beyond `temperature`, `top_p`, `top_k` and `max_tokens`, the typed option vocabulary covers `seed`, frequency/presence penalties, `reasoning_effort` for reasoning models (which reject temperature/top_p — the generator handles this), `max_completion_tokens` and `thinking_budget` for extended-thinking models. The full vocabulary lives in `Model-Definition-Builder/definition-core/static/option-vocabulary.json`.

### Reasoning effort per model {#reasoning-effort-per-model}

`reasoning_effort` takes five levels for every model: `minimal`, `low`, `medium`, `high`, `maximum`. The vocabulary translates them once per score template: the OpenAI-style templates send `minimal` to `high` as they are and `maximum` as `high`; the environment-configured Azure template sends `minimal` as `none` and `maximum` as `xhigh`; and the Anthropic template sends `output_config.effort`, with `minimal` and `low` as `low` and `maximum` as `max`. A model that accepts other words than its template carries its own translation on the option in `definition.yaml`:

```yaml
options:
  reasoning_effort:
    default: medium
    value_map:
      minimal: none
      low: low
      medium: medium
      high: high
      maximum: xhigh
```

The keys must be exactly the five levels and the values the words the provider accepts. A missing or extra key, an empty value, or a `value_map` on an option its template does not translate - a custom pass-through option or an informational one included - is refused by `mdb generate`, `validate` (V003) and `register`. `mdb add` writes the map from the catalog's list of the effort words a model accepts - `minimal` to `minimal` where the model takes it, else to its lowest word, and `maximum` to its highest - and only where it differs from the template's: `gpt_6_luna` sends `minimal` as `none` and `maximum` as `xhigh`, and `gpt_61_sol` sends `minimal` as `low` (it has no `none`) and `maximum` as `xhigh`. For a model the catalog does not know, write the map by hand and run `mdb generate`.

### Claude 5.5: effort instead of sampling {#claude-5-5}

Claude 5.5 (Haiku, Sonnet, Opus) refuses `temperature` other than 1, `top_p` and `top_k` at any value (even `top_p` 1), and a thinking budget (`thinking {type: enabled}`); it thinks adaptively and is steered by `output_config.effort`. `mdb add` therefore gives these models `reasoning_effort` (default `medium`) and `max_tokens` (default 4000), and no sampling option; a `temperature` a caller passes anyway is not sent. The thinking counts against `max_tokens`: a smaller default can end a call before its answer is written, and the Prompt Builder asks a judge for at least 4000 tokens for the same reason. `reasoning_effort` and `thinking_budget` exclude each other - keep `thinking_budget` for older Claude models. The default `medium` is the API's own for Haiku 5.5 and Opus 5.5; Sonnet 5.5 defaults to `high` at the API, so set `default: high` in its `definition.yaml` where you want that.

### How long a call may take {#call-timeout}

A hosted-API scorer waits `runtime.timeout_s` seconds for the provider (default 60), then the call fails in the container. A model that reasons before it answers can take minutes at a high effort, so `mdb add` gives a reasoning model 300, and so do the shipped definitions with a `reasoning_effort` option. Change the value in `definition.yaml`, then `mdb generate`, `mdb register --update` and `mdb publish`:

```yaml
runtime:
  template: openai_chat
  requirements_profile: api-wrapper
  timeout_s: 300
```

Keep it below the time the caller waits: a manifested prompt waits `LLMCONTAINERTIMEOUT` seconds (600 by default), and the Ingress of the rendered YAML lets a request take 300 seconds (`nginx.ingress.kubernetes.io/proxy-read-timeout`, and the router of [`--ingress contour`](#ingress-contour) the same). A self-hosted scorer has no such timeout.
