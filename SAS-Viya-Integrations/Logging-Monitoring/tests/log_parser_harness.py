"""Runs the log parser's Python outside of SAS and builds synthetic container logs for it.

The parser lives twice: in Log-Parser-Code.sas and in the SAS program of the
"LLM - Log Parser" custom step. Both hold it between "submit;" and "endsubmit;" of
a PROC PYTHON step, where SAS provides the SAS object and the compute server the
swat package. Here both are replaced by stand-ins that record what the parser does
with them, so the whole block runs unchanged under pytest.

Every log line in the tests is made up by the builders below. They follow the line
format of the SCR containers, they are not copies of real requests.
"""
import json
import sys
import types
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[2]
CODE_FILE = INTEGRATIONS / "Logging-Monitoring" / "Log-Parser-Code.sas"
STEP_FILE = INTEGRATIONS / "Custom-Steps" / "LLM - Log Parser.step"


def code_file_program():
    """The SAS program of the code file, with LF line endings."""
    return CODE_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")


def step_definition():
    return json.loads(STEP_FILE.read_text(encoding="utf-8"))


def step_program():
    """The SAS program the custom step runs."""
    return step_definition()["templates"]["SAS"].replace("\r\n", "\n")


def python_block(sas_program):
    """The Python between the submit; and endsubmit; lines of a SAS program."""
    lines = sas_program.split("\n")
    start = lines.index("submit;")
    end = lines.index("endsubmit;")
    assert start < end
    return "\n".join(lines[start + 1:end]) + "\n"


class FakeSAS:
    """Stands in for the SAS object PROC PYTHON provides."""

    def __init__(self, log_path):
        # _log_row_count starts at -1, as the SAS program sets it before PROC PYTHON
        self.macro_variables = {
            "_log_path": str(log_path),
            "_casSessionUUID": "00000000-0000-0000-0000-000000000000",
            "_log_row_count": "-1",
        }
        self.messages = []

    def symget(self, name):
        return self.macro_variables[name]

    def symput(self, name, value):
        self.macro_variables[name] = value

    def sasfnc(self, function, *arguments):
        return {"cashost": "cas.example.com", "casport": "5570"}[arguments[0]]

    def logMessage(self, message, messageType="NOTE"):
        self.messages.append((messageType, message))


class FakeCAS:
    """Stands in for swat.CAS and keeps every upload it is asked for."""

    def __init__(self, **connection):
        self.connection = connection
        self.uploads = []

    def upload_frame(self, frame, casout=None, importoptions=None):
        self.uploads.append({"frame": frame.copy(), "casout": casout, "importoptions": importoptions})
        return object()


class ParserRun:
    """What one run of the parser's Python left behind."""

    def __init__(self, namespace, sas):
        self.namespace = namespace
        self.sas = sas

    @property
    def row_count(self):
        return self.sas.macro_variables["_log_row_count"]

    @property
    def uploads(self):
        return self.namespace["conn"].uploads


def run_parser(monkeypatch, log_path, python=None):
    """Executes the parser's Python against a log file and returns the ParserRun.

    An exception of the parser is raised to the caller, after the FakeSAS object was
    attached to it as `sas`, so a test can still read the macro variables.
    """
    fake_swat = types.ModuleType("swat")
    fake_swat.CAS = FakeCAS
    monkeypatch.setitem(sys.modules, "swat", fake_swat)
    monkeypatch.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    monkeypatch.setenv("SAS_SERVICES_TOKEN", "not-a-token")
    # The parser sets this one itself; registering it here lets pytest restore it
    monkeypatch.setenv("CAS_CLIENT_SSL_CA_LIST", "")

    sas = FakeSAS(log_path)
    namespace = {"SAS": sas, "__name__": "log_parser"}
    source = python if python is not None else python_block(code_file_program())
    try:
        exec(compile(source, "Log-Parser-Code.sas (python)", "exec"), namespace)
    except Exception as error:
        error.sas = sas
        raise
    return ParserRun(namespace, sas)


def parse(monkeypatch, tmp_path, text):
    """Writes the text as a log file, runs the parser and returns the frame as parse_log_file returns it."""
    log_path = tmp_path / "llms.log"
    log_path.write_text(text, encoding="utf-8", newline="")
    run = run_parser(monkeypatch, log_path)
    return run.namespace["parse_log_file"](str(log_path))


# ---------------------------------------------------------------------------
# Synthetic log lines in the format the SCR containers write
# ---------------------------------------------------------------------------

def log_line(timestamp, logger, message, level="INFO"):
    return f"{timestamp} - {logger} - {level} - {message}"


