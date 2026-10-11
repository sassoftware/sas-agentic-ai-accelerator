# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""One ledger, two writers: the run lock and the column types of the ledger.

The ingestion ledger (<project>_LEDGER) is written by the RAG steps of a SAS
Studio flow and by the single-program job Ingest-Documents.sas, and the two do
not leave it alike. The Load and Purge steps upload it through SWAT, whose
text columns are VARCHAR; the job loads it from a SAS data set, whose text
columns are CHAR. CAS refuses to append a row whose column is the one where
the table has the other:

    ERROR: The variable doc_id has been specified with incompatible data types.

That was found on a live system in both directions. Each writer declared its
run lock the one way it leaves the ledger itself, so a flow could not lock a
ledger the job had written, and the job could not lock one a flow had written.

Nothing here runs SAS or CAS - only SAS runs SAS. The tests read the shipped
files and pin the text: that no row is appended to the ledger with one fixed
declaration, that the declaration follows what the table is asked for, that
the job and the steps hold the same construction, and that every other
statement that rewrites the ledger inherits its columns from the table. The
behaviour in CAS is measured on a live system, not here.

The Python the steps run is executed, where it is Python: a ledger value read
back from a CHAR column is compared without the blanks CAS pads it with.
"""
import json
import pathlib
import re
import textwrap

import pytest

from rag_core.steps import LEDGER_COLUMNS

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"
JOBS = RAG.parent / "RAG-Ingestion"
JOB = JOBS / "Ingest-Documents.sas"

RAG_STEPS = {
    "RAG - Chunk Documents": "rgch", "RAG - Embed Chunks": "rgem",
    "RAG - Enrich Chunks": "rgen", "RAG - Extract Text": "rgex",
    "RAG - List Documents": "rgls", "RAG - Load Vector Store": "rgld",
    "RAG - Purge Documents": "rgpu", "RAG - Register Setup": "rgrg",
    "RAG - Retrieve Context": "rgrt",
}
# the steps that take the run lock, and the ones that read the ledger back
LOCKERS = ("RAG - List Documents", "RAG - Purge Documents")
READERS = ("RAG - List Documents", "RAG - Load Vector Store",
           "RAG - Purge Documents", "RAG - Register Setup")

# what a ledger row is declared with, either way (the lengths are the job's)
LENGTHS = {"doc_id": 40, "source_uri": 1024, "source_kind": 16,
           "content_hash": 64, "mtime": 32, "status": 12, "error_text": 512,
           "pipeline_version": 32, "config_hash": 32, "chunk_count": 8,
           "run_id": 32, "updated_at": 24}


# ---------------------------------------------------------------------------
# the files, taken apart
# ---------------------------------------------------------------------------
def _step(name):
    return json.loads((STEPS / (name + ".step")).read_text(encoding="utf-8"))


def _sas(name):
    return _step(name)["templates"]["SAS"]


def _python(name):
    return _sas(name).split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _around_python(name):
    """The SAS of a step without the program PROC PYTHON runs."""
    sas = _sas(name)
    head, rest = sas.split("\nsubmit;\n", 1)
    return head + "\n" + rest.split("\nendsubmit;", 1)[1]


def _job():
    return JOB.read_text(encoding="utf-8").replace("\r\n", "\n")


def _job_around_python():
    sas = _job()
    head, rest = sas.split("\nsubmit;\n", 1)
    return head + "\n" + rest.split("\nendsubmit;", 1)[1]


def _without_comments(sas):
    return re.sub(r"/\*.*?\*/", "", sas, flags=re.DOTALL)


def _macro(sas, name):
    """The text of one macro definition, %macro to %mend."""
    found = re.search(r"%macro " + re.escape(name) + r"\b.*?%mend " + re.escape(name) + ";",
                      sas, re.DOTALL)
    assert found, f"the macro {name} is gone"
    return found.group(0)


def _data_steps(sas):
    """Every DATA step of a program: (the DATA statement, its body)."""
    return [(found.group(1), found.group(2)) for found in re.finditer(
        r"\bdata\s+([^;]+);(.*?)\brun;", _without_comments(sas),
        re.DOTALL | re.IGNORECASE)]


def _writers():
    """(label, prefix, the SAS around the Python) of everything that takes
    the run lock: the two steps and the job."""
    found = [(name, "_" + RAG_STEPS[name] + "_", _around_python(name))
             for name in LOCKERS]
    found.append(("Ingest-Documents.sas", "_rag_", _job_around_python()))
    return found


def _neutral(text, prefix):
    return text.replace(prefix, "_X_")


WRITERS = [writer[0] for writer in _writers()]


def _writer(label):
    return next(writer for writer in _writers() if writer[0] == label)


# ---------------------------------------------------------------------------
# the run lock is declared the way the table holds its text
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label", WRITERS)
def test_no_row_is_appended_to_the_ledger_with_a_fixed_declaration(label):
    """An append whose LENGTH statement names one type can only be written to
    a ledger of that type. Every append takes its declaration from the macro
    that follows the table."""
    _, prefix, sas = _writer(label)
    appends = [(statement, body) for statement, body in _data_steps(sas)
               if "append=yes" in statement.lower()]
    assert appends, "nothing appends the run lock any more"
    for statement, body in appends:
        assert "LEDGER" in statement or "ledgerTable" in statement, statement
        assert not re.search(r"\blength\b", body, re.IGNORECASE), statement
        assert "varchar" not in body.lower() and "$" not in body, statement
        assert body.lstrip().startswith("%" + prefix + "ledger_length\n"), statement
        assert '"__run_lock__"' in body or "'__run_lock__'" in body


@pytest.mark.parametrize("label", WRITERS)
def test_the_table_is_asked_right_before_the_lock_is_appended(label):
    _, prefix, sas = _writer(label)
    clean = _without_comments(sas)
    calls = list(re.finditer(r"%" + prefix + r"ledger_types\(([^)]*)\);\s*data\s+([^;]+);",
                             clean))
    assert len(calls) == 1
    assert "append=yes" in calls[0].group(2)
    # and nowhere else is the lock appended
    assert len(re.findall(r"append=yes", clean)) == 1


@pytest.mark.parametrize("label,expected", [
    ("RAG - List Documents",
     "rglscas, &_rgls_tablesCaslib., &_rgls_ragProject._LEDGER, varchar"),
    ("RAG - Purge Documents",
     "rgpucas, &_rgpu_tablesCaslib., &_rgpu_ragProject._LEDGER, varchar"),
    ("Ingest-Documents.sas", "_ragcas, &ledgerCaslib., &ledgerTable., char"),
])
def test_the_question_names_the_session_the_ledger_and_the_writers_own_type(
        label, expected):
    """The last argument is what the writer falls back to when the table does
    not answer: the type it leaves the ledger in itself, which is the type it
    declared before the table was asked at all."""
    _, prefix, sas = _writer(label)
    found = re.search(r"%" + prefix + r"ledger_types\(([^)]*)\);", _without_comments(sas))
    assert found.group(1) == expected


@pytest.mark.parametrize("label", LOCKERS)
def test_a_ledger_a_step_starts_is_declared_by_the_same_macro(label):
    """No ledger yet: the step creates it with the lock row, as varchar - the
    type the Load step replaces it with."""
    _, prefix, sas = _writer(label)
    starts = [(statement, body) for statement, body in _data_steps(sas)
              if "_LEDGER(promote=yes)" in statement]
    assert len(starts) == 1
    statement, body = starts[0]
    assert body.lstrip().startswith("%" + prefix + "ledger_length\n")
    assert not re.search(r"\blength\b", body, re.IGNORECASE)
    before = _without_comments(sas).split("data " + statement + ";", 1)[0]
    assert before.rstrip().endswith("%let " + prefix + "ledgerTypes = varchar;")


def test_the_job_and_the_steps_hold_the_same_two_macros():
    """One construction in three places. A change made to one of them and not
    to the others is the drift this test exists for."""
    texts = {}
    for label, prefix, sas in _writers():
        texts[label] = (_neutral(_macro(sas, prefix + "ledger_types"), prefix),
                        _neutral(_macro(sas, prefix + "ledger_length"), prefix))
    assert len(set(texts.values())) == 1, sorted(texts)
    types, length = next(iter(texts.values()))
    assert "_X_" in types and "_X_" in length


def test_the_table_is_asked_through_column_info_and_doc_id_answers():
    _, prefix, sas = _writer("Ingest-Documents.sas")
    macro = _macro(sas, prefix + "ledger_types")
    assert "%macro _rag_ledger_types(session, caslib, table, fallback);" in macro
    assert "session &session.;" in macro
    assert re.search(r'table\.columnInfo result=_rag_ci /\s+'
                     r'table=\{caslib="&caslib\.", name="&table\."\};', macro)
    assert "saveresult _rag_ci dataout=work._rag_ledger_ci;" in macro
    assert 'if lowcase(strip(Column)) = "doc_id" then' in macro
    assert 'call symputx("_rag_ledgerTypes", lowcase(strip(Type)), "G");' in macro
    # neither char nor varchar: the writer's own type, and the log says so
    assert ("%if &_rag_ledgerTypes. NE char %then "
            "%if &_rag_ledgerTypes. NE varchar %then %do;") in macro
    assert "%let _rag_ledgerTypes = &fallback.;" in macro
    assert "could not be read - the run lock is written as &fallback." in macro
    assert ("holds its text columns as &_rag_ledgerTypes. - the run lock is "
            "written the same way") in macro


def test_the_declaration_names_every_ledger_column_both_ways():
    _, prefix, sas = _writer("Ingest-Documents.sas")
    macro = _macro(sas, prefix + "ledger_length")
    found = re.search(r"%if &_rag_ledgerTypes\. EQ char %then %do;\s+length (.*?);\s+%end;"
                      r"\s+%else %do;\s+length (.*?);\s+%end;", macro, re.DOTALL)
    assert found, "the two declarations are no longer one %if and its %else"
    as_char = re.findall(r"(\w+) (\$ \d+|\d+)", found.group(1))
    as_varchar = re.findall(r"(\w+) (varchar\(\d+\)|\d+)", found.group(2))
    assert [name for name, _ in as_char] == LEDGER_COLUMNS
    assert [name for name, _ in as_varchar] == LEDGER_COLUMNS
    assert list(LENGTHS) == LEDGER_COLUMNS
    for name, spelled in as_char:
        expected = "8" if name == "chunk_count" else "$ %d" % LENGTHS[name]
        assert spelled == expected, name
    for name, spelled in as_varchar:
        expected = "8" if name == "chunk_count" else "varchar(%d)" % LENGTHS[name]
        assert spelled == expected, name


def test_the_char_declaration_is_the_one_the_job_stores_its_ledger_with():
    """A lock row appended to a ledger the job wrote has the columns of that
    ledger, length for length."""
    sas = _job_around_python()
    stored = next(body for statement, body in _data_steps(sas)
                  if statement.strip() == "work._rag_ledger_new")
    declared = re.search(r"length (.*?);", stored, re.DOTALL).group(1)
    assert re.findall(r"(\w+) (\$ \d+|\d+)", declared) == [
        (name, "8" if name == "chunk_count" else "$ %d" % LENGTHS[name])
        for name in LEDGER_COLUMNS]


# ---------------------------------------------------------------------------
# what each writer leaves, and everything else that rewrites the ledger
# ---------------------------------------------------------------------------
def test_the_job_leaves_a_char_ledger_and_the_steps_a_varchar_one():
    """The two types are a fact of how each writer stores the ledger, and the
    reason the lock has to follow the table. Whoever changes one of the two
    stores meets this test first."""
    job = _job_around_python()
    store = _macro(job, "_rag_store_ledger")
    assert 'load data=work._rag_ledger_new casout="&ledgerTable.";' in store
    for name in ("RAG - Load Vector Store", "RAG - Purge Documents"):
        python = _python(name)
        assert '"type": "double" if column in numeric else "varchar"}' in python
        assert re.search(r'cas_stage\(\s*project_table\("_LEDGER"\)', python), name
        prefix = RAG_STEPS[name]
        assert "%%_%s_swap(_LEDGER, &_%s_nledger.);" % (prefix, prefix) in _sas(name)


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_a_released_lock_leaves_the_ledger_with_the_columns_it_had(name):
    """Every step that fails hands the lock back by rewriting the ledger
    without that row. The rewrite reads the table and declares nothing, so
    the ledger keeps its types, whichever they are."""
    prefix = RAG_STEPS[name]
    sas = _around_python(name)
    rewrites = [(statement, body) for statement, body in _data_steps(sas)
                if "_LEDGER_R(replace=yes)" in statement]
    if name == "RAG - Retrieve Context":      # reads the collection, no ledger
        assert not rewrites
        return
    assert len(rewrites) == 1
    body = rewrites[0][1]
    assert not re.search(r"\blength\b", body, re.IGNORECASE)
    assert re.fullmatch(
        r"\s*set %sout\.&_%s_ragProject\._LEDGER\(where=\(not \(doc_id = "
        r'"__run_lock__"\s+and run_id = "&_%s_runId\."\)\)\);\s*'
        % (prefix, prefix, prefix), body), body


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_no_step_declares_a_ledger_row_outside_the_shared_macro(name):
    """`doc_id varchar(40)` or `doc_id $ 40` on a row written to the ledger
    is the defect; `$ 40` on an inventory or a report is not (those are
    tables of the step itself)."""
    prefix = "_" + RAG_STEPS[name] + "_"
    sas = _around_python(name)
    outside = sas
    if name in LOCKERS:
        outside = sas.replace(_macro(sas, prefix + "ledger_length"), "")
    assert "varchar(" not in _without_comments(outside)
    for statement, body in _data_steps(outside):
        if "_LEDGER" in statement and "_LEDGER_R" not in statement:
            assert not re.search(r"\blength\b", body, re.IGNORECASE), statement


def test_the_retrieval_test_job_does_not_touch_the_ledger():
    text = (JOBS / "Test-Retrieval.sas").read_text(encoding="utf-8")
    assert "ledger" not in text.lower() and "append=" not in text.lower()


# ---------------------------------------------------------------------------
# the SAS still nests
# ---------------------------------------------------------------------------
def _balance(sas, opener, closer):
    clean = _without_comments(sas)
    return (len(re.findall(opener + r"\b", clean)),
            len(re.findall(closer + r"\b", clean)))


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_every_do_has_its_end_in_a_step(name):
    sas = _around_python(name)
    opened, closed = _balance(sas, "%do", "%end")
    assert opened == closed
    opened, closed = _balance(sas, "%macro", "%mend")
    assert opened == closed
    for found in re.finditer(r"%macro (\w+)", _without_comments(sas)):
        body = _macro(sas, found.group(1))
        assert _balance(body, "%do", "%end")[0] == _balance(body, "%do", "%end")[1], \
            found.group(1)


def test_every_do_has_its_end_in_the_job():
    sas = _job_around_python()
    assert _balance(sas, "%do", "%end")[0] == _balance(sas, "%do", "%end")[1]
    assert _balance(sas, "%macro", "%mend")[0] == _balance(sas, "%macro", "%mend")[1]
    for name in ("_rag_ledger_types", "_rag_ledger_length", "_rag_export_ledger",
                 "_rag_store_ledger"):
        body = _macro(sas, name)
        assert _balance(body, "%do", "%end")[0] == _balance(body, "%do", "%end")[1], name


def test_the_macros_are_defined_before_the_macro_that_calls_them():
    for label, prefix, sas in _writers():
        caller = "%macro _rag_export_ledger;" if label.endswith(".sas") \
            else "%macro " + prefix + "lock;"
        assert sas.index("%mend " + prefix + "ledger_types;") < sas.index(caller), label
        assert sas.index("%mend " + prefix + "ledger_length;") < sas.index(caller), label


# ---------------------------------------------------------------------------
# the step files keep their form
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_a_step_file_is_one_line_of_json_in_its_canonical_form(name):
    raw = (STEPS / (name + ".step")).read_bytes().decode("utf-8")
    assert "\n" not in raw and "\r" not in raw
    assert raw.isascii()
    step = json.loads(raw)
    assert json.dumps(step) == raw
    assert list(step) == ["description", "displayName", "flowMetadata",
                          "metadataVersion", "name", "properties", "templates",
                          "type", "ui"]
    assert list(step["templates"]) == ["SAS"]


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_the_embedded_python_of_a_step_is_a_program(name):
    compile(_python(name), name + ".step", "exec")


def _changelog(name):
    def find(node):
        if isinstance(node, dict):
            if node.get("id") == "textChangelog":
                return node["text"]
            node = list(node.values())
        if isinstance(node, list):
            for value in node:
                found = find(value)
                if found:
                    return found
        return None
    return find(json.loads(_step(name)["ui"]))


@pytest.mark.parametrize("name,version", [
    ("RAG - Chunk Documents", "1.3"), ("RAG - Embed Chunks", "1.4"),
    ("RAG - Enrich Chunks", "1.1"), ("RAG - Extract Text", "1.3"),
    ("RAG - List Documents", "1.3"), ("RAG - Load Vector Store", "1.3"),
    ("RAG - Purge Documents", "1.3"), ("RAG - Register Setup", "1.3"),
    ("RAG - Retrieve Context", "1.3"),
])
def test_the_about_page_of_a_step_says_what_changed(name, version):
    """The About page is where SAS Studio shows which version of a step is
    registered - the one place to see, on a site, whether the step with the
    lock that follows the ledger has arrived."""
    changelog = _changelog(name)
    newest = changelog.split("\n* Version: ")[0]
    assert newest.startswith("* Version: %s (11OCT2026)\n      - " % version)
    assert "NOTE about a quoted string of more than 262 bytes" in newest
    assert ("The run lock is declared the way the ledger holds its text "
            "columns" in newest) == (name in LOCKERS)
    assert ("A ledger with CHAR columns is read without the blanks" in newest) \
        == (name in READERS)
    versions = [float(v) for v in
                re.findall(r"^\* Version: (\d+\.\d+) ", changelog, re.MULTILINE)]
    assert versions == sorted(versions, reverse=True)
    assert len(versions) == len(set(versions))


# ---------------------------------------------------------------------------
# a ledger value read back from a CHAR column
# ---------------------------------------------------------------------------
def _ledger_value_source(name):
    found = re.search(r"\n(        def ledger_value\(value\):\n.*?\n            return value\n)",
                      _python(name), re.DOTALL)
    assert found, name + " no longer defines ledger_value"
    return found.group(1)


def _ledger_value(name):
    scope = {}
    exec(textwrap.dedent(_ledger_value_source(name)), scope)
    return scope["ledger_value"]


def test_the_four_steps_that_read_the_ledger_read_it_the_same_way():
    assert len({_ledger_value_source(name) for name in READERS}) == 1
    for name in READERS:
        python = _python(name)
        assert re.search(
            r"ledger\.append\(\{column: ledger_value\(row\.get\(column\)\)\n"
            r"\s+for column in (LEDGER_COLUMNS|inventory_columns)\}\)", python), name
        assert "else row.get(column))" not in python, name
    for name in sorted(set(RAG_STEPS) - set(READERS)):
        assert "_LEDGER" not in _python(name), name


@pytest.mark.parametrize("name", READERS)
def test_a_char_value_is_compared_without_its_padding(name):
    """CAS stores `run-1760176800-437` in a CHAR(32) column as 32 bytes. Read
    back padded, it is not the id of the run that holds the lock, and the run
    would refuse its own lock as somebody else's."""
    ledger_value = _ledger_value(name)
    assert ledger_value("run-1760176800-437".ljust(32)) == "run-1760176800-437"
    assert ledger_value("__run_lock__".ljust(40)) == "__run_lock__"
    assert ledger_value(" " * 64) == ""
    assert ledger_value("") == ""
    assert ledger_value(None) == ""


