"""Every %put, %let and macro call of the LLM and MM custom steps: which text it resolves.

Measured on 2026-10-13 with LLM - Get Options 1.1: its DATA step had put the statement
'filename _slo_LLM temp;' into a macro variable, a %put resolved that variable with an ampersand,
and the semicolon of the value ended the %put - ERROR 180-322 and a failed job.

That is one kind of value. A macro variable the program makes while it runs - with CALL SYMPUT,
CALL SYMPUTX or INTO of PROC SQL - holds its text as it is: nothing has masked a semicolon, a
quotation mark, an ampersand or a percent sign of it. A statement of the macro language that
resolves it with an ampersand reads that text again as code. %SUPERQ and SYMGET give the text as
text. So in the nine steps read here no %put, no %let and no macro call resolves such a variable
with an ampersand - but for the ones named below, each with what its value can be.

What is not read here, and why:
- A value of a control (the name of a model, an id, a path). It is no value the program makes: it
  is assigned in front of the program, and a statement of the program that copies it with a %let
  resolves what was assigned there.
- A value the Python of a step hands to SAS: every one goes through the function sas_safe of that
  program (LLM - Get All Prompts), which its own tests hold.
- A statement that is none of the three: a macro variable that is a statement in open code
  (&_slo_fl_path.; of the two steps that write a code file) is followed in test_custom_steps.py, and
  a value between quotation marks in a DATA step or in an address is resolved there by SAS.
- LLM - Log Parser, whose program is generated from Log-Parser-Code.sas and tested beside it.

The SAS code can not run here. The tests read the programs as text.
"""
import json
import re
from pathlib import Path

import pytest

STEPS = Path(__file__).resolve().parents[2] / "Custom-Steps"
READ = ["LLM - Create LLM Call.step", "LLM - Get All Prompts.step", "LLM - Get Options.step", "LLM - Get Prompt Experiments.step",
        "LLM - List Available LLMs.step", "MM - Get All Repositories.step", "MM - Get Model Information.step",
        "MM - Get Models in Project.step", "MM - Get Projects in Repository.step"]
# The words behind a percent sign that are the macro language itself: every other one is the call of a macro
LANGUAGE = {"put", "let", "if", "then", "else", "do", "end", "to", "by", "macro", "mend", "local", "global", "sysfunc", "qsysfunc", "superq",
            "str", "nrstr", "bquote", "nrbquote", "eval", "sysevalf", "scan", "qscan", "upcase", "length", "symexist", "symdel",
            "sysmacdelete", "return", "include", "substr", "index", "unquote", "until", "while", "goto", "sysmacexist"}
# The variables a program makes while it runs and resolves with an ampersand in a %put, a %let or a macro call: what each can hold
RESOLVED = {
    "LLM - Get All Prompts.step": {
        "_gap_rs_caslib": "the CAS library of a RAG setup: the Python of the step leaves out a setup whose library holds a quotation "
                          "mark, an ampersand, a percent sign or a semicolon",
        "_gap_rs_table": "the run table of a RAG setup: a SAS name, or the Python of the step leaves the setup out",
        "_gap_rs_ledger": "the ledger of a RAG setup: a SAS name, or the Python of the step hands over an empty text",
        "_gap_lruns": "a number: count(*) of PROC SQL",
    },
}


def program(name):
    """The SAS of a step, without the Python between submit and endsubmit."""
    text = json.loads((STEPS / name).read_bytes().decode("utf-8"))["templates"]["SAS"].replace("\r\n", "\n")
    return re.sub(r"(?ms)^submit;\n.*?^endsubmit;", "submit;\nendsubmit;", text)


def python_of(name):
    text = json.loads((STEPS / name).read_bytes().decode("utf-8"))["templates"]["SAS"].replace("\r\n", "\n")
    return "\n".join(re.findall(r"(?ms)^submit;\n(.*?)^endsubmit;", text))


