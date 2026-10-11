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
                 "_rag_store_ledger", "_rag_release"):
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
    # the run id, and who hands a lock back
    assert ("The run id ends in ten digits taken from a UUID" in newest) \
        == (name in LOCKERS)
    assert ("hands the run lock back only when the lock row of its own run "
            "stands in the ledger" in newest) == (name != "RAG - Retrieve Context")
    assert ("A preview takes no run lock and neither promotes nor saves the "
            "ledger" in newest) == (name == "RAG - Purge Documents")
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


# ---------------------------------------------------------------------------
# the job reads its run lock back
# ---------------------------------------------------------------------------
def _job_python():
    return _job().split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def test_the_job_exports_the_ledger_again_after_it_appended_its_lock():
    """RAG - List Documents reads its lock back and refuses to ingest without
    one. The job appended its lock and never looked: a lock CAS had refused
    went unnoticed, and the job ran unguarded. It now exports the ledger a
    second time, with the statements the first export is made of."""
    macro = _macro(_job(), "_rag_export_ledger")
    first = ('        filename _ragled "%sysfunc(pathname(work))/rag_ledger_in.json";\n'
             "        proc json out=_ragled noSASTags;\n"
             "            export _ragcl.&ledgerTable.;\n"
             "        run; quit;\n"
             "        filename _ragled clear;\n")
    second = first.replace("_ragled", "_raglck").replace(
        "rag_ledger_in.json", "rag_ledger_lock.json")
    assert macro.count(first) == 1 and macro.count(second) == 1
    append = macro.index("data _ragcl.&ledgerTable.(append=yes);")
    assert macro.index(first) < append < macro.index(second)
    # inside the group that runs only when the ledger exists, like the lock
    group = macro.split("%if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;", 1)[1]
    assert group.index(second) < group.index("    %end;\n    libname _ragcl clear;")
    # a fileref is at most eight characters
    assert len("_raglck") <= 8


def test_the_second_export_uses_no_statement_the_job_did_not_run_before():
    """The read-back was to be made of what already runs in this file. The
    statements of the macro, with the names of the second export put back to
    the names of the first, are the statements it had."""
    macro = _without_comments(_macro(_job(), "_rag_export_ledger"))
    statements = {" ".join(statement.split())
                  for statement in macro.replace("_raglck", "_ragled").replace(
                      "rag_ledger_lock.json", "rag_ledger_in.json").split(";")}
    once = {" ".join(statement.split()) for statement in macro.split(
        "filename _raglck", 1)[0].split(";")} | {
        " ".join(statement.split()) for statement in macro.split(
            "filename _raglck clear;", 1)[1].split(";")}
    assert statements == once


def _lock_check(tmp_path, before, after, ledger_exists=True):
    """The job's own read-back, cut out of its Python and run on two exports
    of a ledger. Returns the message it refuses with, or "" when it goes on."""
    python = _job_python()
    start = python.index("        # ---- the lock of THIS run, read back")
    end = python.index("        # run-<epoch>-<digits>:")
    block = textwrap.dedent(python[start:end])
    assert "if os.path.exists(ledger_path):" in block
    ledger_path = tmp_path / "rag_ledger_in.json"
    lock_path = tmp_path / "rag_ledger_lock.json"
    for path in (ledger_path, lock_path):
        if path.exists():
            path.unlink()
    if ledger_exists:
        ledger_path.write_text(json.dumps(before), encoding="utf-8")
    if after is not None:
        lock_path.write_text(json.dumps(after), encoding="utf-8")

    class _Sas:
        @staticmethod
        def symget(name):
            return {"ledgerCaslib": "Public", "ledgerTable": "HR_LEDGER"}[name]

    import os

    scope = {"os": os, "json": json, "SAS": _Sas,
             "WORKPATH": str(tmp_path) + os.sep,
             "ledger_path": str(ledger_path),
             "lock_rows": [row for row in before
                           if row.get("doc_id") == "__run_lock__"]}
    try:
        exec(compile(block, "Ingest-Documents.sas", "exec"), scope)
    except RuntimeError as error:
        return str(error)
    return ""


