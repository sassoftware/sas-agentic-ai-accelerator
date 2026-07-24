/*********************************************************************************
    Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Optimize a Prompt Builder prompt with DSPy (Phase 3 of the prompt-judging
    roadmap). This program is deployed as a SAS Job Execution JOB DEFINITION and
    launched by the LLM Prompt Builder's Optimize button; it can also be run
    from SAS Studio by setting the macro variables below by hand.

    What it does (all inside the selected compute context):
      1. reads the prompt-test's Prompt-Experiment-Tracker.json from SAS Model
         Manager and turns the runs with a Best Response into training examples
         (inputs = the run's variable values, reference = the best response),
      2. reads the target LLM's options.json from its Model Manager model and
         calls the model through the SAS Container Runtime (SCR) endpoint with
         a small DSPy adapter (the same 3-input contract the Builder uses),
      3. runs a DSPy optimizer (bootstrap few-shot, or MIPROv2 which also
         rewrites the instruction text) against the chosen metric
         (exact match, token-overlap F1, or an LLM judge),
      4. bakes the optimised program back into a Prompt-Builder-shaped prompt
         and records the WHOLE run - optimised prompt, few-shot demos and the
         per-example before/after evaluations - as an entry of the prompt's
         own Prompt-Optimization-Tracker.json (next to its experiment tracker;
         this job is that file's only writer), plus a dataset snapshot file.
         No additional Model Manager models are created; the Builder shows the
         run history and loads a result back into the workbench on demand.

    Prerequisites (see the "Enabling Prompt Optimization" administration guide):
      - The compute context this job runs in must have a Python environment
        with the packages in requirements.txt (dspy >= 3.2.1, requests)
        installed. This is the most common failure mode - the job checks both
        the presence AND the version of dspy at startup and fails fast with a
        clear message (surfaced in the Prompt Builder's Optimize panel).
      - Provider API keys (for hosted models) must be in a governed SAS
        library.table with columns name + value; only the LIBRARY and TABLE
        names are passed to this job, never the keys.

    Parameters (passed by the Prompt Builder as Job Execution arguments; each
    arrives as a macro variable of the same name):
      promptModelId    - the prompt-test model being optimised (required)
      promptName       - its display name (required)
      targetModelId    - the LLM model in Model Manager to optimise for (required)
      targetModelName  - its SCR module/model name (required)
      scrEndpoint      - base URL of the SCR endpoint (required)
      deploymentType   - k8s (default) or aca
      datasetSource    - tracker (default, the prompt's experiment runs) or
                         cas (a governed CAS table built from the shipped
                         Create-Optimization-Dataset.sas template)
      casServer        - the CAS server the Builder browsed/validated the
                         dataset on (default cas-shared-default; recorded for
                         provenance - the compute session reads through its
                         own default CAS server, so in a multi-server
                         deployment the optimization context must connect to
                         the server that hosts the caslib)
      casLibrary       - the caslib holding the dataset when datasetSource=cas
      casTable         - the CAS table: one column per prompt variable plus a
                         response column with the reference answer
      metric           - exact (default), overlap (token-level F1) or judge
      judgeModelName   - the judge LLM name when metric=judge
      optimizer        - bootstrap (default, selects few-shot demos) or
                         miprov2 (additionally rewrites the instruction text;
                         needs more model calls)
      maxDemos         - max few-shot examples to select (default 4)
      minSamples       - minimum qualifying runs required (default 30)
      keyLibrary       - SAS library of the governed API-key table (optional)
      keyTable         - table in that library: columns name, value (optional)

    Progress is emitted with SAS.logMessage() and lands in the job log as
    "NOTE: Python-Subprocess - ..." lines; the Prompt Builder polls the log and
    shows the latest milestone while the job runs.
*********************************************************************************/

/* ---- Defaults for optional parameters (Job Execution only defines macro
       variables for arguments that were actually sent) ---- */
%macro _opt_default(name, value);
    %if %symexist(&name.) = 0 %then %do;
        %global &name.;
        %let &name. = &value.;
    %end;
    %else %if %superq(&name.) = %then %let &name. = &value.;
%mend _opt_default;

%_opt_default(deploymentType, k8s);
%_opt_default(datasetSource, tracker);
%_opt_default(metric, exact);
%_opt_default(judgeModelName, );
%_opt_default(optimizer, bootstrap);
%_opt_default(maxDemos, 4);
%_opt_default(minSamples, 30);
%_opt_default(keyLibrary, );
%_opt_default(keyTable, );
%_opt_default(casServer, cas-shared-default);
%_opt_default(casLibrary, );
%_opt_default(casTable, );
/* Set by SAS Job Execution on every job run (/jobExecution/jobs/<id>) and by
   the Builder-launched request respectively; blank when run interactively */
%_opt_default(SYS_JES_JOB_URI, );
%_opt_default(_contextName, );

/* Required parameters (promptModelId, promptName, targetModelId,
   targetModelName, scrEndpoint) are validated INSIDE the Python program, so a
   missing one fails through the same path as every other error - with a clear
   milestone and a failed tracker entry - instead of a SAS-side ABORT. */