def made_while_it_runs(text):
    """The macro variables a program makes with CALL SYMPUT, CALL SYMPUTX or INTO, in lower case."""
    made = re.findall(r"call\s+symputx?\s*\(\s*['\"](\w+)['\"]", text, flags=re.IGNORECASE)
    made += re.findall(r"\binto\s*:\s*(\w+)", text, flags=re.IGNORECASE)
    return {name.lower() for name in made}


def macro_statements(text):
    """Every %put, %let and macro call of a program, as (kind, the statement up to its semicolon)."""
    found = []
    for line in text.split("\n"):
        for match in re.finditer(r"%(\w+)", line):
            word = match.group(1).lower()
            if word in LANGUAGE and word not in ("put", "let"):
                continue
            statement = line[match.start():]
            end = statement.find(";")
            found.append((word if word in ("put", "let") else "call", statement if end < 0 else statement[:end + 1]))
    return found


def test_the_steps_that_are_read():
    names = sorted(path.name for path in STEPS.glob("*.step") if path.name.startswith(("LLM - ", "MM - ")))

    assert names == sorted(READ + ["LLM - Log Parser.step"])
    # what the reading is made of: every line of the nine programs that holds one of the three, a text of the
    # code a step writes included
    kinds = [kind for name in READ for kind, _ in macro_statements(program(name))]
    # 18, 85 and 36 until 2026-10-20: MM - Get Model Information 1.2 has four %put, four %let and one macro call more, and
    # the code LLM - Create LLM Call 1.6 and LLM - Get Options 1.5 write four %let more each (the provider key)
    assert {kind: kinds.count(kind) for kind in ("put", "let", "call")} == {"put": 22, "let": 97, "call": 37}


@pytest.mark.parametrize("name", READ, ids=[name[:-5] for name in READ])
def test_no_statement_of_the_macro_language_resolves_a_text_the_program_made(name):
    text = program(name)
    made = made_while_it_runs(text)
    resolved = {}
    for kind, statement in macro_statements(text):
        for variable in re.findall(r"&+(\w+)", statement):
            if variable.lower() in made:
                resolved.setdefault(variable, []).append(statement)

    assert sorted(resolved) == sorted(RESOLVED.get(name, {})), resolved
    # the reading is not empty: every program has such statements, and each makes a variable while it runs - MM - Get
    # Model Information since 1.2, whose list of properties is the one of the model (_mgi_vars, the names of SAS
    # variables of the table the JSON engine makes, which a %length reads and a VAR statement names)
    assert macro_statements(text) and made


def test_what_the_two_steps_that_write_a_code_file_make_is_read_as_text():
    """The two steps make five variables. The reason of a run that wrote no code (_slo_why) holds the address of
    the options file and the names of its options: it is read through %SUPERQ. The statement of the path
    (_slo_fl_path) holds the path: no %put, %let or macro call resolves it - the NOTE of LLM - Get Options reads it
    through %SUPERQ."""
    for name in ("LLM - Create LLM Call.step", "LLM - Get Options.step"):
        text = program(name)

        assert made_while_it_runs(text) >= {"_slo_optionsfileuri", "_slo_ok", "_slo_why", "_slo_fl_path"}
        puts = [statement for kind, statement in macro_statements(text) if kind == "put"]
        assert len(puts) == 2 and not [statement for statement in puts if "&" in statement]
        # the first %put of LLM - Create LLM Call is a text of its DATA step, which names no variable
        assert [re.findall(r"%superq\((\w+)\)", statement) for statement in puts] == [
            ["_slo_fl_path"] if name == "LLM - Get Options.step" else [], ["_slo_why"]]
        # the reason is set in three statements, each of which reads the address through %SUPERQ, in one DATA step through
        # SYMGET, and in the statement of a run without a model, which names no variable
        reasons = [statement for kind, statement in macro_statements(text) if kind == "let" and statement.startswith("%let _slo_why")]
        assert len(reasons) == 4 and all("&" not in statement for statement in reasons)
        assert ["%superq(_slo_optionsFileURI)" in statement for statement in reasons] == [True, True, False, True]
        assert "%" not in reasons[2][1:] and "symget('_slo_optionsFileURI')" in text
        # the name of the model and the address are looked at through %SUPERQ where the step asks whether there is a model
        assert "%let _slo_chosen = %eval(%length(%superq(_slo_LLM_name)) GT 0 and %length(%superq(_slo_optionsFileURI)) GT 0);" in text


