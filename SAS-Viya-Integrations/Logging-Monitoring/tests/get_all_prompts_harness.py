"""Runs the Python of Get-All-Prompts.sas outside of SAS, against a made-up SAS Model Manager.

The program lives twice: in Get-All-Prompts.sas and in the SAS program of the
"LLM - Get All Prompts" custom step. Both hold its Python between "submit;" and
"endsubmit;" of a PROC PYTHON step, where SAS provides the SAS object and the
requests package reaches SAS Viya. Here the SAS object is a stand-in that keeps the
macro variables and the log messages, and requests is a stand-in that answers from
the projects, models and files a test puts into a FakeViya - so the whole block
runs unchanged under pytest, with its paging and its failures.

Every tracker, setup and score code in the tests is made up by the builders below.
They have the shape of the files the Prompt Builder, the optimization job and the
step RAG - Register Setup write, they are not copies of real ones.
"""
import json
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

INTEGRATIONS = Path(__file__).resolve().parents[2]
CODE_FILE = INTEGRATIONS / "Logging-Monitoring" / "Get-All-Prompts.sas"
STEP_FILE = INTEGRATIONS / "Custom-Steps" / "LLM - Get All Prompts.step"

VIYA_HOST = "https://viya.example.com"
CORE_START = "* gap core - from here to the line that ends the core the custom step LLM - Get All Prompts runs the same lines;"
CORE_END = "* end of the gap core;"

# The settings as the code file sets them at its top
DEFAULT_SETTINGS = {
    "_gap_tag": "Prompt-Engineering",
    "_gap_rag_tag": "RAG-Engineering",
    "_gap_llm_project": "LLM Model Project",
    "_gap_embedding_project": "Embedding Model Project",
    "_gap_decision_project": "Decision Model Project",
    "_gap_out_caslib": "Public",
    "_gap_out_experiments": "PROMPT_EXPERIMENTS",
    "_gap_out_optimizations": "PROMPT_OPTIMIZATIONS",
    "_gap_out_rag_setups": "RAG_SETUPS",
    "_gap_out_rag_runs": "RAG_RUNS",
    "_gap_out_catalog": "MODEL_CATALOG",
}


def code_file_program():
    """The SAS program of the code file, with LF line endings."""
    return CODE_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")


def step_definition():
    return json.loads(STEP_FILE.read_text(encoding="utf-8"))


def step_program():
    """The SAS program the custom step runs."""
    return step_definition()["templates"]["SAS"].replace("\r\n", "\n")


def step_ui():
    return json.loads(step_definition()["ui"])


def python_block(sas_program):
    """The Python between the submit; and endsubmit; lines of a SAS program."""
    lines = sas_program.split("\n")
    start = lines.index("submit;")
    end = lines.index("endsubmit;")
    assert start < end
    return "\n".join(lines[start + 1:end]) + "\n"


def core(sas_program):
    """The lines the code file and the custom step share."""
    lines = sas_program.split("\n")
    return "\n".join(lines[lines.index(CORE_START):lines.index(CORE_END) + 1])


# ---------------------------------------------------------------------------
# Stand-ins for SAS and for SAS Viya
# ---------------------------------------------------------------------------

class FakeSAS:
    """Stands in for the SAS object PROC PYTHON provides."""

    def __init__(self, work, settings=None):
        # _gap_rc starts at -1, as the SAS program sets it before PROC PYTHON
        self.macro_variables = dict(DEFAULT_SETTINGS, _gap_viyaHost=VIYA_HOST, _gap_work=str(work), _gap_rc="-1")
        self.macro_variables.update(settings or {})
        self.messages = []

    def symget(self, name):
        return self.macro_variables.get(name)

    def symput(self, name, value):
        self.macro_variables[name] = value

    def logMessage(self, message, messageType="NOTE"):
        self.messages.append((messageType, message))

    def of_type(self, message_type):
        return [message for kind, message in self.messages if kind == message_type]


class FakeResponse:
    def __init__(self, status_code, body=b""):
        self.status_code = status_code
        self.content = body


