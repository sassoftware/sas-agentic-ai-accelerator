# Model Definition Builder

The Model Definition Builder takes the chore out of creating and maintaining the LLM (and, in an upcoming release, Embedding) model definitions of this repository. One small `definition.yaml` per model is the source of truth; the `mdb` CLI generates every other framework asset from it:

- the score script (`<modelId>Score.py`) with the canonical options parser and error handling
- `inputVar.json` / `outputVar.json` (byte-identical fleet-wide by construction)
- `modelConfiguration.json` (boilerplate stored once, per-model fields from the manifest)
- `options.json` (defaults derived from the same source as the score script — the historical three-way drift is impossible)
- `requirements.json` (versioned install profiles, incl. the gated Hugging Face flow)
- `README.md` and `Model-Card.md`
- the model's row in `llm_fact_sheet.csv` (keyed by the folder name, so key typos cannot happen)

Generated folders are fully compatible with the established `register-LLMs.py` / `publish-LLMs.py` scripts — nothing about registering or publishing changes.

## Install

```bash
cd Model-Definition-Builder/cli
python -m venv .venv
.venv/Scripts/activate        # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e .
mdb --help
```

For air-gapped sites, build a wheel bundle on a connected machine (`pip download sas-mdb -d wheels/ --no-binary :none:` against this folder) and install from the transferred directory with `pip install --no-index --find-links wheels/ sas-mdb`.

## Quick start

```bash
mdb add                          # interactive wizard: provider -> model -> done
mdb add openrouter deepseek/deepseek-v3.1 --yes
mdb add azure-foundry --resource myres --deployment my-gpt41 --id gpt_41_az --yes

mdb validate <model_id> --live   # one real provider call before anything touches Viya
mdb generate --all --check       # CI drift gate: committed files match their manifests
mdb sync --all                   # fact-sheet upsert (legacy rows preserved verbatim)
mdb import <model_id>            # adopt an existing hand-written folder
mdb test <model_id>              # invoke the generated scoreModel() locally
```

Provider API keys are read from the environment or a `.env` at the repo root (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `AZURE_OPENAI_API_KEY`, `MISTRAL_API_KEY`). Keys never enter manifests or generated files — `mdb validate` scans for secret-shaped strings.

## Restricted networks

Locked-down environments are a first-class path, not a fallback:

- `--offline` (or `MDB_OFFLINE=true`) uses the bundled catalog snapshots in `definition-core/catalog/` and manual entry — no outbound calls at all
- `HTTP(S)_PROXY` / `NO_PROXY` and `REQUESTS_CA_BUNDLE` are honored for every request
- `--no-verify-ssl` / `MDB_VERIFY_SSL=false` mirrors the `-k` convention of the existing Python scripts

## Layout

```
Model-Definition-Builder/
  definition-core/         # language-neutral: shared with the planned web app
    schema/                # manifest JSON Schema (exported from the pydantic models)
    templates/             # score-script templates per provider family + shared partials
    static/                # inputVar/outputVar, modelConfiguration boilerplate,
                           # typed option vocabulary, tag taxonomy
    catalog/               # static provider catalog snapshots for offline use
  cli/                     # the mdb Python package (pip install -e cli/)
```

`definition-core` is deliberately consumable without Python: the JSON Schema validates manifests, templates are restricted to a Jinja2/Nunjucks-portable subset (enforced by a lint test), and all boilerplate is plain JSON. The planned Model Definition Builder web app (Prompt Builder-style, registering directly into SAS Model Manager) renders the identical assets from this same core.

## Drift protection

Every generated folder carries a `.mdb-lock.json` recording the hash of each generated file. `mdb generate` refuses to overwrite files that were edited by hand (fold the change into `definition.yaml`, list the file under `generation.overrides`, or pass `--force`), and `mdb generate --all --check` is the CI gate that fails a pull request whose committed assets do not match their manifests. Fleet-wide fixes become one template change plus one `mdb generate --all` commit.

## Adding a provider

Adapters implement the small ABC in `cli/src/mdb/providers/base.py` (catalog, default options, manifest assembly, smoke test) and register either as a built-in or via the `mdb.providers` entry-point group from any pip package. The OpenAI-compatible adapter is parameterized and covers OpenAI, OpenRouter, Mistral and Azure AI Foundry (v1 endpoint) with a single score template — check whether your provider is OpenAI-compatible before writing a new template. Run the test suite (`python -m pytest cli/tests`) to verify template-subset compliance and generation determinism.
