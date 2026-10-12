# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The job Ingest-Documents.sas publishes the run history to CAS.

The run history is kept in the vector store. RAG - Load Vector Store copies
it to CAS after every run, as <project>_RUNS and <project>_DOC_EVENTS, and
was the only one that did: the job recorded its run in the store and
published nothing. A setup the Prompt Builder ingested with its job
therefore had a ledger in CAS and no run table. Measured on a test system on
2026-10-12: the pull of the log chain found the run table of such a setup
not in memory and took one run from its ledger, the newest, which is all a
ledger names.

The job now publishes the two tables itself: the Python program reads from
the store what the step reads and writes it to WORK, and SAS uploads the two
files behind it, with the names, the columns, the types and the labels the
step gives them.

What is compared with the step is taken from the step: its cas_stage is cut
out of the step file and run on the same rows, and its upload options, its
table names and the order of its swap are read from its text. The Python of
the job is run against a stub of SAS and of the store; the SAS behind it is
read, and what is tested of it is its text and the order of its statements.
Nothing here runs SAS or CAS.
"""
import ast
import csv
import datetime
import decimal
import json
import os
import pathlib
import re
import textwrap
import types

import pytest

from rag_core import history, steps
from rag_core.history import (EVENT_COLUMNS, EVENT_NUMERIC, RUN_COLUMNS,
                              RUN_NUMERIC, cas_types, cas_values,
                              write_cas_file)

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"
JOB = RAG.parent / "RAG-Ingestion" / "Ingest-Documents.sas"

COLLECTION = "rag_demo_v1"
UTC = datetime.timezone.utc


def _job():
    return JOB.read_text(encoding="utf-8").replace("\r\n", "\n")


def _python():
    return _job().split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _before_python():
    return _job().split("\nsubmit;\n", 1)[0]


def _after_python():
    return _job().split("\nendsubmit;", 1)[1]


def _without_comments(sas):
    return re.sub(r"/\*.*?\*/", "", sas, flags=re.DOTALL)


def _macro(name):
    """One macro of the job without its comments, %macro to %mend."""
    found = re.search(r"%macro " + name + r"\b.*?%mend " + name + ";",
                      _job(), re.DOTALL)
    assert found, name
    return _without_comments(found.group(0))


def _load_step():
    return json.loads((STEPS / "RAG - Load Vector Store.step").read_text(
        encoding="utf-8"))["templates"]["SAS"]


def _load_python():
    return _load_step().split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _in_order(text, *markers):
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), \
        [marker for _position, marker in sorted(zip(positions, markers))]


# ---------------------------------------------------------------------------
# rows of a store, as its driver hands them over: more columns than are
# published, a time as a datetime, a number as int, float, Decimal or None
# ---------------------------------------------------------------------------
def _run(run_id, minute, **changed):
    row = {column: None for column in RUN_COLUMNS}
    row.update({
        "run_id": run_id, "collection": COLLECTION, "backend": "pgvector",
        "started_at": datetime.datetime(2026, 10, 11, 16, minute, 2, tzinfo=UTC),
        "finished_at": datetime.datetime(2026, 10, 11, 16, minute, 9, tzinfo=UTC),
        "status": "completed", "pipeline_version": "v1", "config_id": "0123456789",
        "embed_model": "all_minilm_l6_v2", "embed_dims": 384,
        "chunker": "recursive", "input_token_limit": 256, "overlap_tokens": 30,
        "docs_new": 0, "docs_unchanged": 3, "docs_ingested": 0,
        "chunks_written": 0, "collection_chunks": 3, "embed_calls": 0,
        "embed_tokens": 0, "embed_seconds": 0.0, "error_text": None,
    })
    row.update(changed)
    return row


def _event(run_id, doc_id, **changed):
    row = {"id": 7, "run_id": run_id, "doc_id": doc_id,
           "source_uri": "/docs/" + doc_id + ".md", "source_kind": "path",
           "status": "new", "previous_content_hash": "",
           "new_content_hash": "h-" + doc_id, "chunk_count_before": 0,
           "chunk_count_after": 1, "error_text": "",
           "recorded_at": datetime.datetime(2026, 10, 11, 16, 44, 9, tzinfo=UTC)}
    row.update(changed)
    return row


FIRST = "run-1791737042-0000000011"
SECOND = "run-1791737222-0000000022"
THIS = "run-1791740981-0000000033"

# the newest first, as History.runs() orders them: this run, which found
# nothing to do, and the two runs before it
STORE_RUNS = [
    _run(THIS, 49),
    _run(SECOND, 47, docs_new=1, docs_unchanged=2, docs_ingested=1,
         chunks_written=decimal.Decimal("2"), embed_calls=2, embed_tokens=61,
         embed_seconds=0.0796811580657959, rag_project="HR"),
    _run(FIRST, 44, docs_new=2, docs_unchanged=0, docs_ingested=2,
         docs_failed=1, chunks_written=5, embed_calls=5, embed_tokens="143",
         embed_seconds="n/a", status="completed",
         error_text='1 document failed: "b.pdf", no text layer;\nsee the log'),
]
STORE_EVENTS = [
    _event(SECOND, "d3"),
    _event(FIRST, "d1"),
    _event(FIRST, "d2", status="failed", chunk_count_after=None,
           error_text="no text layer, 0 pages read"),
]


# ---------------------------------------------------------------------------
# the step: its cas_stage, cut out of the step file and run
# ---------------------------------------------------------------------------
class _Frame:
    """pandas.DataFrame as cas_stage calls it, without pandas."""

    def __init__(self, data, columns):
        self.data = data
        self.columns = list(columns)


class _Upload:
    def __init__(self):
        self.calls = []

    def upload_frame(self, frame, casout=None, importoptions=None):
        self.calls.append((frame, casout, importoptions))


def _step_stages(name, rows, columns, numeric):
    """What RAG - Load Vector Store hands to SWAT for one history table:
    (the rows, the values, the columns of the frame, casout, importoptions,
    the columns it labels)."""
    python = _load_python()
    start = python.index("        def cas_stage(name, rows, columns, numeric=()):")
    end = python.index("        # ---- rag_core bootstrap")
    conn, labelled = _Upload(), []
    scope = {"pd": types.SimpleNamespace(DataFrame=_Frame), "conn": conn,
             "CASLIB": "Public",
             "label_columns": lambda table, names: labelled.append((table, list(names)))}
    exec(compile(textwrap.dedent(python[start:end]), "RAG - Load Vector Store.step",
                 "exec"), scope)
    count = scope["cas_stage"](name, rows, columns, numeric=numeric)
    (frame, casout, importoptions), = conn.calls
    return count, frame.data, frame.columns, casout, importoptions, labelled


TABLES = [("HR_RUNS", STORE_RUNS, RUN_COLUMNS, RUN_NUMERIC),
          ("HR_DOC_EVENTS", STORE_EVENTS, EVENT_COLUMNS, EVENT_NUMERIC)]


def test_the_step_publishes_every_run_the_store_holds_for_the_collection():
    """Not the row of the run that publishes: what History.runs() and
    History.doc_events() return for the collection, the newest 500 runs and
    the newest 5000 events. That is why one more run of a setup brings the
    runs before it into CAS."""
    python = _load_python()
    assert re.search(r'cas_stage\(project_table\("_RUNS"\),\s+'
                     r"\[dict\(r\) for r in reader\.runs\(collection\)\],\s+"
                     r"RUN_COLUMNS, numeric=RUN_NUMERIC\)", python)
    assert re.search(r'cas_stage\(project_table\("_DOC_EVENTS"\),\s+'
                     r"\[dict\(e\) for e in reader\.doc_events\(collection\)\],\s+"
                     r"EVENT_COLUMNS, numeric=EVENT_NUMERIC\)", python)
    conn = _Store()
    reader = history.History(conn, "postgres")
    reader.runs(COLLECTION)
    reader.doc_events(COLLECTION)
    assert conn.statements == [
        ("SELECT * FROM rag_runs WHERE collection = %s ORDER BY started_at DESC "
         "LIMIT %s", [COLLECTION, 500]),
        ("SELECT e.* FROM rag_doc_events e JOIN rag_runs r ON r.run_id = e.run_id "
         "WHERE r.collection = %s ORDER BY e.recorded_at DESC LIMIT %s",
         [COLLECTION, 5000])]


# ---------------------------------------------------------------------------
# rag_core: the rows as the step stages them, without SWAT
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,rows,columns,numeric", TABLES,
                         ids=["runs", "document events"])
def test_the_values_and_the_types_are_the_ones_the_step_stages(
        name, rows, columns, numeric):
    count, data, frame_columns, casout, importoptions, _ = _step_stages(
        name, rows, columns, numeric)
    assert count == len(rows)
    assert frame_columns == list(columns)
    assert cas_values(rows, columns, numeric) == data
    assert cas_types(columns, numeric) == [
        (column["name"], column["type"]) for column in importoptions["vars"]]
    # every column, in its order, and no column the store has beside them
    assert [name for name, _ in cas_types(columns, numeric)] == list(columns)
    assert casout == {"name": name + "_S", "caslib": "Public", "replace": True}


def test_a_measure_is_a_double_and_everything_else_a_varchar():
    types_of = dict(cas_types(RUN_COLUMNS, RUN_NUMERIC))
    assert {name for name, kind in types_of.items() if kind == "double"} \
        == set(RUN_NUMERIC)
    assert set(types_of.values()) == {"double", "varchar"}
    assert types_of["run_id"] == types_of["started_at"] == "varchar"
    assert dict(cas_types(EVENT_COLUMNS, EVENT_NUMERIC)) == {
        "run_id": "varchar", "doc_id": "varchar", "source_uri": "varchar",
        "source_kind": "varchar", "status": "varchar",
        "previous_content_hash": "varchar", "new_content_hash": "varchar",
        "chunk_count_before": "double", "chunk_count_after": "double",
        "error_text": "varchar"}


def test_a_value_is_staged_the_way_the_step_stages_it():
    """A time is its text, None an empty text; a measure is a float, and 0
    where it is missing or cannot be read."""
    oldest = dict(zip(RUN_COLUMNS, cas_values(STORE_RUNS, RUN_COLUMNS,
                                              RUN_NUMERIC)[2]))
    assert oldest["started_at"] == "2026-10-11 16:44:02+00:00"
    assert oldest["rag_project"] == "" and oldest["enrich_model"] == ""
    assert oldest["embed_tokens"] == 143.0 and oldest["embed_seconds"] == 0.0
    assert oldest["docs_skipped"] == 0.0            # None in the store
    second = dict(zip(RUN_COLUMNS, cas_values(STORE_RUNS, RUN_COLUMNS,
                                              RUN_NUMERIC)[1]))
    assert second["chunks_written"] == 2.0 and isinstance(second["chunks_written"], float)
    assert second["embed_seconds"] == 0.0796811580657959
    assert all(isinstance(value, (str, float)) for value in second.values())


@pytest.mark.parametrize("name,rows,columns,numeric", TABLES,
                         ids=["runs", "document events"])
def test_the_file_holds_the_names_and_then_a_line_per_row(
        tmp_path, name, rows, columns, numeric):
    path = tmp_path / (name + ".csv")
    assert write_cas_file(str(path), rows, columns, numeric) == len(rows)
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    with open(path, encoding="utf-8", newline="") as fh:
        read = list(csv.reader(fh))
    assert read[0] == list(columns)
    staged = cas_values(rows, columns, numeric)
    assert len(read) == len(staged) + 1
    for line, values in zip(read[1:], staged):
        assert len(line) == len(columns)
        for column, text, value in zip(columns, line, values):
            if column in numeric:
                assert float(text) == value, column
            else:
                assert text == value, column


def test_a_text_with_a_line_break_a_comma_and_a_quotation_mark_stays_one_value(tmp_path):
    path = tmp_path / "runs.csv"
    write_cas_file(str(path), STORE_RUNS, RUN_COLUMNS, RUN_NUMERIC)
    with open(path, encoding="utf-8", newline="") as fh:
        read = list(csv.reader(fh))
    assert len(read) == 4
    assert read[3][RUN_COLUMNS.index("error_text")] \
        == '1 document failed: "b.pdf", no text layer;\nsee the log'


@pytest.mark.parametrize("name,rows,columns,numeric", TABLES,
                         ids=["runs", "document events"])
def test_the_file_is_the_one_pandas_writes_for_the_frame_of_the_step(
        tmp_path, name, rows, columns, numeric):
    """SWAT writes the DataFrame of the step with to_csv and has CAS read
    that file. Line for line it is the file written here."""
    pandas = pytest.importorskip("pandas")
    _, data, frame_columns, _, _, _ = _step_stages(name, rows, columns, numeric)
    frame = pandas.DataFrame(data, columns=frame_columns)
    path = tmp_path / (name + ".csv")
    write_cas_file(str(path), rows, columns, numeric)
    with open(path, encoding="utf-8", newline="") as fh:
        written = fh.read()
    assert written == frame.to_csv(index=False, lineterminator="\n")


def test_no_rows_are_a_file_with_its_first_line_only(tmp_path):
    path = tmp_path / "events.csv"
    assert write_cas_file(str(path), [], EVENT_COLUMNS, EVENT_NUMERIC) == 0
    assert path.read_text(encoding="utf-8") == ",".join(EVENT_COLUMNS) + "\n"


# ---------------------------------------------------------------------------
# the Python of the job, run: from the test of recordHistory to the line that
# closes the store
# ---------------------------------------------------------------------------
class _Sas:
    def __init__(self):
        self.macros = {}
        self.log = []

    def symget(self, name):
        return self.macros.get(name, "")

    def symput(self, name, value):
        self.macros[name] = value

    def logMessage(self, message):
        self.log.append(message)


class _Cursor:
    def __init__(self, store):
        self._store = store
        self._rows = []
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(str(sql).split())
        self._store.statements.append((sql, list(params or [])))
        if self._store.refuses and self._store.refuses in sql:
            raise RuntimeError('relation "rag_doc_events" does not exist;\n'
                               "LINE 1: SELECT e.* /* 100% */ & more")
        self._rows = self._store.events if "rag_doc_events" in sql \
            else self._store.runs
        names = list(self._rows[0]) if self._rows else ["run_id"]
        self.description = [(name,) for name in names]

    def fetchall(self):
        return [tuple(row.values()) for row in self._rows]


class _Store:
    """The connection of the vector store, with the two history tables."""

    def __init__(self, runs=(), events=(), refuses=""):
        self.runs, self.events, self.refuses = list(runs), list(events), refuses
        self.statements = []

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


class _Adapter:
    HISTORY_DIALECT = "postgres"

    def __init__(self, store):
        self._store = store

    def raw_connection(self):
        return self._store


def _hand_over(tmp_path, record="1", store=None, workpath=None):
    """The lines of the job's Python between the count of the collection and
    the line that closes the store, run. Returns (the stub of SAS, the calls
    of record_history, the store)."""
    python = _python()
    start = python.index('        if P["recordHistory"] != "0":')
    end = python.index("        adapter.close()\n", start)
    block = textwrap.dedent(python[start:end])
    # the two functions the block calls are the ones of the program
    helpers = python[python.index("    def sas_safe(text):"):
                     python.index("    # The saved ledger could not be loaded.")]
    sas = _Sas()
    store = store if store is not None else _Store(STORE_RUNS, STORE_EVENTS)
    recorded = []
    scope = {
        "SAS": sas, "adapter": _Adapter(store), "stages": [], "inventory": [],
        "real_rows": [], "discovered": [], "run_id": THIS, "cfg_hash": "0123456789",
        "total": 3, "dims": 384, "load_stats": {},
        "client": types.SimpleNamespace(usage={}),
        "record_history": lambda *args, **kwargs: recorded.append((args, kwargs)),
        "WORKPATH": workpath or (str(tmp_path) + os.sep),
        "P": {"recordHistory": record, "collection": COLLECTION, "backend": "pgvector",
              "chunker": "recursive", "inputTokenLimit": "256", "overlapTokens": "30",
              "pipelineVersion": "v1", "embedModel": "all_minilm_l6_v2"},
    }
    exec(compile(textwrap.dedent(helpers), "Ingest-Documents.sas", "exec"), scope)
    exec(compile(block, "Ingest-Documents.sas", "exec"), scope)
    return sas, recorded, store


def _pairs(text, between, inside):
    """What the two %scan calls of the macro make of a list the Python
    program handed over."""
    return [tuple(item.split(inside)) for item in text.split(between) if item]


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def test_the_job_hands_every_run_of_the_collection_over_not_its_own(tmp_path):
    """The run that publishes found nothing to do. The store holds that run
    and the two before it, and all three are in the file - the runs that
    embedded the documents are what a reader of the table is after."""
    sas, recorded, store = _hand_over(tmp_path)
    assert len(recorded) == 1 and recorded[0][0][3] == THIS
    assert sas.macros["_rag_histState"] == "read"
    assert sas.macros["_rag_histRuns"] == "3" and sas.macros["_rag_histEvents"] == "3"
    runs = _read(tmp_path / "rag_runs_out.csv")
    assert runs[0] == RUN_COLUMNS
    assert [line[0] for line in runs[1:]] == [THIS, SECOND, FIRST]
    events = _read(tmp_path / "rag_doc_events_out.csv")
    assert events[0] == EVENT_COLUMNS
    assert [(line[0], line[1]) for line in events[1:]] == [
        (SECOND, "d3"), (FIRST, "d1"), (FIRST, "d2")]
    # read with the calls of the step, for the collection of the run
    assert store.statements == [
        ("SELECT * FROM rag_runs WHERE collection = %s ORDER BY started_at DESC "
         "LIMIT %s", [COLLECTION, 500]),
        ("SELECT e.* FROM rag_doc_events e JOIN rag_runs r ON r.run_id = e.run_id "
         "WHERE r.collection = %s ORDER BY e.recorded_at DESC LIMIT %s",
         [COLLECTION, 5000])]
    assert sas.log == ["RAGINGEST rag history: read for CAS, runs 3 and "
                       "document events 3 of the collection"]


@pytest.mark.parametrize("kind,name,rows,columns,numeric", [
    ("Run",) + TABLES[0], ("Event",) + TABLES[1]], ids=["runs", "document events"])
def test_the_columns_the_job_publishes_are_the_columns_of_the_step(
        tmp_path, kind, name, rows, columns, numeric):
    """Names, order and types: the list the Python program hands over, taken
    apart the way the macro takes it apart, is the list the step gives CAS;
    and the file holds the values the step stages."""
    sas, _, _ = _hand_over(tmp_path)
    count, data, frame_columns, _, importoptions, labelled = _step_stages(
        name, rows, columns, numeric)
    handed = sas.macros["_rag_hist%sVars" % kind]
    assert _pairs(handed, " ", ":") == [
        (column["name"], column["type"]) for column in importoptions["vars"]]
    assert sas.macros["_rag_hist%ss" % kind] == str(count)
    read = _read(tmp_path / ("rag_runs_out.csv" if kind == "Run"
                              else "rag_doc_events_out.csv"))
    assert read[0] == frame_columns
    assert [[float(text) if column in numeric else text
             for column, text in zip(columns, line)] for line in read[1:]] == data
    # the labels are the ones the step puts on the staged table
    assert labelled == [(name + "_S", list(columns))]
    assert dict(_pairs(sas.macros["_rag_hist%sLabels" % kind], "|", "=")) \
        == steps.column_labels(columns)
    # both lists stand in macro statements: names, the two types, the
    # characters that split them, and plain text for a label
    assert re.fullmatch(r"[A-Za-z0-9_]+:(varchar|double)( [A-Za-z0-9_]+:(varchar|double))*",
                        handed)
    assert re.fullmatch(r"[A-Za-z0-9_ .\-=|]+", sas.macros["_rag_hist%sLabels" % kind])
    # and the macro takes the two lists apart by these characters
    table = _macro("_rag_history_table")
    for call in ("%scan(&_rag_hvars., &_rag_hi., %str( ))",
                 '{name = "%scan(&_rag_hv., 1, %str(:))", type = "%scan(&_rag_hv., 2, %str(:))"}',
                 "%scan(&_rag_hlabels., &_rag_hi., %str(|))",
                 '{name = "%scan(&_rag_hl., 1, %str(=))", label = "%scan(&_rag_hl., 2, %str(=))"}'):
        assert call in table, call


def test_the_labels_of_the_two_tables_are_plain_text():
    """Six columns of each table have a label. None holds a character the
    hand-over would leave out."""
    for columns, labelled in ((RUN_COLUMNS, ["run_id", "rag_project", "collection",
                                             "status", "pipeline_version", "error_text"]),
                              (EVENT_COLUMNS, ["run_id", "doc_id", "source_uri",
                                               "source_kind", "status", "error_text"])):
        labels = steps.column_labels(columns)
        assert list(labels) == labelled
        for label in labels.values():
            assert re.fullmatch(r"[A-Za-z0-9 _.\-]+", label), label


def test_with_the_history_switched_off_nothing_is_read_written_or_handed_over(tmp_path):
    sas, recorded, store = _hand_over(tmp_path, record="0")
    assert recorded == [] and store.statements == []
    assert sas.macros == {} and sas.log == []
    assert list(tmp_path.iterdir()) == []


def _hist_calls(node):
    """The names of the _rag_hist... variables a node hands to SAS."""
    found = []
    for call in ast.walk(node):
        if isinstance(call, ast.Call) and ast.unparse(call.func) == "SAS.symput":
            name = ast.unparse(call.args[0])
            if "_rag_hist" in name:
                found.append(name)
    return found


def test_the_hand_over_stands_inside_the_test_of_record_history():
    """Every _rag_hist variable is set under `if P["recordHistory"] != "0":`,
    behind the call that records the run, and nowhere else in the program."""
    main = ast.parse(_python()).body[0]
    guards = [node for node in ast.walk(main) if isinstance(node, ast.If)
              and ast.unparse(node.test) == "P['recordHistory'] != '0'"]
    assert len(guards) == 1
    guard = guards[0]
    assert not guard.orelse
    assert [type(statement) for statement in guard.body] == [ast.Expr, ast.Try]
    assert ast.unparse(guard.body[0].value.func) == "record_history"
    inside = _hist_calls(guard)
    assert sorted(set(inside)) == [
        "'_rag_histError'", "'_rag_histState'", "f'_rag_hist{kind}Labels'",
        "f'_rag_hist{kind}Vars'", "f'_rag_hist{kind}s'"]
    assert _hist_calls(main) == inside
    # the store is still open, and the run not yet reported as one that
    # went through
    program = _python()
    _in_order(program, "            record_history(\n",
              "# ---- the run history, read back for CAS",
              "        adapter.close()\n", '"rag_ledger_out.json"',
              'SAS.symput("_rag_rc", "0")')


def test_before_the_python_program_the_history_is_off():
    """The state the SAS behind the program reads starts at off with every
    run of the program, so a run that fails early, and a run with the
    history switched off, publish nothing - also in a SAS session that ran
    the job before and left the files of that run in WORK."""
    head = _without_comments(_before_python())
    for start in ("%let _rag_histState = off;", "%let _rag_histError = ;",
                  "%let _rag_histRuns = 0;", "%let _rag_histRunVars = ;",
                  "%let _rag_histRunLabels = ;", "%let _rag_histEvents = 0;",
                  "%let _rag_histEventVars = ;", "%let _rag_histEventLabels = ;"):
        assert head.count(start) == 1, start
        assert head.index(start) < head.index("%macro _rag_reload_saved(")
    assert "_rag_hist" not in _without_comments(_before_python()).split(
        "%macro _rag_reload_saved(", 1)[1]


# ---------------------------------------------------------------------------
# a history that cannot be read does not fail the run
# ---------------------------------------------------------------------------
def _builder_says_failed(state, messages):
    """ingestionOutcome of the Prompt Builder (test_job_outcome.py)."""
    lines = [re.sub(r"^RAGINGEST\s+", "", message) for message in messages]
    return state != "completed" or any(
        re.match(r"Ingestion failed\b", line, re.IGNORECASE) for line in lines)


def test_a_store_that_refuses_the_read_leaves_a_reason_and_no_failed_run(tmp_path):
    store = _Store(STORE_RUNS, STORE_EVENTS, refuses="rag_doc_events")
    sas, recorded, _ = _hand_over(tmp_path, store=store)     # nothing is raised
    assert len(recorded) == 1
    assert sas.macros["_rag_histState"] == "failed"
    reason = sas.macros["_rag_histError"]
    # nothing SAS would read as code: the reason stands in a statement, so
    # the quotation marks, the percent sign, the ampersand, the semicolon,
    # the line break and the two marks of a comment are gone from it
    assert reason == ("RuntimeError: relation rag_doc_events does not exist "
                      "LINE 1: SELECT e.* / * 100 * / more")
    assert len(reason) <= 200
    # the counts and the columns are handed over only with the state read
    assert set(sas.macros) == {"_rag_histState", "_rag_histError"}
    assert sas.log == ["RAGINGEST rag history: not read for CAS (" + reason
                       + ") - the ingestion itself is unaffected"]
    # a milestone the Prompt Builder shows and does not read as a failure
    assert not _builder_says_failed("completed", sas.log)
    assert "_rag_rc" not in sas.macros and "_rag_error" not in sas.macros


def test_a_file_that_cannot_be_written_is_the_same(tmp_path):
    missing = str(tmp_path / "no such folder") + os.sep
    sas, _, _ = _hand_over(tmp_path, workpath=missing)
    assert sas.macros["_rag_histState"] == "failed"
    assert set(sas.macros) == {"_rag_histState", "_rag_histError"}
    assert sas.log[0].startswith("RAGINGEST rag history: not read for CAS (")
    assert not _builder_says_failed("completed", sas.log)


def test_a_column_that_is_no_plain_name_stops_the_hand_over(tmp_path, monkeypatch):
    """A column name stands in the statement that uploads the file."""
    monkeypatch.setattr(history, "EVENT_COLUMNS",
                        EVENT_COLUMNS + ['x", type = "double"}'])
    sas, _, _ = _hand_over(tmp_path)
    assert sas.macros["_rag_histState"] == "failed"
    assert "is no name a SAS statement can hold" in sas.macros["_rag_histError"]
    assert "_rag_histRunVars" not in sas.macros


def test_a_label_that_is_not_plain_text_is_left_out(tmp_path, monkeypatch):
    """A label stands in a statement too. The table is published without
    it, as the step publishes a table whose labels it could not set."""
    monkeypatch.setitem(steps.COLUMN_LABELS, "doc_id", 'Document "ID"; %put x')
    monkeypatch.setitem(steps.COLUMN_LABELS, "source_uri", "Source = location | path")
    sas, _, _ = _hand_over(tmp_path)
    assert sas.macros["_rag_histState"] == "read"
    labels = dict(_pairs(sas.macros["_rag_histEventLabels"], "|", "="))
    assert list(labels) == ["run_id", "source_kind", "status", "error_text"]
    for value in sas.macros.values():
        assert not set(value) & set("'\"%&;\n"), value


def test_a_store_without_a_run_hands_over_none(tmp_path):
    """The store can be read and holds nothing for the collection - a store
    that refused to record the run, for one. The counts say so, and SAS
    leaves the tables as they are."""
    sas, _, _ = _hand_over(tmp_path, store=_Store())
    assert sas.macros["_rag_histState"] == "read"
    assert sas.macros["_rag_histRuns"] == "0" and sas.macros["_rag_histEvents"] == "0"
    assert _read(tmp_path / "rag_runs_out.csv") == [RUN_COLUMNS]


def test_the_handler_of_the_hand_over_ends_nothing():
    """It catches everything, raises nothing and returns nothing: the
    program goes on to close the store, write the ledger and report the run
    as one that went through."""
    main = ast.parse(_python()).body[0]
    guard = next(node for node in ast.walk(main) if isinstance(node, ast.If)
                 and ast.unparse(node.test) == "P['recordHistory'] != '0'")
    attempt = guard.body[1]
    assert isinstance(attempt, ast.Try)
    assert len(attempt.handlers) == 1 and not attempt.finalbody and not attempt.orelse
    handler = attempt.handlers[0]
    assert ast.unparse(handler.type) == "Exception" and handler.name == "exc"
    assert not [node for node in ast.walk(attempt)
                if isinstance(node, (ast.Return, ast.Raise))
                and "is no name a SAS statement can hold" not in ast.unparse(node)]
    assert not [node for node in ast.walk(handler)
                if isinstance(node, (ast.Return, ast.Raise))]
    # the one statement that makes the run a good one is still the last
    assert ast.unparse(main.body[-1].body[-1]) == "SAS.symput('_rag_rc', '0')"


# ---------------------------------------------------------------------------
# the SAS behind the Python program
# ---------------------------------------------------------------------------
def test_the_history_is_published_behind_the_ledger_and_before_the_last_line():
    """The run is over when the history is published: its ledger is stored,
    which takes its lock out, and a run that failed has handed its lock
    back. The last line of the log stays the outcome of the run, and the
    condition code of a failed run the last statement."""
    after = _after_python()
    assert after.count("%_rag_publish_history;") == 1
    _in_order(after, "proc python terminate;", "%_rag_store_ledger;",
              "%mend _rag_release;\n%_rag_release;",
              "%macro _rag_history_table(kind, suffix, file);",
              "%mend _rag_history_table;",
              "%macro _rag_publish_history;",
              "%mend _rag_publish_history;\n%_rag_publish_history;",
              'line = "NOTE: RAG ingestion succeeded: " || text;',
              "%macro _rag_job_state;")
    assert after.rstrip().endswith("%mend _rag_job_state;\n%_rag_job_state;")
    # nothing in front of the Python program publishes
    head = _without_comments(_before_python())
    assert "_rag_publish_history" not in head and "_rag_history_table" not in head


def test_only_a_run_that_went_through_with_a_history_read_publishes():
    publish = _macro("_rag_publish_history")
    _in_order(publish,
              "%if &_rag_rc. NE 0 %then %return;",
              "%if &_rag_histState. EQ off %then %return;",
              "%if &_rag_histState. NE read %then %do;",
              "%put WARNING: RAG ingestion: the run history was not published to CAS",
              "%return;\n    %end;",
              "cas _ragcas4;",
              "%_rag_history_table(Run, _RUNS, rag_runs_out.csv);",
              "%_rag_history_table(Event, _DOC_EVENTS, rag_doc_events_out.csv);",
              "cas _ragcas4 terminate;",
              "%put NOTE: RAG ingestion: the run history was published to CAS")
    assert publish.count("%return;") == 3
    # the session is closed on every way through: nothing returns once it
    # is open, in neither macro
    assert "%return;" not in publish[publish.index("cas _ragcas4;"):]
    table = _macro("_rag_history_table")
    assert "%return;" not in table[table.index("proc cas;"):]
    assert table.count("%return;") == 1
    # a table without rows is left before anything is asked of CAS
    _in_order(table, "%if &_rag_hrows. LE 0 %then %do;",
              "the table was left as it is.;", "%return;", "proc cas;")


def test_the_tables_have_the_names_the_step_gives_them():
    """<project>_RUNS and <project>_DOC_EVENTS, uploaded as <name>_S beside
    them, in the caslib of the ledger. The project is the ledger table
    without its ending _LEDGER, which is how the Prompt Builder names the
    ledger of a setup."""
    step = _load_step()
    assert "%_rgld_swap(_RUNS, &_rgld_nruns.);" in step
    assert "%_rgld_swap(_DOC_EVENTS, &_rgld_nevents.);" in step
    assert 'promote casdata="&_rgld_ragProject.&suffix._S"' in step
    assert 'casout={"name": name + "_S", "caslib": CASLIB,' in _load_python()
    publish = _macro("_rag_publish_history")
    _in_order(publish,
              "%let _rag_histPrefix = &ledgerTable.;",
              "%if %length(&ledgerTable.) GT 7 %then",
              "%if %upcase(%substr(&ledgerTable., %eval(%length(&ledgerTable.) - 6))) "
              "EQ _LEDGER %then",
              "%let _rag_histPrefix = %substr(&ledgerTable., 1, "
              "%eval(%length(&ledgerTable.) - 7));")
    table = _macro("_rag_history_table")
    assert "%let _rag_ht = &_rag_histPrefix.&suffix.;" in table
    assert len("_LEDGER") == 7
    builder = (RAG.parents[1] / "LLM-Prompt-Builder" / "src" / "objects"
               / "rag-builder.ts").read_text(encoding="utf-8")
    assert "ledgerTable: `${setup.tables.prefix}_LEDGER`," in builder
    assert "ledgerCaslib: setup.tables.caslib," in builder
    # every table the macro names is one of the two, or its upload
    named = set(re.findall(r'name = "([^"%]*)"', table))
    assert named == {"&_rag_ht._S", "&_rag_ht."}
    assert set(re.findall(r'caslib = "([^"]*)"', table)) == {"&ledgerCaslib."}


def test_the_upload_has_the_options_of_the_step():
    """SWAT hands the file of a DataFrame to CAS with the options the step
    names and the locale it adds itself. The job names the same."""
    python = _load_python()
    assert ('importoptions={"fileType": "csv",\n'
            + " " * 45 + '"allowEmbeddedNewLines": True,\n'
            + " " * 45 + '"vars": spec})') in python
    assert '"replace": True},' in python
    table = _macro("_rag_history_table")
    assert re.search(
        r'upload path = "%sysfunc\(pathname\(work\)\)/&file\."\s+'
        r'casOut = \{caslib = "&ledgerCaslib\.", name = "&_rag_ht\._S", replace = true\}\s+'
        r'importOptions = \{fileType = "csv", allowEmbeddedNewLines = true,\s+'
        r'locale = "EN-us", vars = \{', table)


def test_the_locale_of_the_upload_is_the_one_swat_adds():
    """Read from SWAT where it is installed: upload() writes a DataFrame as
    a file and adds this locale to the options, so a number is read with a
    decimal point whatever the locale of the session is."""
    swat = pytest.importorskip("swat")
    import inspect
    source = inspect.getsource(swat.cas.connection.CAS.upload)
    assert "data.to_csv(filename, encoding='utf-8'," in source
    assert "importoptions['locale'] = 'EN-us'" in source


def test_a_table_is_replaced_in_the_order_of_the_step_and_only_when_complete():
    """Upload, drop, promote, save - and the labels on the upload before it
    is promoted, as the step sets them. The upload takes the place of the
    table only when it holds every row that was written."""
    swap = re.search(r"%macro _rgld_swap\b.*?%mend _rgld_swap;", _load_step(), re.DOTALL)
    _in_order(swap.group(0), "droptable casdata=", "promote casdata=", "save casdata=")
    table = _macro("_rag_history_table")
    _in_order(table,
              "upload path = ",
              'table.tableExists result = found / caslib = "&ledgerCaslib." name = "&_rag_ht._S";',
              "if found.exists != 0 then do;",
              'if upcase(entry.Name) == upcase("&_rag_ht._S") then staged = entry.Rows;',
              "if staged == &_rag_hrows. then do;",
              'table.alterTable status = labelled / caslib = "&ledgerCaslib." name = "&_rag_ht._S"',
              'table.dropTable status = dropped / caslib = "&ledgerCaslib." name = "&_rag_ht." quiet = true;',
              'table.promote status = promoted / caslib = "&ledgerCaslib." name = "&_rag_ht._S"',
              'targetLib = "&ledgerCaslib." target = "&_rag_ht.";',
              "if promoted.severity == 0 then do;",
              'table.save status = stored / table = {caslib = "&ledgerCaslib.", name = "&_rag_ht."}',
              'caslib = "&ledgerCaslib." name = "&_rag_ht." replace = true;',
              'if stored.severity == 0 then outcome = "published";',
              'call symputx("_rag_houtcome", outcome, "L");')
    # each of the four is called once
    for action in ("upload path", "table.dropTable", "table.promote", "table.save",
                   "table.alterTable"):
        assert table.count(action) == 1, action
    # what the outcome is before each of them, so a failure names its place
    assert re.findall(r'outcome = "(\w+)"', table) == [
        "upload", "rows", "replace", "save", "published"]
    assert "%local" in table and "_rag_houtcome _rag_hstaged;" in table


def test_nothing_of_the_publication_is_a_step_that_can_stop_the_session():
    """PROC CAS and macro statements only. A DATA step or another procedure
    that ends in an error leaves a batch session checking syntax, and the
    last line of the job is written by a DATA step."""
    for name in ("_rag_history_table", "_rag_publish_history"):
        macro = _macro(name)
        assert not re.search(r"\bdata\s", macro, re.IGNORECASE), name
        assert not re.search(r"\brun;", macro, re.IGNORECASE), name
        assert re.findall(r"\bproc (\w+)", macro, re.IGNORECASE) \
            == (["cas"] if name == "_rag_history_table" else []), name
        assert macro.count("%do") == macro.count("%end"), name
    assert _macro("_rag_history_table").count("quit;") == 1


def test_a_table_that_could_not_be_published_is_a_warning_and_never_an_error():
    lines = re.findall(r"%put ([^;]*);", _macro("_rag_history_table")) \
        + re.findall(r"%put ([^;]*);", _macro("_rag_publish_history"))
    assert len(lines) == 8
    assert [line.split(":")[0] for line in lines] == [
        "NOTE", "WARNING", "WARNING", "WARNING", "WARNING", "WARNING",
        "WARNING", "NOTE"]
    known = {"ledgerCaslib", "_rag_ht", "_rag_hrows", "_rag_hstaged",
             "_rag_histPrefix", "_rag_histDone"}
    for line in lines:
        assert line.startswith(("NOTE: RAG ingestion: ", "WARNING: RAG ingestion: "))
        # text the macro processor leaves alone: the one reason that is
        # open text is read with %superq
        assert not set(line.replace("%superq(_rag_histError)", "")) & set("%'\""), line
        assert set(re.findall(r"&(\w+)", line)) <= known, line
        assert line.count("(") == line.count(")"), line
        if line.startswith("WARNING"):
            assert "the ingestion itself went through" in line.lower(), line
            assert "the next run" in line, line
    # one line for each place a table can fail at, and one for a store that
    # could not be read
    outcomes = re.findall(r"%else %if &_rag_houtcome\. EQ (\w+) %then",
                          _macro("_rag_history_table"))
    assert outcomes == ["upload", "rows", "replace", "save"]
    assert "%if &_rag_houtcome. EQ published %then %do;" in _macro("_rag_history_table")
    assert "%let _rag_houtcome = unknown;" in _macro("_rag_history_table")
    for name in ("_rag_history_table", "_rag_publish_history"):
        macro = _macro(name)
        assert "ERROR" not in macro.upper().replace("_RAG_HISTERROR", ""), name
        # the run stays what it was: nothing here writes its return code, its
        # message or its state
        assert not re.search(r"%let _rag_(rc|error|state|summary)\b", macro), name
        assert "_rag_rc" not in macro.replace("%if &_rag_rc. NE 0 %then %return;", "")


def test_what_cas_answered_does_not_decide_the_state_of_the_job():
    """The condition code is put back behind the publication, as the reload
    of a saved table puts it back, so the one statement that fails a job
    still asks the run and nothing else."""
    publish = _macro("_rag_publish_history")
    _in_order(publish, "%let _rag_hist_syscc = &syscc.;", "cas _ragcas4;",
              "%_rag_history_table(Run,", "%_rag_history_table(Event,",
              "cas _ragcas4 terminate;", "%let syscc = &_rag_hist_syscc.;")
    assert ("%local _rag_histPrefix _rag_histDone _rag_hist_syscc "
            "_rag_hist_check;") in publish
    # An error of CAS must not leave a batch session checking syntax only:
    # the DATA step that writes the last line of the log would not run. The
    # option is switched off around the two tables and put back to what it
    # was.
    _in_order(publish, "%let _rag_hist_check = %sysfunc(getoption(syntaxcheck));",
              "options nosyntaxcheck;", "cas _ragcas4;", "cas _ragcas4 terminate;",
              "options &_rag_hist_check.;")
    assert re.findall(r"\boptions ([^;]*);", publish) == [
        "nosyntaxcheck", "&_rag_hist_check."]
    after = _after_python()
    assert after.count("SYSCC = ") == 1
    assert "%if &_rag_rc. NE 0 %then %let SYSCC = 1012;" in after
    assert not re.search(r"\babort\b", _without_comments(_job()), re.IGNORECASE)


def test_the_note_names_each_table_with_its_rows():
    """One NOTE for the run: `<caslib>.<table> with <n> rows` for each table
    that was published, the two joined by `and`."""
    table = _macro("_rag_history_table")
    _in_order(table, "%if &_rag_houtcome. EQ published %then %do;",
              "%if %length(%superq(_rag_histDone)) GT 0 %then "
              "%let _rag_histDone = &_rag_histDone. and;",
              "%let _rag_histDone = &_rag_histDone. &ledgerCaslib..&_rag_ht. "
              "with &_rag_hrows. rows;")
    publish = _macro("_rag_publish_history")
    assert ("%if %length(%superq(_rag_histDone)) GT 0 %then\n"
            "        %put NOTE: RAG ingestion: the run history was published to CAS, "
            "promoted and saved - &_rag_histDone..;") in publish
    # the macro that collects the parts does not own the variable
    assert "_rag_histDone" not in re.findall(r"%local ([^;]*);", table)[0]


def test_the_job_still_writes_the_ledger_in_two_places_and_nowhere_else():
    """The history tables are replaced with actions of PROC CAS; the three
    statements of PROC CASUTIL stay the ones of the ledger."""
    code = _without_comments(_before_python() + _after_python())
    for statement in ("droptable casdata=", "promote casdata=", "save casdata=",
                      "load data="):
        assert statement not in _macro("_rag_history_table")
        assert statement not in _macro("_rag_publish_history")
    assert code.count("proc casutil") == 2
    # and the ledger table is named here only to take the project from it
    assert "ledgerTable" not in _macro("_rag_history_table")
    publish = _macro("_rag_publish_history")
    assert "ledgerTable" not in publish[publish.index("%if &_rag_histState. NE read"):]
