"""Optimize-Prompt-DSPy.sas before its first model call, and the HTTP errors it reports.

Measured on SAS Viya 2026.09 on 2026-10-20 with the stock "SAS Job Execution compute context",
which runs its servers as a service account (runServerAs sas-be-sa; the session ran as sas, its
token stood for sas.compute): a target with a provider key failed for want of the key, and a
target without one made all 19 model calls of the optimization and was then refused the upload of
its result with HTTP 403 - "{`version`:2,`httpStatusCode`:403,`message`:`Forbidden`,...}" in the
log. In the "SAS Studio compute context", which runs as the user, the same run completed.

The job now ends before its first model call when its session does not run as the user who
started it - compared as Start-Here.sas and Load-Accelerator-Tables.sas compare them - or when it
may not add a file to the prompt and remove it again, and it writes an HTTP error as a sentence.
The links of the model were no answer: the session of the service account got the same update
and addContent links of the model as its owner (measured the same day).

The Python program of the job runs here as it stands, against stand-ins for SAS, for SAS Viya
(requests) and for dspy. The SAS code can not run; its statements are read as text.
"""
import base64
import json
import re
import sys
import types
from pathlib import Path

import pytest

PROGRAM_FILE = Path(__file__).resolve().parents[1] / "Optimize-Prompt-DSPy.sas"
PROMPT = "prompt-1"
JES = "SAS Job Execution compute context"


def source():
    return PROGRAM_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")


def python_program():
    text = source()
    start = text.index("submit;\n", text.index("proc python restart;")) + len("submit;\n")
    return text[start:re.compile(r"^[ \t]*endsubmit;", re.M).search(text, start).start()]


def sas_code():
    """The SAS code around the Python program, without its block comments."""
    text = source()
    head, rest = text.split("\n    submit;\n", 1)
    return re.sub(r"/\*.*?\*/", "", head + "\n" + rest.split("\nendsubmit;", 1)[1], flags=re.DOTALL)


# ---- stand-ins -------------------------------------------------------------
class Response:
    def __init__(self, status, value=None, text=None, reason=None):
        self.status_code = status
        self.text = text if text is not None else ("" if value is None else json.dumps(value))
        if reason is not None:
            self.reason = reason

    def json(self):
        return json.loads(self.text)


def sas_error(status, message, **more):
    """An error of SAS Viya as it answers one: application/vnd.sas.error, version 2."""
    return Response(status, {"version": 2, "httpStatusCode": status, "message": message, "details": ["path: /x"], "errorCode": 0, **more})


class Viya:
    """SAS Viya and the model containers, as far as the job asks them. Every request is in `calls`."""

    def __init__(self, identity="uid7781", refuse=None):
        self.identity = identity
        # (method, path prefix) -> Response for a request that is refused
        self.refuse = refuse or {}
        self.calls = []
        self.files = {PROMPT: {}, "llm-target": {}}
        self.count = 0
        self.put(PROMPT, "Prompt-Experiment-Tracker.json", json.dumps(tracker_rows()).encode("utf-8"))
        self.put("llm-target", "options.json", json.dumps({"temperature": {"default": 0.2}}).encode("utf-8"))

    def put(self, model_id, name, data):
        self.count += 1
        self.files.setdefault(model_id, {})[name] = (f"c-{self.count}", data)

    def refused(self, method, path):
        for (refused_method, prefix), response in self.refuse.items():
            if refused_method == method and path.startswith(prefix):
                return response
        return None

    def answer(self, method, url, files=None, json_body=None):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        self.calls.append((method, path))
        refusal = self.refused(method, path)
        if refusal is not None:
            return refusal
        if path == "/identities/users/@currentUser":
            return Response(200, {"id": self.identity})
        if path.startswith("/credentials/domains/"):
            return Response(200, {"secrets": {"OpenAI": base64.b64encode(b"made-up-key").decode("ascii")}})
        if method == "GET" and path.startswith("/modelRepository/models/") and "/contents" in path:
            model_id = path.split("/")[3]
            return Response(200, {"items": [{"id": content_id, "name": name, "fileUri": f"/files/files/{model_id}--{content_id}"}
                                            for name, (content_id, _) in self.files.get(model_id, {}).items()]})
        if method == "GET" and path.startswith("/files/files/"):
            model_id, content_id = path[len("/files/files/"):-len("/content")].split("--")
            for held_id, data in self.files[model_id].values():
                if held_id == content_id:
                    return Response(200, text=data.decode("utf-8"))
            return sas_error(404, "No file was found.")
        if method == "POST" and files is not None:
            model_id = path.split("/")[3]
            name, data, _ = files["files"]
            self.put(model_id, name, data)
            return Response(201, {"items": [{"name": name}]})
        if method == "DELETE" and path.startswith("/modelRepository/models/"):
            model_id, content_id = path.split("/")[3], path.split("/")[5]
            self.files[model_id] = {name: held for name, held in self.files[model_id].items() if held[0] != content_id}
            return Response(204)
        if method == "POST" and path.startswith("/scr/"):
            return Response(200, {"data": {"response": "cold", "run_time": 0.5, "prompt_length": 20, "output_length": 4}})
        return sas_error(404, f"not in the stand-in: {method} {path}")

    def model_calls(self):
        return [call for call in self.calls if call[1].startswith("/scr/")]