_DOC = {"doc_id": "d1", "mtime": "1791800000", "status": "ok"}
_STALE = {"doc_id": "__run_lock__", "mtime": "1791700000.5", "status": "lock"}
_OURS = {"DOC_ID": "__run_lock__", "MTIME": "1791812345.25", "STATUS": "lock"}
_REFUSED = ("the run lock could not be written to the ledger Public.HR_LEDGER "
            "- refusing to ingest unguarded. The lock row of this run was not "
            "in the ledger when it was read back: look in the SAS log above "
            "for an ERROR of the append, or of the export after it")


def test_a_lock_that_was_written_lets_the_job_go_on(tmp_path):
    assert _lock_check(tmp_path, [_DOC], [_DOC, _OURS]) == ""
    # beside the stale lock of a run that crashed
    assert _lock_check(tmp_path, [_DOC, _STALE], [_DOC, _STALE, _OURS]) == ""
    # a CHAR ledger: its values come back padded
    padded = {"doc_id": "__run_lock__".ljust(40), "mtime": "1791812345.25".ljust(32)}
    assert _lock_check(tmp_path, [_DOC], [_DOC, padded]) == ""


def test_a_lock_that_was_not_written_stops_the_job(tmp_path):
    """What CAS does to a row whose column types differ from the table: the
    append is refused, the ledger is what it was."""
    assert _lock_check(tmp_path, [_DOC], [_DOC]) == _REFUSED
    # the stale lock of another run is not the lock of this one
    assert _lock_check(tmp_path, [_DOC, _STALE], [_DOC, _STALE]) == _REFUSED
    # and neither is a second export that is not there, or holds nothing
    assert _lock_check(tmp_path, [_DOC], None) == _REFUSED
    assert _lock_check(tmp_path, [_DOC], {}) == _REFUSED


def test_a_first_run_takes_no_lock_and_is_not_stopped(tmp_path):
    """Without a ledger there is no export and no lock, as before."""
    assert _lock_check(tmp_path, [], None, ledger_exists=False) == ""


def test_the_read_back_stands_where_the_job_can_still_stop():
    """After the look at the lock of another run, before the run gets its id
    and before anything is embedded or loaded."""
    python = _job_python()
    another = python.index('"this ledger (run lock held) - retry later")')
    ours = python.index("# ---- the lock of THIS run, read back")
    run_id = python.index('run_id = (f"run-{int(time.time())}-"')
    connections = python.index("# ---- connections")
    assert another < ours < run_id < connections
    # the message leaves the block the way every failure of the job does
    assert 'SAS.symput("_rag_error", sas_safe(error_text)[:500])' in python
    assert "refusing to ingest unguarded" in python


# ---------------------------------------------------------------------------
# the run id
#
# run-<epoch>-<digits>. rag_runs is keyed on it and shared by every project in
# the database, so the digits are there to tell apart two runs that start in
# the same second. They were &sysjobid in the steps and the process id in the
# job, and neither does that: on a live system twelve flow runs, each in a
# compute server of its own, ended in -344 (the four other runs in -343 or
# -345), and the job runs in 400 to 408. Nothing here runs SAS; the steps
# are read, the job's expression is run.
# ---------------------------------------------------------------------------
RUN_ID = re.compile(r"run-\d{10}-\d{10}")


@pytest.mark.parametrize("name", LOCKERS)
def test_the_run_id_of_a_step_does_not_end_in_the_process_id(name):
    prefix = "_" + RAG_STEPS[name] + "_"
    lock = _without_comments(_macro(_around_python(name), prefix + "lock"))
    assigned = re.findall(r"%let " + prefix + r"runId = (.*);", lock)
    assert assigned == [
        "run-%sysfunc(compress(%sysfunc(putn(&" + prefix + "now., 12.))))"
        "-&" + prefix + "digits."]
    # eight hexadecimal digits of a UUID, as a number of ten digits
    assert ("%let " + prefix + "uuid = %sysfunc(uuidgen());") in lock
    assert ("%let " + prefix + "digits = %sysfunc(inputn(%substr(&" + prefix
            + "uuid., 1, 8), hex8.), z10.);") in lock
    # the process id is what is left when UUIDGEN returns nothing, and only
    # that: it is assigned first and replaced
    fallback = lock.index("%let " + prefix + "digits = &sysjobid.;")
    assert fallback < lock.index("%if %length(&" + prefix + "uuid.) GE 8 %then") \
        < lock.index("%let " + prefix + "digits = %sysfunc(inputn(") \
        < lock.index("%let " + prefix + "runId = ")
    assert lock.count("&sysjobid") == 1
    # both are local to the lock macro; the run id is the global one
    assert re.search(r"%local [^;]*" + prefix + r"uuid\b[^;]*" + prefix
                     + r"digits\b", lock)


