---
sidebar_position: 11.5
---

# Enabling Prompt Optimization

This step is optional. With it, the LLM Prompt Builder gains an **Optimize** section that improves a prompt automatically with [DSPy](https://dspy.ai): a SAS Job Execution job rewrites nothing by hand — it uses the experiment runs the prompt engineer marked as **Best Response** (or a governed CAS table) as training data, runs an optimizer (bootstrap few-shot, or MIPROv2 which also rewrites the instruction) against a metric (exact match, token-overlap F1 or an LLM judge), and records the result **on the prompt itself** for the user to review, judge and accept. Nothing is ever applied automatically.

The feature is off by default and gated behind the `enableOptimization` Option of the Prompt Builder object. Enabling it takes four steps:

1. [Prepare a compute context whose Python has `dspy`](#1-prepare-the-compute-context) — the key prerequisite.
2. [Import the optimize job definition](#2-import-the-optimize-job).
3. [Create the governed API-key table](#3-create-the-governed-api-key-table) (only needed for hosted models that require keys).
4. [Set the Prompt Builder Options](#4-configure-the-prompt-builder).

## 1. Prepare the compute context

The job runs DSPy inside `proc python`, so the **SAS Compute context** it runs in must have a Python environment with the packages from [`SAS-Viya-Integrations/Prompt-Optimization/requirements.txt`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Prompt-Optimization/requirements.txt) installed:

```text
dspy>=3.2.1
requests>=2.31
optuna>=3.0
```

Install them into the Python environment your compute contexts use (the same one configured for `proc python` via `PROC_PYPATH`), for example:

```bash
pip install "dspy>=3.2.1" "requests>=2.31" "optuna>=3.0"
```

`optuna` is only needed for the **MIPROv2** optimizer (dspy treats it as an optional extra, so a plain dspy install does not pull it in — verified live). Without it, bootstrap few-shot still works and the job fails a MIPROv2 run fast with a clear message.

:::warning The most common failure
A compute context whose Python lacks `dspy` — or carries one older than **3.2.1**, the version the job's DSPy adapter is validated against — is the most common reason an optimization fails. The job checks both at startup and fails fast; the message (*"…lacks the dspy package"* or *"dspy X is too old"*) appears directly in the Prompt Builder's Optimize panel and in the run's optimization-tracker entry. If you see it, install/upgrade the requirements in that context's Python or point the Prompt Builder at a context that has them.
:::

Because not every deployment wants DSPy in its default environment, the context is **configurable**: you can prepare a dedicated context (for example `SAS Job Execution — DSPy`) with a Python environment that has the packages, and point the Prompt Builder at it. Size the context generously — an optimization run makes many model calls and runs for several minutes.

:::tip When the context's Python is read-only
`sas-pyconfig`-managed environments are mounted **read-only** in compute pods, so `pip install` into their site-packages fails. If you cannot (or don't want to) change the managed environment itself, overlay just the missing packages from a writable persistent mount the compute pods share (for example the compute landing zone):

```bash
# from any compute session (e.g. proc python), install ONLY the missing
# packages so nothing shadows the environment's own libraries:
python3 -m pip install --no-deps --target /srv/nfs/kubedata/compute-landingzone/prompt-optimization-pylib \
    optuna alembic colorlog sqlalchemy mako greenlet
```

Then add one autoexec line to the compute context (SAS Environment Manager → Contexts → your compute context → Advanced → autoexec):

```sas
options set=PYTHONPATH "/srv/nfs/kubedata/compute-landingzone/prompt-optimization-pylib";
```

`proc python` reads `PYTHONPATH` when it starts, so every job session in that context sees the overlay. Because the overlay contains only packages absent from the managed environment, it cannot shadow existing libraries.
:::

## 2. Import the optimize job

The job program is [`SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas). Create a **SAS Job Execution job definition** from it:

1. Open **SAS Job Execution** (`/SASJobExecution/manage`) and create a new job definition, for example under `/Public/Jobs`, named `Optimize-Prompt-DSPy`.
2. Paste the program as the job's code.
3. Note the job's **SAS Content path** (for example `/Public/Jobs/Optimize-Prompt-DSPy`) — that path is what the Prompt Builder's `optimizeJobProgram` Option points at.

The Prompt Builder launches the job through the Job Execution REST API with `_contextName` set to the context from step 1, and passes everything else (prompt, target LLM, dataset source, metric, optimizer) as job parameters. No secrets travel in the request.

Next to the job program ships [`Create-Optimization-Dataset.sas`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Prompt-Optimization/Create-Optimization-Dataset.sas) — a **template, not a job**: prompt engineers run it (e.g. in SAS Studio) to build a governed **CAS dataset table** (one column per prompt variable plus a `response` column) they can pick as the dataset source in the Optimize panel instead of the prompt's own experiments. The panel validates the table's columns and row count before launching; the compute context from step 1 must be able to reach the caslib.

## 3. Create the governed API-key table

Hosted models (OpenAI, Anthropic, Gemini, …) need their provider API key at call time. The browser gets its keys from the report's assigned data; the **job** reads them from a governed SAS table instead:

- Create a table with two character columns, `name` and `value` — one row per provider. The `name` must match what the LLM's `options.json` references via `API_KEY.default` (for example `OpenAI`, `Anthropic`), exactly like the [API-key table of the Prompt Builder itself](./Setup-Additional-UIs.md).
- Put it in a **SAS library that is pre-assigned in the compute context** from step 1 (for example via the context's autoexec), and restrict read access to the users who may run optimizations — access to this table is the access control for who can spend on optimization.
- The Prompt Builder passes only the **library and table names** to the job; the job reads the keys server-side and keeps them out of every log, tracker and produced prompt.

Self-hosted models without an `API_KEY` option need no key table.

## 4. Configure the Prompt Builder

In the Visual Analytics report, select the Prompt Builder object and open its **Options** pane:

| Option | Value |
| --- | --- |
| **Enable prompt optimization (DSPy)** | `Enabled` — reveals the settings below and the in-app Optimize section |
| **Optimization compute context** | The compute context from step 1 |
| **Optimize job path** | The job's Content path from step 2, e.g. `/Public/Jobs/Optimize-Prompt-DSPy` |
| **Minimum optimization samples** | Minimum Best-Response runs required before a run is allowed. Default `30`; the panel warns below 50 |
| **API-key library** / **API-key table** | The governed library and table from step 3 (leave blank when only key-less models are used) |

The same values can be supplied as URL parameters (`enableOptimization=true&computeContext=...&optimizeJobProgram=...`) when the Builder is used outside Visual Analytics.

## What a run produces

Everything stays **on the prompt-test itself**, next to its `Prompt-Experiment-Tracker.json` — a run creates **no additional Model Manager models**:

- An entry in the prompt's **`Prompt-Optimization-Tracker.json`** (written only by the job) holding the complete run: dataset/optimizer/metric configuration, metric before/after, the **baseline and the optimised prompt**, the selected few-shot demos, per-validation-example before/after evaluations, and — for failed runs — the error message the Builder displays.
- A **dataset snapshot** (`Prompt-Optimization-Dataset-<n>.json`), so every optimised prompt stays traceable to the exact examples it was optimised on.
- The Builder renders these entries as the **Optimization history** in the Optimize section, where a run can be inspected (evolution details) and loaded back into the workbench as an experiment.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Job fails immediately: *"lacks the dspy package"* | The compute context's Python is missing the requirements — see step 1. |
| Job fails immediately: *"dspy X is too old"* | The context's dspy predates 3.2.1, which the job's adapter is validated against — upgrade it (`pip install -U "dspy>=3.2.1"`). |
| Job fails: *"MIPROv2 … needs the optuna package"* | dspy's MIPROv2 requires the optional `optuna` package — install it in the context's Python (see step 1) or use the bootstrap optimizer. |
| Job fails: *"needs an API key for provider …"* | The governed key table has no row for that provider (or the Options don't name the table) — see step 3. |
| Launch blocked: *"The CAS table … was not found"* / *"lacks required columns"* | The dataset table isn't loaded/promoted in that caslib, or its columns don't match the prompt's variables + `response` — rebuild it with `Create-Optimization-Dataset.sas`. |
| Optimize button disabled: *"not fully configured"* | `computeContext` or `optimizeJobProgram` is blank in the Options pane. |
| Optimize button disabled: *"At least N runs …"* | The prompt has fewer Best-Response runs than the minimum — run and mark more experiments. |
| Launch fails: *"is not a Job Execution job definition"* | `optimizeJobProgram` doesn't point at the imported job's Content path — see step 2. |
| SCR smoke test fails | The SCR endpoint/deployment type in the Options pane is wrong, the target container isn't running, or the key is invalid. |
