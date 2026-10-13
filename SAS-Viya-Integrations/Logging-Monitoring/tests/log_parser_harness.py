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
import uuid
from datetime import datetime, timedelta
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

    def __init__(self, log_path, ready="1", table_mode="REPLACE"):
        # _log_row_count starts at -1, as the SAS program sets it before PROC PYTHON, and _log_ready is 1
        # unless one of its checks refused the run. _log_table_mode is what the SAS program made of the
        # setting _log_append_table: APPEND, REPLACE or START
        self.macro_variables = {
            "_log_ready": ready,
            "_log_table_mode": table_mode,
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
        # swat hands the rows to CAS as a file it writes with to_csv(..., encoding='utf-8'), so a
        # text that has no UTF-8 bytes ends the upload there. The stand-in encodes the rows as well.
        frame.to_csv(index=False).encode("utf-8")
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


def run_parser(monkeypatch, log_path, python=None, ready="1", table_mode="REPLACE"):
    """Executes the parser's Python against a log file and returns the ParserRun.

    An exception of the parser is raised to the caller, after the FakeSAS object was
    attached to it as `sas`, so a test can still read the macro variables. ready is
    what the SAS program has set _log_ready to: "0" when one of its checks refused the run.
    table_mode is what it has set _log_table_mode to: "APPEND" for a run that appends.
    """
    fake_swat = types.ModuleType("swat")
    fake_swat.CAS = FakeCAS
    monkeypatch.setitem(sys.modules, "swat", fake_swat)
    monkeypatch.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    monkeypatch.setenv("SAS_SERVICES_TOKEN", "not-a-token")
    # The parser sets this one itself; registering it here lets pytest restore it
    monkeypatch.setenv("CAS_CLIENT_SSL_CA_LIST", "")

    sas = FakeSAS(log_path, ready, table_mode)
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


def made_up_id(*parts):
    """An id in the shape of the ones the containers log. The same block gets the same id
    wherever it is built, and blocks that differ in anything get different ones."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, repr(parts)))


def request_head(timestamp, model, inputs, transaction=None, headers=(), request_id=None,
                 user_agent="python-requests/2.32.5"):
    """What a container logs for a scoring request before the score code runs: the request
    line, the headers (one per line, without a timestamp, and closed by two empty lines),
    the request body and the first three lines of the transaction the request is given.

    inputs       the request inputs as (name, value) pairs
    headers      further request header lines
    """
    transaction = transaction or made_up_id(timestamp, model, inputs)
    request_id = request_id or made_up_id("request", timestamp, model, inputs).replace("-", "")
    request_data = {"inputs": [{"name": name, "value": value} for name, value in inputs]}
    transaction_logger = f"transaction_{transaction}"
    return [
        log_line(timestamp, "api", f"Request: POST /{model}"),
        log_line(timestamp, "api", "Request Headers: Host: viya.example.com"),
        f"X-Request-Id: {request_id}",
        "X-Real-Ip: 192.0.2.17",
        "X-Forwarded-For: 192.0.2.17",
        *([f"User-Agent: {user_agent}"] if user_agent is not None else []),
        "Content-Type: application/json",
        *headers,
        "",
        "",
        # The containers log the body as a Python literal, which is what repr writes
        log_line(timestamp, "api", f"Request Data: {request_data!r}"),
        log_line(timestamp, "api", "Randomly generated client_id is None"),
        log_line(timestamp, "api", "Randomly generated transaction_id is None"),
        log_line(timestamp, transaction_logger, f"Score request received from POST /{model}"),
        log_line(timestamp, transaction_logger, "Score request content type: application/json"),
        log_line(timestamp, transaction_logger, "The data was successfully formatted into a pandas DataFrame."),
    ]


def request_block(timestamp, model, inputs, score_lines, transaction=None, headers=(), status=200, finished=None, **head):
    """One scoring request the way a container logs it at TRACE level.

    inputs       the request inputs as (name, value) pairs
    score_lines  what the score code logs, as (key, value) pairs
    headers      further request header lines (they follow the Host header, one per line, without a timestamp)
    finished     the time of the lines that are logged once the model has answered - the time of the request line by default
    """
    transaction = transaction or made_up_id(timestamp, model, inputs)
    transaction_logger = f"transaction_{transaction}"
    finished = finished or timestamp
    lines = request_head(timestamp, model, inputs, transaction=transaction, headers=headers, **head)
    lines += [log_line(finished, "scoreModel", f"{key}: {value}") for key, value in score_lines]
    lines += [
        log_line(finished, transaction_logger, f"The model was successfully scored using the scoreModel function from the {model}.py file in 0.5 seconds."),
        log_line(finished, "api", f"Elapsed time for /{model} was 500000000 nanoseconds"),
        log_line(finished, "api", f"Response: Status Code {status}"),
        log_line(finished, "api", f"Response Data: \"{{'client_id': '{made_up_id('client', transaction)}', 'module_id': '{model}.py', 'step_id': 'scoreModel', 'transaction_id': '{transaction}'}}\""),
    ]
    return lines


PROVIDER_ERROR = 'HTTP 401: {"error": {"message": "Missing Authentication header", "code": 401}}'


def failed_block(timestamp, model, inputs, error=PROVIDER_ERROR, exception="RuntimeError: Request failed: 401",
                 transaction=None, finished=None, **head):
    """A scoring request whose score code raised. The score code logs the answer of the
    provider (error; None for an exception it did not see coming), the container logs the
    traceback and one more line, and nothing follows: no 'Response:' line is written."""
    transaction = transaction or made_up_id(timestamp, model, inputs)
    transaction_logger = f"transaction_{transaction}"
    finished = finished or timestamp
    lines = request_head(timestamp, model, inputs, transaction=transaction, **head)
    if error is not None:
        lines.append(log_line(finished, "scoreModel", error, level="ERROR"))
    lines += [
        log_line(finished, transaction_logger, "An exception occurred within the scoreModel function of the score code:", level="ERROR"),
        "Traceback (most recent call last):",
        '  File "app_scoring.py", line 217, in score',
        "    scores = score_function(",
        "             ^^^^^^^^^^^^^^^",
        f'  File "{model}.py", line 134, in scoreModel',
        "    answer = ask_the_provider(payload)",
        exception,
        log_line(finished, transaction_logger, "Error occurred in the model scoring function:     answer = ask_the_provider(payload)", level="ERROR"),
    ]
    return lines


def split_at_scoring(block):
    """A block in two parts: what is logged before the score code runs, and the rest."""
    scoring = next(index for index, line in enumerate(block) if "The data was successfully formatted" in line) + 1
    return block[:scoring], block[scoring:]


def is_log_line(line):
    return line[:4].isdigit() and line[4:5] == "-" and " - " in line


def log_units(block):
    """A block as its log lines, each with the lines behind it that carry no timestamp (the
    headers, a response that goes on, a traceback): what a container writes in one piece.
    The first five are the request line, the headers, the request data and the two
    'Randomly generated' lines, the three behind them the first lines of the transaction."""
    units = []
    for line in block:
        if is_log_line(line):
            units.append([line])
        else:
            units[-1].append(line)
    return units


def lines_of(units):
    return [line for unit in units for line in unit]


def in_file_order(lines, start="2026-01-05 09:00:00"):
    """The lines with the times a file would show them with: every log line one second later
    than the one before, whatever time its block was built with. Returns the lines and the
    times of the request lines, in their order."""
    moment = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    timed, request_times = [], []
    for line in lines:
        if is_log_line(line):
            moment += timedelta(seconds=1)
            line = moment.strftime("%Y-%m-%d %H:%M:%S") + line[19:]
            if " - Request: POST" in line:
                request_times.append(line[:19])
        timed.append(line)
    return timed, request_times


def get_block(timestamp, path="/openapi.json"):
    """A request that is no scoring call - the containers log them between the scoring requests."""
    return [
        log_line(timestamp, "api", f"Request: GET {path}"),
        log_line(timestamp, "api", "Request Headers: Host: viya.example.com"),
        "User-Agent: curl/8.5.0",
        "Accept: application/json",
        "",
        "",
        log_line(timestamp, "api", "Randomly generated client id is aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        log_line(timestamp, "api", "Randomly generated transaction id is aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"),
    ]


def startup_lines(timestamp, model):
    """What a container writes when it starts, before it has served a request."""
    return [
        log_line(timestamp, "root", f"The model name is {model}."),
        log_line(timestamp, "root", "The server is listening on port 8080."),
    ]


LLM_INPUT_NAMES = ("systemPrompt", "userPrompt", "options")


def llm_inputs(system_prompt="You are a concise assistant.", user_prompt="What is the capital of Austria?",
               options="{temperature:0.2,top_p:1,max_tokens:40}"):
    return [("systemPrompt", system_prompt), ("userPrompt", user_prompt), ("options", options)]


def llm_block(timestamp="2026-01-05 09:15:00", model="demo_llm", system_prompt="You are a concise assistant.",
              user_prompt="What is the capital of Austria?", options="{temperature:0.2,top_p:1,max_tokens:40}",
              prompt_length=21, output_length=9, run_time=1.25, response="The capital of Austria is Vienna.", **more):
    return request_block(
        timestamp, model,
        llm_inputs(system_prompt, user_prompt, options),
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


def decision_inputs(state=DECISION_STATE, questions=DECISION_QUESTIONS, options="{}"):
    return [("state", state), ("questions", questions), ("options", options)]


def decision_block(timestamp="2026-01-05 09:20:00", model="demo_decider", state=DECISION_STATE, questions=DECISION_QUESTIONS,
                   options="{}", prompt_length=120, output_length=14, run_time=0.4, answers=DECISION_ANSWERS,
                   answer="billing (0.900)", **more):
    return request_block(
        timestamp, model,
        decision_inputs(state, questions, options),
        [("prompt_length", prompt_length), ("output_length", output_length), ("run_time", run_time),
         ("answer", answer), ("answers", answers)],
        **more)


EMBEDDED_DOCUMENT = "Chapter 7 of the staff handbook: travel expenses are refunded within thirty days."


def embedding_inputs(document=EMBEDDED_DOCUMENT, options="{Embedding_Mode:document}", project="handbook"):
    return [("document", document), ("options", options), ("project", project)]


def embedding_block(timestamp="2026-01-05 09:25:00", model="demo_embedder", document=EMBEDDED_DOCUMENT,
                    options="{Embedding_Mode:document}", project="handbook", tokens=17, run_time=0.08, **more):
    return request_block(
        timestamp, model,
        embedding_inputs(document, options, project),
        [("project", project), ("tokens", tokens), ("run_time", run_time)],
        **more)


def retrieval_envelope(collection="staff_handbook_v1", query="How long does a travel refund take?", hits=2):
    """The results of a retrieval as the RAG runtime hands them to a prompt: one JSON
    object that names the collection and the question beside the hits."""
    return json.dumps({
        "query": query,
        "collection": collection,
        "ingestion_run_id": "20260105T080000Z-1a2b3c",
        "retrieval_mode": "dense",
        "hits": [{"rank": rank + 1, "score": round(0.9 - rank / 10, 2), "text": f"Made-up passage {rank + 1} {{with braces}}.",
                  "metadata": {"source_uri": f"handbook/chapter-{rank + 7}.pdf", "page": rank + 3}} for rank in range(hits)],
        "graph_context": [],
    })


def as_log(*blocks):
    """Joins blocks of lines into the text of a log file."""
    return "".join(line + "\n" for block in blocks for line in block)