def tracker_rows():
    rows = []
    for run_id, (city, answer) in enumerate((("Oslo", "cold"), ("Cairo", "hot"), ("Lima", "mild"), ("Nuuk", "cold"), ("Rome", "warm")), 1):
        rows.append({"runId": run_id, "model": "", "systemPrompt": "Say how the weather is.", "userPrompt": "Weather in {{city}}?",
                     "variables": [{"name": "city", "value": city}]})
        rows.append({"runId": run_id, "model": "target_llm", "response": answer, "best_prompt": 1})
    return rows


def requests_module(viya):
    module = types.ModuleType("requests")
    module.request = lambda method, url, **kwargs: viya.answer(method, url, files=kwargs.get("files"))
    module.get = lambda url, **kwargs: viya.answer("GET", url)
    module.post = lambda url, **kwargs: viya.answer("POST", url, files=kwargs.get("files"), json_body=kwargs.get("json"))
    return module


def dspy_module():
    dspy = types.ModuleType("dspy")
    dspy.__version__ = "3.2.1"
    dspy.settings = types.SimpleNamespace(lm=None)
    dspy.configure = lambda lm=None: setattr(dspy.settings, "lm", lm)

    class BaseLM:
        def __init__(self, model=None, **kwargs):
            self.model = model

    class Example(dict):
        def with_inputs(self, *names):
            self._inputs = list(names)
            return self

        def inputs(self):
            return {name: self[name] for name in self._inputs}

        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                raise AttributeError(name)

    class Signature:
        def __init__(self, text, instructions=""):
            self.instructions = instructions

    class Predict:
        def __init__(self, signature, demos=None):
            self.signature, self.demos = signature, list(demos or [])

        def predictors(self):
            return [self]

        def __call__(self, **inputs):
            answer = dspy.settings.lm.forward(messages=[{"role": "user", "content": json.dumps(inputs)}])
            return types.SimpleNamespace(response=answer.choices[0].message.content)

    class BootstrapFewShot:
        def __init__(self, metric=None, max_bootstrapped_demos=4, max_labeled_demos=4):
            pass

        def compile(self, program, trainset=None):
            return Predict(program.signature, (trainset or [])[:1])

    for item in (BaseLM, Example, Signature, Predict, BootstrapFewShot):
        setattr(dspy, item.__name__, item)
    dspy.Example = lambda **fields: Example(fields)
    dspy.Prediction = types.SimpleNamespace
    dspy.GEPA = object
    return dspy


class Sas:
    def __init__(self, symbols, work):
        self.symbols = dict(symbols)
        self.workpath = str(work) + "/"
        self.log = []

    def symget(self, name):
        return self.symbols.get(name)

    def symput(self, name, value):
        self.symbols[name] = value

    def logMessage(self, message):
        self.log.append(message)


