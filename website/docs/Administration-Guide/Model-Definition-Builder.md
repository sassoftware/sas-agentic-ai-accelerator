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
pip install -e .
```

Requires Python 3.10 or newer. For air-gapped environments, download the dependency wheels on a connected machine and install with `pip install --no-index --find-links <wheel-dir> sas-mdb`.

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

Azure definitions are environment-neutral by default: the resource host you enter during `mdb add` is used for smoke tests, but is **not** baked into the definition unless you confirm it (or pass `--commit-resource`). A deployed container resolves its target in this order: per-call `azure_openai_resource` option → `AZURE_OPENAI_RESOURCE` container environment variable → the baked default. Set `AZURE_OPENAI_RESOURCE` in each deployment's YAML to serve dev, test and production (or different customer subscriptions) from the same published image — and set it in your `.env` so the wizard and smoke tests use your environment automatically. `mdb validate` reminds you when a definition carries a baked resource.

**Host flavors and API styles.** Azure hands out the same OpenAI-compatible data plane under three host suffixes, and mdb accepts any of them verbatim (a bare name still expands to the classic `*.openai.azure.com`):

| Host | Resource type |
| --- | --- |
| `<res>.openai.azure.com` | classic Azure OpenAI resource |
| `<res>.cognitiveservices.azure.com` | Azure AI Services / Foundry resource |
| `<res>.services.ai.azure.com` | Azure AI Foundry endpoint (sometimes region-qualified) |

By default mdb calls the **GA v1 endpoint** (`/openai/v1/chat/completions`, deployment name in the body — Microsoft's recommended surface). If your resource or org policy still requires the **legacy deployment-scoped route** — recognizable by URLs like `…/openai/deployments/<name>/chat/completions?api-version=2025-01-01-preview` — answer the wizard's *API version* question (or set the `azure_api_version` option / `AZURE_OPENAI_API_VERSION` container environment variable) and the calls switch to that route, same resolution order as the resource.

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

After adding a model:

```bash
mdb validate <model_id> --live     # smoke-test the provider directly
mdb register <model_id>            # or mdb ship <model_id> to register + publish
```

## Register, update and publish from the CLI

`mdb` now owns the full Viya lifecycle for managed definitions (install the extra: `pip install -e Model-Definition-Builder/cli[viya]`):

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
```

On a fresh environment, `mdb setup` creates the `LLM Repository` and the LLM/Embedding Model Projects (idempotent — existing objects are left untouched), and `mdb register` performs the same check automatically for the kind it registers, so you do not have to run setup explicitly. `mdb setup` also writes the authorization-group rules (`sas-viya-cli-commands.txt`) and the `llm-prompt-builder.json` / `rag-builder.json` builder seed files — it is the single entry point for bootstrapping the environment.

`--update` removes the old delete-and-re-register workaround: after `mdb generate`, one command refreshes the registered model while keeping its ID, history (a new model version is created) and project placement. Both kinds use one implementation — embedding models register into the Embedding Model Project with the same content roles and fact-sheet enrichment. Each registered model also stores its `definition.yaml` as model content, so the source of truth travels with the model.

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
