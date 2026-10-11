"""Runs the Python of Link-Logs-To-Models.sas outside of SAS and builds log rows and catalog rows for it.

The program holds its Python between "submit;" and "endsubmit;" of a PROC PYTHON step,
where SAS provides the SAS object and the compute server the swat package. Here both
are replaced by stand-ins. The stand-in for CAS keeps tables as pandas frames and does
what the program asks CAS for - list the columns, hand out rows page by page, number
the rows in a copy, merge the link columns back, count rows, order columns - so the
whole block runs unchanged under pytest. It does not show what CAS does with these
calls, only that the program makes them in an order that leaves the right table.

Every log row and catalog row in the tests is made up by the builders below. They
have the columns the log parser and Get-All-Prompts.sas write, they are not copies of
real requests.
"""
import hashlib
import json
import re
import sys
import types
from datetime import datetime
from pathlib import Path

import pandas as pd

INTEGRATIONS = Path(__file__).resolve().parents[2]
CODE_FILE = INTEGRATIONS / "Logging-Monitoring" / "Link-Logs-To-Models.sas"

# The log table: the twelve columns of every release, the columns the parser of this
# release adds, and the link columns, which the parser leaves empty.
OLD_COLUMNS = [
    "timestamp", "model", "system_prompt", "user_prompt", "temperature", "top_p", "top_k", "max_tokens",
    "prompt_length", "output_length", "runtime", "response",
]
PARSER_COLUMNS = [
    "kind", "status_code", "failed", "error_text", "transaction_id", "request_id", "user_agent", "embedding_mode",
    "project", "input_hash", "answer", "answer_confidence", "options", "rag_collection", "rag_query_hash",
]
LINK_COLUMNS = [
    "model_id", "caller_model_id", "caller_project_id", "caller_kind", "caller_name", "experiment_run_id",
    "rag_model_id", "match_method", "match_candidates",
]
LOG_COLUMNS = OLD_COLUMNS + PARSER_COLUMNS + LINK_COLUMNS

CATALOG_COLUMNS = [
    "modelID", "projectID", "projectName", "name", "role", "runID", "target_model", "system_template", "user_template",
    "resolved_system_prompt", "resolved_user_prompt", "collection", "template_source", "resolved_system_hash", "resolved_user_hash",
]
RUNS_COLUMNS = ["run_id", "collection", "embed_model", "started_at", "finished_at", "modelID", "projectID"]


def code_file_program():
    """The SAS program, with LF line endings."""
    return CODE_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")


def python_block(sas_program):
    """The Python between the submit; and endsubmit; lines of a SAS program."""
    lines = sas_program.split("\n")
    start = lines.index("submit;")
    end = lines.index("endsubmit;")
    assert start < end
    return "\n".join(lines[start + 1:end]) + "\n"


