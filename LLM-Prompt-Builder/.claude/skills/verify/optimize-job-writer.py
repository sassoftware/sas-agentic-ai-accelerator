"""
The writer of Prompt-Optimization-Tracker.json outside the page: the Python
program of SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas,
run as it stands, once for every way it ends, against stand-ins for SAS, for
SAS Viya (requests) and for dspy. Not a suite: verify-round-trip.js runs it
and hands what the job wrote to the check of the page.

    python -I optimize-job-writer.py <Optimize-Prompt-DSPy.sas> <out.json>

Every run appends its entry to the tracker of one prompt-test, as the job
does on SAS Viya. out.json holds the tracker after each run and the outcome
the job set (_opt_rc / _opt_error):

    {"runs": [{"name": ..., "outcome": ..., "tracker": [...]}, ...]}

The stand-in for dspy does what the program asks of it and no more: a
program answers with what the model container answers, an optimizer returns
the program with the first examples of the training set as its demos.
"""
import base64
import json
import os
import re
import sys
import tempfile
import types

JOB, OUT = sys.argv[1], sys.argv[2]

with open(JOB, encoding="utf-8") as handle:
    source = handle.read()
start = source.index("submit;\n", source.index("proc python restart;")) + len("submit;\n")
PROGRAM = source[start:re.compile(r"^[ \t]*endsubmit;", re.M).search(source, start).start()]

PROMPT = "prompt-1"
WORK = tempfile.mkdtemp(prefix="optimize-job-")


# ---- SAS Viya: the models, their files and the model containers ----------
class Viya:
    def __init__(self):
        # model id -> {name: (content id, bytes)}
        self.files = {}
        self.names = {}
        self.count = 0
        # model name -> the answer of its container, or None for one that fails
        self.answers = {}

    def add_model(self, model_id, name, files):
        self.names[model_id] = name
        self.files[model_id] = {}
        for file_name, value in files.items():
            self.put(model_id, file_name, json.dumps(value).encode("utf-8"))

    def put(self, model_id, name, data):
        self.count += 1
        self.files.setdefault(model_id, {})[name] = (f"c-{self.count}", data)

    def tracker(self):
        held = self.files.get(PROMPT, {}).get("Prompt-Optimization-Tracker.json")
        return json.loads(held[1].decode("utf-8")) if held else None


VIYA = Viya()


class Response:
    def __init__(self, status, value=None, text=None):
        self.status_code = status
        self.text = text if text is not None else ("" if value is None else json.dumps(value))

    def json(self):
        return json.loads(self.text)


def _path(url):
    """The path and query of a URL, without its scheme and host."""
    return "/" + url.split("://", 1)[-1].split("/", 1)[-1]


def request(method, url, headers=None, verify=None, timeout=None, **kwargs):
    path = _path(url)
    if method == "GET" and path.startswith("/modelRepository/models?filter=eq(name,'"):
        name = path.split("eq(name,'", 1)[1].rsplit("')", 1)[0]
        return Response(200, {"items": [{"id": model_id, "name": model_name} for model_id, model_name in VIYA.names.items() if model_name == name]})
    if method == "GET" and path.startswith("/modelRepository/models/") and "/contents" in path:
        model_id = path.split("/")[3]
        items = [
            {"id": content_id, "name": name, "fileUri": f"/files/files/{model_id}--{content_id}"}
            for name, (content_id, _) in VIYA.files.get(model_id, {}).items()
        ]
        return Response(200, {"items": items})
    if method == "GET" and path.startswith("/files/files/") and path.endswith("/content"):
        model_id, content_id = path[len("/files/files/"):-len("/content")].split("--")
        for name, (held_id, data) in VIYA.files.get(model_id, {}).items():
            if held_id == content_id:
                return Response(200, text=data.decode("utf-8"))
        return Response(404, {"message": "no such file"})
    if method == "DELETE" and path.startswith("/modelRepository/models/"):
        parts = path.split("/")
        model_id, content_id = parts[3], parts[5]
        VIYA.files[model_id] = {name: held for name, held in VIYA.files.get(model_id, {}).items() if held[0] != content_id}
        return Response(204)
    return Response(404, {"message": f"not in the stand-in: {method} {path}"})


def get(url, params=None, headers=None, verify=None, timeout=None):
    path = _path(url)
    if path.startswith("/credentials/domains/"):
        return Response(200, {"secrets": {"OpenAI": base64.b64encode(b"made-up-key").decode("ascii")}})
    return request("GET", url, headers=headers)