/* Viya host for the Model Manager REST calls made from Python */
%let _opt_viyaHost = %sysfunc(getoption(SERVICESBASEURL));

/* Overall outcome flag the Python program sets; reported at the end. The job
   itself always completes - the outcome lives in the optimization-tracker
   entry the Prompt Builder reads (see the note at the end of this program) */
%let _opt_rc = 1;
%let _opt_error = The Python program did not run.;

/* ---- Provider API keys ----
   When a governed library.table was configured, export it to a JSON file in the
   job's work directory for the Python program to read. The values never appear
   in the log (proc json writes to the file only) and the file lives in WORK,
   which is destroyed with the session. */
%macro _opt_export_keys;
    %if %superq(keyLibrary) ne and %superq(keyTable) ne %then %do;
        %let _opt_cas_started = 0;
        %if %sysfunc(exist(&keyLibrary..&keyTable.)) = 0 %then %do;
            /* The accelerator's key table normally lives in CAS (see
               create-api-key-table.sas, caslib CASUSER): a fresh compute
               session has no CAS libraries assigned, so connect and assign
               them to make the table visible. Best effort. */
            cas _optcas;
            caslib _all_ assign sessref=_optcas;
            %let _opt_cas_started = 1;
        %end;
        %if %sysfunc(exist(&keyLibrary..&keyTable.)) %then %do;
            filename _optkeys "%sysfunc(pathname(work))/optimize_keys.json";
            proc json out=_optkeys noSASTags;
                export &keyLibrary..&keyTable.;
            run; quit;
            filename _optkeys clear;
        %end;
        %else %do;
            data _null_;
                putLog "WARNING: The API-key table &keyLibrary..&keyTable. does not exist - hosted models that need a key will fail.";
            run;
        %end;
        %if &_opt_cas_started. = 1 %then %do;
            cas _optcas terminate;
        %end;
    %end;
%mend _opt_export_keys;
%_opt_export_keys;

/* ---- CAS dataset source ----
   When datasetSource=cas, export the governed dataset table to a JSON file in
   WORK for the Python program (same pattern as the key export). The libname
   with an explicit caslib= avoids the 8-character truncation a blanket
   "caslib _all_ assign" would apply to longer caslib names. */
%macro _opt_export_dataset;
    %if %superq(datasetSource) = cas and %superq(casLibrary) ne and %superq(casTable) ne %then %do;
        cas _optdata;
        libname _optds cas caslib="&casLibrary." sessref=_optdata;
        %if %sysfunc(exist(_optds.&casTable.)) %then %do;
            filename _optdata "%sysfunc(pathname(work))/optimize_dataset.json";
            proc json out=_optdata noSASTags;
                export _optds.&casTable.;
            run; quit;
            filename _optdata clear;
        %end;
        %else %do;
            data _null_;
                putLog "WARNING: The CAS table &casLibrary..&casTable. is not available - the job will fail with a clear message.";
            run;
        %end;
        libname _optds clear;
        cas _optdata terminate;
    %end;
%mend _opt_export_dataset;
%_opt_export_dataset;

proc python restart;
    submit;
# ============================================================================
# DSPy prompt optimization. Everything below runs in the compute context's
# Python; SAS Viya REST calls authenticate with the session's service token
# (the same pattern the accelerator's Track-Prompt-Experiments.sas uses).
# ============================================================================
import inspect
import json
import os
import random
import re
import traceback
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace


def sas_safe(text):
    """Sanitize text that travels through SAS.logMessage/SAS.symput: both embed
    it into generated SAS statements, where an unbalanced quote or a macro
    trigger corrupts the code stream - a single apostrophe in an error message
    left the whole job stuck in 'running' with a compute segfault (verified
    live). Exception texts routinely contain quotes, so everything is cleaned."""
    cleaned = str(text)
    for bad, repl in (("'", "`"), ('"', "`"), ("%", "pct "), ("&", "+"), (";", ",")):
        cleaned = cleaned.replace(bad, repl)
    return cleaned


def progress(message):
    """Milestones for the Prompt Builder: NOTE: Python-Subprocess - <message>."""
    try:
        SAS.logMessage(sas_safe(message))
    except Exception:
        print(str(message))


# ---- Parameters ------------------------------------------------------------
P = {
    name: (SAS.symget(name) if SAS.symget(name) is not None else "")
    for name in [
        "promptModelId", "promptName", "targetModelId", "targetModelName",
        "scrEndpoint", "deploymentType", "datasetSource", "metric",
        "judgeModelName", "optimizer", "maxDemos", "minSamples",
        "casServer", "casLibrary", "casTable",
    ]
}
P = {k: str(v).strip() for k, v in P.items()}
BASE = str(SAS.symget("_opt_viyaHost") or "").rstrip("/")
TOKEN = os.environ.get("SAS_SERVICES_TOKEN", "")
VERIFY = os.environ.get("SSLCALISTLOC") or os.environ.get("CAS_CLIENT_SSL_CA_LIST") or True
MAX_DEMOS = max(0, min(16, int(P["maxDemos"] or "4")))
MIN_SAMPLES = max(1, int(P["minSamples"] or "30"))
WORKPATH = SAS.workpath if SAS.workpath.endswith(os.sep) else SAS.workpath + os.sep


