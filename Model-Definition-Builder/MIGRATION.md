# Migrating from the legacy Python scripts to `mdb`

This guide is for administrators who set up SAS Model Manager and registered or
published models with the framework's original standalone Python scripts. Those
scripts have been removed and replaced by the **Model Definition Builder
(`mdb`)** — one CLI for the whole lifecycle. It also covers the step most
existing deployments need: **backfilling models that were registered before the
1.6.0 attribute/lifecycle improvements** (and repairing models whose variables
were duplicated by repeated updates).

## What changed, and when

- **1.3.0** removed the standalone scripts (`Model-Manager-Setup.py`,
  `utility/prompt-builder-json.py`, `register-LLMs.py`, `publish-LLMs.py`,
  `register-Embedding.py`, `publish-Embedding.py`). They live only in git
  history now. `mdb setup` / `register` / `publish` / `ship` replace them, with
  **one path for both LLM and Embedding** definitions.
- **1.6.0** made a registered model carry the full attribute set SAS Model
  Manager expects — model family, provider, version, per-token/second costs,
  endpoint, and a real lifecycle (`register → publish → retire`) — and fixed a
  bug where `--update` duplicated a model's input/output variables. Models
  registered before 1.6.0 are missing these attributes; see
  [Backfill existing registrations](#4-backfill-models-registered-before-160).

## Command mapping (old → `mdb`)

| Legacy script | `mdb` equivalent |
|---|---|
| `Model-Manager-Setup.py` — create the LLM Repository + LLM/Embedding projects and the authorization commands | `mdb setup` |
| `createLLMRepository.sas` (SAS Studio) — create the repository | `mdb setup` |
| `utility/prompt-builder-json.py` — build the Prompt Builder seed JSON | `mdb setup` (writes `llm-prompt-builder.json` + `rag-builder.json`) |
| `register-LLMs.py -l …` / `register-Embedding.py -m …` | `mdb register <id…>` or `mdb register --all` (both kinds; `--update` for in place) |
| `publish-LLMs.py -d …` / `publish-Embedding.py -d …` | `mdb publish <id…> [--wait]` (`-d` or `SAS_PUBLISH_DESTINATION`) |
| _(register + publish in one step — no old equivalent)_ | `mdb ship <id>` |
| Apply `sas-viya-cli-commands.txt` (LLM Consumers / Prompt Engineers) | Still applied by hand — but now **generated** by `mdb setup` |

## 1. Install and configure `mdb`

```bash
cd Model-Definition-Builder/cli
python -m venv .venv
.venv/Scripts/activate          # Windows; Linux/macOS: source .venv/bin/activate
pip install -e ".[viya]"        # the [viya] extra pulls in sasctl for Model Manager ops
mdb --help
```

Copy `.env.example` to `.env` at the repo root and fill in the connection and
operation settings (CLI arguments override these; precedence is
`CLI > environment > .env > default`):

| Variable | Used by | Notes |
|---|---|---|
| `SAS_VIYA_URL` / `SAS_VIYA_USER` / `SAS_VIYA_PASSWORD` | every Viya command | connection + auth |
| `SAS_VIYA_VERIFY_SSL` | every Viya command | `false` only for a self-signed cert |
| `SAS_SCR_ENDPOINT` | `setup`, `register`, `endpoints`, `deploy` | e.g. `https://<host>/llm` |
| `SAS_DEPLOYMENT_TYPE` | `setup` | `k8s` (default) or `aca` |
| `SAS_RESPONSIBLE_PARTY` | `setup`, `register` | project-level responsible party |
| `SAS_PUBLISH_DESTINATION` | `publish`, `ship` | SCR destination (e.g. `llmACR`) |
| `SAS_CAS_LIBRARY` / `SAS_CAS_SERVER` | `load-facts` | CAS library (default `Public`) / server (default `cas-shared-default`) for the fact-sheet tables |
| `SAS_LLM_MODEL_CARD_REPORT_URI` / `SAS_EMBEDDING_MODEL_CARD_REPORT_URI` | `register` | optional per-kind model-card custom chart (host = `SAS_VIYA_URL`); the example is the [LLM Usage Report](../website/docs/Administration-Guide/Logging-&-Monitoring.md) |

## 2. Bootstrap the environment (replaces `Model-Manager-Setup.py`)

```bash
mdb setup
```

Idempotent — existing objects are left untouched. It creates the **LLM
Repository** and the **LLM Model Project** / **Embedding Model Project**, and
writes three files to the current directory:

- `sas-viya-cli-commands.txt` — the SAS Viya CLI commands that create the
  **LLM Consumers** and **Prompt Engineers** groups and the repository/folder
  authorization rules. Review it, then run it (it grants access).
- `llm-prompt-builder.json` / `rag-builder.json` — the Prompt Builder / RAG
  Builder quick-start seeds (repository + project IDs, SCR endpoint).

`mdb register` runs the repository/project check automatically, so `mdb setup`
is optional if you only need registration — use it to bootstrap a fresh
environment and to (re)generate the authorization commands.

## 3. Register & publish (replaces `register-*.py` / `publish-*.py`)

```bash
mdb register <model_id> [<model_id> …]     # or: mdb register --all
mdb publish  <model_id> --wait             # destination via -d or SAS_PUBLISH_DESTINATION
mdb ship     <model_id>                     # validate --live -> register --update -> publish --wait
```

One path handles both LLM and Embedding definitions. `mdb list` shows the
registered fleet with each model's provider, family, version, lifecycle status
and endpoint.

## 4. Backfill models registered before 1.6.0

Models registered by the old scripts (or by mdb before 1.6.0) are missing the
attributes below and may have **duplicated** input/output variables from
repeated updates. This is the main migration task for an existing deployment.

Newly-populated on register/update:

- `llmodelType` (model family — Claude/GPT/Gemini/Phi/Qwen/…)
- `deploymentId` (the provider model/version string)
- `inputTokenCount` / `outputTokenCount` / `hostingCosts` (precise per-token /
  per-second costs) and `eventProbVar`
- `endPoint` (the SCR endpoint)
- `modelStatus` / `approvalState` (the lifecycle; backfilled when absent)

**Audit first.** Use the **`model-audit`** Claude Code skill
(`Model-Definition-Builder/.claude/skills/model-audit/`) to list every
registered model, show which of these attributes are missing, and flag
duplicated variables. It also prints the exact remediation command per model.
Without Claude Code, `mdb list` (and `mdb list --json`) shows family, version,
lifecycle and endpoint gaps at a glance.

**Then backfill:**

```bash
# For a model that still has its local definition folder:
mdb register <model_id> --update            # backfills attributes + clears duplicate variables
mdb register --all --update                 # every managed definition at once

# For a model registered by the OLD scripts with no local definition.yaml,
# recover the definition first, review it, then update:
mdb pull <model_id> --import                # rebuilds the folder + a definition.yaml from the model
mdb register <model_id> --update
```

`--update` only backfills lifecycle attributes when they are absent — it never
resets a model that is already `deployed`/`approved`. `mdb publish` advances a
model to `deployed`/`approved`; `mdb retire` sets it to `retired`.

## 5. Verify

```bash
mdb list                                     # provider, family, version, status, approval, endpoint
mdb list --kind embedding --json             # machine-readable, per kind
```

Re-run the `model-audit` skill; every model should report no missing attributes
and no duplicated variables.

## Regenerate the fact sheets from the definitions

The `LLM-Definitions/llm_fact_sheet.csv` and
`Embedding-Definitions/embedding_fact_sheet.csv` files (consumed by
`mdb register` for metadata enrichment and by `Load-Fact-Sheets.sas` for the
monitoring reports) are a **generated artifact** — derived entirely from the
model definitions, never hand-edited. After a bulk migration (many definitions
adopted or pulled), regenerate them in one step instead of syncing each model:

```bash
mdb sync --rebuild            # rebuild both sheets from every managed definition
mdb sync --rebuild --prune    # also drop rows for models that no longer have a definition folder
```

Rebuilding sorts rows by `model_id`, creates the file if it is missing, is
idempotent, and preserves hand-maintained rows without a definition folder
(unless `--prune`). Commit the regenerated sheets alongside the definitions.

To publish the sheets to CAS for the monitoring report — replacing the manual
`Load-Fact-Sheets.sas` step — use:

```bash
mdb load-facts                 # upload/promote/save into the Public library (default)
mdb load-facts --caslib MyLib  # a different CAS library (env: SAS_CAS_LIBRARY)
mdb load-facts --rebuild       # regenerate the sheets first, then load
```

This uploads each sheet with global scope (promoted) and saves it to disk as
`LLM_FACT_SHEET` / `EMBEDDING_FACT_SHEET`, dropping any existing table first — the
same result as the SAS script, driven from the CLI over the register/publish
session.

## Notes

- The legacy scripts are gone from the working tree; recover one from git
  history only for reference (`git log --all -- '**/register-LLMs.py'`).
- `mdb retire --archive` moves a retired definition to a git-ignored
  `_archive/` folder (and drops its fact-sheet row) so it no longer ships in the
  transfer package; plain `mdb retire` keeps it in place, tagged deprecated.
- The Administration Guide (`website/docs/Administration-Guide/`) has the
  full reference for `Setup-SAS-Model-Manager`, `Register-&-Publish-LLMs` and
  `Register-&-Publish-Embedding-Models`.