def test_what_get_all_prompts_resolves_was_looked_at_by_its_python():
    """The four variables of RESOLVED come from a table the Python of the step wrote. A name that could not stand
    in SAS code never gets there."""
    name = "LLM - Get All Prompts.step"
    python, text = python_of(name), program(name)

    assert "if not SAS_NAME.fullmatch(table) or re.search('[\"\\'&%;]', caslib):" in python
    assert "'ledger_table': ledger if SAS_NAME.fullmatch(ledger) else ''," in python
    assert re.search(r"^SAS_NAME = re\.compile\(r'\[A-Za-z_\]\[A-Za-z0-9_\]\{0,31\}'\)$", python, flags=re.MULTILINE)
    for variable, column in (("_gap_rs_caslib", "tables_caslib"), ("_gap_rs_table", "runs_table"), ("_gap_rs_ledger", "ledger_table")):
        assert f"call symputx('{variable}', {column}, 'L');" in text
    assert "select count(*) into :_gap_lruns trimmed from work._gap_runs_one;" in text


@pytest.mark.parametrize("name", READ, ids=[name[:-5] for name in READ])
def test_a_step_deletes_the_macros_it_defines_and_no_other(name):
    """Found while the statements were read: MM - Get Model Information 1.0 ended with %sysmacdelete of
    _mgm_model_pagination, the macro of MM - Get Models in Project. The step defines no macro, so the statement
    deleted nothing a run of the step had made. Version 1.1 has no such statement."""
    text = program(name)
    defined = re.findall(r"^\s*%macro (\w+)", text, flags=re.MULTILINE)
    deleted = re.findall(r"%sysmacdelete (\w+)", text)

    assert sorted(deleted) == sorted(defined), name
    if name == "MM - Get Model Information.step":
        # version 1.2 reads the model in a macro of its own, and deletes it
        assert defined == ["_mgi_get_information"] and text.endswith("%symdel _mgi_viyaHost _mgi_model_id / nowarn;")
        # the comment at the top names this step and the prefix of its macro variables
        assert text.startswith("/**************************************************************\n"
                               "    Get the information SAS Model Manager holds on one model\n\n"
                               "    mgi is short for:\n      Model Manager Get Information\n")
        assert "_mgm_" not in text and "mgm" not in text
        ui = json.loads(json.loads((STEPS / name).read_bytes().decode("utf-8"))["ui"])
        changelog = [child["text"] for page in ui["pages"] for section in page["children"] for child in section.get("children", [])
                     if child["id"] == "textChangelog"]
        assert changelog[0].startswith("* Version: 1.2 (20OCT2026)\n")
        changelog = [changelog[0][changelog[0].index("* Version: 1.1 "):]]
        assert changelog == [
            "* Version: 1.1 (13OCT2026)\n"
            "      - The last statement of the program deleted the macro of the step MM - Get Models in Project, which this step does not "
            "define. The statement is gone, and the comment at the top of the program names this step\n"
            "* Version: 1.0 (03AUG2024)\n      - Initial version"]


def test_the_macro_call_a_data_step_makes_is_made_of_an_id_of_sas_model_manager():
    """LLM - List Available LLMs calls a macro once per model through CALL EXECUTE, with the id of the model as
    its argument. The id is a column of 36 characters that SAS Model Manager fills."""
    text = program("LLM - List Available LLMs.step")

    assert text.count("call execute(") == 1
    assert "call execute(catx(' ', '%nrstr(%_sll_model_options_file)', '(', id, ')'));" in text
    assert "length name $256. id $36. optionsFileURI $49.;" in text and "set _sll_rp.items(keep=id name);" in text
    # no other step makes a macro call out of data
    for name in READ:
        assert ("call execute" in program(name).lower()) == (name == "LLM - List Available LLMs.step"), name
