"""The settings of the five programs of this folder: a value that is set before a program runs wins.

Each program held its settings as LET statements at its top, so a site kept its values inside
the shipped file - and lost them with the file of the next release, or with an import that
replaces the file. Each program now takes a setting only where its macro variable is not set
yet, by one small macro of its own (the programs are run stand-alone, so there is no shared
file to include). A site keeps its values in a program of its own that sets them and includes
the shipped file.

The two custom steps are not changed by this: a step runs the part of its program below the
settings and gets every setting from a control.

The SAS code can not run here. The macro that sets a default is compared with the one text it
may have, and the calls are then followed the way that text says: a name that is in the symbol
table is left alone, any other name is set.
"""
import json
import re
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[1]
STEPS = FOLDER.parent / "Custom-Steps"

# The one text of the macro, for the prefix of a program
IDIOM = """%macro _{p}_default(_{p}_name, _{p}_value);
    %if %symexist(&_{p}_name.) EQ 0 %then %do;
        %global &_{p}_name.;
        %let &_{p}_name. = &_{p}_value.;
    %end;
%mend _{p}_default;"""

# Per program: its prefix, and the settings with the values its LET statements held before. One setting of the
# parser has no value any more: _log_append_table held 0, which replaced a table that held the rows of earlier runs
# without a word (measured on 2026-10-13: 3,630 rows became 1,024). Without a value the program stops at such a table
PROGRAMS = {
    "Log-Parser-Code.sas": ("log", {
        "_log_path": "sasserver:/export/pvs/sasdata/data/llm/llms.log", "_log_target_table_engine": "CAS",
        "_log_target_table_lib": "Public", "_log_target_table_name": "LLM_LOGS", "_log_append_table": "",
        "_log_promote_table": "1", "_log_save_table": "1"}),
    "Get-All-Prompts.sas": ("gap", {
        "_gap_tag": "Prompt-Engineering", "_gap_rag_tag": "RAG-Engineering", "_gap_llm_project": "LLM Model Project",
        "_gap_embedding_project": "Embedding Model Project", "_gap_decision_project": "Decision Model Project",
        "_gap_out_caslib": "Public", "_gap_out_experiments": "PROMPT_EXPERIMENTS", "_gap_out_optimizations": "PROMPT_OPTIMIZATIONS",
        "_gap_out_rag_setups": "RAG_SETUPS", "_gap_out_rag_runs": "RAG_RUNS", "_gap_out_catalog": "MODEL_CATALOG"}),
    "Link-Logs-To-Models.sas": ("lnk", {
        "_lnk_log_caslib": "Public", "_lnk_log_table": "LLM_LOGS", "_lnk_catalog_caslib": "Public",
        "_lnk_catalog_table": "MODEL_CATALOG", "_lnk_runs_caslib": "Public", "_lnk_runs_table": "RAG_RUNS", "_lnk_redo_all": "0",
        "_lnk_min_literal": "20", "_lnk_utc_offset": "0", "_lnk_query_window": "300", "_lnk_promote_table": "1",
        "_lnk_save_table": "1"}),
    "Load-Fact-Sheets.sas": ("lfs", {"_lfs_path": "/SAS Agentic AI Accelerator/Data", "_lfs_caslib": "Public"}),
    "Build-RAG-Cost-View.sas": ("rcv", {
        "_rcv_fact_caslib": "Public", "_rcv_runs_caslib": "Public", "_rcv_runs_tables": "RAG_HR_RUNS", "_rcv_out_caslib": "Public",
        "_rcv_out_table": "RAG_RUN_COST"}),
}
EVERY_PROGRAM = pytest.mark.parametrize("name", list(PROGRAMS))