def fail(message):
    SAS.symput("_opt_error", sas_safe(message)[:500])
    raise RuntimeError(str(message))


# ---- Dependency preflight (the #1 deployment issue) ------------------------
# Imported lazily from inside the main try/except so a missing or outdated
# package fails the run through the SAME path as every other error: a clear
# "Optimization failed: ..." milestone (which the Builder shows live), a
# failed optimization-tracker entry, and a failed Job Execution state.
# The minimum is the version the SCRLM adapter is validated against - older
# releases differ in the BaseLM response contract (e.g. how usage is read).
MIN_DSPY_VERSION = (3, 2, 1)
requests = None
dspy = None


def import_dependencies():
    global requests, dspy
    try:
        import requests as requests_module
    except Exception:
        fail("The Python environment of this compute context lacks the requests package - install the packages in Prompt-Optimization/requirements.txt.")
    requests = requests_module
    try:
        import dspy as dspy_module
    except Exception:
        fail("The Python environment of this compute context lacks the dspy package - install the packages in Prompt-Optimization/requirements.txt into that Python environment, or point the computeContext Option at a prepared context.")
    dspy = dspy_module
    raw_version = str(getattr(dspy_module, "__version__", "0"))
    version = tuple(int(part) for part in re.findall(r"\d+", raw_version))[:3]
    if (version + (0, 0, 0))[:3] < MIN_DSPY_VERSION:
        minimum = ".".join(str(part) for part in MIN_DSPY_VERSION)
        fail(f"dspy {raw_version} is too old - the optimization job is validated against dspy >= {minimum}. Update the Python environment of the compute context (see Prompt-Optimization/requirements.txt).")
    if P["optimizer"] == "miprov2":
        # dspy treats optuna (MIPROv2's trial search) as an optional extra, so
        # its absence only surfaces mid-run - verified live. Gate it up front.
        try:
            import optuna  # noqa: F401
        except Exception:
            fail("The MIPROv2 optimizer needs the optuna package - install it into the compute context Python (see Prompt-Optimization/requirements.txt) or choose the bootstrap optimizer.")


