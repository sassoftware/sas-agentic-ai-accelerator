# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The RAG steps and the 262 bytes of a quoted string.

SAS writes

    NOTE: The quoted string currently being processed has become more than
          262 bytes long.  You might have unbalanced quotation marks.

for every quoted string that is longer than 262 bytes when it is read - and it
reads the string AFTER the macro processor has put its text in. A literal that
is 22 bytes in the file,

    do i = 1 to countw("%superq(_rgrg_summary)", "|");

is as long as the summary it resolves to. Every RAG step printed its summary
through two such literals, so every run whose summary was longer than 262
bytes wrote the NOTE twice, right above the summary. It was found on a live
system in RAG - Register Setup, whose summary is longer than that in every
run; the same two literals stood in all nine steps.

The summary is now read with `symget` into a variable. These tests pin that,
and they pin what is left: no literal in the SAS of a step is longer than 262
bytes as it stands in the file, and no run-time text of open length reaches a
quoted string.

The message of a step that FAILED was the last one that did. The ERROR line
was written as `putLog "ERROR: <step> failed: %superq(_rgls_error)"` with a
message of up to 500 characters, and a failing RAG - List Documents wrote the
NOTE directly above its ERROR line (found on a live system). The line is now
put together in a variable, its message read with `symget`, in all nine
steps. The single-program job Ingest-Documents.sas wrote its last line the
same way, and its message for a run lock that was not read back is longer
than 262 bytes with any table name; it holds the same change.

That the summary was the cause is read from the text; SAS was not asked. The
file of RAG - Register Setup as it was run held a second pair of strings of
that length: two docstrings of the program between `submit;` and
`endsubmit;`, of 362 and 438 bytes. What speaks against them: SAS does not
read that program as SAS text. RAG - List Documents holds an odd number of
apostrophes in the comments of its Python, so a scan that paired quotation
marks across its lines would never find its `endsubmit;` - and the step runs.
And no line of a Python block is longer than 95 bytes. The docstrings were
cut to under 262 bytes all the same, in all nine steps, so that no step file
holds a string of that length in either language; what they explained stands
below them as comments.

Nothing here runs SAS. That the NOTE is gone from a run that succeeds was
measured on a live system: for RAG - Register Setup in SAS Studio, whose log
goes from `%_rgrg_status;` straight to the summary, and for a flow of RAG -
List Documents, Extract Text, Chunk Documents, Embed Chunks and Load Vector
Store on SAS Viya 2026.09, whose log holds no such NOTE. The logs of RAG -
Enrich Chunks, RAG - Purge Documents and RAG - Retrieve Context were not read
for it; they hold the same change.