class FakeViya:
    """A SAS Model Manager that holds what a test puts into it and answers the requests of the program.

    Listings are paged the way the service pages them: `start` and `limit` choose the
    page, `count` is the number of all items. `page_size` caps a page below the limit
    that was asked for, `without_count` leaves the count out, and every path in
    `failing` is answered with HTTP 500.
    """

    def __init__(self, page_size=None, without_count=False):
        self.projects = []
        self.models = {}
        self.contents = {}
        self.files = {}
        self.details = {}
        self.failing = set()
        self.requests = []
        self.page_size = page_size
        self.without_count = without_count
        self._next = 0

    def _id(self, prefix):
        self._next += 1
        return f"{prefix}0000-4000-8000-{self._next:012d}"

    def add_project(self, name, tags=(), **more):
        project = dict({"id": self._id("aaaaaaaa-"), "name": name, "tags": list(tags)}, **more)
        self.projects.append(project)
        self.models[project["id"]] = []
        return project

    def add_model(self, project, name, files=None, properties=None, **more):
        """Adds a model. `files` maps a file name to its content (a text, or anything that is stored as JSON),
        or to (content, role). `properties` are only in the model itself, not in the listing of its project."""
        model = dict({
            "id": self._id("bbbbbbbb-"), "name": name, "projectId": project["id"], "description": f"About {name}",
            "function": "Prompt", "createdBy": "ada", "modifiedBy": "grace", "tags": [],
            "creationTimeStamp": "2026-01-05T09:15:00.250Z", "modifiedTimeStamp": "2026-01-06T10:30:00.000Z",
        }, **more)
        self.models[project["id"]].append(model)
        self.contents[model["id"]] = []
        self.details[model["id"]] = dict(model, properties=[{"name": key, "value": value}
                                                             for key, value in (properties or {}).items()])
        for file_name, content in (files or {}).items():
            role = "score" if file_name.endswith(".py") else "documentation"
            if isinstance(content, tuple):
                content, role = content
            self.add_file(model, file_name, content, role)
        return model

    def add_file(self, model, name, content, role="documentation"):
        file_uri = "/files/files/" + self._id("cccccccc-")
        self.contents[model["id"]].append({"id": self._id("dddddddd-"), "name": name, "role": role, "fileUri": file_uri})
        text = content if isinstance(content, str) else json.dumps(content)
        self.files[file_uri + "/content"] = text.encode("utf-8")
        return file_uri

    def paths(self, prefix=""):
        return [path for path, _ in self.requests if path.startswith(prefix)]

    # -- answering ----------------------------------------------------------
    def get(self, url, headers=None, verify=None, timeout=None):
        assert url.startswith(VIYA_HOST + "/"), url
        parts = urlsplit(url)
        path = parts.path
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        self.requests.append((path, dict(query, **{"@accept": (headers or {}).get("Accept")})))
        assert (headers or {}).get("Authorization") == "Bearer not-a-token"
        if path in self.failing:
            return FakeResponse(500, b"{}")
        if path in self.files:
            return FakeResponse(200, self.files[path])
        segments = path.strip("/").split("/")
        if segments[:2] == ["modelRepository", "projects"] and len(segments) == 2:
            return self._page(self._filtered(self.projects, query.get("filter", "")), query)
        if segments[:2] == ["modelRepository", "projects"] and len(segments) == 3:
            found = [project for project in self.projects if project["id"] == segments[2]]
            return self._json(found[0]) if found else FakeResponse(404)
        if segments[:2] == ["modelRepository", "projects"] and segments[3:] == ["models"]:
            return self._page(self.models.get(segments[2], []), query)
        if segments[:2] == ["modelRepository", "models"] and segments[3:] == ["contents"]:
            return self._page(self.contents.get(segments[2], []), query)
        if segments[:2] == ["modelRepository", "models"] and len(segments) == 3 and segments[2] in self.details:
            return self._json(self.details[segments[2]])
        return FakeResponse(404)

    @staticmethod
    def _json(value):
        return FakeResponse(200, json.dumps(value).encode("utf-8"))

    @staticmethod
    def _filtered(projects, expression):
        expression = unquote(expression)
        if not expression:
            return projects
        assert expression.startswith("eq(") and expression.endswith(")"), expression
        field, _, literal = expression[3:-1].partition(",")
        assert literal[0] == literal[-1] and literal[0] in "'\"", literal
        value = literal[1:-1]
        if field == "tags":
            return [project for project in projects if value in project.get("tags", [])]
        assert field == "name", expression
        return [project for project in projects if project["name"] == value]

    def _page(self, items, query):
        start = int(query.get("start", 0))
        limit = int(query.get("limit", 20))
        size = min(limit, self.page_size) if self.page_size else limit
        page = {"items": items[start:start + size], "start": start, "limit": limit}
        if not self.without_count:
            page["count"] = len(items)
        return self._json(page)