@pytest.mark.parametrize("name", READERS)
def test_only_trailing_blanks_are_taken_off(name):
    ledger_value = _ledger_value(name)
    assert ledger_value("  /docs/a file.pdf  ") == "  /docs/a file.pdf"
    assert ledger_value("line\n") == "line\n"
    assert ledger_value(3.0) == 3.0
    assert ledger_value(0) == 0


def test_a_padded_ledger_reads_like_the_same_ledger_unpadded():
    """The rows a step builds from a CHAR ledger are the rows it builds from
    the same ledger as VARCHAR - which is what run_list and the lock check
    compare."""
    ledger_value = _ledger_value("RAG - List Documents")
    row = {"doc_id": "0123456789abcdef", "source_uri": "/docs/policy.pdf",
           "source_kind": "filesystem", "content_hash": "a" * 64,
           "mtime": "1760176800.5", "status": "ingested", "error_text": "",
           "pipeline_version": "v1", "config_hash": "b" * 16, "chunk_count": 3.0,
           "run_id": "run-1760176800-437", "updated_at": "2026-10-11T08:00:00Z"}
    padded = {name: (value.ljust(LENGTHS[name]) if isinstance(value, str) else value)
              for name, value in row.items()}
    assert {name: ledger_value(padded[name]) for name in LEDGER_COLUMNS} == row
