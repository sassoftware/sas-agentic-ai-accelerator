---
sidebar_position: 11.5
---

# Enabling Prompt Optimization

This step is optional. With it, the LLM Prompt Builder gains an **Optimize** section that improves a prompt automatically with [DSPy](https://dspy.ai): a SAS Job Execution job rewrites nothing by hand — it uses the experiment runs the prompt engineer marked as **Best Response** (or a governed CAS table) as training data, runs an optimizer (bootstrap few-shot; MIPROv2, which also rewrites the instruction; or GEPA, which evolves it from natural-language feedback) against a metric (exact match, token-overlap F1 or an LLM judge), and records the result **on the prompt itself** for the user to review, judge and accept. Nothing is ever applied automatically.

The feature is off by default. It is switched on with **Enable prompt optimization (DSPy)** (`enableOptimization`), one of the three switches among the options of the Prompt Builder object — the other two, for decision templates and for RAG setups, are independent of it. The switch is a checkbox; while it is not ticked the Optimize step is not shown and nothing is requested for it. Enabling it takes four steps:

1. [Prepare a compute context that runs as the requesting user and whose Python has `dspy`](#1-prepare-the-compute-context) — the key prerequisite.
2. [Import the optimize job definition](#2-import-the-optimize-job).
3. [Provide the provider keys via the credential domain](#3-provide-the-provider-keys-via-the-credential-domain) (only needed for hosted models that require keys).
4. [Set the Prompt Builder Options](#4-configure-the-prompt-builder).

## 1. Prepare the compute context

The **SAS Compute context** the job runs in must run its sessions **as the user who starts the job**. The job resolves the provider keys from the credential domain and writes its result to the prompt in SAS Model Manager with the identity of its session. A context that runs its servers under a service account resolves the keys as that account and writes as that account, which may not change the prompt of a user. Whether a context does is its attribute `runServerAs`, which the answer of `GET /compute/contexts/<id>` holds as `attributes.runServerAs`. A context without the attribute runs as the requesting user. On SAS Viya 2026.09 the stock *SAS Job Execution compute context* has `runServerAs` set to a service account (`sas-be-sa`), and *SAS Studio compute context* has none (read on 2026-10-20).

Measured on 2026-10-20 on SAS Viya 2026.09, with the job of the Prompt Builder in the *SAS Job Execution compute context*, before the check below: a target model with a provider key failed for want of the key, *The job resolved the domain as identity sas.compute*; a target model without one made all 19 model calls of the optimization and was then refused the upload of its result with HTTP 403. In the *SAS Studio compute context* the same run completed and wrote its result to the prompt. Granting the credential to the service account is therefore no remedy: the write to the prompt is still refused (see [Managing Credentials](./Managing-Credentials.md)).

From release 2.3.0 the job checks both before its first model call and ends with a message when either fails:

- It compares the user its session runs as with the user who started the job, as *Start Here* and *Load-Accelerator-Tables* do. When the two differ the message names both, and the account as SAS Viya sees it, and says to point **Optimization: compute context** at a context that runs as the user who starts the job, such as *SAS Studio compute context*.
- It adds a small file, `Prompt-Optimization-Write-Check.json`, to the prompt and removes it again — the two requests the write of the result makes. When SAS Model Manager refuses either, the message says so with the HTTP status. The links of the model do not tell: on 2026-10-20 the session of the service account got the same `update` and `addContent` links of the model as its owner.

Measured on 2026-10-20 on SAS Viya 2026.09 with the program of this release, against a prompt-test of five runs and the key-less target `qwen_25_05b`: in the *SAS Job Execution compute context* the job ended after 5 seconds with *The optimization job runs as sas (to SAS Viya: sas.compute), not as …, who started it: the compute context SAS Job Execution compute context does not run as the user who starts the job. Point the option Optimization: compute context …*, with no other milestone and no file written. In a test copy whose comparison of the two users was taken out, the same context got to the second check and ended with *… may not write to the prompt … in SAS Model Manager as sas.compute: adding a file to it was answered with HTTP 403 (Forbidden).* In the *SAS Studio compute context* the run completed - 12 model calls, the result on the prompt, and no `Prompt-Optimization-Write-Check.json` left on it. Not measured: a user who may read a prompt-test and not change it, and a refused removal of the file.

The job runs DSPy inside `proc python`, so the context must also have a Python environment with the packages from [`SAS-Viya-Integrations/Prompt-Optimization/requirements.txt`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Prompt-Optimization/requirements.txt) installed:

```text
dspy>=3.2.1
requests>=2.31
optuna>=3.0
```

Install them into the Python environment your compute contexts use — the same one configured for `proc python` via `PROC_PYPATH`, see [Configuring SAS Viya for Python Integration](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calsrvpgm/n1a7ados7ybdn1n15f0td8twwca9.htm) — for example:

```bash
pip install "dspy>=3.2.1" "requests>=2.31" "optuna>=3.0"
```

`optuna` is only needed for the **MIPROv2** optimizer (dspy treats it as an optional extra, so a plain dspy install does not pull it in — verified live). Without it, bootstrap few-shot still works and the job fails a MIPROv2 run fast with a clear message. The **GEPA** optimizer needs no extra package: its engine (`gepa`) is a hard dependency of dspy ≥ 3.2.1 and installs with it.

:::warning The most common failure
A compute context whose Python lacks `dspy` — or carries one older than **3.2.1**, the version the job's DSPy adapter is validated against — is the most common reason an optimization fails. The job checks both at startup and fails fast; the message (*"…lacks the dspy package"* or *"dspy X is too old"*) appears directly in the Prompt Builder's Optimize panel and in the run's optimization-tracker entry. If you see it, install/upgrade the requirements in that context's Python or point the Prompt Builder at a context that has them.
:::

Because not every deployment wants DSPy in its default environment, the context is **configurable**: you can prepare a dedicated context (for example `Prompt Optimization — DSPy`) that runs as the requesting user, with a Python environment that has the packages, and point the Prompt Builder at it. Size the context generously — an optimization run makes many model calls and runs for several minutes.

:::note Install centrally — this is an administrator task
`sas-pyconfig`-managed environments are mounted **read-only** in compute pods, so the packages cannot be (and should not be) installed from a compute session — a user-level `pip install` will fail by design. The packages belong in the **centrally managed Python configuration**: add the entries from `requirements.txt` to the `pip_installed_packages` option of the target environment (e.g. `default_py`) in the `sas-pyconfig` configuration and re-run the `sas-pyconfig` job — see [Managing Python Packages with sas-pyconfig](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calsrvpgm/p1iu2rzpk1j1b4n1shfqxpqzyso4.htm) and, for the overall `proc python` setup, [Configuring SAS Viya for Python Integration](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calsrvpgm/n1a7ados7ybdn1n15f0td8twwca9.htm). This keeps every compute pod consistent, survives updates and re-provisioning, and keeps package governance where it belongs. Avoid ad-hoc alternatives such as `PYTHONPATH` overlays on shared storage — they bypass the managed environment and are easy to leave behind unmaintained.
:::

## 2. Import the optimize job

The job program is [`SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas). Create a **SAS Job Execution job definition** from it:

1. Open **SAS Job Execution** (`/SASJobExecution/manage`) and create a new job definition, for example under `/Public/Jobs`, named `Optimize-Prompt-DSPy`.
2. Paste the program as the job's code.
3. Note the job's **SAS Content path** (for example `/Public/Jobs/Optimize-Prompt-DSPy`) — that path is what the Prompt Builder's `optimizeJobProgram` Option points at.

The job definition holds a copy of the program. The transfer package of the content folder brings a job definition `Prompt Builder/Optimize-Prompt-DSPy` with the program of its release - the package is compared with the repository before it ships -, so a site that imports the package creates no job definition of its own, points `optimizeJobProgram` at `/SAS Agentic AI Accelerator/Prompt Builder/Optimize-Prompt-DSPy` and gets the program of a new release with the import. A job definition you created by hand keeps the program of the day it was made: after an upgrade, replace its code with `Optimize-Prompt-DSPy.sas` of the release. From release 2.3.0 the program puts the ERROR line of a failed run together in a variable, where it wrote the message of the run into a quoted string. Measured on 2026-10-11 on SAS Viya 2026.09, with a target model whose provider key the identity of the job does not hold: with the program of release 2.3.0 in the job definition, the log of the failing run held *ERROR: Prompt optimization failed: The model gpt_41_mini needs an API key for provider OpenAI but the credential domain provided none …*, typed as an error like the line that continues it, and no NOTE about a quoted string. With the program of 2.2.0 the same failing run had written *NOTE: The quoted string currently being processed has become more than 262 bytes long* five lines above its ERROR line.

The Prompt Builder launches the job through the Job Execution REST API with `_contextName` set to the context from step 1, and passes everything else (prompt, target LLM, dataset source, metric, optimizer) as job parameters. No secrets travel in the request.

Next to the job program ships [`Create-Optimization-Dataset.sas`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Prompt-Optimization/Create-Optimization-Dataset.sas) — a **template, not a job**: prompt engineers run it (e.g. in SAS Studio) to build a governed **CAS dataset table** (one column per prompt variable plus a `response` column) they can pick as the dataset source in the Optimize panel instead of the prompt's own experiments. The panel validates the table's columns and row count before launching; the compute context from step 1 must be able to reach the caslib.

## 3. Provide the provider keys via the credential domain

Hosted models (OpenAI, Anthropic, Gemini, …) need their provider API key at call time. Both the browser and the **job** resolve them from the **credential domain** (default `agentic-ai-keys`) under the identity of the user who launched the run — set it up once as described in [Managing Credentials](./Managing-Credentials.md). Who holds a credential in the domain is the access control for who can spend on optimization; the keys stay out of every request, log, tracker and produced prompt.

Self-hosted models without an `API_KEY` option need no credential.

## 4. Configure the Prompt Builder

In the Visual Analytics report, select the Prompt Builder object and open its **Options** pane:

| Option | Value |
| --- | --- |
| **Enable prompt optimization (DSPy)** | Ticked — it is a checkbox. Ticking it reveals the three `Optimization:` options directly below it and the in-app Optimize step |
| **Optimization: compute context** | The compute context from step 1 |
| **Optimization: job path** | The job's Content path from step 2, e.g. `/Public/Jobs/Optimize-Prompt-DSPy` |
| **Optimization: minimum samples** | Minimum Best-Response runs required before a run is allowed. Default `30`; the panel warns below 50 |
| **Credential domain** | The domain from step 3, among the shared options at the top of the pane. Defaults to `agentic-ai-keys`; enter `none` when only key-less models are used (RAG setups cannot be used with `none`) |

The three `Optimization:` options are not shown while the checkbox is not ticked: Visual Analytics reloads the object when the switch changes, and the options appear with the reload. Up to release 2.2 the switch was a dropdown with *Enabled* and *Disabled*, and Visual Analytics showed *Disabled* in it after every reload, whatever was stored. A report saved then keeps its setting: where optimization was enabled, the checkbox is ticked. The compute context set here is the optimization job's only — a deployment that also builds RAG setups sets the ingestion context separately, as **RAG: ingestion compute context** (see [Deploying the Builder UIs](./Setup-Additional-UIs.md#configuration-properties-panel)).

The same values can be supplied as URL parameters (`enableOptimization=true&computeContext=...&optimizeJobProgram=...`) when the Builder is used outside Visual Analytics. The switch reads `true` (what Visual Analytics writes for a ticked checkbox), `1`, `yes` or `on` as enabled; a missing or unreadable value leaves it off.

## What a run produces

Everything stays **on the prompt-test itself**, next to its `Prompt-Experiment-Tracker.json` — a run creates **no additional Model Manager models**:

- An entry in the prompt's **`Prompt-Optimization-Tracker.json`** (written only by the job) holding the complete run: dataset/optimizer/metric configuration, metric before/after, the **baseline and the optimised prompt**, the selected few-shot demos, per-validation-example before/after evaluations, and — for failed runs — the error message the Builder displays.
- A **dataset snapshot** (`Prompt-Optimization-Dataset-<n>.json`), so every optimised prompt stays traceable to the exact examples it was optimised on.
- The Builder renders these entries as the **Optimization history** in the Optimize section, where a run can be inspected (evolution details) and loaded back into the workbench as an experiment.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Job fails immediately: *"The optimization job runs as …, not as …, who started it: the compute context … does not run as the user who starts the job"* | The context runs its sessions under a service account — see step 1. Point **Optimization: compute context** at a context that runs as the requesting user, such as *SAS Studio compute context*. Granting the credential to the service account does not help: the write of the result to the prompt is refused. |
| Job fails immediately: *"may not write to the prompt … in SAS Model Manager"* or *"may not remove files from the prompt …"* | SAS Model Manager refused the user to add a file to the prompt-test or to remove one (the HTTP status is in the message). The result of a run is written there — the user needs the permission to change the prompt-test's project. |
| Job fails immediately: *"lacks the dspy package"* | The compute context's Python is missing the requirements — see step 1. |
| Job fails immediately: *"dspy X is too old"* | The context's dspy predates 3.2.1, which the job's adapter is validated against — upgrade it (`pip install -U "dspy>=3.2.1"`). |
| Job fails: *"MIPROv2 … needs the optuna package"* | dspy's MIPROv2 requires the optional `optuna` package — install it in the context's Python (see step 1) or use the bootstrap optimizer. |
| Job fails: *"needs an API key for provider …"* | The credential domain has no entry for that provider for the launching user or their groups — see step 3 and [Managing Credentials](./Managing-Credentials.md). |
| A message with *"was answered with HTTP …"* | A request to SAS Viya or to a model container was refused. The message names the status, its name and the message of the answer, for example *HTTP 403 (Forbidden)*. Up to release 2.2 it held the answer as it came, a JSON error whose quotation marks the log shows as backticks. |
| Launch blocked: *"The CAS table … was not found"* / *"lacks required columns"* | The dataset table isn't loaded/promoted in that caslib, or its columns don't match the prompt's variables + `response` — rebuild it with `Create-Optimization-Dataset.sas`. |
| The flow has no **Optimize** step | **Enable prompt optimization (DSPy)** is not ticked in the Options pane — the default, and what a report import without saved options leaves behind. For a decision template the step is **Evaluate** by design. |
| Optimize button disabled: *"not fully configured"* | **Optimization: compute context** (`computeContext`) or **Optimization: job path** (`optimizeJobProgram`) is blank in the Options pane. |
| Optimize button disabled: *"At least N runs …"* | The prompt has fewer Best-Response runs than the minimum — run and mark more experiments. |
| Launch fails: *"is not a Job Execution job definition"* | `optimizeJobProgram` doesn't point at the imported job's Content path — see step 2. |
| SCR smoke test fails | The SCR endpoint/deployment type in the Options pane is wrong, the target container isn't running, or the key is invalid. |