class PullRun:
    """What one run of the program's Python left behind."""

    def __init__(self, namespace, sas, work):
        self.namespace = namespace
        self.sas = sas
        self.work = Path(work)

    @property
    def rc(self):
        return self.sas.macro_variables["_gap_rc"]

    def rows(self, key):
        """The rows of a table as they were written for SAS."""
        return json.loads((self.work / f"_gap_{key}.json").read_text(encoding="utf-8"))

    def map(self, key):
        return json.loads((self.work / f"_gap_{key}.map").read_text(encoding="utf-8"))

    def macro(self, name):
        return self.sas.macro_variables[name]

    def catalog(self, **where):
        return [row for row in self.rows("catalog") if all(row[name] == value for name, value in where.items())]


def run_pull(monkeypatch, work, viya=None, settings=None, python=None, without_requests=False):
    """Executes the program's Python against a FakeViya and returns the PullRun."""
    viya = viya if viya is not None else FakeViya()
    if without_requests:
        # None in sys.modules makes the import fail, which is how a missing package looks
        monkeypatch.setitem(sys.modules, "requests", None)
    else:
        fake_requests = types.ModuleType("requests")
        fake_requests.get = viya.get
        monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setenv("SAS_SERVICES_TOKEN", "not-a-token")
    monkeypatch.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")

    sas = FakeSAS(work, settings)
    namespace = {"SAS": sas, "__name__": "get_all_prompts"}
    source = python if python is not None else python_block(code_file_program())
    exec(compile(source, "Get-All-Prompts.sas (python)", "exec"), namespace)
    return PullRun(namespace, sas, work)


def functions(monkeypatch, work):
    """The functions of the program's Python, for the tests that call one of them."""
    return run_pull(monkeypatch, work).namespace


# ---------------------------------------------------------------------------
# Made-up files in the shape of the real ones
# ---------------------------------------------------------------------------

def variable(name, value, kind="string", description=""):
    return {"name": name, "description": description, "type": kind, "value": value}


def header_row(run_id, system_prompt, user_prompt, variables=None, mode=None, expected=None, manifest="default", **more):
    """The header row of a run, as the Prompt Builder saves it: the templates, the variables, an empty model."""
    if manifest == "default":
        manifest = {"integratedLLMCall": False, "selectedOutputs": ["response"], "outputVariables": []}
    row = {
        "runId": run_id, "systemPrompt": system_prompt, "userPrompt": user_prompt,
        "variables": variables, "manifest": manifest, "mode": mode, "expected": expected,
        "model": "", "options": "", "response": "", "run_time": None, "prompt_length": None,
        "output_length": None, "best_prompt": None, "fastest_prompt": None, "fewest_tokens_prompt": None,
        "judge_rank": None, "judge_best": None, "judge_model": None, "judge_confidence": None,
        "judge_reasoning": None, "judge_include_self": None, "judge_auto": None, "judge_cost": None,
        "judge_mode": None, "judge_panel": None, "judge_agreement": None, "judge_ballots": None,
        "judge_chairman_model": None, "judge_chairman_reasoning": None,
    }
    row.update(more)
    return row


def model_row(run_id, model, response="An answer.", options="{temperature:0.2,top_p:1,max_tokens:40}", **more):
    """The row of one model that answered in a run."""
    row = {
        "runId": run_id, "systemPrompt": "", "userPrompt": "", "model": model, "options": options,
        "response": response, "run_time": 1.25, "prompt_length": 21, "output_length": 9, "best_prompt": None,
        "fastest_prompt": True, "fewest_tokens_prompt": False, "cheapest_prompt": False, "cost": None,
        "judge_rank": None, "judge_best": None,
    }
    row.update(more)
    return row