SYMBOLS = {
    "promptModelId": PROMPT, "promptName": "Weather Prompt", "mode": "optimize",
    "targetModelId": "llm-target", "targetModelName": "target_llm", "targetModelNames": "",
    "scrEndpoint": "http://viya.invalid/scr", "deploymentType": "k8s", "datasetSource": "tracker",
    "metric": "exact", "judgeModelName": "", "optimizer": "bootstrap", "maxDemos": "2",
    "minSamples": "1", "casServer": "cas-shared-default", "casLibrary": "", "casTable": "",
    "keyDomain": "agentic-ai-keys", "_opt_viyaHost": "http://viya.invalid",
    "SYS_JES_JOB_URI": "/jobExecution/jobs/job-x", "_contextName": "SAS Studio compute context",
    "_opt_runsAs": "", "_opt_startedBy": "", "_opt_rc": "1", "_opt_error": "",
}


def run_job(tmp_path, monkeypatch, viya, **symbols):
    """The Python program of the job, run once to its end. Returns the SAS stand-in."""
    sas = Sas({**SYMBOLS, **symbols}, tmp_path)
    monkeypatch.setitem(sys.modules, "requests", requests_module(viya))
    monkeypatch.setitem(sys.modules, "dspy", dspy_module())
    monkeypatch.setenv("SAS_SERVICES_TOKEN", "made-up-token")
    namespace = {"__name__": "__optimize_job__", "SAS": sas}
    exec(compile(python_program(), str(PROGRAM_FILE), "exec"), namespace)
    sas.namespace = namespace
    return sas


# ---- the session runs as another account -----------------------------------
def test_a_session_of_another_account_ends_before_any_model_call_and_names_both(tmp_path, monkeypatch):
    viya = Viya(identity="sas.compute")
    sas = run_job(tmp_path, monkeypatch, viya, _opt_runsAs="sas", _opt_startedBy="uid7781", _contextName=JES)

    assert sas.symbols["_opt_rc"] == "1"
    error = sas.symbols["_opt_error"]
    assert error == (
        f"The optimization job runs as sas (to SAS Viya: sas.compute), not as uid7781, who started it: the compute context {JES} "
        "does not run as the user who starts the job. Point the option Optimization: compute context of the Prompt Builder at "
        "one that does, such as SAS Studio compute context. Nothing was called - that account resolves the provider keys and "
        "writes the result to the prompt, which it may not.")
    # the message reaches the log of the job and the panel as it was written: nothing sas_safe changes, and the
    # 500 characters the program hands on keep its remedy with a context name of 100 characters and longer users
    assert not set(error) & set("`'\"%&;")
    remedy_ends = error.index("such as SAS Studio compute context.") + len("such as SAS Studio compute context.")
    assert remedy_ends + (100 - len(JES)) + 2 * 40 < 500
    assert sas.log[-1] == "Optimization failed: " + error
    # nothing but the question who the token stands for: no model, no file, no provider key
    assert viya.calls == [("GET", "/identities/users/@currentUser")]
    assert viya.model_calls() == []


def test_a_session_of_another_account_without_a_context_name_and_without_an_answer_who_it_is(tmp_path, monkeypatch):
    viya = Viya(refuse={("GET", "/identities/"): sas_error(401, "Unauthorized")})
    sas = run_job(tmp_path, monkeypatch, viya, _opt_runsAs="svc4410", _opt_startedBy="uid7781", _contextName="")

    assert sas.symbols["_opt_error"].startswith(
        "The optimization job runs as svc4410, not as uid7781, who started it: its compute context does not run as the user")
    assert viya.model_calls() == []