Two kinds of test stand here, and they are not the same. The tests of the
summary (it never stands inside a quoted string, it is read with symget, the
variable holds what the Python block hands over) fail on the step files from
before the change: they test the defect. The two tests of the length of a
string as it stands in the file do NOT: no literal was longer than 262 bytes
before the change either - the summary was 22 bytes in the file and as long
as its text when SAS read it. They are a guard for a string that is written
later, such as a new WARNING line, and say so.
"""
import io
import json
import pathlib
import re
import tokenize

import pytest

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"
JOB = RAG.parent / "RAG-Ingestion" / "Ingest-Documents.sas"

RAG_STEPS = {
    "RAG - Chunk Documents": "rgch", "RAG - Embed Chunks": "rgem",
    "RAG - Enrich Chunks": "rgen", "RAG - Extract Text": "rgex",
    "RAG - List Documents": "rgls", "RAG - Load Vector Store": "rgld",
    "RAG - Purge Documents": "rgpu", "RAG - Register Setup": "rgrg",
    "RAG - Retrieve Context": "rgrt",
}
LIMIT = 262


def _sas(name):
    step = json.loads((STEPS / (name + ".step")).read_text(encoding="utf-8"))
    return step["templates"]["SAS"]


def _python(name):
    return _sas(name).split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _around_python(name):
    head, rest = _sas(name).split("\nsubmit;\n", 1)
    return head + "\n" + rest.split("\nendsubmit;", 1)[1]


def _job():
    return JOB.read_text(encoding="utf-8").replace("\r\n", "\n")


def _job_python():
    return _job().split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _job_around_python():
    head, rest = _job().split("\nsubmit;\n", 1)
    return head + "\n" + rest.split("\nendsubmit;", 1)[1]


def _quoted_strings(sas):
    """The quoted strings of a SAS program as SAS finds them: comments are
    skipped, a string runs to the next quotation mark of its own kind, and
    that mark doubled is a mark inside the string."""
    found = []
    position = 0
    while position < len(sas):
        if sas.startswith("/*", position):
            end = sas.find("*/", position + 2)
            position = len(sas) if end < 0 else end + 2
            continue
        mark = sas[position]
        if mark in "\"'":
            end = position + 1
            while end < len(sas):
                if sas[end] == mark:
                    if sas[end + 1:end + 2] == mark:
                        end += 2
                        continue
                    break
                end += 1
            assert end < len(sas), "a quoted string is not closed"
            found.append(sas[position + 1:end])
            position = end + 1
            continue
        position += 1
    return found


def test_the_scanner_reads_strings_the_way_the_tests_need():
    assert _quoted_strings("""a = "x"; /* "not" this */ b = 'it''s';""") == ["x", "it''s"]


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_no_quoted_string_of_a_step_is_longer_than_262_bytes(name):
    """A guard, not the test of the defect: it measures the literals as they
    stand in the file, and held before the summary was read with symget too.
    It fails when a literal of more than 262 bytes is written into a step."""
    strings = _quoted_strings(_around_python(name))
    assert strings
    longest = max(strings, key=lambda text: len(text.encode("utf-8")))
    assert len(longest.encode("utf-8")) <= LIMIT, longest[:80]


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_the_summary_never_stands_inside_a_quoted_string(name):
    """The defect itself: the summary, up to 1500 characters, resolved inside
    a literal."""
    variable = "_%s_summary" % RAG_STEPS[name]
    for text in _quoted_strings(_around_python(name)):
        # the name alone, as the argument of symget, is not the value
        if text == variable:
            continue
        assert variable not in text, text[:80]


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_the_summary_is_read_with_symget_and_printed_a_part_a_line(name):
    """Each part is one value with its prefix. `putLog "NOTE:   " part;`
    wrote two: a part that did not fit into the rest of the line then went
    to the next line without the prefix, and the prefix stayed behind as an
    empty NOTE line (seen in a live log of RAG - Register Setup, whose list
    of artifacts is longer than a log line)."""
    prefix = RAG_STEPS[name]
    sas = _around_python(name)
    block = re.search(
        r"    data _null_;\n"
        r"        length summary \$ 6000 part \$ (\d+) line \$ (\d+);\n"
        r'        summary = symget\("_%s_summary"\);\n'
        r'        putLog "NOTE: ----- %s -----";\n'
        r'        do i = 1 to countw\(summary, "\|"\);\n'
        r'            part = strip\(scan\(summary, i, "\|"\)\);\n'
        r'            line = "(NOTE:   )" \|\| part;\n'
        r'            if part ne "" then putLog line;\n'
        r"        end;\n"
        r"    run;\n" % (prefix, re.escape(name)), sas)
    assert block, "the summary is no longer printed the pinned way"
    # the line holds the prefix and all of the part
    assert int(block.group(2)) >= int(block.group(1)) + len(block.group(3))
    assert 'putLog "NOTE:   " part;' not in sas
    # the summary, and the message of a failed run
    assert sas.count("symget(") == 2
    assert "countw(\"%superq" not in sas and "scan(\"%superq" not in sas


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_the_variable_holds_whatever_the_python_block_hands_over(name):
    """The Python block cuts the summary at 1500 characters; in UTF-8 that is
    at most 6000 bytes, the length the DATA step gives the variable."""
    prefix = RAG_STEPS[name]
    python = _python(name)
    handed_over = re.findall(
        r'SAS\.symput\(target, sas_safe\(" \| "\.join\(SUMMARY\)\)\[:(\d+)\]\)', python)
    assert len(handed_over) == 1, "the summary is no longer cut where it was"
    assert 'publish_summary("_%s_summary")' % prefix in python \
        or "publish_summary('_%s_summary')" % prefix in python
    declared = re.findall(r"length summary \$ (\d+) part \$ \d+ line \$ \d+;",
                          _around_python(name))
    assert len(declared) == 1
    # both numbers are read from the file: a longer cut, or a shorter
    # variable, and the summary is truncated where SAS reads it
    assert int(handed_over[0]) * 4 <= int(declared[0])


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_the_error_line_is_put_together_from_a_message_read_with_symget(name):
    """The defect of the failing run: the message, up to 500 characters,
    resolved inside the literal of the ERROR line."""
    prefix = RAG_STEPS[name]
    block = re.search(
        r"        data _null_;\n"
        r"            length message \$ (\d+) line \$ (\d+);\n"
        r'            message = symget\("_%s_error"\);\n'
        r'            line = "(ERROR: %s failed: )" \|\| message;\n'
        r"            putLog line;\n"
        r"        run;\n" % (prefix, re.escape(name)), _around_python(name))
    assert block, "the ERROR line is no longer written the pinned way"
    # the Python block cuts the message at 500 characters; in UTF-8 that is
    # at most 2000 bytes, and the line holds its prefix as well
    assert 'SAS.symput("_%s_error", sas_safe(error_text)[:500])' % prefix in _python(name)
    assert int(block.group(1)) >= 500 * 4
    assert int(block.group(2)) >= int(block.group(1)) + len(block.group(3))


def _takes_no_open_text(strings, *variables):
    """`%superq` puts a value into a string unresolved and whole, so none
    stands in a string. A line the log reads as an ERROR resolves nothing at
    all, and the message and the summary appear in a string only as a name,
    which is how symget is given them."""
    for text in strings:
        assert "%superq(" not in text, text[:80]
        if text.startswith("ERROR:"):
            assert "&" not in text and "%" not in text, text[:80]
        for variable in variables:
            assert text == variable or variable not in text, text[:80]


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_no_quoted_string_of_a_step_takes_a_text_of_open_length(name):
    prefix = RAG_STEPS[name]
    strings = _quoted_strings(_around_python(name))
    assert [text for text in strings if text.startswith("ERROR:")] == [
        "ERROR: %s failed: " % name]
    _takes_no_open_text(strings, "_%s_error" % prefix, "_%s_summary" % prefix)


def test_the_scan_meets_an_error_line_that_resolves_a_macro_variable():
    """The form the ERROR line had, in both spellings a macro variable can
    reach a string in: this is what the test above is there to refuse."""
    for line in ('putLog "ERROR: RAG - List Documents failed: %superq(_rgls_error)";',
                 'putLog "ERROR: RAG - List Documents failed: &_rgls_error.";'):
        with pytest.raises(AssertionError):
            _takes_no_open_text(_quoted_strings(line), "_rgls_error")


def _newest_changelog(name):
    """The entry of 11OCT2026 of the changelog on a step's About tab - the
    newest one until the version of 20OCT2026 came above it."""
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
    step = json.loads((STEPS / (name + ".step")).read_text(encoding="utf-8"))
    changelog = find(json.loads(step["ui"]))
    return next(("* Version: " + entry).rstrip("\n") for entry in changelog.split("* Version: ")[1:]
                if entry.split("\n", 1)[0].endswith("(11OCT2026)"))


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_the_about_tab_no_longer_says_a_failing_step_can_write_the_note(name):
    """It said so while the ERROR line was a quoted string, and it was
    true: seen live in a failing RAG - List Documents."""
    lines = _newest_changelog(name).split("\n      - ")
    assert not any("can still write the NOTE" in line for line in lines)
    error = [line for line in lines if line.startswith(
        "The ERROR line of a run that fails is put together from a message "
        "read with symget as well")]
    assert len(error) == 1
    assert ("seen on SAS Viya 2026.09 in a failing run of this step" in error[0]) \
        == (name == "RAG - List Documents")
    part = [line for line in lines if line.startswith(
        "A part of the summary that is longer than the log line is written "
        "with its NOTE prefix")]
    assert len(part) == 1
    # what was measured of the NOTE of a run that succeeds, and where
    summary = [line for line in lines
               if line.startswith("The run summary is read with symget")]
    assert len(summary) == 1
    flow = ("Measured on SAS Viya 2026.09: the log of a flow of RAG - List "
            "Documents, Extract Text, Chunk Documents, Embed Chunks and Load "
            "Vector Store holds no such NOTE")
    in_the_flow = name in ("RAG - List Documents", "RAG - Extract Text",
                           "RAG - Chunk Documents", "RAG - Embed Chunks",
                           "RAG - Load Vector Store")
    assert summary[0].endswith(flow) == in_the_flow


# ---------------------------------------------------------------------------
# the single-program job: its last line, written the same way
# ---------------------------------------------------------------------------
def test_the_last_line_of_the_job_is_put_together_from_what_symget_reads():
    """ERROR with the message of a failed run, NOTE with the summary of one
    that went through - neither stands in a quoted string any more."""
    block = re.search(
        r"data _null_;\n"
        r"    length text \$ (\d+) line \$ (\d+);\n"
        r'    if "&_rag_rc\." ne "0" then do;\n'
        r'        text = symget\("_rag_error"\);\n'
        r'        line = "(ERROR: RAG ingestion failed: )" \|\| text;\n'
        r"    end;\n"
        r"    else do;\n"
        r'        text = symget\("_rag_summary"\);\n'
        r'        line = "(NOTE: RAG ingestion succeeded: )" \|\| text;\n'
        r"    end;\n"
        r"    putLog line;\n"
        r"run;", _job_around_python())
    assert block, "the last line of the job is no longer written the pinned way"
    python = _job_python()
    assert 'SAS.symput("_rag_error", sas_safe(error_text)[:500])' in python
    assert 'SAS.symput("_rag_summary", sas_safe(summary)[:400])' in python
    assert int(block.group(1)) >= 500 * 4
    assert int(block.group(2)) >= int(block.group(1)) + max(
        len(block.group(3)), len(block.group(4)))


def test_no_quoted_string_of_the_job_takes_a_text_of_open_length():
    strings = _quoted_strings(_job_around_python())
    assert [text for text in strings if text.startswith("ERROR:")] == [
        "ERROR: RAG ingestion failed: "]
    _takes_no_open_text(strings, "_rag_error", "_rag_summary")
    # and no literal is longer than 262 bytes as it stands in the file
    assert max(len(text.encode("utf-8")) for text in strings) <= LIMIT


def test_the_message_of_a_lock_that_was_not_read_back_is_why():
    """It is longer than 262 bytes before the name of the table is in it,
    so every run that failed this way wrote the NOTE above its last line."""
    python = _job_python()
    message = python.split('"the run lock could not be written to the ledger "', 1)[1]
    message = message.split("        # run-<epoch>-<digits>:", 1)[0]
    said = "".join(re.findall(r'"([^"]*)"', message.replace('"."', "")))
    line = ("ERROR: RAG ingestion failed: RuntimeError: the run lock could "
            "not be written to the ledger " + said)
    assert "refusing to ingest unguarded" in line
    assert len(line) > LIMIT


# ---------------------------------------------------------------------------
# the prompt optimization job: its ERROR line, written the same way
#
# Optimize-Prompt-DSPy.sas is no RAG program and has no tests of its own. It
# stands here because it is the program the RAG job took its last lines from,
# and it kept the form they had: `putLog "ERROR: Prompt optimization failed:
# %superq(_opt_error)"`, with a message of up to 500 characters.
# ---------------------------------------------------------------------------
OPTIMIZE = RAG.parent / "Prompt-Optimization" / "Optimize-Prompt-DSPy.sas"


def _optimize():
    return OPTIMIZE.read_text(encoding="utf-8").replace("\r\n", "\n")


def _optimize_around_python():
    head, rest = _optimize().split("\n    submit;\n", 1)
    return head + "\n" + rest.split("\nendsubmit;", 1)[1]


def _optimize_python():
    return _optimize().split("\n    submit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def test_the_error_line_of_the_optimization_job_is_read_with_symget():
    block = re.search(
        r"data _null_;\n"
        r"    length text \$ (\d+) line \$ (\d+);\n"
        r'    if "&_opt_rc\." ne "0" then do;\n'
        r'        text = symget\("_opt_error"\);\n'
        r'        line = "(ERROR: Prompt optimization failed: )" \|\| text;\n'
        r"        putLog line;\n"
        r"    end;\n"
        r"    else\n"
        r'        putLog "NOTE: Prompt optimization succeeded\.";\n'
        r"run;\n?$", _optimize_around_python())
    assert block, "the last lines of the job are no longer written the pinned way"
    # the Python program cuts the message at 500 characters where its text is
    # open; in UTF-8 that is at most 2000 bytes, and the line holds its prefix
    python = _optimize_python()
    handed_over = re.findall(r'SAS\.symput\("_opt_error", (.*)\)\n', python)
    assert handed_over == ["sas_safe(message)[:500]",
                           'sas_safe(tracker_entry["error"])',
                           "sas_safe(error_text)[:500]"]
    # the one that is not cut is a sentence of the program itself
    assert ('tracker_entry["error"] = "No target could be scored - every '
            'candidate was unreachable."') in python
    assert int(block.group(1)) >= 500 * 4
    assert int(block.group(2)) >= int(block.group(1)) + len(block.group(3))


def test_no_quoted_string_of_the_optimization_job_takes_a_text_of_open_length():
    strings = _quoted_strings(_optimize_around_python())
    assert [text for text in strings if text.startswith("ERROR:")] == [
        "ERROR: Prompt optimization failed: "]
    _takes_no_open_text(strings, "_opt_error")
    assert max(len(text.encode("utf-8")) for text in strings) <= LIMIT
    # what is still resolved inside a string is a name or the WORK path
    resolved = sorted({variable for text in strings
                       for variable in re.findall(r"&+(\w+)", text)})
    assert resolved == ["_opt_rc", "casLibrary", "casTable"]


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_a_macro_variable_in_a_string_is_a_name_or_a_state_not_a_text(name):
    """What else is resolved inside a string is short by what it is: a
    caslib, a project, a table or its suffix, a run id, a file name, a
    state. The list is closed, so a new string that takes run-time text is
    met here."""
    prefix = RAG_STEPS[name]
    known = {"caslib", "table", "suffix", "fallback", "file",
             "_%s_tablesCaslib" % prefix, "_%s_ragProject" % prefix,
             "_%s_runId" % prefix, "_%s_ledgerTypes" % prefix}
    for text in _quoted_strings(_around_python(name)):
        for variable in re.findall(r"&+(\w+)", text):
            assert variable in known, (variable, text[:80])


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_no_python_string_of_a_step_is_longer_than_262_bytes(name):
    """The second candidate, removed as well: a docstring is a quoted string
    too, should SAS ever count the ones of the program it hands to Python."""
    strings = [token.string for token in
               tokenize.generate_tokens(io.StringIO(_python(name)).readline)
               if token.type == tokenize.STRING]
    assert strings
    longest = max(strings, key=lambda text: len(text.encode("utf-8")))
    assert len(longest.encode("utf-8")) <= LIMIT, longest[:80]


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_no_line_of_the_python_block_could_hold_such_a_string(name):
    assert max(len(line.encode("utf-8")) for line in _python(name).split("\n")) < LIMIT


@pytest.mark.parametrize("name,function,first_comment", [
    (name, "multiline", "# SAS Studio splits such a field for us")
    for name in sorted(RAG_STEPS)
] + [("RAG - Register Setup", "verify_requested",
      "# SAS Studio hands a checkbox over as 1 (ticked) or 0 (unticked).")])
def test_what_a_cut_docstring_explained_is_still_there(name, function, first_comment):
    found = re.search(r"\n( +)def %s\(.*?\):\n +\"\"\"(.*?)\"\"\"\n +(#.*)\n" % function,
                      _python(name), re.DOTALL)
    assert found, function
    assert found.group(3).startswith(first_comment)


def test_register_setup_keeps_its_warning_lines_word_for_word():
    """The lines that say a model does not verify TLS certificates were the
    first suspects. Each is a literal of its own, none longer than 200 bytes.
    Three lines came later (a run that failed after the score code was
    stored; an upload without a usable answer), three more for a tag that
    was written to a model the run could not read, and the second wording
    of the line on the tag for an upload without an answer, under the same
    limit."""
    strings = [text for text in _quoted_strings(_around_python("RAG - Register Setup"))
               if text.startswith("WARNING: RAG - Register Setup: ")]
    assert len(strings) == 16
    assert len(set(strings)) == 16
    assert max(len(text.encode("utf-8")) for text in strings) == 200
    assert ("WARNING: RAG - Register Setup: this retrieval model does NOT verify "
            "TLS certificates. Its two HTTPS calls - to the embedding container "
            "and to the SAS Viya credentials service - accept any certificate.") in strings
    assert ("WARNING: RAG - Register Setup: deploy the RAG content again "
            "(deploy-rag-content) and run this step once more for the setting "
            "to take effect.") in strings