def program(name):
    return (FOLDER / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def without_comments(text):
    """A program without its comments: the block comments and the statements that start with a star."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("*"))


def default_calls(text, prefix):
    """The calls of the macro that sets a default, in their order: (name, value)."""
    return re.findall(rf"^%_{prefix}_default\((\w+), (.*)\);$", text, flags=re.MULTILINE)


def settings_after(name, set_before=None):
    """The symbol table behind the settings of a program, for the macro variables that were set
    before it: the macro is the one text above, and its calls are followed as that text says."""
    prefix, _ = PROGRAMS[name]
    text = program(name)
    assert text.count(IDIOM.format(p=prefix)) == 1, "the macro of the program is not the one this test follows"
    symbols = dict(set_before or {})
    for variable, value in default_calls(text, prefix):
        if variable not in symbols:          # %if %symexist(&name.) EQ 0
            symbols[variable] = value        # %global &name.; %let &name. = &value.;
    return symbols


def test_every_program_of_the_folder_is_looked_at():
    assert sorted(path.name for path in FOLDER.glob("*.sas")) == sorted(PROGRAMS)


@EVERY_PROGRAM
def test_without_a_value_every_setting_is_the_default_it_was(name):
    _, defaults = PROGRAMS[name]

    assert settings_after(name) == defaults
    # in the order they stood in, each once
    prefix = PROGRAMS[name][0]
    assert [variable for variable, _ in default_calls(program(name), prefix)] == list(defaults)


@EVERY_PROGRAM
def test_a_value_that_is_set_before_the_program_wins(name):
    _, defaults = PROGRAMS[name]
    for variable in defaults:
        after = settings_after(name, {variable: "set by the site"})
        assert after[variable] == "set by the site"
        assert {other: value for other, value in after.items() if other != variable} == \
            {other: value for other, value in defaults.items() if other != variable}
    # all of them at once, and a variable that is set and empty stays empty
    assert settings_after(name, {variable: "" for variable in defaults}) == {variable: "" for variable in defaults}
    # a macro variable of somebody else is left alone
    assert settings_after(name, {"other": "1"}) == dict(defaults, other="1")


@EVERY_PROGRAM
def test_nothing_sets_a_setting_over_a_value_that_is_there(name):
    """The defaults are the only place a setting is given a value in front of the place it is read at.
    Two settings of the parser are rewritten later, by the program itself: the path loses its
    sasserver: and the library becomes the caslib of its libref - as they were before."""
    prefix, defaults = PROGRAMS[name]
    code = without_comments(program(name))
    idiom = IDIOM.format(p=prefix)
    macro_at = code.index(idiom)
    deleted_at = code.index(f"%sysmacdelete _{prefix}_default;")
    calls = [match.start() for match in re.finditer(rf"^%_{prefix}_default\(", code, flags=re.MULTILINE)]

    assert len(calls) == len(defaults) and macro_at < calls[0] and calls[-1] < deleted_at
    assert code.count(f"%sysmacdelete _{prefix}_default;") == 1
    # no setting is read in front of the last default, and no LET statement names one
    first_read = min(code.find(f"&{variable}.") % (len(code) + 1) for variable in defaults)
    assert first_read > deleted_at
    # the message of the parser for a run without a value of _log_append_table shows the two statements a site
    # can set in front of the program. They stand in %nrstr, which keeps the macro processor from running them
    shown = re.findall(r"%nrstr\((%let \w+ = \d;)\)", code)
    assert shown == (["%let _log_append_table = 1;", "%let _log_append_table = 0;"] * 2 if name == "Log-Parser-Code.sas" else [])
    assigned = re.findall(r"%let\s+(\w+)\s*=", re.sub(r"%nrstr\([^()]*\)", "", code))
    assert [variable for variable in assigned if variable in defaults] == []
    rewritten = sorted(set(re.findall(r"call symputx\('(\w+)'", code) + re.findall(r"into\s+:(\w+)", code)) & set(defaults))
    assert rewritten == (["_log_path", "_log_target_table_lib"] if name == "Log-Parser-Code.sas" else [])


@EVERY_PROGRAM
def test_a_default_is_text_a_macro_call_takes_as_one_argument(name):
    """A comma would split a default into two arguments, a parenthesis, a quote or a macro trigger
    would be read by the macro processor."""
    prefix, defaults = PROGRAMS[name]
    for variable, value in default_calls(program(name), prefix):
        assert re.fullmatch(r"_%s_[a-z_]+" % prefix, variable), variable
        if variable == "_log_append_table":
            # the one setting without a value: the call only makes sure that the macro variable exists
            assert value == ""
            continue
        assert re.fullmatch(r"[A-Za-z0-9_ :/.\-]+", value) and value == value.strip(), (variable, value)


@EVERY_PROGRAM
def test_the_comment_of_the_settings_holds_nothing_sas_reads_as_code(name):
    """A statement that starts with a star is read by the macro processor: a percent sign or an
    ampersand in it is a macro call, a quote opens a text. The comment says LET statement for that reason."""
    lines = program(name).split("\n")
    comments = [line for line in lines if "is taken from the lines below only where its macro variable is" in line]
    assert len(comments) == 1
    comment = comments[0]
    if comment.startswith("*"):
        assert comment.endswith(";") and not re.search(r"[%&'\";]", comment[:-1])
    else:
        assert comment.startswith("/*")
    assert "with a LET statement" in program(name)


@EVERY_PROGRAM
def test_every_setting_is_deleted_at_the_end_as_before(name):
    """The program deletes its settings with its other macro variables, so a second run in one SAS
    session starts from the defaults again - unless the program in front of it sets its values again."""
    _, defaults = PROGRAMS[name]
    deleted = set(" ".join(" ".join(part.split()) for part in re.findall(r"%symdel\s+([^;]*);", program(name))).replace("/ nowarn", "").split())
    assert set(defaults) <= deleted


# ---------------------------------------------------------------------------
# The two steps: every setting of the part they run comes from a control
# ---------------------------------------------------------------------------

def step(name):
    definition = json.loads((STEPS / name).read_text(encoding="utf-8"))
    ui = json.loads(definition["ui"])

    def controls(children):
        found = {}
        for child in children:
            found[child["id"]] = child
            found.update(controls(child.get("children", [])))
        return found

    return definition["templates"]["SAS"].replace("\r\n", "\n"), controls(ui["pages"]), ui["values"]


def test_the_parser_step_gets_every_setting_from_a_control():
    """SAS Studio sets one macro variable per control, and for an output table the variables of its
    library, its name and its engine. The step holds none of the default lines of the code file."""
    step_program, controls, values = step("LLM - Log Parser.step")
    _, defaults = PROGRAMS["Log-Parser-Code.sas"]

    assert "_log_default" not in step_program
    assert {control_id: controls[control_id]["type"] for control_id in ("_log_path", "_log_target_table", "_log_append_table",
                                                                        "_log_promote_table", "_log_save_table")} == {
        "_log_path": "path", "_log_target_table": "outputtable", "_log_append_table": "checkbox",
        "_log_promote_table": "checkbox", "_log_save_table": "checkbox"}
    from_controls = {"_log_path", "_log_append_table", "_log_promote_table", "_log_save_table"} | {
        "_log_target_table" + suffix for suffix in ("_engine", "_lib", "_name")}
    assert from_controls == set(defaults)
    for variable in defaults:
        # the setting of the append is read as text, so that a value nobody thought of is compared and not run
        read = "%superq(_log_append_table)" if variable == "_log_append_table" else f"&{variable}."
        assert read in step_program, variable
    assert "&_log_append_table." not in step_program
    # a control that has to be filled in, or a checkbox, which is 0 or 1 whatever the user does: the step never
    # runs without a value of _log_append_table, so it never stops at a table that holds rows
    assert controls["_log_path"]["required"] is True and controls["_log_target_table"]["required"] is True
    assert {values[control_id] for control_id in ("_log_append_table", "_log_promote_table", "_log_save_table")} == {False}


def test_the_pull_step_gets_every_setting_from_a_control():
    step_program, controls, values = step("LLM - Get All Prompts.step")
    _, defaults = PROGRAMS["Get-All-Prompts.sas"]
    head = step_program.split("* gap core - from here")[0]

    assert "%_gap_default(" not in step_program and "%macro _gap_default" not in step_program
    # the output table gives the library and the table of the experiments, in the first lines of the step
    assert controls["_gap_output"]["type"] == "outputtable" and controls["_gap_output"]["required"] is True
    assert "%let _gap_out_experiments = &_gap_output_name.;" in head and "%let _gap_out_caslib=%sysfunc(getlcaslib(&_gap_output_lib));" in head
    # every other setting is a field of the step, with the default of the code file as the value it comes with
    fields = {variable: default for variable, default in defaults.items() if variable not in ("_gap_out_caslib", "_gap_out_experiments")}
    for variable, default in fields.items():
        assert controls[variable]["type"] == "textfield" and values[variable] == default, variable
        assert re.search(rf"^%_gap_setting\({variable},", head, flags=re.MULTILINE), variable


# ---------------------------------------------------------------------------
# What the README hands a site
# ---------------------------------------------------------------------------

def test_the_readme_shows_a_site_program_with_settings_of_the_program():
    readme = (FOLDER / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    example = readme.split("## Settings that stay yours")[1].split("```sas\n")[1].split("```")[0]
    lines = [line for line in example.split("\n") if line]

    assert len(lines) == 3
    assert lines[0] == "%let _log_path = sasserver:/data/llm/llms.log;"
    assert lines[1] == ("filename shipped filesrvc folderPath='/SAS Agentic AI Accelerator/Logging and Monitoring' "
                        "filename='Log-Parser-Code.sas';")
    assert lines[2] == "%include shipped;"
    assert "_log_path" in PROGRAMS["Log-Parser-Code.sas"][1]
    # every program and every setting is named in the section
    section = readme.split("## Settings that stay yours")[1].split("\n## ")[0]
    for name, (_, defaults) in PROGRAMS.items():
        assert name in section, name
        for variable in defaults:
            assert f"`{variable}`" in section, variable


# ---------------------------------------------------------------------------
# A setting or a list of names handed to a macro function or a macro as one text
# ---------------------------------------------------------------------------

def test_the_settings_link_logs_hands_to_its_macro_are_one_argument_each():
    """Review-18 (open item 16): %_lnk_ensure_loaded was called with six settings as references with an
    ampersand, so a value with a comma - a name list pasted into the wrong setting - was two arguments and
    the call failed. The settings are handed over through %superq, which the macro processor takes as one
    argument whatever it holds."""
    from macro_harness import Macros, arguments

    program = (FOLDER / "Link-Logs-To-Models.sas").read_text(encoding="utf-8").replace("\r\n", "\n")
    calls = re.findall(r"^%_lnk_ensure_loaded\((.*)\);$", program, flags=re.MULTILINE)
    assert len(calls) == 3
    values = {name: "A,B" for name in ("_lnk_log_caslib", "_lnk_log_table", "_lnk_catalog_caslib", "_lnk_catalog_table",
                                       "_lnk_runs_caslib", "_lnk_runs_table")}
    macros = Macros(values)
    for call in calls:
        assert len(arguments(macros.resolve(call))) == 3, call
        assert "&" not in call
    # the way it was written: the comma of a value ends an argument
    assert len(arguments(macros.resolve("&_lnk_log_caslib., &_lnk_log_table., _lnk_log_exists"))) == 5


def test_the_column_names_get_all_prompts_looks_for_are_one_text():
    """The same for %sysfunc(indexw(...)) in Get-All-Prompts.sas: the column names of a run table are handed to it
    through %superq, so a name list with a comma is one argument."""
    from macro_harness import Macros, arguments

    program = (FOLDER / "Get-All-Prompts.sas").read_text(encoding="utf-8").replace("\r\n", "\n")
    looks = re.findall(r"%sysfunc\(indexw\((.*?), (RUN_ID|UPDATED_AT|%upcase\(&_gap_col\.\))\)\)", program)
    assert len(looks) == 5 and {text for text, _ in looks} == {"%str( )%superq(_gap_lhave)", "%str( )%superq(_gap_have)"}
    macros = Macros({"_gap_have": "A,B RUN_ID", "_gap_lhave": "A,B UPDATED_AT"})
    for text, word in looks:
        assert len(arguments(macros.resolve(f"{text}, RUN_ID"))) == 2, text
    # the way it was written - %str masks its own text, not the value a reference in it resolves to - a comma of a
    # name ends an argument
    assert len(arguments(macros.resolve(" &_gap_have., RUN_ID"))) == 3
