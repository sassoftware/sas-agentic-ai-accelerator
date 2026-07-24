# Prompt Optimization (DSPy)

Server side of the LLM Prompt Builder's **Optimize** feature: a SAS Job
Execution job that improves a Prompt Builder prompt with
[DSPy](https://dspy.ai), driven from the Builder's Optimize panel.

| File | Purpose |
| --- | --- |
| `Optimize-Prompt-DSPy.sas` | The job program (`proc python` + DSPy). Import it as a **Job Execution job definition**; its SAS Content path is what the Builder's `optimizeJobProgram` Option points at. |
| `requirements.txt` | Python packages the job's compute context must have installed (`dspy`, `requests`). |

## How it fits together

1. The Prompt Builder saves the prompt, then launches this job through the Job
   Execution REST API with `_contextName` set to the configured compute
   context.
2. The job reads the prompt's `Prompt-Experiment-Tracker.json` from SAS Model
   Manager. Runs with a **Best Response** become the training examples — the
   response the user vouched for is the reference answer.
3. Models are called through the **SCR endpoints** (the same governed
   containers the Builder uses) via a small `SCRLM` DSPy adapter speaking the
   SAS 3-input contract. Provider API keys come from a governed SAS
   library.table — only its *name* travels in the job request.
4. A DSPy optimizer (bootstrap few-shot, MIPROv2, or GEPA — which evolves the
   instruction from natural-language feedback, the judge's own reasoning when
   the metric is the judge) maximises the chosen metric (exact match, token
   overlap, or an LLM judge). The result is baked back into a Prompt-Builder
   prompt and recorded — with the per-example evaluations and a dataset
   snapshot — as an entry of the prompt's own
   `Prompt-Optimization-Tracker.json` (this job is that file's only writer;
   no additional Model Manager models are created).
5. The job reports milestones with `SAS.logMessage()`; the Builder polls the
   job and shows them live.

Setup, prerequisites and troubleshooting are documented in the Administration
Guide page **Enabling Prompt Optimization**.