def post(url, json=None, files=None, headers=None, verify=None, timeout=None, **kwargs):
    path = _path(url)
    if files is not None:
        model_id = path.split("/")[3]
        name, data, _ = files["files"]
        VIYA.put(model_id, name, data)
        return Response(201, {"id": "c-new"})
    # A model container: /<model>/<model> below the SCR endpoint.
    model = path.rstrip("/").split("/")[-1]
    answer = VIYA.answers.get(model)
    if answer is None:
        return Response(503, {"message": f"no container answers for {model}"})
    user = next((item["value"] for item in json["inputs"] if item["name"] == "userPrompt"), "")
    text = answer(user) if callable(answer) else answer
    return Response(200, {"data": {"response": text, "run_time": 0.5, "prompt_length": 20, "output_length": 4}})


requests_stub = types.ModuleType("requests")
requests_stub.request, requests_stub.get, requests_stub.post = request, get, post


# ---- dspy ------------------------------------------------------------------
dspy = types.ModuleType("dspy")
dspy.__version__ = "3.2.1"
dspy.settings = types.SimpleNamespace(lm=None)


def configure(lm=None):
    dspy.settings.lm = lm


class BaseLM:
    def __init__(self, model=None, **kwargs):
        self.model = model


class Example:
    def __init__(self, **fields):
        self._fields = dict(fields)
        self._inputs = []

    def with_inputs(self, *names):
        self._inputs = list(names)
        return self

    def inputs(self):
        return {name: self._fields[name] for name in self._inputs}

    def keys(self):
        return list(self._fields.keys())

    def __getitem__(self, name):
        return self._fields[name]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._fields[name]
        except KeyError:
            raise AttributeError(name)


class Prediction(types.SimpleNamespace):
    pass


class Signature:
    def __init__(self, text, instructions=""):
        self.text = text
        self.instructions = instructions


class Predict:
    def __init__(self, signature, demos=None):
        self.signature = signature
        self.demos = list(demos or [])

    def predictors(self):
        return [self]

    def __call__(self, **inputs):
        lm = dspy.settings.lm
        messages = [
            {"role": "system", "content": self.signature.instructions},
            {"role": "user", "content": "\n".join(f"{name}: {value}" for name, value in inputs.items())},
        ]
        answer = lm.forward(messages=messages)
        return Prediction(response=answer.choices[0].message.content)


class BootstrapFewShot:
    def __init__(self, metric=None, max_bootstrapped_demos=4, max_labeled_demos=4):
        self.metric = metric
        self.count = max_labeled_demos

    def compile(self, program, trainset=None):
        return Predict(program.signature, (trainset or [])[: self.count])


class MIPROv2:
    def __init__(self, metric=None, auto=None, max_bootstrapped_demos=4, max_labeled_demos=4, num_threads=1):
        self.metric = metric
        self.count = max_labeled_demos

    def compile(self, program, trainset=None, valset=None, minibatch=True, requires_permission_to_run=True):
        compiled = Predict(Signature(program.signature.text, program.signature.instructions + " Answer in one word."), (trainset or [])[: self.count])
        return compiled


class GEPA:
    def __init__(self, metric=None, auto=None, reflection_lm=None, num_threads=1):
        self.metric = metric
        self.reflection_lm = reflection_lm

    def compile(self, program, trainset=None, valset=None):
        # The reflection reads the feedback of the metric on an example.
        for example in (trainset or [])[:1]:
            self.metric(example, program(**example.inputs()))
        self.reflection_lm.forward(messages=[{"role": "user", "content": "Propose a better instruction."}])
        return Predict(Signature(program.signature.text, "Reflected: " + program.signature.instructions))


for item in (configure, BaseLM, Example, Prediction, Signature, Predict, BootstrapFewShot, MIPROv2, GEPA):
    setattr(dspy, item.__name__, item)

sys.modules["requests"] = requests_stub
sys.modules["dspy"] = dspy
sys.modules["optuna"] = types.ModuleType("optuna")


# ---- SAS -------------------------------------------------------------------
class Sas:
    def __init__(self, symbols):
        self.symbols = dict(symbols)
        self.workpath = WORK + os.sep

    def symget(self, name):
        return self.symbols.get(name)

    def symput(self, name, value):
        self.symbols[name] = value

    def logMessage(self, message):
        pass


# ---- The prompt-test, its models and the runs ------------------------------
def rows(*answers):
    """A tracker the page wrote: one run per (city, answer), each with a Best Response."""
    written = []
    for run_id, (city, answer) in enumerate(answers, 1):
        written.append({"runId": run_id, "model": "", "systemPrompt": "Say how the weather is.", "userPrompt": "Weather in {{city}}?",
                        "variables": [{"name": "city", "description": "", "type": "string", "value": city}]})
        written.append({"runId": run_id, "model": "target_llm", "response": answer, "best_prompt": 1})
    return written


VIYA.add_model(PROMPT, "Weather Prompt", {"Prompt-Experiment-Tracker.json": rows(
    ("Oslo", "cold"), ("Cairo", "hot"), ("Lima", "mild"), ("Rome", "warm"), ("Nuuk", "cold"))})
