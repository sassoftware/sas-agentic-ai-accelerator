# Model Definition Builder

The Model Definition Builder takes the chore out of creating and maintaining the LLM and Embedding model definitions of this repository. One small `definition.yaml` per model is the source of truth; the `mdb` CLI generates every other framework asset from it:

- the score script (`<modelId>Score.py`) with the canonical options parser and error handling
- `inputVar.json` / `outputVar.json` (byte-identical fleet-wide by construction)
- `modelConfiguration.json` (boilerplate stored once, per-model fields from the manifest)
- `options.json` (defaults derived from the same source as the score script — the historical three-way drift is impossible)
- `requirements.json` (versioned install profiles, incl. the gated Hugging Face flow)
- `README.md` and `Model-Card.md`
- the model's row in `llm_fact_sheet.csv` (keyed by the folder name, so key typos cannot happen)

Registering and publishing is done with the `mdb` CLI itself — `mdb register`, `mdb publish` and `mdb ship` (see below) — one path for both LLM and Embedding definitions.

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
mdb add azure-foundry --kind embedding --resource myres --deployment my-emb3 --id emb3_az --yes
mdb add azure-foundry-env --deployment my-gpt41 --id azure_env --yes   # key/resource/deployment from the container env

mdb validate <model_id> --live   # one real provider call before anything touches Viya
mdb test <model_id>              # run the generated scoreModel() locally - what SCR will execute
mdb test <model_id> --mas        # ...called the way the MAS REST API calls it (plain strings)
mdb generate --all --check       # CI drift gate: committed files match their manifests
mdb sync --all                   # fact-sheet upsert (legacy rows preserved verbatim)
mdb import <model_id>            # adopt an existing hand-written folder
```

Provider API keys are read from the environment or a `.env` at the repo root (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `AZURE_OPENAI_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY`, `VOYAGE_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`). Keys never enter manifests or generated files — `mdb validate` scans for secret-shaped strings. Environment-specific hosts stay out of definitions by default: an Azure resource is read from the `AZURE_OPENAI_RESOURCE` container environment variable and is never a scoring option (where a container sends its requests is a property of the deployment, not of the caller); Bedrock regions and self-hosted Ollama/vLLM base URLs resolve per call via options or the `AWS_BEDROCK_REGION` / `OLLAMA_BASE_URL` / `VLLM_BASE_URL` container environment variables. The `azure-foundry-env` adapter takes that one step further: the key and the deployment resolve the same way (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`), so the definition declares no `API_KEY` input at all and one published image serves any Azure deployment.

The Viya commands (`setup`, `register`, `publish`, ...) need `SAS_VIYA_URL` and one credential, tried in this order: `SAS_VIYA_TOKEN` (an OAuth access token), `SAS_VIYA_USER` + `SAS_VIYA_PASSWORD` (the password grant), or - with neither set - the SAS Viya CLI's own login (`sas-viya auth loginCode`, read from `~/.sas/credentials.json`). On an SSO / SCIM / OIDC site the CLI login is the route: no password is needed and everything is created in your name. Each command prints which one it used.

**LLM vs embedding definitions.** Every definition has a *kind* — `llm` (chat) or `embedding` — which decides the score template, the destination folder (`LLM-Definitions/` vs `Embedding-Definitions/`) and the Model Manager project at register time. `mdb add` states the kind up front ("Adding an EMBEDDING model definition → …"), shows it first in the review table, and decides it from the catalog when it can (known embedding models are labeled `[embedding]` in the picker). To override or when entering an unknown model by hand, pass `--kind llm|embedding` — honored by every provider whose adapter has an embedding template (`mdb providers` shows a kinds column); embedding-only providers such as Voyage always produce embedding definitions. A kind/template mismatch fails validation (V012).

Self-hosted OpenAI-compatible servers are first-class: `mdb add ollama <model>` and `mdb add vllm <model>` create chat (`--kind llm`) or embedding (`--kind embedding`) definitions that call the server at scoring time. The weights stay on the server (only the thin api-wrapper requirements ship in the image), and an optional bearer token comes from `OLLAMA_API_KEY` / `VLLM_API_KEY`.

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