def test_the_sas_code_compares_the_two_users_as_start_here_and_the_loader_do():
    """Load-Accelerator-Tables.sas compares SYS_COMPUTE_SESSION_OWNER with SYSUSERID as the persons they name: a domain
    behind @ or in front of a backslash is dropped, the case does not count, and a session without an owner - the
    program run in SAS Studio - is not asked. The job does the same before PROC PYTHON, and hands both on."""
    text = sas_code()
    check = text[text.index("%let _opt_runsAs = ;"):text.index("proc python restart;")]
    positions = [check.index(marker) for marker in (
        "%let _opt_runsAs = ;", "%let _opt_startedBy = ;", "data _null_;",
        "if symexist('SYS_COMPUTE_SESSION_OWNER') then do;",
        "_opt_owner = lowcase(scan(scan(strip(symget('SYS_COMPUTE_SESSION_OWNER')), -1, '\\'), 1, '@'));",
        "_opt_user = lowcase(scan(scan(strip(symget('SYSUSERID')), -1, '\\'), 1, '@'));",
        "if lengthn(_opt_owner) > 0 and strip(_opt_owner) ne strip(_opt_user) then do;",
        "call symputx('_opt_runsAs', strip(symget('SYSUSERID')));",
        "call symputx('_opt_startedBy', strip(symget('SYS_COMPUTE_SESSION_OWNER')));",
        "run;")]
    assert positions == sorted(positions)
    loader = (PROGRAM_FILE.parents[1] / "Setup" / "Load-Accelerator-Tables.sas").read_text(encoding="utf-8")
    assert "lowcase(scan(scan(strip(symget('SYS_COMPUTE_SESSION_OWNER')), -1, '\\'), 1, '@'))" in loader
    # no statement resolves either user as a macro reference
    assert not re.search(r"&sysuserid|&SYS_COMPUTE_SESSION_OWNER", text, flags=re.IGNORECASE)


def test_the_two_checks_come_before_the_first_read_and_the_first_model_call():
    main = python_program().split("def main():", 1)[1]
    order = ["check_session_user()", "import_dependencies()", "check_prompt_writable()",
             "load_tracker_dataset(", "build_model_options(", "call_scr("]
    positions = [main.index(marker) for marker in order]
    assert positions == sorted(positions)


# ---- the session may not write to the prompt -------------------------------
def test_a_prompt_the_user_may_not_add_a_file_to_ends_the_run_before_any_model_call(tmp_path, monkeypatch):
    viya = Viya(refuse={("POST", f"/modelRepository/models/{PROMPT}/contents"): sas_error(403, "Forbidden")})
    sas = run_job(tmp_path, monkeypatch, viya)

    assert sas.symbols["_opt_rc"] == "1"
    assert sas.symbols["_opt_error"] == (
        "The optimization job may not write to the prompt Weather Prompt in SAS Model Manager as uid7781: adding a file "
        "to it was answered with HTTP 403 (Forbidden). A run writes its result there, so nothing was called. Ask for the "
        "permission to change the prompt-test, or optimize one you may change.")
    assert viya.model_calls() == []
    # the experiments were not read either
    assert not [call for call in viya.calls if call[1].startswith("/files/files/")]


def test_a_prompt_the_user_may_add_to_but_not_remove_from_says_that_the_file_is_left(tmp_path, monkeypatch):
    viya = Viya(refuse={("DELETE", f"/modelRepository/models/{PROMPT}/contents/"): sas_error(
        403, "You are not authorized to delete this content.", remediation="Ask your administrator for access.")})
    sas = run_job(tmp_path, monkeypatch, viya)

    assert sas.symbols["_opt_error"] == (
        "The optimization job may not remove files from the prompt Weather Prompt in SAS Model Manager: removing "
        "Prompt-Optimization-Write-Check.json was answered with HTTP 403 (Forbidden): You are not authorized to delete this "
        "content. Ask your administrator for access. A run replaces its files there, so nothing was called. Ask for the "
        "permission to change the prompt-test - the file stays on it until it is removed.")
    assert viya.model_calls() == []
    # the file was added once, and then only the failed run was recorded, as every failed run is
    assert [call for call in viya.calls if call[0] != "GET"] == [
        ("POST", f"/modelRepository/models/{PROMPT}/contents"), ("DELETE", f"/modelRepository/models/{PROMPT}/contents/c-3"),
        ("POST", f"/modelRepository/models/{PROMPT}/contents")]
    assert json.loads(viya.files[PROMPT]["Prompt-Optimization-Tracker.json"][1])[0]["error"] == sas.symbols["_opt_error"]