def text_hash(text):
    """The hash the three programs share: SHA-256 of the text with its whitespace squeezed."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Stand-ins for SAS and CAS
# ---------------------------------------------------------------------------

class FakeSAS:
    """Stands in for the SAS object PROC PYTHON provides."""

    def __init__(self, **macro_variables):
        # The values the SAS program has set when PROC PYTHON starts
        self.macro_variables = {
            "_casSessionUUID": "00000000-0000-0000-0000-000000000000",
            "_lnk_ready": "1",
            "_lnk_log_caslib": "Public",
            "_lnk_log_table": "LLM_LOGS",
            "_lnk_catalog_caslib": "Public",
            "_lnk_catalog_table": "MODEL_CATALOG",
            "_lnk_runs_caslib": "Public",
            "_lnk_runs_table": "RAG_RUNS",
            "_lnk_runs_exists": "1",
            "_lnk_redo_all": "0",
            "_lnk_min_literal": "20",
            "_lnk_utc_offset": "0",
            "_lnk_row_count": "-1",
            "_lnk_refused": "0",
        }
        self.macro_variables.update(macro_variables)
        self.messages = []

    def symget(self, name):
        return self.macro_variables[name]

    def symput(self, name, value):
        self.macro_variables[name] = value

    def sasfnc(self, function, *arguments):
        return {"cashost": "cas.example.com", "casport": "5570"}[arguments[0]]

    def logMessage(self, message, messageType="NOTE"):
        self.messages.append((messageType, message))

    def lines(self, message_type):
        return [message for kind, message in self.messages if kind == message_type]


class FakeResult(dict):
    severity = 0
    reason = ""
    status = ""


STAGE_STEP = re.compile(r"data '(?P<out>[^']+)'n\(caslib='(?P<outlib>[^']+)'\); set '(?P<src>[^']+)'n\(caslib='(?P<srclib>[^']+)'\); "
                        r"_lnk_row = _n_; _lnk_one = 1; run;")
MERGE_STEP = re.compile(r"data '(?P<out>[^']+)'n\(caslib='(?P<outlib>[^']+)' drop=(?P<outdrop>[^)]+)\); "
                        r"merge '(?P<left>[^']+)'n\(caslib='(?P<leftlib>[^']+)' in=_lnk_in drop=(?P<leftdrop>[^)]+)\) "
                        r"'(?P<right>[^']+)'n\(caslib='(?P<rightlib>[^']+)'\); by _lnk_one _lnk_row; if _lnk_in; run;")


class FakeCAS:
    """Stands in for swat.CAS. Tables are pandas frames by (caslib, name), both without case."""

    # What the tests put into CAS before the program runs, and what a table says of its column labels
    tables = {}
    labels = {}
    # Rows that are added to the log table while the program reads it, to show what the row check does
    rows_added_meanwhile = None

    def __init__(self, **connection):
        self.connection = connection
        self.actions = []
        self.uploads = []
        FakeCAS.last = self

    @staticmethod
    def key(caslib, name):
        return (caslib.upper(), name.upper())

    def table(self, caslib, name):
        return FakeCAS.tables[self.key(caslib, name)]

    def retrieve(self, action, **parameters):
        self.actions.append((action, parameters))
        return getattr(self, "_" + action.replace(".", "_"))(**parameters)

    def _table_columnInfo(self, table):
        frame = self.table(table["caslib"], table["name"])
        labels = FakeCAS.labels.get(self.key(table["caslib"], table["name"]), {})
        info = pd.DataFrame({"Column": list(frame.columns), "Label": [labels.get(column, "") for column in frame.columns]})
        return FakeResult(ColumnInfo=info)

    def _table_fetch(self, table, index, sasTypes, maxRows, fetchVars=None, **paging):
        frame = self.table(table["caslib"], table["name"])
        if table.get("where") == "missing(match_method)":
            frame = frame[frame["match_method"].isna() | (frame["match_method"] == "")]
        elif table.get("where"):
            raise AssertionError(f"a where clause the stand-in does not know: {table['where']}")
        if fetchVars:
            frame = frame[[variable["name"] for variable in fetchVars]]
        assert paging["to"] - paging["from"] + 1 <= maxRows
        page = frame.iloc[paging["from"] - 1:paging["to"]]
        return FakeResult(Fetch=page.reset_index(drop=True)) if len(page) else FakeResult()

    def _dataStep_runCode(self, code, single):
        assert single == "yes"
        staged = STAGE_STEP.fullmatch(code)
        merged = MERGE_STEP.fullmatch(code)
        if staged:
            frame = self.table(staged["srclib"], staged["src"]).copy()
            frame["_lnk_row"] = range(1, len(frame) + 1)
            frame["_lnk_one"] = 1
            FakeCAS.tables[self.key(staged["outlib"], staged["out"])] = frame
            if FakeCAS.rows_added_meanwhile is not None:
                source = self.key(staged["srclib"], staged["src"])
                records = FakeCAS.tables[source].to_dict("records") + FakeCAS.rows_added_meanwhile.to_dict("records")
                FakeCAS.tables[source] = pd.DataFrame(records, columns=FakeCAS.tables[source].columns)
        elif merged:
            left = self.table(merged["leftlib"], merged["left"]).drop(columns=merged["leftdrop"].split())
            right = self.table(merged["rightlib"], merged["right"])
            frame = left.merge(right, on=["_lnk_one", "_lnk_row"], how="left").sort_values(["_lnk_one", "_lnk_row"])
            FakeCAS.tables[self.key(merged["outlib"], merged["out"])] = frame.drop(columns=merged["outdrop"].split()).reset_index(drop=True)
        else:
            raise AssertionError(f"a DATA step the stand-in does not know: {code}")
        return FakeResult()

    def _simple_numRows(self, table):
        return FakeResult(numrows=len(self.table(table["caslib"], table["name"])))

    def _table_alterTable(self, caslib, name, columnOrder):
        frame = self.table(caslib, name)
        ordered = [column for column in columnOrder if column in frame.columns]
        FakeCAS.tables[self.key(caslib, name)] = frame[ordered + [column for column in frame.columns if column not in ordered]]
        return FakeResult()

    def _table_dropTable(self, caslib, name, quiet):
        FakeCAS.tables.pop(self.key(caslib, name), None)
        return FakeResult()

    def upload_frame(self, frame, casout=None, importoptions=None):
        self.uploads.append({"frame": frame.copy(), "casout": casout, "importoptions": importoptions})
        FakeCAS.tables[self.key(casout["caslib"], casout["name"])] = frame.copy()
        return object()


class LinkRun:
    """What one run of the program's Python left behind."""

    def __init__(self, namespace, sas):
        self.namespace = namespace
        self.sas = sas

    @property
    def cas(self):
        return self.namespace.get("conn")

    @property
    def new_table(self):
        """The table the SAS program puts in the place of the log table, None when the Python built none."""
        return FakeCAS.tables.get(("PUBLIC", "LLM_LOGS_NEW"))


