# Base Definition

This folder is a **reference copy** of the file set every LLM definition consists of. It is kept so you can see the anatomy of a definition in one place; it is not the way to add a model any more.

**To add a model, use the [Model Definition Builder](../../Model-Definition-Builder/README.md):**

```bash
mdb add                                   # interactive wizard: provider -> model -> done
mdb add openai gpt-4o-mini --id gpt_4o_mini --yes
mdb validate gpt_4o_mini --live           # one real provider call
mdb test gpt_4o_mini                      # run the generated scoreModel() locally
mdb register gpt_4o_mini && mdb publish gpt_4o_mini --wait
```

`mdb add` writes a `definition.yaml` and generates every other file from it - the score script, `inputVar.json` / `outputVar.json`, `modelConfiguration.json`, `options.json`, `requirements.json`, `README.md`, `Model-Card.md` and the model's row in `llm_fact_sheet.csv` - so the files never drift apart, and `mdb generate --all --check` in CI keeps it that way. A provider that has no adapter yet is added with `mdb provider scaffold <name>` (see the [administration guide](../../website/docs/Administration-Guide/Model-Definition-Builder.md)), not by copying this folder.

## What each file is

| File | Role |
| --- | --- |
| `baseScore.py` | The score script. `scoreModel(userPrompt, systemPrompt, options)` returns `response, run_time, prompt_length, output_length`. Inputs arrive as one-element lists or Series (CAS, the SCR REST path) or as plain strings (the MAS REST API, SCR's CSV batch wrapper); `_scalar()` normalises them, as the generated scorers do. |
| `inputVar.json` / `outputVar.json` | The scoring contract. Identical for every LLM definition - never edited. |
| `modelConfiguration.json` | Model metadata for SAS Model Manager: name, description, score code file, tags. |
| `options.json` | The options a caller may pass in the `options` string, with defaults and descriptions. `API_KEY.default` is the credential-domain entry name the Prompt Builder resolves the key from. |
| `requirements.json` | The steps SAS Container Runtime performs when building the image (pip installs, weight downloads). |
| `Model-Card.md` | Documentation shown next to the model in SAS Model Manager. |

## Tags

`modelConfiguration.json` carries one sizing tag - `small` (a proprietary-API wrapper), `medium` (an open-weight model under 3B parameters) or `large` (3B to 8B) - which the SCR publish uses as a sizing indication, plus `LLM`/`SLM`, `Proprietary`/`Open-Source`, the provider and optionally `deprecated` (hides the model from the Prompt Builder). The full taxonomy is in `Model-Definition-Builder/definition-core/static/tag-taxonomy.json`. The framework serves models on CPU; models above 8B parameters are better served through a hosting provider or a self-hosted OpenAI-compatible server (`mdb add ollama` / `mdb add vllm`).
