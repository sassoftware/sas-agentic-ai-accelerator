---
name: model-audit
description: Audit the LLM/Embedding models registered in SAS Model Manager for the attributes and lifecycle that mdb 1.6.0 populates (model family, provider/version, per-token/second costs, endpoint, modelStatus/approvalState) and for duplicated input/output variables, then report which models need `mdb register --update` (or `mdb pull` first). Use when migrating an existing deployment to mdb 1.6.0+ or to verify a registered model carries its full metadata.
---

# Audit registered models for the mdb 1.6.0 attributes

Models registered by the framework's legacy Python scripts — or by `mdb` before
1.6.0 — are missing the attributes SAS Model Manager expects (model family
`llmodelType`, `deploymentId`, `inputTokenCount`/`outputTokenCount`/`hostingCosts`,
`endPoint`, and the `modelStatus`/`approvalState` lifecycle), and may have
**duplicated** input/output variables from repeated `--update` runs. This skill
retrieves that information for every registered model and tells you exactly which
ones to backfill. It is **read-only** (GETs only).

See `Model-Definition-Builder/MIGRATION.md` for the full migration context.

## Recipe

1. Install mdb with the Viya extra (once), from `Model-Definition-Builder/cli`:
   `pip install -e ".[viya]"` (pulls in `sasctl`).
2. Ensure the repo-root `.env` has `SAS_VIYA_URL`, `SAS_VIYA_USER`,
   `SAS_VIYA_PASSWORD` (and `SAS_VIYA_VERIFY_SSL=false` for a self-signed cert) —
   the same settings the `mdb` commands use.
3. Run the audit (companion script next to this file):
   `python Model-Definition-Builder/.claude/skills/model-audit/audit-models.py`
   Add `--json` for machine-readable output to parse and summarize.
4. Read the report and, for each model flagged **needs update**, run the printed
   remediation (see below). Re-run the audit; every model should read `ok`.

## What it reports

Per registered model (across the LLM Model Project and Embedding Model Project):
- `llmodelType` (family), `deploymentId` (version), `modelStatus`/`approvalState`.
- The variable count, flagged `dup!` when the same `(name, role)` appears more
  than once (the pre-1.6.0 `--update` duplication bug).
- A `missing` list — any of: `family (llmodelType)`, `version (deploymentId)`,
  `endPoint`, `cost` (none of inputTokenCount/outputTokenCount/hostingCosts set),
  `lifecycle` (modelStatus/approvalState absent).
- `needs_update: true` when anything is missing or variables are duplicated.

## Remediation

- Model that still has its local definition folder:
  `mdb register <model_id> --update` — backfills the attributes and clears the
  duplicate variables. `mdb register --all --update` does the whole fleet.
- Model registered by the legacy scripts with **no** local `definition.yaml`:
  `mdb pull <model_id> --import` to recover the folder + a reverse-engineered
  `definition.yaml`, review it, then `mdb register <model_id> --update`.
- Lifecycle: `mdb publish` advances a model to `deployed`/`approved`;
  `mdb retire` sets it to `retired`. `--update` never resets a model that is
  already deployed/approved — it only backfills a lifecycle that is absent.

## Gotchas

- The audit fetches each model's **full detail** (a GET per model) plus its
  variables, because the models list-summary endpoint omits custom attributes
  such as `llmodelType` and returns `deploymentId`/`endPoint` only for some
  models. Expect ~2 requests per registered model.
- `cost` is reported missing only when a model has **none** of the three price
  fields. A self-hosted model priced per second carries `hostingCosts` (no token
  prices) and an API model carries token prices (no `hostingCosts`) — both count
  as having cost.
- A model with `modelStatus`/`approvalState` absent (common on pre-1.6.0
  embedding models) is flagged `lifecycle`; `--update` backfills it to
  "ready for validation" / "awaiting approval" without touching a model that is
  already deployed.