# ---- SAS Viya REST helpers -------------------------------------------------
def viya(method, path, expect_json=True, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("Authorization", "Bearer " + TOKEN)
    response = requests.request(method, BASE + path, headers=headers, verify=VERIFY, timeout=120, **kwargs)
    if response.status_code >= 400:
        fail(f"Viya request {method} {path} failed with HTTP {response.status_code}: {response.text[:300]}")
    return response.json() if expect_json and response.text else response


def get_model_contents(model_id):
    return viya("GET", f"/modelRepository/models/{model_id}/contents?limit=100").get("items", [])


def read_content_json(item):
    file_uri = item.get("fileUri") or ""
    if not file_uri:
        fail(f"Model content {item.get('name')} has no fileUri.")
    response = viya("GET", file_uri + "/content", expect_json=False)
    return json.loads(response.text)


def add_model_content(model_id, name, payload):
    data = json.dumps(payload, indent=2)
    response = requests.post(
        BASE + f"/modelRepository/models/{model_id}/contents",
        headers={"Authorization": "Bearer " + TOKEN},
        files={"files": (name, data.encode("utf-8"), "application/json")},
        verify=VERIFY, timeout=120,
    )
    if response.status_code >= 400:
        fail(f"Uploading {name} to model {model_id} failed with HTTP {response.status_code}: {response.text[:300]}")


def replace_model_content(model_id, name, payload):
    for item in get_model_contents(model_id):
        if item.get("name") == name and item.get("id"):
            viya("DELETE", f"/modelRepository/models/{model_id}/contents/{item['id']}", expect_json=False)
    add_model_content(model_id, name, payload)


# ---- Provider keys (exported by the SAS wrapper, never logged) -------------
def load_key_map():
    """Provider name -> key. Accepts both column conventions: KeyName/KeyValue
    (the accelerator's create-api-key-table.sas) and name/value."""
    key_path = WORKPATH + "optimize_keys.json"
    if not os.path.exists(key_path):
        return {}
    with open(key_path, "r", encoding="utf-8") as key_file:
        rows = json.load(key_file)
    key_map = {}
    for row in rows if isinstance(rows, list) else []:
        lowered = {str(k).lower(): v for k, v in row.items()}
        name = lowered.get("keyname") or lowered.get("name")
        value = lowered.get("keyvalue") or lowered.get("value")
        if name and value:
            key_map[str(name).strip()] = str(value).strip()
    return key_map


# ---- SCR access (mirrors the browser's callSCRLLM) -------------------------
def scr_url(model_name):
    if P["deploymentType"] == "aca":
        return f"https://{model_name.replace('_', '-')}.{P['scrEndpoint']}/{model_name}"
    return f"{P['scrEndpoint']}/{model_name}/{model_name}"


# Per-role call accounting from the SCR responses (the SAS contract returns
# prompt_length/output_length/run_time with every call): recorded in the
# tracker entry so the Builder can show how many calls a run actually made
# and estimate what it cost.
USAGE = {
    "target": {"calls": 0, "promptTokens": 0.0, "outputTokens": 0.0, "runTime": 0.0},
    "judge": {"calls": 0, "promptTokens": 0.0, "outputTokens": 0.0, "runTime": 0.0},
}


def usage_snapshot():
    return {
        role: {
            "calls": bucket["calls"],
            "promptTokens": int(round(bucket["promptTokens"])),
            "outputTokens": int(round(bucket["outputTokens"])),
            "runTime": round(bucket["runTime"], 1),
        }
        for role, bucket in USAGE.items()
    }


def call_scr(model_name, options, system_prompt, user_prompt, role="target"):
    # The containers expect the options as a single unquoted {k:v,...} string -
    # the SAS 3-input contract, including API_KEY when the model needs one.
    options_string = "{" + ",".join(f"{k}:{v}" for k, v in options.items()) + "}"
    body = {"inputs": [
        {"name": "systemPrompt", "value": system_prompt},
        {"name": "userPrompt", "value": user_prompt},
        {"name": "options", "value": options_string},
    ]}
    response = requests.post(scr_url(model_name), json=body, verify=VERIFY, timeout=600)
    if response.status_code != 200:
        raise RuntimeError(f"SCR call to {model_name} failed with HTTP {response.status_code}: {response.text[:300]}")
    payload = response.json()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    bucket = USAGE["judge" if role == "judge" else "target"]
    bucket["calls"] += 1
    for field, target_key in (("prompt_length", "promptTokens"), ("output_length", "outputTokens"), ("run_time", "runTime")):
        try:
            bucket[target_key] += float(data.get(field) or 0)
        except Exception:
            pass
    if data.get("error"):
        raise RuntimeError(f"SCR call to {model_name} returned an error: {data['error']}")
    return str(data.get("response") or "")


def build_model_options(model_id, model_name, key_map):
    options_item = next((i for i in get_model_contents(model_id) if i.get("name") == "options.json"), None)
    if options_item is None:
        fail(f"The model {model_name} has no options.json - is it an accelerator LLM definition?")
    raw = read_content_json(options_item)
    options = {}
    for name, spec in raw.items():
        default = spec.get("default") if isinstance(spec, dict) else spec
        options[name] = default
    if "API_KEY" in options:
        provider = str(options["API_KEY"])
        key = key_map.get(provider)
        if not key:
            fail(f"The model {model_name} needs an API key for provider {provider} but the governed key table has none - add it or configure optimizeKeyLibrary/optimizeKeyTable.")
        options["API_KEY"] = key
    return options


def build_scrlm_class():
    """Build the SCRLM adapter class. Wrapped in a factory because it
    subclasses dspy.BaseLM, which only exists after the dependency preflight
    imported dspy - a module-level class statement would crash before the
    friendly missing-dspy error could be produced."""

    class SCRLM(dspy.BaseLM):
        """DSPy LM adapter for the SAS Container Runtime LLM containers.

        The SCR API is the SAS 3-input contract (systemPrompt/userPrompt/
        options), not OpenAI - so forward() folds the chat messages into those
        two prompts and wraps the container's text answer in the OpenAI-ish
        shape BaseLM expects. Same URL forms as the browser (k8s vs aca).
        """

        def __init__(self, model_name, options):
            super().__init__(model=model_name)
            self.scr_options = dict(options)

        def forward(self, prompt=None, messages=None, **kwargs):
            chat = messages or [{"role": "user", "content": prompt or ""}]
            system_prompt = "\n".join(str(m.get("content", "")) for m in chat if m.get("role") == "system")
            user_prompt = "\n\n".join(str(m.get("content", "")) for m in chat if m.get("role") != "system")
            text = call_scr(self.model, self.scr_options, system_prompt, user_prompt)
            # usage must be a plain dict: dspy's BaseLM does dict(response.usage)
            # (verified against dspy 3.2.1 — a SimpleNamespace raises TypeError).
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=text, tool_calls=None),
                    finish_reason="stop",
                )],
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                model=self.model,
            )

    return SCRLM


# ---- Dataset: the prompt's experiment tracker ------------------------------
def load_tracker_dataset(prompt_model_id):
    """Best-Response runs -> (examples, latest prompt, source variables).

    The tracker is the flat PETRow list the Builder saves: one header row per
    run (model == '') carrying the prompts + variables, then one row per model.
    A run qualifies when a model row has best_prompt == 1; its response is the
    human-vouched reference the optimisation targets.
    """
    tracker_item = next(
        (i for i in get_model_contents(prompt_model_id) if i.get("name") == "Prompt-Experiment-Tracker.json"), None)
    if tracker_item is None:
        fail("The prompt has no saved Prompt-Experiment-Tracker.json - run and save experiments first.")
    rows = read_content_json(tracker_item)
    runs = {}
    for row in rows if isinstance(rows, list) else []:
        runs.setdefault(row.get("runId"), []).append(row)

    examples, latest_header = [], None
    for run_id in sorted(k for k in runs if k is not None):
        header = next((r for r in runs[run_id] if not r.get("model")), None)
        best = next((r for r in runs[run_id] if r.get("model") and r.get("best_prompt") in (1, True)), None)
        if header is not None:
            latest_header = header
        if header is None or best is None:
            continue
        variables = header.get("variables") or []
        inputs = {v["name"]: str(v.get("value") or "") for v in variables if v.get("name")}
        if not inputs:
            # Runs made without the variables manager: the whole user prompt is
            # the varying input.
            inputs = {"userPrompt": str(header.get("userPrompt") or "")}
        examples.append({"inputs": inputs, "response": str(best.get("response") or "")})
    if latest_header is None:
        fail("The experiment tracker holds no runs.")
    return examples, latest_header