def test_a_prompt_the_user_may_write_to_keeps_no_file_of_the_check_and_the_run_goes_on(tmp_path, monkeypatch):
    viya = Viya()
    # a file an earlier run left, when its session ended between the two requests
    viya.put(PROMPT, "Prompt-Optimization-Write-Check.json", b"{}")
    sas = run_job(tmp_path, monkeypatch, viya)

    assert sas.symbols["_opt_rc"] == "0", sas.symbols["_opt_error"]
    assert "Prompt-Optimization-Write-Check.json" not in viya.files[PROMPT]
    assert "Prompt-Optimization-Tracker.json" in viya.files[PROMPT]
    # the file the earlier run left is removed first: SAS Model Manager may refuse a second file of one name
    added = viya.calls.index(("POST", f"/modelRepository/models/{PROMPT}/contents"))
    removed = [index for index, call in enumerate(viya.calls) if call[0] == "DELETE"]
    first_model_call = viya.calls.index(viya.model_calls()[0])
    assert removed[0] < added < removed[1] < first_model_call
    assert sas.log[:2] == ["Checking that the result can be written to the prompt", "Loading the experiment tracker dataset"]


# ---- an HTTP error is a sentence --------------------------------------------
@pytest.fixture()
def http_failure(tmp_path, monkeypatch):
    viya = Viya()
    sas = run_job(tmp_path, monkeypatch, viya, promptModelId="")  # ends at the parameters; the functions are defined
    return sas.namespace["http_failure"]


def test_an_error_of_sas_viya_is_its_status_and_its_message(http_failure):
    # the answer of finding (b), as SAS Model Manager gave it
    assert http_failure(sas_error(403, "Forbidden")) == "HTTP 403 (Forbidden)."
    assert http_failure(sas_error(404, "No model with ID abc was found.")) == "HTTP 404 (Not Found): No model with ID abc was found."
    assert http_failure(sas_error(400, "The request is not valid", remediation="Correct the request.")) == \
        "HTTP 400 (Bad Request): The request is not valid. Correct the request."
    # the reason phrase of the answer goes before the one of the stand-in table
    assert http_failure(Response(403, {"message": "Forbidden"}, reason="Forbidden")) == "HTTP 403 (Forbidden)."


def test_an_answer_that_is_no_json_error_is_its_text_without_markup(http_failure):
    assert http_failure(Response(502, text="<html><body><h1>502 Bad Gateway</h1>\n<hr>nginx</body></html>")) == \
        "HTTP 502 (Bad Gateway): 502 Bad Gateway nginx."
    assert http_failure(Response(500, text="")) == "HTTP 500 (Internal Server Error)."
    assert http_failure(Response(503, text="x" * 500)) == "HTTP 503 (Service Unavailable): " + "x" * 300 + "..."
    # a JSON string and a JSON list are their text, shortened
    assert http_failure(Response(500, "The provider refused the key.")) == \
        "HTTP 500 (Internal Server Error): The provider refused the key."
    assert http_failure(Response(400, [{"loc": ["body", "inputs"], "msg": "field required"}])) == \
        'HTTP 400 (Bad Request): [{"loc":["body","inputs"],"msg":"field required"}].'
    assert http_failure(Response(400, ["y" * 400])).endswith("y" * 290 + "...")
    # a provider's error inside "error", and a FastAPI-style list in "detail"
    assert http_failure(Response(401, {"error": {"message": "The provider refused the key.", "type": "auth"}})) == \
        "HTTP 401 (Unauthorized): The provider refused the key."
    assert http_failure(Response(400, {"detail": [{"msg": "field required"}]})) == \
        'HTTP 400 (Bad Request): [{"msg":"field required"}].'


def scr_error(line, transaction="23579f6e-cb00-432c-9f68-9424c07700db"):
    """A model container (SCR) whose score code raised, as it answered on SAS Viya 2026.09 (2026-10-20):
    HTTP 422 with the reason in capitals, and the source line of the raise behind the status in errorMessage."""
    return Response(422, {"errorCode": 422, "errorMessage": "422 Unprocessable Entity:" + " " * 32 + line,
                          "httpStatus": "422 UNPROCESSABLE_ENTITY", "id": transaction,
                          "remediation": "Contact an administrator to view the full traceback logs. "
                                         f"The transaction id is {transaction}.", "version": "2"},
                    reason="UNPROCESSABLE ENTITY")


