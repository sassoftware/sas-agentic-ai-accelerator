# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The messages of the RAG steps read in the SAS log as they were written.

Every message of a RAG step reaches the log through SAS.logMessage, and every
value it hands to SAS through SAS.symput - both put the text into a SAS
statement, so each goes through the function sas_safe of the step first. Up
to version 1.3 that function wrote a blank in the place of a quotation mark,
a percent sign, an ampersand and a semicolon. Seen on 2026-10-20 in the log
of RAG - Chunk Documents and RAG - Embed Chunks on SAS Viya 2026.09:

    rag chunk: 2 chunks from 2 documents (budget 217 tokens = the setup s
    256-token chunk window x 85  safety margin)
    rag embed: 2 embedded, 0 failed, usage { calls : 2,  run_time : 0.03...

sas_safe now writes the character the logging programs write for each (the
NOT_FOR_SAS of Link-Logs-To-Models.sas): a backtick for a quotation mark,
pct for a percent sign, + for an ampersand, a comma for a semicolon, and the
two marks of a comment with a blank between their halves. The ingestion job
and Test-Retrieval.sas hand their messages to SAS the same way and hold the
same function. And the lines of rag_core that held such characters on every
run - the counts of the list step, the budget of the chunks, and the usage of
the embedding and the enrichment calls - say the same in words.

SAS can not run here: the function is taken from the text of each program
and run in Python. Measured on SAS Viya 2026.09 on 2026-10-20, with the steps
and the rag_core of this change in a SAS Studio compute context: the NOTE
lines of RAG - List Documents, Chunk Documents and Embed Chunks read

    rag list: 2 documents (new=2)
    rag chunk: 2 chunks from 2 documents (budget 217 tokens: 85 percent of
    the chunk window of 256 tokens the setup asks for, the rest is a safety
    margin)
    rag embed: 2 embedded, 0 failed, usage: 2 calls, 39 tokens, 0.02 seconds
"""
import ast
import json
import pathlib
import re

import pytest

from rag_core.sources import FileSystemSource
from rag_core.steps import run_chunk, run_embed, run_list, usage_text

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"
PROGRAMS = [RAG.parent / "RAG-Ingestion" / "Ingest-Documents.sas", RAG.parent / "RAG-Ingestion" / "Test-Retrieval.sas"]
STEP_FILES = sorted(STEPS.glob("RAG - *.step"))
NEW_VERSIONS = {"RAG - Chunk Documents": "1.4", "RAG - Embed Chunks": "1.5", "RAG - Enrich Chunks": "1.2",
                "RAG - Extract Text": "1.4", "RAG - List Documents": "1.4", "RAG - Load Vector Store": "1.4",
                "RAG - Purge Documents": "1.4", "RAG - Register Setup": "1.4", "RAG - Retrieve Context": "1.4"}
# What SAS reads as code in a statement: a quote, a macro trigger, the end of a statement, a comment
NOT_FOR_SAS = ("'", '"', "%", "&", ";", "/" + "*", "*" + "/")


def _python(path):
    if path.suffix == ".step":
        text = json.loads(path.read_text(encoding="utf-8"))["templates"]["SAS"]
    else:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return text.split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _sas_safe(path):
    """The function sas_safe of the program, as a function of this test."""
    tree = ast.parse(_python(path))
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "sas_safe")
    function.decorator_list = []
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
    return scope["sas_safe"], ast.unparse(function.body[1:] if ast.get_docstring(function) else function.body)


def test_the_nine_steps_and_the_two_programs_are_read():
    assert [path.stem for path in STEP_FILES] == sorted(NEW_VERSIONS)
    assert all(path.is_file() for path in PROGRAMS)


@pytest.mark.parametrize("path", STEP_FILES + PROGRAMS, ids=lambda path: path.stem)
def test_a_character_sas_reads_as_code_is_written_as_one_that_reads_the_same(path):
    sas_safe, _ = _sas_safe(path)

    assert sas_safe("the setup's 256-token window x 85% safety margin") == "the setup`s 256-token window x 85pct  safety margin"
    assert sas_safe('usage {"calls": 2}; R&D') == "usage {`calls`: 2}, R+D"
    assert sas_safe("a /" + "* b *" + "/ c") == "a / * b * / c"
    hostile = "Bob's \"100%\" R&D; /" + "* note *" + "/ &x %y"
    assert not [mark for mark in NOT_FOR_SAS if mark in sas_safe(hostile)]
    # nothing else changes: a text without such a character is handed over as it is, line breaks included
    plain = "rag embed: 2 embedded, 0 failed (budget 217 tokens)\n    second line"
    assert sas_safe(plain) == plain


def test_the_eleven_programs_hold_one_function():
    bodies = {path.stem: _sas_safe(path)[1] for path in STEP_FILES + PROGRAMS}

    assert len(set(bodies.values())) == 1, sorted(bodies)
    # the mapping is the one of the logging programs
    link = (RAG.parent / "Logging-Monitoring" / "Link-Logs-To-Models.sas").read_text(encoding="utf-8")
    theirs = re.search(r"^NOT_FOR_SAS = (.*)$", link, flags=re.MULTILINE).group(1)
    # a tuple of texts, two of them put together with + - nothing else is in either expression
    constant = {"__builtins__": {}}
    assert eval(theirs, constant) == ((("'", '`'), ('"', '`'), ('%', 'pct '), ('&', '+'), (';', ','),
                                       ('/' + '*', '/ *'), ('*' + '/', '* /')))
    ours = re.search(r"for bad, good in (\(.*?\)):\n", next(iter(bodies.values())), flags=re.DOTALL).group(1)
    assert set(re.findall(r"[^\s'\"`(),+]", re.sub(r"'[^']*'|\"[^\"]*\"", "", ours))) == set()
    assert eval(ours, constant) == eval(theirs, constant)


@pytest.mark.parametrize("path", STEP_FILES, ids=lambda path: path.stem)
def test_the_about_tab_says_so_in_a_version_of_its_own(path):
    ui = json.loads(json.loads(path.read_text(encoding="utf-8"))["ui"])

    def find(children):
        for child in children:
            if child.get("id") == "textChangelog":
                return child["text"]
            found = find(child.get("children", []))
            if found:
                return found
        return None
    changelog = find(ui["pages"])
    newest = changelog.split("\n* Version: ")[0]
    assert newest.startswith("* Version: %s (20OCT2026)\n      - A quotation mark, a percent sign, an ampersand or a "
                             "semicolon of a message in the log or of the run summary is written as a character that "
                             "reads the same" % NEW_VERSIONS[path.stem])
    assert "RAG - Chunk Documents wrote the setup s 256-token chunk window x 85  safety margin (seen on 2026-10-20)" in newest
    versions = [float(v) for v in re.findall(r"^\* Version: (\d+\.\d+) ", changelog, re.MULTILINE)]
    assert versions == sorted(versions, reverse=True) and len(versions) == len(set(versions))


# ---------------------------------------------------------------------------
# The lines of rag_core that held such characters on every run
# ---------------------------------------------------------------------------

def test_the_usage_is_said_in_words():
    assert usage_text({"calls": 2, "run_time": 0.0313112735748291, "tokens": 116}) == "2 calls, 116 tokens, 0.03 seconds"
    assert usage_text({"calls": 3, "input_tokens": 240, "output_tokens": 36, "run_time": 1.5, "failed": 1}) == \
        "3 calls, 240 input tokens, 36 output tokens, 1 failed, 1.50 seconds"
    assert usage_text({}) == usage_text(None) == "none"
    # a measurement the function does not know is said with its name
    assert usage_text({"calls": 1, "cost": 0.5}) == "1 calls, cost 0.5"


class _Client:
    def __init__(self):
        self.usage = {"calls": 0, "run_time": 0.0, "tokens": 0}

    def embed(self, text, mode="document"):
        self.usage["calls"] += 1
        self.usage["tokens"] += 58
        self.usage["run_time"] += 0.015
        return [0.1, 0.2]


def test_the_lines_of_the_chunk_and_the_embed_step_hold_nothing_sas_reads_as_code():
    said = []
    elements = [{"doc_id": "d1", "type": "paragraph", "text": "Some text of the first document.", "page": 1},
                {"doc_id": "d2", "type": "paragraph", "text": "Some text of the second document.", "page": 1}]
    inventory = [{"doc_id": "d1", "status": "new", "source_uri": "/a.txt", "content_hash": "h1"},
                 {"doc_id": "d2", "status": "new", "source_uri": "/b.txt", "content_hash": "h2"}]
    chunks = run_chunk(elements, inventory, "recursive", 256, "v1", log=said.append)
    run_embed(chunks, _Client(), max_workers=1, log=said.append)

    assert said == ["rag chunk: 2 chunks from 2 documents (budget 217 tokens: 85 percent of the chunk window of 256 tokens "
                    "the setup asks for, the rest is a safety margin)",
                    "rag embed: 2 embedded, 0 failed, usage: 2 calls, 116 tokens, 0.03 seconds"]
    for line in said:
        assert not [mark for mark in NOT_FOR_SAS if mark in line], line


def test_the_line_of_the_list_step_counts_in_words_and_is_no_summary(tmp_path):
    """The counts per status were a dict in the line - "2 documents ({`new`: 2})" in the log, measured on
    2026-10-20 with the sanitiser of 1.4 - and are words now. They stand in parentheses: the Prompt Builder
    finds the summary of an ingestion run by the counts behind the word documents, and this line is none."""
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    (tmp_path / "b.txt").write_text("two", encoding="utf-8")
    said = []
    run_list(FileSystemSource(str(tmp_path)), [], "run-1", "v1", "cfg", log=said.append)

    assert said[0] == "rag list: 2 documents (new=2)"
    assert not re.search(r"\bdocuments\s+(\w+=\d+(?:,\s*\w+=\d+)*)", said[0])
    assert not [mark for mark in NOT_FOR_SAS if mark in said[0]]