QUESTIONS = json.dumps({
    "team": {"type": "choice", "instructions": "Which team owns this request?",
             "criteria": {"repairs": "a damaged vehicle", "billing": "an invoice", "unknown": "not enough information"}},
    "urgent": {"type": "noul", "instructions": "Does the sender ask for an answer today?"},
}, separators=(",", ":"))

ANSWERS = json.dumps({
    "team": {"type": "choice", "choice": "repairs", "probabilities": {"repairs": 0.9, "billing": 0.05, "unknown": 0.05},
             "confidence": 0.9},
    "urgent": {"type": "noul", "noul": 0.2},
})


def decision_header(run_id, state_template="Request: {{request}}", questions=QUESTIONS, variables=None, expected=None):
    """The header row of a decision run: the questions as its system prompt, the state template as its user prompt."""
    if variables is None:
        variables = [variable("request", "My windscreen has a crack since the hail on Tuesday.")]
    return header_row(run_id, questions, state_template, variables, mode="decision",
                      expected=expected if expected is not None else {"team": "repairs", "urgent": "no"})


def optimization_entry(optimization_id=1, **more):
    """One entry of a Prompt-Optimization-Tracker.json, as the optimization job writes it."""
    entry = {
        "startedAt": "2026-02-03T08:00:00.000000+00:00", "finishedAt": "2026-02-03T08:12:30.500000+00:00",
        "status": "succeeded", "jobId": "eeeeeeee-0000-4000-8000-000000000001", "targetModel": "demo_llm",
        "computeContext": "SAS Job Execution compute context", "datasetSource": "tracker",
        "datasetRef": "Prompt-Experiment-Tracker.json", "sampleCount": 40, "optimizer": "bootstrap",
        "metric": "judge", "judgeModel": "demo_judge", "metricBefore": 0.61, "metricAfter": 0.78,
        "baselinePrompt": {"systemPrompt": "Answer briefly.", "userPrompt": "{{question}}"},
        "optimizedPrompt": {"systemPrompt": "Answer in one sentence.", "userPrompt": "{{question}}", "demos": []},
        "trainSize": 28, "validationSize": 12, "evaluations": [{"inputs": {"question": "Why?"}}],
        "datasetSnapshot": "Prompt-Optimization-Dataset-1.json", "error": None,
        "usage": {"target": {"calls": 120, "promptTokens": 9000, "outputTokens": 2100, "runTime": 96.5},
                  "judge": {"calls": 52, "promptTokens": 14000, "outputTokens": 800, "runTime": 40.0}},
        "optimizationId": optimization_id,
    }
    entry.update(more)
    return entry


def rag_setup(collection="handbook_v1", prefix="HANDBOOK", caslib="Public", **more):
    """A rag-setup.json as the Prompt Builder saves it."""
    setup = {
        "version": 1,
        "documentation": {"modelPurpose": "Answers from the staff handbook", "intendedUse": "", "expectedBenefit": "",
                          "outOfScopeUseCases": "", "limitations": ""},
        "artifactsFolder": "/Projects/RAG/generated",
        "source": {"path": "/data/handbook", "includeCode": False},
        "extraction": {"extractor": ""},
        "chunking": {"chunker": "recursive", "inputTokenLimit": 256, "overlapTokens": 32},
        "embedding": {"model": "demo_embedder", "dims": 384, "deploymentType": "k8s", "scrEndpoint": ""},
        "store": {"backend": "pgvector", "host": "db.example.com", "port": 5432, "database": "rag",
                  "sslmode": "require", "collection": collection},
        "tables": {"prefix": prefix, "caslib": caslib},
        "pipelineVersion": "v1",
        "credentialDomain": "demo-keys",
        "policies": {"deletedPolicy": "retire", "retainDays": 0, "recordHistory": True, "embedReplicas": 1,
                     "persistElements": True, "persistChunks": True},
        "job": {"definitionUri": "/jobDefinitions/definitions/ffffffff-0000-4000-8000-000000000001"},
    }
    setup.update(more)
    return setup