def _job_run_id():
    """The expression the job's Python gives its run id where SAS handed
    none over, evaluated."""
    import os
    import time

    python = _job_python()
    expression = python.split("        if not run_id:\n            run_id = ", 1)[1].split(
        "\n        M(f\"run {run_id}:", 1)[0]
    assert "getpid" not in expression
    return eval(expression, {"os": os, "time": time, "int": int})


def test_the_run_id_of_the_job_is_made_in_sas_the_way_a_step_makes_it():
    """The lock row has to carry the id, and SAS appends the row before the
    Python program starts. The lines are the ones of RAG - List Documents,
    with the prefix of the job."""
    lock = _without_comments(_macro(_around_python("RAG - List Documents"), "_rgls_lock"))
    start = lock.index("%let _rgls_now = ")
    end = lock.index("%if &_rgls_state. NE run %then %return;")
    lines = [line.strip() for line in lock[start:end].split("\n") if line.strip()]
    assert len(lines) == 6 and lines[-1].startswith("%let _rgls_runId = run-")
    export = _without_comments(_macro(_job_around_python(), "_rag_export_ledger"))
    made = [line.strip() for line in export.split("\n") if line.strip()]
    wanted = [line.replace("_rgls_", "_rag_") for line in lines]
    first = made.index(wanted[0])
    assert made[first:first + len(wanted)] == wanted
    # before the session, the reload and the lock - whatever the run meets
    assert first < made.index("cas _ragcas;")
    assert "%local _rag_now _rag_uuid _rag_digits;" in made[:first]
    # the id is the global one: assigned in open code before the macro runs
    job = _job_around_python()
    assert job.index("%let _rag_runId = ;") < job.index("%macro _rag_export_ledger;")
    assert "_rag_runId" not in re.findall(r"%local ([^;]*);", export)[0]


class _RunIdSas:
    def __init__(self, handed_over):
        self.handed_over = handed_over

    def symget(self, name):
        assert name == "_rag_runId"
        return self.handed_over


def _job_takes(handed_over):
    """The lines that give the run its id, run with what SAS handed over."""
    import os
    import time

    python = _job_python()
    start = python.index('        run_id = str(SAS.symget("_rag_runId") or "").strip()')
    end = python.index('        M(f"run {run_id}:')
    scope = {"os": os, "time": time, "SAS": _RunIdSas(handed_over)}
    exec(textwrap.dedent(python[start:end]), scope)
    return scope["run_id"]


def test_the_python_of_the_job_takes_the_run_id_sas_made():
    assert _job_takes("run-1791722515-0000000344") == "run-1791722515-0000000344"
    assert _job_takes(" run-1791722515-4294967295 ") == "run-1791722515-4294967295"
    # none handed over: the program makes one, in the same form
    for nothing in ("", "   ", None):
        assert RUN_ID.fullmatch(_job_takes(nothing))


def test_the_run_id_of_the_job_ends_in_ten_digits_that_differ():
    drawn = {_job_run_id() for _ in range(50)}
    for run_id in drawn:
        assert RUN_ID.fullmatch(run_id), run_id
    # fifty ids of the same second or two: the epoch does not tell them apart
    assert len({run_id.rsplit("-", 1)[1] for run_id in drawn}) == 50


def test_every_reader_of_a_run_id_still_reads_the_new_one():
    """What is read from a run id is its second part, the epoch; and the id
    has to fit the 32 characters of the ledger column and of the inventory
    (64 in the SingleStore history tables)."""
    from rag_core.history import started_at_from_run_id

    # the largest value eight hexadecimal digits hold, as the steps write it
    longest = "run-1791722515-%010d" % 0xFFFFFFFF
    assert RUN_ID.fullmatch(longest) and len(longest) == 25
    assert len(longest) <= LENGTHS["run_id"]
    assert started_at_from_run_id(longest) == "2026-10-11 12:41:55"
    assert started_at_from_run_id(_job_run_id()) != ""
    # the smallest is padded, so the id has one width
    assert RUN_ID.fullmatch("run-1791722515-%010d" % 0x1)