def request_block(timestamp, model, inputs, score_lines, transaction="11111111-2222-3333-4444-555555555555",
                  headers=(), status=200):
    """One scoring request the way a container logs it at TRACE level.

    inputs       the request inputs as (name, value) pairs
    score_lines  what the score code logs, as (key, value) pairs
    headers      further request header lines (they follow the Host header, one per line, without a timestamp)
    """
    request_data = {"inputs": [{"name": name, "value": value} for name, value in inputs]}
    transaction_logger = f"transaction_{transaction}"
    lines = [
        log_line(timestamp, "api", f"Request: POST /{model}"),
        log_line(timestamp, "api", "Request Headers: Host: viya.example.com"),
        "Content-Type: application/json",
        *headers,
        "",
        "",
        # The containers log the body as a Python literal, which is what repr writes
        log_line(timestamp, "api", f"Request Data: {request_data!r}"),
        log_line(timestamp, "api", "Randomly generated client_id is None"),
        log_line(timestamp, transaction_logger, f"Score request received from POST /{model}"),
        log_line(timestamp, transaction_logger, "The data was successfully formatted into a pandas DataFrame."),
    ]
    lines += [log_line(timestamp, "scoreModel", f"{key}: {value}") for key, value in score_lines]
    lines += [
        log_line(timestamp, transaction_logger, f"The model was successfully scored using the scoreModel function from the {model}.py file in 0.5 seconds."),
        log_line(timestamp, "api", f"Elapsed time for /{model} was 500000000 nanoseconds"),
        log_line(timestamp, "api", f"Response: Status Code {status}"),
        log_line(timestamp, "api", f"Response Data: \"{{'module_id': '{model}.py', 'transaction_id': '{transaction}'}}\""),
    ]
    return lines


def get_block(timestamp, path="/openapi.json"):
    """A request that is no scoring call - the containers log them between the scoring requests."""
    return [
        log_line(timestamp, "api", f"Request: GET {path}"),
        log_line(timestamp, "api", "Request Headers: Host: viya.example.com"),
        "Accept: application/json",
        "",
        "",
        log_line(timestamp, "api", "Randomly generated client id is aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ]


def startup_lines(timestamp, model):
    """What a container writes when it starts, before it has served a request."""
    return [
        log_line(timestamp, "root", f"The model name is {model}."),
        log_line(timestamp, "root", "The server is listening on port 8080."),
    ]


def llm_block(timestamp="2026-01-05 09:15:00", model="demo_llm", system_prompt="You are a concise assistant.",
              user_prompt="What is the capital of Austria?", options="{temperature:0.2,top_p:1,max_tokens:40}",
              prompt_length=21, output_length=9, run_time=1.25, response="The capital of Austria is Vienna.", **more):
    return request_block(
        timestamp, model,
        [("systemPrompt", system_prompt), ("userPrompt", user_prompt), ("options", options)],
        [("prompt_length", prompt_length), ("output_length", output_length), ("run_time", run_time), ("response", response)],
        **more)


DECISION_QUESTIONS = json.dumps({
    "department": {"type": "choice", "instructions": "Which team owns this ticket?",
                   "criteria": {"billing": "charges and refunds", "technical": "bugs and outages", "unknown": "not enough information"}},
    "refund": {"type": "noul", "instructions": "Is the customer asking for money back?"},
}, separators=(",", ":"))

DECISION_ANSWERS = json.dumps({
    "department": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.9, "technical": 0.05, "unknown": 0.05}, "confidence": 0.9},
    "refund": {"type": "noul", "noul": 0.75},
})

DECISION_STATE = "I was charged twice for my plan this morning and need one charge returned."


def decision_block(timestamp="2026-01-05 09:20:00", model="demo_decider", state=DECISION_STATE, questions=DECISION_QUESTIONS,
                   options="{}", prompt_length=120, output_length=14, run_time=0.4, answers=DECISION_ANSWERS, **more):
    return request_block(
        timestamp, model,
        [("state", state), ("questions", questions), ("options", options)],
        [("prompt_length", prompt_length), ("output_length", output_length), ("run_time", run_time),
         ("answer", "billing (0.900)"), ("answers", answers)],
        **more)


EMBEDDED_DOCUMENT = "Chapter 7 of the staff handbook: travel expenses are refunded within thirty days."


def embedding_block(timestamp="2026-01-05 09:25:00", model="demo_embedder", document=EMBEDDED_DOCUMENT,
                    options="{Embedding_Mode:document}", project="handbook", tokens=17, run_time=0.08, **more):
    return request_block(
        timestamp, model,
        [("document", document), ("options", options), ("project", project)],
        [("project", project), ("tokens", tokens), ("run_time", run_time)],
        **more)


def as_log(*blocks):
    """Joins blocks of lines into the text of a log file."""
    return "".join(line + "\n" for block in blocks for line in block)