VIYA.add_model("llm-target", "target_llm", {"options.json": {"temperature": {"default": 0.2}}})
VIYA.add_model("llm-second", "second_llm", {"options.json": {"temperature": {"default": 0.5}, "API_KEY": {"default": "OpenAI"}}})
VIYA.add_model("llm-judge", "judge_llm", {"options.json": {"temperature": {"default": 0.0}}})
VIYA.add_model("llm-down", "down_llm", {"options.json": {"temperature": {"default": 0.0}}})
VIYA.answers = {
    "target_llm": "cold",
    "second_llm": lambda user: "hot" if "Cairo" in user else "cold",
    # The judge says what it sees: the candidate is the reference, or not.
    "judge_llm": lambda user: json.dumps({
        "reasoning": "Compared with the reference.",
        "equivalent": "true" if user.split("== REFERENCE ==", 1)[-1].split("== CANDIDATE ==")[0].strip()
        == user.split("== CANDIDATE ==", 1)[-1].split("Return the JSON")[0].strip() else "false",
    }),
    "down_llm": None,
}

BASE_SYMBOLS = {
    "promptModelId": PROMPT, "promptName": "Weather Prompt", "mode": "optimize",
    "targetModelId": "llm-target", "targetModelName": "target_llm", "targetModelNames": "",
    "scrEndpoint": "http://scr.invalid", "deploymentType": "k8s", "datasetSource": "tracker",
    "metric": "exact", "judgeModelName": "", "optimizer": "bootstrap", "maxDemos": "2",
    "minSamples": "1", "casServer": "cas-shared-default", "casLibrary": "", "casTable": "",
    "keyDomain": "agentic-ai-keys", "_opt_viyaHost": "http://viya.invalid",
    "SYS_JES_JOB_URI": "/jobExecution/jobs/job-x", "_contextName": "ctx",
}

RUNS = [
    ("an optimization whose baseline is not perfect (bootstrap, exact)", {}),
    ("an optimization whose baseline is perfect", {"targetModelId": "llm-perfect", "targetModelName": "perfect_llm"}),
    ("an optimization with the judge metric and GEPA", {"metric": "judge", "judgeModelName": "judge_llm", "optimizer": "gepa"}),
    ("an optimization over a CAS table with numbers in it (MIPROv2, overlap)", {"datasetSource": "cas", "casLibrary": "Public", "casTable": "WEATHER", "optimizer": "miprov2", "metric": "overlap"}),
    ("a comparison of three targets, one of them without a container", {"mode": "compare", "targetModelNames": "target_llm, second_llm, down_llm"}),
    ("a sweep of two targets", {"mode": "sweep", "targetModelNames": "target_llm, second_llm", "optimizer": "bootstrap"}),
    ("a comparison of which no target answers", {"mode": "compare", "targetModelNames": "down_llm, missing_llm"}),
    ("an optimization whose target has no options.json", {"targetModelId": "llm-none", "targetModelName": "none_llm"}),
    ("an optimization with fewer runs than it needs", {"minSamples": "50"}),
    ("an optimization whose judge is not registered", {"metric": "judge", "judgeModelName": "unknown_judge"}),
]
VIYA.add_model("llm-perfect", "perfect_llm", {"options.json": {"temperature": {"default": 0.2}}})
VIYA.answers["perfect_llm"] = lambda user: {"Oslo": "cold", "Cairo": "hot", "Lima": "mild", "Rome": "warm", "Nuuk": "cold"}.get(user.split("city: ", 1)[-1].strip(), "OK")
VIYA.add_model("llm-none", "none_llm", {})
with open(os.path.join(WORK, "optimize_dataset.json"), "w", encoding="utf-8") as handle:
    json.dump([{"CITY": "Oslo", "response": "cold", "temperature": -3.5}, {"CITY": "Cairo", "response": "hot", "temperature": 31},
               {"CITY": "Lima", "response": "mild", "temperature": 18}, {"CITY": "Rome", "response": None, "temperature": 20}], handle)

results = []
for name, symbols in RUNS:
    sas = Sas({**BASE_SYMBOLS, **symbols, "_opt_rc": "1", "_opt_error": ""})
    namespace = {"__name__": "__optimize_job__", "SAS": sas}
    os.environ["SAS_SERVICES_TOKEN"] = "made-up-token"
    error = ""
    try:
        exec(compile(PROGRAM, "Optimize-Prompt-DSPy.sas", "exec"), namespace)
    except BaseException as raised:  # the program must end normally
        error = f"{type(raised).__name__}: {raised}"
    results.append({
        "name": name,
        "outcome": {"rc": sas.symbols.get("_opt_rc"), "error": sas.symbols.get("_opt_error"), "raised": error},
        "tracker": VIYA.tracker(),
    })

with open(OUT, "w", encoding="utf-8") as handle:
    json.dump({"runs": results}, handle)