# ---- Dataset: a governed CAS table (exported to WORK by the SAS wrapper) ---
def load_cas_dataset(source_header):
    """Rows of the exported CAS table -> examples. The PROMPT itself (system/
    user template + variables) still comes from the experiment tracker header -
    the Builder saves the prompt right before launching, so at least one saved
    run must exist. The table needs one column per prompt variable (or a
    userPrompt column when the prompt has no variables) plus a response column
    holding the reference answer - the shipped Create-Optimization-Dataset.sas
    template builds exactly that schema."""
    dataset_path = WORKPATH + "optimize_dataset.json"
    if not os.path.exists(dataset_path):
        fail(f"The CAS table {P['casLibrary']}.{P['casTable']} could not be read - make sure it exists and is loaded into memory (build it with the Create-Optimization-Dataset.sas template).")
    with open(dataset_path, "r", encoding="utf-8") as dataset_file:
        rows = json.load(dataset_file)
    if not isinstance(rows, list) or not rows:
        fail(f"The CAS table {P['casLibrary']}.{P['casTable']} is empty.")
    variables = source_header.get("variables") or []
    input_columns = [str(v.get("name")) for v in variables if v.get("name")] or ["userPrompt"]
    first = {str(k).casefold() for k in rows[0]}
    missing = [c for c in input_columns + ["response"] if c.casefold() not in first]
    if missing:
        fail("The CAS table lacks required columns: " + ", ".join(missing) + ". Expected one column per prompt variable plus response - see Create-Optimization-Dataset.sas.")
    examples = []
    for row in rows:
        ci = {str(k).casefold(): ("" if v is None else str(v)) for k, v in row.items()}
        response = ci.get("response", "").strip()
        if not response:
            continue
        examples.append({
            "inputs": {name: ci.get(name.casefold(), "") for name in input_columns},
            "response": response,
        })
    if not examples:
        fail(f"No row of {P['casLibrary']}.{P['casTable']} has a response value.")
    return examples


