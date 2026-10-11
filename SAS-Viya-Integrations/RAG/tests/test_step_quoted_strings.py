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
bytes as it stands in the file, and the only run-time text of open length that
still reaches a quoted string is the message of a step that FAILED (at most
500 characters, beside the ERROR line it is written in).

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

Nothing here runs SAS. That the NOTE is gone was measured on a live system
for RAG - Register Setup: its log goes from `%_rgrg_status;` straight to the
summary. The logs of the other eight steps were not read for the NOTE; they
hold the same change.

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
def test_the_summary_is_read_with_symget_and_printed_as_before(name):
    prefix = RAG_STEPS[name]
    sas = _around_python(name)
    block = re.search(
        r"    data _null_;\n"
        r"        length summary \$ 6000 part \$ 500;\n"
        r'        summary = symget\("_%s_summary"\);\n'
        r'        putLog "NOTE: ----- %s -----";\n'
        r'        do i = 1 to countw\(summary, "\|"\);\n'
        r'            part = strip\(scan\(summary, i, "\|"\)\);\n'
        r'            if part ne "" then putLog "NOTE:   " part;\n'
        r"        end;\n"
        r"    run;\n" % (prefix, re.escape(name)), sas)
    assert block, "the summary is no longer printed the pinned way"
    assert sas.count("symget(") == 1
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
    declared = re.findall(r"length summary \$ (\d+) part \$ \d+;",
                          _around_python(name))
    assert len(declared) == 1
    # both numbers are read from the file: a longer cut, or a shorter
    # variable, and the summary is truncated where SAS reads it
    assert int(handed_over[0]) * 4 <= int(declared[0])


@pytest.mark.parametrize("name", sorted(RAG_STEPS))
def test_the_failure_message_is_the_only_open_text_left_in_a_string(name):
    """`%superq` puts a value into a string unresolved and whole. One such
    string is left per step: the ERROR line of a failed run, whose message
    the Python block cuts at 500 characters. A failed run can therefore still
    write the NOTE beside its ERROR line; a run that succeeds cannot."""
    prefix = RAG_STEPS[name]
    with_superq = [text for text in _quoted_strings(_around_python(name))
                   if "%superq(" in text]
    assert with_superq == ["ERROR: %s failed: %%superq(_%s_error)" % (name, prefix)]
    assert 'SAS.symput("_%s_error", sas_safe(error_text)[:500])' % prefix in _python(name)


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
    stored; an upload without a usable answer), under the same limit."""
    strings = [text for text in _quoted_strings(_around_python("RAG - Register Setup"))
               if text.startswith("WARNING: RAG - Register Setup: ")]
    assert len(strings) == 12
    assert len(set(strings)) == 12
    assert max(len(text.encode("utf-8")) for text in strings) == 200
    assert ("WARNING: RAG - Register Setup: this retrieval model does NOT verify "
            "TLS certificates. Its two HTTPS calls - to the embedding container "
            "and to the SAS Viya credentials service - accept any certificate.") in strings
    assert ("WARNING: RAG - Register Setup: deploy the RAG content again "
            "(deploy-rag-content) and run this step once more for the setting "
            "to take effect.") in strings
