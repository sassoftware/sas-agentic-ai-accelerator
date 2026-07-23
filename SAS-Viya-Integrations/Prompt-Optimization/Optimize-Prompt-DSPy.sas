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
      3. runs a DSPy optimizer (bootstrap few-shot) against the chosen metric
         (exact match or an LLM judge),
      4. bakes the optimised program back into a Prompt-Builder-shaped prompt,
         writes it as a NEW prompt-test next to the original, snapshots the
         dataset it optimised on, and appends an entry to the source prompt's
         Prompt-Optimization-Tracker.json (this job is that file's only writer).

    Prerequisites (see the "Enabling Prompt Optimization" administration guide):
      - The compute context this job runs in must have a Python environment
        with the packages in requirements.txt (dspy, requests) installed.
        This is the most common failure mode - the job fails fast with a clear
        message when dspy is missing.
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
      datasetSource    - tracker (the only source in this release)
      metric           - exact (default) or judge
      judgeModelName   - the judge LLM name when metric=judge
      optimizer        - bootstrap (the only optimizer in this release)
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
/* Set by SAS Job Execution on every job run (/jobExecution/jobs/<id>) and by
   the Builder-launched request respectively; blank when run interactively */
%_opt_default(SYS_JES_JOB_URI, );
%_opt_default(_contextName, );

/* ---- Required parameters ---- */
%macro _opt_require(name);
    %if %symexist(&name.) = 0 %then %do;
        data _null_;
            putLog "ERROR: The required job parameter &name. was not provided.";
            abort abend 42;
        run;
    %end;
%mend _opt_require;

%_opt_require(promptModelId);
%_opt_require(promptName);
%_opt_require(targetModelId);
%_opt_require(targetModelName);
%_opt_require(scrEndpoint);

/* Viya host for the Model Manager REST calls made from Python */
%let _opt_viyaHost = %sysfunc(getoption(SERVICESBASEURL));

/* Overall outcome flag the Python program sets; checked at the end so a failed
   optimisation fails the Job Execution job (state=failed in the Builder) */
%let _opt_rc = 1;
%let _opt_error = The Python program did not run.;

/* ---- Provider API keys ----
   When a governed library.table was configured, export it to a JSON file in the
   job's work directory for the Python program to read. The values never appear
   in the log (proc json writes to the file only) and the file lives in WORK,
   which is destroyed with the session. */
%macro _opt_export_keys;
    %if %superq(keyLibrary) ne and %superq(keyTable) ne %then %do;
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
    %end;
%mend _opt_export_keys;
%_opt_export_keys;

proc python restart;
    submit;
# ============================================================================
# DSPy prompt optimization. Everything below runs in the compute context's
# Python; SAS Viya REST calls authenticate with the session's service token
# (the same pattern the accelerator's Track-Prompt-Experiments.sas uses).
# ============================================================================
import json
import os
import random
import re
import traceback
from datetime import datetime, timezone
from types import SimpleNamespace


def progress(message):
    """Milestones for the Prompt Builder: NOTE: Python-Subprocess - <message>."""
    try:
        SAS.logMessage(str(message))
    except Exception:
        print(str(message))


# ---- Parameters ------------------------------------------------------------
P = {
    name: (SAS.symget(name) if SAS.symget(name) is not None else "")
    for name in [
        "promptModelId", "promptName", "targetModelId", "targetModelName",
        "scrEndpoint", "deploymentType", "datasetSource", "metric",
        "judgeModelName", "optimizer", "maxDemos", "minSamples",
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
    SAS.symput("_opt_error", str(message)[:500])
    raise RuntimeError(str(message))


# ---- Fail fast when dspy is missing (the #1 deployment issue) --------------
try:
    import requests
except Exception:
    fail("The Python environment of this compute context lacks the requests package - install the packages in Prompt-Optimization/requirements.txt.")
try:
    import dspy
except Exception:
    fail("The Python environment of this compute context lacks the dspy package - install the packages in Prompt-Optimization/requirements.txt into the context's Python, or point the computeContext Option at a prepared context.")


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
    key_path = WORKPATH + "optimize_keys.json"
    if not os.path.exists(key_path):
        return {}
    with open(key_path, "r", encoding="utf-8") as key_file:
        rows = json.load(key_file)
    key_map = {}
    for row in rows if isinstance(rows, list) else []:
        lowered = {str(k).lower(): v for k, v in row.items()}
        name, value = lowered.get("name"), lowered.get("value")
        if name and value:
            key_map[str(name).strip()] = str(value).strip()
    return key_map


# ---- SCR access (mirrors the browser's callSCRLLM) -------------------------
def scr_url(model_name):
    if P["deploymentType"] == "aca":
        return f"https://{model_name.replace('_', '-')}.{P['scrEndpoint']}/{model_name}"
    return f"{P['scrEndpoint']}/{model_name}/{model_name}"


def call_scr(model_name, options, system_prompt, user_prompt):
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
            fail(f"The model {model_name} needs an API key for provider '{provider}' but the governed key table has none - add it or configure optimizeKeyLibrary/optimizeKeyTable.")
        options["API_KEY"] = key
    return options


class SCRLM(dspy.BaseLM):
    """DSPy LM adapter for the SAS Container Runtime LLM containers.

    The SCR API is the SAS 3-input contract (systemPrompt/userPrompt/options),
    not OpenAI - so forward() folds the chat messages into those two prompts
    and wraps the container's text answer in the OpenAI-ish shape BaseLM
    expects. Same URL forms as the browser (k8s path vs aca host).
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


# ---- Metrics ---------------------------------------------------------------
def normalise(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def exact_metric(example, prediction, trace=None):
    return normalise(getattr(prediction, "response", "")) == normalise(example.response)


def make_judge_metric(judge_lm_options):
    judge_system = (
        "You are an impartial evaluator. You will see a reference answer and a "
        "candidate answer. Decide whether the candidate conveys the same answer "
        "as the reference - the wording may differ. Think step by step, then "
        'return ONLY a JSON object: {"reasoning": "...", "equivalent": true|false}'
    )

    def judge_metric(example, prediction, trace=None):
        user = (
            "== REFERENCE ==\n" + str(example.response) +
            "\n\n== CANDIDATE ==\n" + str(getattr(prediction, "response", "")) +
            "\n\nReturn the JSON object now."
        )
        try:
            raw = call_scr(P["judgeModelName"], judge_lm_options, judge_system, user)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            verdict = json.loads(match.group(0)) if match else {}
            return bool(verdict.get("equivalent"))
        except Exception:
            return False

    return judge_metric


def evaluate(program, dataset, metric):
    if not dataset:
        return 0.0
    hits = 0
    for example in dataset:
        try:
            prediction = program(**example.inputs())
        except Exception:
            continue
        if metric(example, prediction):
            hits += 1
    return hits / len(dataset)


# ---- Bake-out: DSPy program -> Prompt-Builder-shaped prompt ----------------
def bake_out(compiled, source_header, input_names):
    predictor = compiled.predictors()[0]
    instructions = str(getattr(predictor.signature, "instructions", "") or "")
    demo_blocks = []
    for demo in getattr(predictor, "demos", []) or []:
        demo_dict = dict(demo) if not isinstance(demo, dict) else demo
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
    }


# ---- Write-back ------------------------------------------------------------
def create_optimised_prompt_model(source_model, baked, attributes):
    body = {
        "name": f"{P['promptName']} (optimised {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')})",
        "projectId": source_model.get("projectId"),
        "function": "prompt template",
        "tool": "Prompt-Builder",
        "algorithm": "Prompt-Template",
        "tags": ["LLM", "Prompt-Template", "Optimized-Prompt"],
    }
    response = requests.post(
        BASE + "/modelRepository/models",
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/vnd.sas.models.model+json",
            "Accept": "application/json",
        },
        data=json.dumps(body), verify=VERIFY, timeout=120,
    )
    if response.status_code >= 400:
        fail(f"Creating the optimised prompt model failed with HTTP {response.status_code}: {response.text[:300]}")
    created = response.json()
    if isinstance(created, dict) and created.get("items"):
        created = created["items"][0]
    new_model_id = created.get("id")
    # Model Manager DROPS tags and custom attributes on the create POST
    # (verified live: the created model came back with tags=None), so stamp
    # them with a follow-up ETag'd PUT - the same pattern the Builder and mdb
    # use. Custom key-values must go into the model's `properties` array;
    # arbitrary top-level fields are silently discarded (also verified live).
    # Best effort: a failure here must not lose the optimisation result.
    try:
        detail_response = requests.get(
            BASE + f"/modelRepository/models/{new_model_id}",
            headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.sas.models.model+json"},
            verify=VERIFY, timeout=120,
        )
        if detail_response.status_code < 400:
            detail = detail_response.json()
            detail["tags"] = body["tags"]
            model_properties = [p for p in (detail.get("properties") or [])
                                if p.get("name") not in attributes]
            for attr_name, attr_value in attributes.items():
                model_properties.append({"name": attr_name, "value": str(attr_value), "type": "string"})
            detail["properties"] = model_properties
            etag = detail_response.headers.get("ETag")
            put_headers = {"Authorization": "Bearer " + TOKEN,
                           "Content-Type": "application/vnd.sas.models.model+json"}
            if etag:
                put_headers["If-Match"] = etag
            requests.put(BASE + f"/modelRepository/models/{new_model_id}",
                         headers=put_headers, data=json.dumps(detail), verify=VERIFY, timeout=120)
    except Exception:
        progress("Warning: could not stamp tags/provenance attributes on the optimised prompt")
    # Seed the tracker with one header row so the Builder opens the new
    # prompt-test showing the optimised prompt (no model results yet).
    header_row = {
        "runId": 1,
        "systemPrompt": baked["systemPrompt"],
        "userPrompt": baked["userPrompt"],
        "variables": baked["variables"] or None,
        "manifest": None, "model": "", "options": "", "response": "",
        "run_time": None, "prompt_length": None, "output_length": None,
        "best_prompt": None, "fastest_prompt": None, "fewest_tokens_prompt": None,
        "judge_rank": None, "judge_best": None,
    }
    add_model_content(new_model_id, "Prompt-Experiment-Tracker.json", [header_row])
    return new_model_id


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
    "optimizedPrompt": None, "producedPromptModelId": None,
    "datasetSnapshot": None, "error": None,
}

try:
    key_map = load_key_map()
    progress("Loading the experiment tracker dataset")
    examples_raw, source_header = load_tracker_dataset(P["promptModelId"])
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
        metric = make_judge_metric(judge_options)
    else:
        metric = exact_metric

    signature_str = ", ".join(input_names) + " -> response"
    signature = dspy.Signature(signature_str, str(source_header.get("systemPrompt") or ""))
    program = dspy.Predict(signature)

    progress("Scoring the baseline prompt")
    metric_before = evaluate(program, valset, metric)
    tracker_entry["metricBefore"] = metric_before
    progress(f"Baseline metric: {metric_before:.3f}")

    progress(f"Optimising with {P['optimizer']} ({len(trainset)} training examples)")
    optimizer = dspy.BootstrapFewShot(
        metric=metric, max_bootstrapped_demos=MAX_DEMOS, max_labeled_demos=MAX_DEMOS)
    compiled = optimizer.compile(program, trainset=trainset)

    progress("Scoring the optimised prompt")
    metric_after = evaluate(compiled, valset, metric)
    tracker_entry["metricAfter"] = metric_after
    progress(f"Optimised metric: {metric_after:.3f}")

    progress("Baking the optimised program into a prompt")
    baked = bake_out(compiled, source_header, input_names)
    tracker_entry["optimizedPrompt"] = baked

    progress("Writing the results back to SAS Model Manager")
    source_model = viya("GET", f"/modelRepository/models/{P['promptModelId']}")
    produced_id = create_optimised_prompt_model(source_model, baked, {
        "sourcePromptId": P["promptModelId"],
        "optimizedBy": f"dspy-{P['optimizer']}",
    })
    tracker_entry["producedPromptModelId"] = produced_id

    # Snapshot the exact examples optimised on (provenance) and record the run.
    tracker_entry["status"] = "succeeded"
    tracker_entry["finishedAt"] = datetime.now(timezone.utc).isoformat()
    append_optimization_tracker(tracker_entry, dataset_snapshot=examples_raw)

    progress(f"Done - optimised prompt model {produced_id}, metric {metric_before:.3f} -> {metric_after:.3f}")
    SAS.symput("_opt_rc", "0")
except Exception as error:
    error_text = str(error) or error.__class__.__name__
    progress(f"Optimization failed: {error_text}")
    print(traceback.format_exc())
    tracker_entry["status"] = "failed"
    tracker_entry["finishedAt"] = datetime.now(timezone.utc).isoformat()
    tracker_entry["error"] = error_text[:500]
    try:
        # Best effort: record the failure for the Builder's result panel.
        append_optimization_tracker(tracker_entry)
    except Exception:
        pass
    SAS.symput("_opt_error", error_text[:500])
endsubmit;
run; quit;

proc python terminate;
run; quit;

/* ---- Propagate the outcome to Job Execution ---- */
data _null_;
    if "&_opt_rc." ne "0" then do;
        putLog "ERROR: Prompt optimization failed: %superq(_opt_error)";
        abort abend 42;
    end;
    putLog "NOTE: Prompt optimization succeeded.";
run;