def test_an_error_of_a_model_container_is_the_text_of_its_raise_and_where_to_look(http_failure):
    remediation = "Contact an administrator to view the full traceback logs. The transaction id is 23579f6e-cb00-432c-9f68-9424c07700db."
    # the score code of this release: the message of the raise is on the line SCR answers with
    assert http_failure(scr_error('raise RuntimeError("No API key: pass the provider key in the API_KEY option of the call.")')) == \
        "HTTP 422 (Unprocessable Entity): No API key: pass the provider key in the API_KEY option of the call. " + remediation
    # a line that is no raise of a plain string is kept as it is
    assert http_failure(scr_error('raise RuntimeError(f"OpenAI answered {status}: {text}")')) == \
        'HTTP 422 (Unprocessable Entity): raise RuntimeError(f"OpenAI answered {status}: {text}"). ' + remediation
    # the containers published before it answered a KeyError with only the marks under the line
    assert http_failure(scr_error("~~~~~~~^^^^^^^^^^^")) == "HTTP 422 (Unprocessable Entity). " + remediation


def test_an_answer_never_carries_a_key_and_is_cut(http_failure):
    said = http_failure(Response(401, {"error": {"message": "Incorrect API key provided: sk-proj-abcdef1234567890ABCDEF. "
                                                            "Header Authorization: Bearer eyJhbGciOi.J9abc123def456 was refused, "
                                                            "api_key=AIzaSyA1234567890abcdefgh"}}))
    assert "abcdef1234567890" not in said and "eyJhbGciOi" not in said and "AIzaSyA1234567890" not in said, said
    assert said.startswith("HTTP 401 (Unauthorized): Incorrect API key provided: sk-***"), said
    # words are no keys
    assert http_failure(Response(401, {"message": "Basic authentication failed for the password authentication."})) == \
        "HTTP 401 (Unauthorized): Basic authentication failed for the password authentication."
    long = http_failure(sas_error(400, "m" * 1000, remediation="r" * 1000))
    assert long == "HTTP 400 (Bad Request): " + "m" * 300 + "... " + "r" * 300 + "..."


def test_a_model_container_that_raised_is_written_with_its_message_into_the_result(tmp_path, monkeypatch):
    """call_scr: the text of the raise is the error of the run - in the log of the job and in its tracker entry."""
    viya = Viya(refuse={("POST", "/scr/"): scr_error('raise RuntimeError("No API key: pass the provider key in the API_KEY option of the call.")')})
    sas = run_job(tmp_path, monkeypatch, viya)

    said = ("The call of the model container target_llm was answered with HTTP 422 (Unprocessable Entity): "
            "No API key: pass the provider key in the API_KEY option of the call. Contact an administrator to view the "
            "full traceback logs. The transaction id is 23579f6e-cb00-432c-9f68-9424c07700db.")
    assert viya.model_calls()
    assert sas.symbols["_opt_rc"] != "0"
    assert sas.symbols["_opt_error"] == said
    assert json.loads(viya.files[PROMPT]["Prompt-Optimization-Tracker.json"][1])[0]["error"] == said


def test_the_upload_that_was_refused_in_finding_b_is_written_as_a_sentence(tmp_path, monkeypatch):
    """The check passes, the upload of the dataset snapshot is refused: the message holds no JSON."""
    viya = Viya()
    answer = viya.answer

    def second_upload_refused(method, url, files=None, json_body=None):
        if files is not None and files["files"][0].startswith("Prompt-Optimization-Dataset-"):
            viya.calls.append((method, url))
            return sas_error(403, "Forbidden")
        return answer(method, url, files=files, json_body=json_body)

    viya.answer = second_upload_refused
    sas = run_job(tmp_path, monkeypatch, viya)

    assert sas.symbols["_opt_error"] == f"Uploading Prompt-Optimization-Dataset-1.json to the model {PROMPT} was answered with HTTP 403 (Forbidden)."


def test_no_message_of_the_program_writes_an_answer_as_it_came():
    program = python_program()
    assert "response.text[:300]" not in program
    assert program.count("http_failure(response)") == 6
