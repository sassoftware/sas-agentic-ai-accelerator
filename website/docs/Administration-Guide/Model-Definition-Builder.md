---
sidebar_position: 12
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

Supported providers today: OpenRouter, OpenAI, Azure AI Foundry (v1 endpoint, key auth), Mistral, Anthropic and self-hosted Hugging Face models. AWS Bedrock, Google Gemini, Voyage and Embedding definitions follow in the next release.

API keys are read from environment variables or the `.env` file at the repository root (for example `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`). The wizard tells you which variable it used; keys are never written into any generated file.

After adding a model:

```bash
mdb validate <model_id> --live                          # smoke-test the provider directly
cd LLM-Definitions && python register-LLMs.py -l <model_id>
```

## Keeping definitions consistent

`definition.yaml` inside the model folder is the only file you edit. After changing it:

```bash
mdb generate <model_id>       # re-render the generated files
mdb sync <model_id>           # update the fact-sheet row
mdb validate <model_id>       # cross-file coherence rules with fix-it hints
```

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

## Scoring-time options

Options are defined once in the manifest and flow into the score script defaults, `options.json` and the fact sheet together. Beyond `temperature`, `top_p`, `top_k` and `max_tokens`, the typed option vocabulary covers `seed`, frequency/presence penalties, `reasoning_effort` for reasoning models (which reject temperature/top_p — the generator handles this), `max_completion_tokens` and `thinking_budget` for extended-thinking models. The full vocabulary lives in `Model-Definition-Builder/definition-core/static/option-vocabulary.json`.