def run_program(monkeypatch, tables=None, python=None, labels=None, added_meanwhile=None, **macro_variables):
    """Executes the program's Python and returns the LinkRun.

    tables           what is in CAS, as {(caslib, name): frame}. Without tables _lnk_ready is 0,
                     as the SAS program sets it when a table is missing, and the Python only
                     defines its functions.
    labels           the column labels of a table, as {(caslib, name): {column: label}}
    added_meanwhile  rows another session appends to the log table after its rows were numbered
    """
    fake_swat = types.ModuleType("swat")
    fake_swat.CAS = FakeCAS
    monkeypatch.setitem(sys.modules, "swat", fake_swat)
    monkeypatch.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    monkeypatch.setenv("SAS_SERVICES_TOKEN", "not-a-token")
    # The program sets this one itself; registering it here lets pytest restore it
    monkeypatch.setenv("CAS_CLIENT_SSL_CA_LIST", "")
    monkeypatch.setattr(FakeCAS, "tables", {FakeCAS.key(*key): frame.copy() for key, frame in (tables or {}).items()})
    monkeypatch.setattr(FakeCAS, "labels", {FakeCAS.key(*key): value for key, value in (labels or {}).items()})
    monkeypatch.setattr(FakeCAS, "rows_added_meanwhile", added_meanwhile)

    if tables is None:
        macro_variables.setdefault("_lnk_ready", "0")
    sas = FakeSAS(**macro_variables)
    namespace = {"SAS": sas, "__name__": "link_logs"}
    source = python if python is not None else python_block(code_file_program())
    try:
        exec(compile(source, "Link-Logs-To-Models.sas (python)", "exec"), namespace)
    except Exception as error:
        error.sas = sas
        raise
    return LinkRun(namespace, sas)


def functions(monkeypatch):
    """The functions of the program, without a connection to CAS."""
    return run_program(monkeypatch).namespace


# ---------------------------------------------------------------------------
# Log rows the way the parser writes them
# ---------------------------------------------------------------------------