def step_pipeline_yaml(setup="Travel Rules", collection="travel_rules_v1", project="TRAVEL", caslib="Public",
                       ingestion_run_id="20260210T101500Z-1a2b"):
    """The pipeline.yaml the step RAG - Register Setup stores with the model it registers."""
    return "\n".join([
        "# Generated by RAG - Register Setup. The RAG Setup owns this file;",
        "# the Studio Flow and the scheduled job are generated FROM it.",
        'generated_at: "2026-02-10T10:20:00+00:00"',
        f"setup: {json.dumps(setup)}",
        "source:",
        "  kind: files",
        "  location: /data/travel",
        "tables:",
        f"  project: {project}",
        f"  caslib: {caslib}",
        "chunking:",
        "  chunker: sentence",
        "  input_token_limit: 512",
        "  overlap_tokens: 50",
        "embedding:",
        "  model: demo_embedder",
        '  endpoint: "https://viya.example.com/llm"',
        "  dimensions: 384",
        "store:",
        "  backend: pgvector",
        f"  collection: {collection}",
        "  host: db.example.com",
        "  port: 5432",
        "  database: rag",
        "  sslmode: require",
        "  credential_domain: demo-keys",
        "run:",
        "  pipeline_version: v2",
        "  configuration: 0f1e2d3c",
        f"  ingestion_run_id: {ingestion_run_id}",
        "retrieval:",
        "  default_k: 4",
    ]) + "\n"


def prompt_score_code(llm="demo_llm", system_literal='f"""You answer questions about {{{{topic}}}} in one sentence."""',
                      user_literal='f"""Question: {str(question).strip()}\nContext: {str(context).strip()}"""',
                      inputs="question, context"):
    """The score code of a manifested prompt template, as the Prompt Builder writes it.

    The two literals are given as they stand in the code. The default system prompt holds
    {{topic}}, a placeholder no variable belongs to: the Prompt Builder doubles every brace
    of such a text, so that the f-string gives it back as it was typed.
    """
    return f'''import os
import requests
import json

def scoreModel({inputs}):
    "Output: response, run_time"
    # The llm and the target endpoint
    llm = {json.dumps(llm)}
    endpoint = os.getenv("LLMCONTAINERPATH", "https://viya.example.com/llm")
    llmURL = f"""{{endpoint}}/{{llm}}/{{llm}}"""
    # These are the options that were set for the best prompt
    options = f"""{{{{temperature:0.2,max_tokens:60}}}}"""
    # This is the system prompt that was selected as the best one by the prompt engineer
    systemPrompt = {system_literal}
    # Here the user prompt will be created from the inputs of the call
    userPrompt = {user_literal}
    # The request body for the LLM container in the SCR input format
    llmBody = json.dumps({{"inputs": [{{"name": "systemPrompt", "value": systemPrompt}}, {{"name": "userPrompt", "value": userPrompt}}, {{"name": "options", "value": options}}]}})
    response = ""
    run_time = None
    return response, run_time
'''


def decision_score_code(llm="demo_decider", questions=QUESTIONS,
                        state_literal='f"""Request: {str(request).strip()}"""', inputs="request"):
    """The score code of a manifested decision template, as the Prompt Builder writes it."""
    return f'''import os
import requests
import json

def _headline(answerObject, questionType):
    if not isinstance(answerObject, dict):
        return "", None
    return str(answerObject.get("choice", "")), float(answerObject.get("confidence", 0.0))

def scoreModel({inputs}):
    "Output: answers, answer, confidence, team, team_confidence"
    # The decision model and the target endpoint
    llm = {json.dumps(llm)}
    endpoint = os.getenv("LLMCONTAINERPATH", "https://viya.example.com/llm")
    llmURL = f"""{{endpoint}}/{{llm}}/{{llm}}"""
    # These are the options that were set for the best run
    options = f"""{{{{}}}}"""
    # The state the questions are asked about, built from the inputs
    state = {state_literal}
    # The questions of the decision template, in the System One shape
    questions = {json.dumps(questions)}
    llmBody = json.dumps({{"inputs": [{{"name": "state", "value": state}}, {{"name": "questions", "value": questions}}, {{"name": "options", "value": options}}]}})
    answers = ""
    return answers
'''