# ---- Metrics ---------------------------------------------------------------
def normalise(text):
    """Whitespace-collapsed, casefolded, and stripped of surrounding
    punctuation/markup - so a model answering "Cold." or "**cold**" still
    exact-matches the reference "cold"."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return cleaned.strip(".!?,;:*\"'` ")


def exact_metric(example, prediction, trace=None):
    return normalise(getattr(prediction, "response", "")) == normalise(example.response)


OVERLAP_PASS = 0.75


def overlap_metric(example, prediction, trace=None):
    """Token-level F1 between the prediction and the reference (SQuAD style) -
    partial credit for answers that are close but not verbatim, sitting between
    exact-match (too strict for chatty models) and the judge (needs an extra
    LLM). During optimisation (trace is not None) DSPy needs a pass/fail
    decision for demo selection, so a high-overlap threshold applies."""
    pred_tokens = normalise(getattr(prediction, "response", "")).split()
    ref_tokens = normalise(example.response).split()
    if not pred_tokens or not ref_tokens:
        score = 1.0 if pred_tokens == ref_tokens else 0.0
    else:
        common = Counter(pred_tokens) & Counter(ref_tokens)
        overlap = sum(common.values())
        if overlap == 0:
            score = 0.0
        else:
            precision = overlap / len(pred_tokens)
            recall = overlap / len(ref_tokens)
            score = 2 * precision * recall / (precision + recall)
    if trace is not None:
        return score >= OVERLAP_PASS
    return score


def make_judge_metric(judge_lm_options, task_system_prompt=""):
    # The same rubric the Prompt Builder's Phase-1 judge uses (accuracy,
    # relevance to the task, completeness, clarity; ignore length/formatting)
    # - and like that judge, this one SEES THE TASK: equivalence judged blind
    # to the question over-credits generic answers.
    judge_system = (
        "You are an impartial evaluator. You will see the TASK an AI assistant "
        "was given, a REFERENCE answer a human vouched for, and a CANDIDATE "
        "answer. Judge whether the candidate conveys the same answer as the "
        "reference for this task, weighing accuracy, relevance to the task, "
        "completeness, and clarity. Ignore differences in length, wording and "
        "formatting except where they change the meaning. Think step by step, "
        'then return ONLY a JSON object: {"reasoning": "...", "equivalent": true|false}'
    )

    def judge_metric(example, prediction, trace=None):
        task_lines = [f"{name}: {example[name]}" for name in example.inputs().keys()]
        user = (
            "== TASK ==\n" + (str(task_system_prompt) or "(none)") +
            "\n\n== TASK INPUTS ==\n" + "\n".join(task_lines) +
            "\n\n== REFERENCE ==\n" + str(example.response) +
            "\n\n== CANDIDATE ==\n" + str(getattr(prediction, "response", "")) +
            "\n\nReturn the JSON object now."
        )
        try:
            raw = call_scr(P["judgeModelName"], judge_lm_options, judge_system, user, role="judge")
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            verdict = json.loads(match.group(0)) if match else {}
            return bool(verdict.get("equivalent"))
        except Exception:
            return False

    return judge_metric


def evaluate(program, dataset, metric, pass_threshold=1.0):
    """Score a program on a dataset. Returns (score, per-example details) so
    the tracker entry can show WHICH validation examples improved - the
    per-example before/after view is the evolution display the Builder
    renders (the same idea as MLflow's per-example DSPy evaluation traces).
    The aggregate is the mean per-example score; boolean metrics contribute
    0/1 while the overlap metric contributes partial credit, with
    pass_threshold deciding what counts as correct in the evolution view."""
    if not dataset:
        return 0.0, []
    total = 0.0
    details = []
    for example in dataset:
        prediction = None
        try:
            prediction = program(**example.inputs())
            response_text = str(getattr(prediction, "response", ""))
        except Exception as call_error:
            response_text = f"(call failed: {call_error})"
        score = 0.0
        if prediction is not None:
            try:
                score = float(metric(example, prediction))
            except Exception:
                score = 0.0
        total += score
        details.append({
            "inputs": {k: str(example[k]) for k in example.inputs().keys()},
            "expected": str(example.response),
            "response": response_text,
            "correct": score >= pass_threshold,
            "score": round(score, 3),
        })
    return total / len(dataset), details


# ---- Bake-out: DSPy program -> Prompt-Builder-shaped prompt ----------------
def bake_out(compiled, source_header, input_names):
    predictor = compiled.predictors()[0]
    instructions = str(getattr(predictor.signature, "instructions", "") or "")
    demo_rows = []
    demo_blocks = []
    for demo in getattr(predictor, "demos", []) or []:
        demo_dict = dict(demo) if not isinstance(demo, dict) else demo
        demo_rows.append({name: str(demo_dict.get(name, "")) for name in input_names + ["response"]})
        lines = [f"{name}: {demo_dict.get(name, '')}" for name in input_names]
        lines.append(f"response: {demo_dict.get('response', '')}")
        demo_blocks.append("\n".join(lines))
    system_prompt = instructions
    if demo_blocks:
        system_prompt += (
            "\n\nFollow the pattern of these examples:\n\n" + "\n\n---\n\n".join(demo_blocks)
        )
    return {
        "systemPrompt": system_prompt,
        "userPrompt": str(source_header.get("userPrompt") or ""),
        "variables": source_header.get("variables") or [],
        "demos": demo_rows,
    }


# ---- Write-back ------------------------------------------------------------
# Everything a run produces stays ON the source prompt-test, next to its
# Prompt-Experiment-Tracker.json: the run entry (with the optimised prompt,
# demos and per-example evaluations) goes into Prompt-Optimization-Tracker.json
# and the exact dataset into a snapshot file. No extra Model Manager models are
# created - the Builder renders the history and loads a result back into the
# workbench as an experiment.
def append_optimization_tracker(entry, dataset_snapshot=None):
    """Append one run entry (this job is the tracker's only writer). When a
    dataset snapshot is given it is uploaded first, named after the new
    optimizationId, so the stored entry always references it."""
    existing = []
    tracker_item = next(
        (i for i in get_model_contents(P["promptModelId"]) if i.get("name") == "Prompt-Optimization-Tracker.json"), None)
    if tracker_item is not None:
        try:
            existing = read_content_json(tracker_item)
        except Exception:
            existing = []
    if not isinstance(existing, list):
        existing = []
    entry["optimizationId"] = 1 + max([int(e.get("optimizationId") or 0) for e in existing] or [0])
    if dataset_snapshot is not None:
        snapshot_name = f"Prompt-Optimization-Dataset-{entry['optimizationId']}.json"
        replace_model_content(P["promptModelId"], snapshot_name, dataset_snapshot)
        entry["datasetSnapshot"] = snapshot_name
    existing.append(entry)
    replace_model_content(P["promptModelId"], "Prompt-Optimization-Tracker.json", existing)
    return entry["optimizationId"]


# ---- Main ------------------------------------------------------------------
# The Builder matches its launched job to the tracker entry by this id.
job_uri = str(SAS.symget("SYS_JES_JOB_URI") or "")
job_id = job_uri.rstrip("/").split("/")[-1] if job_uri else ""
started_at = datetime.now(timezone.utc).isoformat()
tracker_entry = {
    "startedAt": started_at, "finishedAt": None, "status": "failed",
    "jobId": job_id, "targetModel": P["targetModelName"],
    "computeContext": str(SAS.symget("_contextName") or ""),
    "datasetSource": P["datasetSource"], "datasetRef": "Prompt-Experiment-Tracker.json",
    "sampleCount": 0, "optimizer": P["optimizer"], "metric": P["metric"],
    "judgeModel": P["judgeModelName"] or None,
    "metricBefore": None, "metricAfter": None,
    "baselinePrompt": None, "optimizedPrompt": None,
    "trainSize": None, "validationSize": None, "evaluations": None,
    "datasetSnapshot": None, "error": None,
}

def main():
    # The whole flow lives inside ONE function so its try/except is
    # compiled as a single unit: proc python executes the submit block
    # incrementally, and a large top-level try/except can be split so the
    # except never guards the body - an unhandled failure-path exception
    # then crashed the compute session (verified live: Segmentation
    # Violation in the log, job stuck in running).
    try:
        missing_params = [name for name in
                          ("promptModelId", "promptName", "targetModelId", "targetModelName", "scrEndpoint")
                          if not P[name]]
        if missing_params:
            fail("Required job parameters missing: " + ", ".join(missing_params))
        import_dependencies()
        SCRLM = build_scrlm_class()
        key_map = load_key_map()
        progress("Loading the experiment tracker dataset")
        examples_raw, source_header = load_tracker_dataset(P["promptModelId"])
        if P["datasetSource"] == "cas":
            progress(f"Loading the CAS dataset {P['casLibrary']}.{P['casTable']}")
            examples_raw = load_cas_dataset(source_header)
            tracker_entry["datasetRef"] = f"{P['casLibrary']}.{P['casTable']}"
            tracker_entry["casServer"] = P["casServer"]
        tracker_entry["sampleCount"] = len(examples_raw)
        if len(examples_raw) < MIN_SAMPLES:
            fail(f"Only {len(examples_raw)} runs have a Best Response - at least {MIN_SAMPLES} are required.")
        progress(f"Dataset loaded ({len(examples_raw)} examples)")

        input_names = sorted({name for example in examples_raw for name in example["inputs"]})
        examples = [
            dspy.Example(response=example["response"], **example["inputs"]).with_inputs(*example["inputs"].keys())
            for example in examples_raw
        ]
        rng = random.Random(42)
        rng.shuffle(examples)
        validation_size = max(1, len(examples) // 5)
        valset, trainset = examples[:validation_size], examples[validation_size:]

        progress("Preparing the target model")
        target_options = build_model_options(P["targetModelId"], P["targetModelName"], key_map)
        target_lm = SCRLM(P["targetModelName"], target_options)
        # Smoke test - fail fast on a bad endpoint or key before spending calls.
        call_scr(P["targetModelName"], target_options, "You are a health check.", "Reply with OK.")
        dspy.configure(lm=target_lm)

        if P["metric"] == "judge":
            if not P["judgeModelName"]:
                fail("metric=judge requires judgeModelName.")
            judge_model = next(
                (i for i in viya("GET", "/modelRepository/models?filter=eq(name,'" + P["judgeModelName"] + "')").get("items", [])), None)
            if judge_model is None:
                fail(f"The judge model {P['judgeModelName']} was not found in Model Manager.")
            judge_options = build_model_options(judge_model["id"], P["judgeModelName"], key_map)
            metric = make_judge_metric(judge_options, str(source_header.get("systemPrompt") or ""))
            pass_threshold = 1.0
        elif P["metric"] == "overlap":
            metric = overlap_metric
            pass_threshold = OVERLAP_PASS
        else:
            metric = exact_metric
            pass_threshold = 1.0

        signature_str = ", ".join(input_names) + " -> response"
        signature = dspy.Signature(signature_str, str(source_header.get("systemPrompt") or ""))
        program = dspy.Predict(signature)

        progress("Scoring the baseline prompt")
        metric_before, eval_before = evaluate(program, valset, metric, pass_threshold)
        tracker_entry["metricBefore"] = metric_before
        tracker_entry["baselinePrompt"] = {
            "systemPrompt": str(source_header.get("systemPrompt") or ""),
            "userPrompt": str(source_header.get("userPrompt") or ""),
        }
        tracker_entry["trainSize"] = len(trainset)
        tracker_entry["validationSize"] = len(valset)
        progress(f"Baseline metric: {metric_before:.3f}")

        if metric_before >= 1.0 - 1e-9:
            # A perfect baseline leaves the optimizer no gradient: every
            # candidate scores at most 1.0 on the validation split, so the
            # search cannot distinguish better from worse and the calls are
            # wasted. Record the run (baseline == optimised) and say what
            # would give the optimizer room instead.
            progress("Baseline already scores perfectly on the validation split - skipping optimization. Add harder examples or a stricter metric to give the optimizer room to improve.")
            tracker_entry["metricAfter"] = metric_before
            tracker_entry["evaluations"] = [
                {
                    "inputs": detail["inputs"],
                    "expected": detail["expected"],
                    "baselineResponse": detail["response"],
                    "baselineCorrect": detail["correct"],
                    "baselineScore": detail["score"],
                    "optimizedResponse": detail["response"],
                    "optimizedCorrect": detail["correct"],
                    "optimizedScore": detail["score"],
                }
                for detail in eval_before
            ]
            tracker_entry["optimizedPrompt"] = bake_out(program, source_header, input_names)
            tracker_entry["skippedReason"] = "baseline-perfect"
            tracker_entry["usage"] = usage_snapshot()
            tracker_entry["status"] = "succeeded"
            tracker_entry["finishedAt"] = datetime.now(timezone.utc).isoformat()
            append_optimization_tracker(tracker_entry, dataset_snapshot=examples_raw)
            progress(f"Done - baseline metric {metric_before:.3f} needs no optimization, run recorded on the prompt")
            SAS.symput("_opt_rc", "0")
            return

        progress(f"Optimising with {P['optimizer']} ({len(trainset)} training examples)")
        if P["optimizer"] == "miprov2":
            # MIPROv2 proposes and trials candidate INSTRUCTIONS on top of
            # demo selection - the baked system prompt can differ from the
            # baseline. auto=light keeps the trial count sane; num_threads=1
            # keeps the SCR calls sequential inside proc python; minibatch
            # evaluation is disabled because the validation split is smaller
            # than MIPROv2's default minibatch size. The keyword filtering
            # tolerates signature drift across dspy releases (validated
            # against 3.2.1).
            init_params = inspect.signature(dspy.MIPROv2.__init__).parameters
            init_kwargs = {
                "metric": metric, "auto": "light",
                "max_bootstrapped_demos": MAX_DEMOS, "max_labeled_demos": MAX_DEMOS,
                "num_threads": 1,
            }
            optimizer = dspy.MIPROv2(**{k: v for k, v in init_kwargs.items() if k in init_params})
            compile_params = inspect.signature(optimizer.compile).parameters
            compile_kwargs = {
                "trainset": trainset, "valset": valset,
                "minibatch": False, "requires_permission_to_run": False,
            }
            compiled = optimizer.compile(
                program, **{k: v for k, v in compile_kwargs.items() if k in compile_params})
        else:
            optimizer = dspy.BootstrapFewShot(
                metric=metric, max_bootstrapped_demos=MAX_DEMOS, max_labeled_demos=MAX_DEMOS)
            compiled = optimizer.compile(program, trainset=trainset)

        progress("Scoring the optimised prompt")
        metric_after, eval_after = evaluate(compiled, valset, metric, pass_threshold)
        tracker_entry["metricAfter"] = metric_after
        # Per-validation-example before/after - the evolution view the Builder
        # renders (which examples the optimisation actually fixed or broke).
        tracker_entry["evaluations"] = [
            {
                "inputs": before_detail["inputs"],
                "expected": before_detail["expected"],
                "baselineResponse": before_detail["response"],
                "baselineCorrect": before_detail["correct"],
                "baselineScore": before_detail["score"],
                "optimizedResponse": after_detail["response"],
                "optimizedCorrect": after_detail["correct"],
                "optimizedScore": after_detail["score"],
            }
            for before_detail, after_detail in zip(eval_before, eval_after)
        ]
        progress(f"Optimised metric: {metric_after:.3f}")

        progress("Baking the optimised program into a prompt")
        baked = bake_out(compiled, source_header, input_names)
        tracker_entry["optimizedPrompt"] = baked

        progress(f"Model calls made: {USAGE['target']['calls']} target, {USAGE['judge']['calls']} judge")
        progress("Writing the results back to SAS Model Manager")
        # Snapshot the exact examples optimised on (provenance) and record the
        # whole run - optimised prompt, demos, per-example evaluations - as an
        # entry of the prompt's own optimization tracker. No new models.
        tracker_entry["usage"] = usage_snapshot()
        tracker_entry["status"] = "succeeded"
        tracker_entry["finishedAt"] = datetime.now(timezone.utc).isoformat()
        append_optimization_tracker(tracker_entry, dataset_snapshot=examples_raw)

        progress(f"Done - metric {metric_before:.3f} -> {metric_after:.3f}, run recorded on the prompt")
        SAS.symput("_opt_rc", "0")
    except Exception as error:
        error_text = str(error) or error.__class__.__name__
        progress(f"Optimization failed: {error_text}")
        print(traceback.format_exc())
        tracker_entry["status"] = "failed"
        tracker_entry["finishedAt"] = datetime.now(timezone.utc).isoformat()
        tracker_entry["error"] = error_text[:500]
        # Even a failed run spent calls - record them for the panel.
        tracker_entry["usage"] = usage_snapshot()
        # Best effort: record the failure for the Builder's result panel. Skipped
        # only when even requests could not be imported (nothing can reach Viya).
        if requests is not None:
            try:
                append_optimization_tracker(tracker_entry)
            except Exception:
                pass
        SAS.symput("_opt_error", sas_safe(error_text)[:500])


main()
endsubmit;
run; quit;

proc python terminate;
run; quit;

/* ---- Propagate the outcome to Job Execution ----
   The job ALWAYS ends normally: raising a SAS error condition (any ABORT
   flavor, or a genuine runtime ERROR after proc python has run) leaves the
   compute session "stopped" without Job Execution ever receiving its
   completion handshake - the job then shows "running" forever and the dead
   server keeps counting against the context's server limit until the session
   is deleted (all verified live). The OUTCOME therefore lives in the
   Prompt-Optimization-Tracker entry (status succeeded/failed + error), which
   the Prompt Builder reads when the job completes; the log keeps a plain
   ERROR line for administrators. */
data _null_;
    if "&_opt_rc." ne "0" then
        putLog "ERROR: Prompt optimization failed: %superq(_opt_error)";
    else
        putLog "NOTE: Prompt optimization succeeded.";
run;
