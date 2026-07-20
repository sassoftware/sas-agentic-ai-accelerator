---
sidebar_position: 2
title: Model Definition Builder
---

The Model Definition Builder (`mdb`) is the fastest way to add a new LLM or embedding model to the framework. Instead of copying an existing model folder and editing ten files by hand, you describe the model once and `mdb` generates every asset the framework needs — consistently and reproducibly. This guide walks through creating, checking and iterating on a model. For the operations view (CI drift gates, deployment YAML, air-gapped installs, the option vocabulary), see the [Model Definition Builder administration guide](../Administration-Guide/Model-Definition-Builder.md).

## The idea in one minute

Every model is described by a single `definition.yaml` in its folder — the **only** file you edit. From it, `mdb` renders the score script, `inputVar.json`/`outputVar.json`, `modelConfiguration.json`, `options.json`, `requirements.json`, the documentation files and the model's row in the fact sheet. Because they all come from one source, the three-way drift between score code, options and fact sheet that used to creep into hand-written definitions simply cannot happen. When you want to change something, you edit `definition.yaml` and re-generate.

## Install

```bash
cd Model-Definition-Builder/cli
python -m venv .venv
.venv/Scripts/activate        # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e .
mdb --help
```

Python 3.10 or newer. To register and publish to SAS Viya from the CLI, install the extra: `pip install -e .[viya]`. Provider API keys and your Viya connection are read from a `.env` file at the repository root (copy `.env.example` and fill it in) — keys are never written into any generated file.

## Add your first model

Run `mdb add` from anywhere inside the repository. The wizard asks you to pick a provider, then a model from that provider's live (or bundled) catalog, then a few questions, and shows you exactly what it will create before writing anything:

```bash
mdb add
```

Prefer a one-liner? Every answer has a flag, so the same thing works non-interactively:

```bash
# A hosted model through a provider API
mdb add openai gpt-4o-mini --id gpt_4o_mini --yes
mdb add anthropic claude-sonnet-4-5-20250929 --id claude_sonnet_4_5 --yes

# Azure AI Foundry, addressed by your deployment name
mdb add azure-foundry --resource myres --deployment my-gpt41 --id gpt_41_az --yes

# A model you host yourself on Hugging Face weights (downloaded into the image)
mdb add hf-selfhosted --repo Qwen/Qwen2.5-0.5B-Instruct --id qwen_25_05b --params-billions 0.5 --yes
```

`mdb` picks the provider, model version, endpoint, requirements and applicable options for you. When you pick a reasoning model, for example, it offers `reasoning_effort` instead of `temperature`/`top_p`, because those models reject them.

## See what was generated

```
LLM-Definitions/gpt_4o_mini/
  definition.yaml          # the only file you edit
  gpt4oMiniScore.py        # generated score script
  inputVar.json  outputVar.json
  modelConfiguration.json
  options.json
  requirements.json
  README.md  Model-Card.md
```

Open `definition.yaml` — it is short and readable. Everything else is derived from it.

## Check it before it touches SAS Viya

Two quick checks catch most problems locally:

```bash
mdb validate gpt_4o_mini --live   # coherence rules + one real provider call
mdb test gpt_4o_mini              # run the generated scoreModel() with a prompt
```

`validate --live` sends a single smoke-test request to the provider (using the key from your `.env`), so you know the model answers before you register it. `test` runs the generated score code exactly as SAS Viya will.

## Adjust the model's options

Change anything in `definition.yaml`, then regenerate. For example, to raise the default `max_tokens` and add a `seed`:

```yaml
options:
  temperature:
    default: 0.7
  max_tokens:
    default: 2000
  seed:
    default: 42
```

```bash
mdb generate gpt_4o_mini   # re-render the generated files
mdb sync gpt_4o_mini       # update the fact-sheet row
```