def sas_datetime(timestamp):
    """A 'YYYY-MM-DD HH:MM:SS' text as the SAS datetime the timestamp column holds."""
    return (datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S") - datetime(1960, 1, 1)).total_seconds()


def envelope(collection="handbook_v1", query="How many days of leave does a new employee get?", run_id="run-1767604500"):
    """What a retrieval hands to the prompt that asked for it."""
    return json.dumps({
        "query": query,
        "hits": [{"rank": 1, "score": 0.82, "text": "New employees get 25 days of leave per year.", "source": "handbook.pdf"}],
        "graph_context": [],
        "retrieval_mode": "dense",
        "collection": collection,
        "ingestion_run_id": run_id,
    })


def log_row(defaults, values):
    """A row of the log table: the defaults of its kind, then what the test sets. timestamp is a 'YYYY-MM-DD HH:MM:SS' text."""
    row = dict.fromkeys(LOG_COLUMNS)
    row.update(timestamp="2026-01-05 09:15:00", status_code=200.0, failed=0.0, user_agent="python-requests",
               transaction_id="11111111-2222-3333-4444-555555555555", options="{temperature:0.2}")
    row.update(defaults)
    row.update(values)
    row["timestamp"] = sas_datetime(row["timestamp"])
    return row


def llm_row(system_prompt="You are a concise assistant for the travel desk.", user_prompt="What is the capital of Austria?", retrieval=None, **values):
    """An LLM request. retrieval is the (collection, query) of the envelope its user prompt holds."""
    defaults = dict(kind="llm", model="demo_llm", system_prompt=system_prompt, user_prompt=user_prompt, prompt_length=21.0, output_length=9.0,
                    runtime=1.25, response="The capital of Austria is Vienna.")
    if retrieval is not None:
        defaults.update(rag_collection=retrieval[0], rag_query_hash=text_hash(retrieval[1]))
    return log_row(defaults, values)


QUESTIONS = {
    "department": {"type": "choice", "instructions": "Which team owns this ticket?",
                   "criteria": {"billing": "charges and refunds", "technical": "bugs and outages", "unknown": "not enough information"}},
    "refund": {"type": "noul", "instructions": "Is the customer asking for money back?"},
}
QUESTIONS_AS_SENT = json.dumps(QUESTIONS, separators=(",", ":"))


def decision_row(questions=QUESTIONS_AS_SENT, state="Ticket: I was charged twice for my plan this morning.", **values):
    """A decision request: the questions are in system_prompt and the state in user_prompt."""
    defaults = dict(kind="decision", model="demo_decider", system_prompt=questions, user_prompt=state, prompt_length=120.0, output_length=14.0, runtime=0.4,
                    response='{"department": {"type": "choice", "choice": "billing", "confidence": 0.9}}', answer="billing", answer_confidence=0.9)
    return log_row(defaults, values)


def embedding_row(document="How many days of leave does a new employee get?", mode="query", **values):
    """An embedding request: the text is not in the table, only its hash."""
    defaults = dict(kind="embedding", model="demo_embedder", embedding_mode=mode, project="rag", input_hash=text_hash(document), prompt_length=17.0,
                    runtime=0.08, options="{Embedding_Mode:" + mode + "}")
    return log_row(defaults, values)


def log_table(*rows):
    return pd.DataFrame(list(rows), columns=LOG_COLUMNS)


# ---------------------------------------------------------------------------
# Catalog rows the way Get-All-Prompts.sas writes them
# ---------------------------------------------------------------------------

def uuid(number):
    return f"00000000-0000-0000-0000-{number:012d}"


def catalog_row(model_id, role, name, **values):
    row = dict.fromkeys(CATALOG_COLUMNS, "")
    row.update(modelID=model_id, role=role, name=name, projectID=uuid(900), projectName="Demo project")
    row.update(values)
    return row


def answer_model(name, role="llm", model_id=None):
    """A model that answers requests: its name is the name of its container."""
    return catalog_row(model_id or uuid(1), role, name)


def prompt_template(model_id, name, system_template, user_template, target_model="", role="prompt"):
    """The saved template of a prompt (or, with role decision, of a decision template)."""
    return catalog_row(model_id, role, name, system_template=system_template, user_template=user_template, target_model=target_model)


def experiment_run(model_id, name, run_id, target_model, system_template, user_template, resolved_system_prompt, resolved_user_prompt, role="prompt",
                   hashes=True, column_length=None):
    """One experiment run of a prompt against one model, with the prompts as they were sent.

    hashes         Get-All-Prompts.sas writes the hash of both prompts beside them; False leaves the two columns empty
    column_length  the characters the catalog keeps of a prompt: the hash is taken of the whole text, the text is cut afterwards
    """
    row = catalog_row(model_id, role, name, runID=run_id, target_model=target_model, system_template=system_template, user_template=user_template,
                      resolved_system_prompt=resolved_system_prompt[:column_length], resolved_user_prompt=resolved_user_prompt[:column_length])
    if hashes:
        row.update(resolved_system_hash=text_hash(resolved_system_prompt), resolved_user_hash=text_hash(resolved_user_prompt))
    return row


def rag_setup(model_id, name, collection, target_model="", source=""):
    """A RAG setup. source is what Get-All-Prompts.sas writes into template_source: builder for a setup the
    Prompt Builder saved, step for a model the step RAG - Register Setup registered."""
    return catalog_row(model_id, "rag", name, collection=collection, target_model=target_model, template_source=source)


def catalog_table(*rows):
    return pd.DataFrame(list(rows), columns=CATALOG_COLUMNS)


def ingestion_run(run_id, collection, started_at, finished_at, embed_model="demo_embedder", model_id=""):
    """A row of RAG_RUNS. The times are UTC texts, as the RAG pipeline records them."""
    return {"run_id": run_id, "collection": collection, "embed_model": embed_model, "started_at": started_at, "finished_at": finished_at,
            "modelID": model_id, "projectID": uuid(900) if model_id else ""}


def runs_table(*rows):
    return pd.DataFrame(list(rows), columns=RUNS_COLUMNS)


def lower_case(frame):
    """A frame with its column names in lower case, as the program reads a table."""
    return frame.rename(columns=str.lower)