The options you can use — and how each maps to the provider — are defined centrally; see [scoring-time options](../Administration-Guide/Model-Definition-Builder.md#scoring-time-options) for the full list, and [custom options](../Administration-Guide/Model-Definition-Builder.md#custom-options) if you need a provider-specific parameter that is not in the standard set.

## Add an embedding model

Embedding models work exactly like LLMs — `mdb` puts them in `Embedding-Definitions/` and keeps `embedding_fact_sheet.csv` in step. Several open-source embedding models are bundled and run on CPU, including the RTEB-leaderboard IBM Granite models:

```bash
# Open-source, self-hosted on Hugging Face weights, runs on CPU
mdb add hf-selfhosted --repo ibm-granite/granite-embedding-small-english-r2 \
  --runtime sentence-transformers --id granite_embedding_small_r2 --yes
```

After adding, open `definition.yaml` to set the correct `Embedding_Length` and `Input_Token_Limit` for the model, then `mdb generate` and `mdb sync`.

## Use a self-hosted Ollama or vLLM server

If you already run models on **Ollama** or **vLLM**, `mdb` can point a definition at that server for either chat or embeddings. The model weights stay on the server; the published container just calls it. The server URL is not baked in — it resolves at run time from the `OLLAMA_BASE_URL` / `VLLM_BASE_URL` environment variable (or a per-call option), so the same model works across your dev, test and production servers.

```bash
# a chat model served by a local Ollama, addressed by its Ollama tag
mdb add ollama llama3.1:8b --id llama31_8b_ollama --kind llm --yes

# an embedding model served by vLLM, with the server URL baked as the default
mdb add vllm nomic-ai/nomic-embed-text-v1.5 --id nomic_embed_vllm --kind embedding \
  --base-url http://vllm-svc.ml.svc:8000/v1 --yes
```

Set `OLLAMA_BASE_URL` / `VLLM_BASE_URL` (and an optional `OLLAMA_API_KEY` / `VLLM_API_KEY`) in your `.env` for local smoke tests, and in each deployment's environment to point at that environment's server.

## Register and publish to SAS Viya

Once a model checks out, register and publish it from the CLI (needs the `[viya]` extra and your Viya connection in `.env`):

```bash
mdb register gpt_4o_mini       # create it in SAS Model Manager
mdb publish gpt_4o_mini --wait # publish to SCR and wait for the image build
```

On a brand-new environment you do not need any manual setup first — `mdb register` creates the model repository and the LLM/Embedding project if they do not exist yet (or run `mdb setup` up front). To update a model you already registered, edit `definition.yaml`, `mdb generate`, then `mdb register gpt_4o_mini --update` to replace it in place with a new version. `mdb ship gpt_4o_mini` does validate → register → publish in one step, and `mdb unregister gpt_4o_mini` removes a model from Model Manager again (the local folder is kept). The details are in [Register, update and publish from the CLI](../Administration-Guide/Model-Definition-Builder.md#register-update-and-publish-from-the-cli); the classic `register-*.py` / `publish-*.py` scripts also keep working.

Publishing builds an SCR container image. Hosted-API, Ollama and vLLM models build in seconds (their image only needs `requests`). A self-hosted Hugging Face model bakes its weights into the image and installs PyTorch, so its build is heavier — `mdb` keeps it as lean as possible by installing the CPU-only PyTorch build (SCR runs on CPU) and downloading weights over HTTP, but very large models can still take a while. If a publish fails on a build timeout, that is the signal the image is too heavy for your environment's build limit.

## Keep everything in sync

`definition.yaml` is the source of truth, so the loop is always the same:

```bash
mdb generate <model_id>   # re-render generated files from the manifest
mdb sync <model_id>       # update the fact-sheet row
mdb validate <model_id>   # cross-file coherence checks with fix-it hints
```

`mdb generate --all --check` verifies that every generated file still matches its manifest; it is the CI gate that keeps the whole fleet honest. If you ever hand-edit a generated file, `mdb` notices and tells you to fold the change back into `definition.yaml` (or mark the file as hand-maintained) rather than overwriting your work silently.

## Adopt an existing hand-written model

Have a model folder from before `mdb`? Bring it under management without losing anything:

```bash
mdb import <model_id>           # writes definition.yaml, shows what would change
mdb import <model_id> --apply   # converges the folder onto the generated files
```

The import lists every normalization it would apply so you can review the diff before committing, then re-test and re-publish.

## Where to go next

- [Administration guide](../Administration-Guide/Model-Definition-Builder.md) — CI drift gate, deployment YAML and CI recipes, air-gapped installs, the full option vocabulary, provider retirement radar
- [LLM Definitions](../Administration-Guide/LLM-Definitions.md) — the anatomy of a model definition, if you want to understand what `mdb` generates
- [Register & Publish LLMs](../Administration-Guide/Register-&-Publish-LLMs.md) — the registration flow in depth
