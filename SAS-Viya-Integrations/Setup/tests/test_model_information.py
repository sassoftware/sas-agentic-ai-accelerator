"""MM - Get Model Information 1.2 and SAS-Code-Model-Manager-Interaction/MM-Get-Models-Information.sas: a model is read
with the properties it has.

live-27, measured on 2026-10-20 on SAS Viya 2026.09 with version 1.1 of the step, for the LLMs of the accelerator: its
PROC TRANSPOSE named a fixed list of 37 properties of a model, and the JSON of an LLM lacks six of them - trainTable,
targetEvent, candidateChampion, publishTimeStamp, externalModelId and sasOptions. SAS wrote "ERROR: Variable TRAINTABLE not
found." and five more, went into syntax check mode (OBS=0), the job failed with SYSCC 3000, and the five tables behind the
step were written without a row, by a table selected from a table and by an ID entered by hand alike. The program of the
folder held the same list. Two more defects stood beside it: the DATA step of work._mgi_outputVariables was there twice, and
for a model that was never published the answer has no part publishedModels, so its SET statement named a table that is
not there.

Version 1.2 cuts the list to the properties the answer holds, in the order of the list - a DATA step asks the table of the
JSON engine for each name with VARNUM -, writes a table only for a part the answer has and says in a NOTE which part it did
not write, deletes the six tables of an earlier run first, and ends with one ERROR line and SYSCC 1012 for a run without a
model ID and for a request SAS Model Manager did not answer with HTTP 200. Measured on 2026-10-20 with version 1.2 and the
program: gpt_5_mini (published) gave six tables (31, 9, 3, 4, 2 and 1 rows), gemini_flash_15_8b (never published) five and
the NOTE for publishedModels, a model of Model Studio with 37 properties 37 rows, an unknown ID the ERROR line with status 404
and no table, and the step as it comes the ERROR line that no model ID was chosen. Each run that read a model ended with
SYSCC 0.

SAS can not run here. The macro of the program is followed with macro_harness (casl_harness.follow, which the tests of the
loader use too): PROC HTTP is stood in for and leaves the status a test names, and the DATA step that makes the list leaves
the names of the list a model has.
"""
import json
import re
import sys
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1]
INTEGRATIONS = SETUP.parent
sys.path.insert(0, str(INTEGRATIONS / "Logging-Monitoring" / "tests"))

import casl_harness  # noqa: E402

STEP = INTEGRATIONS / "Custom-Steps" / "MM - Get Model Information.step"
SCRIPT = INTEGRATIONS / "SAS-Code-Model-Manager-Interaction" / "MM-Get-Models-Information.sas"
# The VAR statement of version 1.1 (and of the program until 2026-10-20), in its order
LIST_OF_1_1 = ("creationTimeStamp createdBy modifiedTimeStamp modifiedBy id 'name'n 'role'n scoreCodeType algorithm 'function'n modeler "
               "modelType trainTable eventProbVar targetVariable targetEvent targetLevel tool toolVersion candidateChampion "
               "publishTimeStamp externalModelId indirectFolderId repositoryId projectId projectVersionId 'immutable'n folderRef "
               "'location'n modelVersionName projectName projectVersionName projectVersionNum modelLocks version nondeterministic "
               "sasOptions")
LACKING = ["trainTable", "targetEvent", "candidateChampion", "publishTimeStamp", "externalModelId", "sasOptions"]
PARTS = ["files", "inputVariables", "outputVariables", "modelVersions", "publishedModels"]
TABLES = ["_mgi_baseInformation"] + ["_mgi_" + part for part in PARTS]
MODEL = "921363c4-f79d-43b5-9b4b-b6886dab7160"


def step_program():
    return json.loads(STEP.read_bytes().decode("utf-8"))["templates"]["SAS"]


def script():
    return SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")


PROGRAMS = {"MM - Get Model Information": step_program, "MM-Get-Models-Information.sas": script}


def the_list(text):
    found = re.findall(r"^\s*_mgi_list = '([^']*)';$", text, flags=re.MULTILINE)
    assert len(found) == 1
    return found[0].split()


def follow(text, model_id=MODEL, status="200", columns=None, parts=PARTS):
    """Follows the macro of a program for a model ID, the status PROC HTTP leaves, the columns of the table ROOT the JSON
    engine makes of the answer and the parts the answer has. Returns (the lines of SAS code, the lines of the log, the
    macro variables at its end)."""
    columns = the_list(text) if columns is None else columns
    step = {"http": False}

    def on_line(line, macros):
        if line.startswith("proc http"):
            step["http"] = True
        elif step["http"] and line == "quit;":
            macros.symbols["sys_prochttp_status_code"] = status
            step["http"] = False

    def data_step(lines, macros):
        joined = "\n".join(lines)
        if "call symputx('SYS_PROCHTTP_STATUS_CODE', ' ', 'G');" in joined:
            macros.symbols["sys_prochttp_status_code"] = ""
        if "call symputx('_mgi_vars', _mgi_have, 'L');" in joined:
            # VARNUM finds a name of the list where the table has a column of that name, in any case
            have = {column.lower() for column in columns}
            macros.symbols["_mgi_vars"] = " ".join(name for name in the_list(text) if name.lower() in have)

    functions = {"max": lambda arguments: max(int(argument) for argument in arguments),
                 "exist": lambda arguments: int(arguments[0].split(".", 1)[1] in parts)}
    symbols = {"_mgi_model_id": model_id, "_mgi_viyaHost": "https://viya.example.com", "SYSCC": "0",
               "SYS_PROCHTTP_STATUS_CODE": "an answer of an earlier request"}
    generated, log, macros = casl_harness.follow(text, "_mgi_get_information", symbols, None, functions, None, data_step, on_line)
    return generated, log, macros


def written_tables(generated):
    return re.findall(r"^data work\.(\w+);$", "\n".join(generated), flags=re.MULTILINE) + \
        re.findall(r"out=work\.(\w+);$", "\n".join(generated), flags=re.MULTILINE)


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_the_list_is_the_list_of_version_1_1(name):
    """The properties, their order and their names are the ones version 1.1 transposed; only the name literals are plain
    names now, as VARNUM takes them."""
    assert the_list(PROGRAMS[name]()) == [word[1:-2] if word.endswith("'n") else word for word in LIST_OF_1_1.split()]
    assert set(LACKING) <= set(the_list(PROGRAMS[name]()))


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_an_llm_of_the_accelerator_is_read_with_the_properties_it_has(name):
    """The model of live-27: the answer of an LLM lacks the six properties - the VAR statement names the 31 others, and
    no line of the log is an ERROR."""
    text = PROGRAMS[name]()
    columns = [column for column in the_list(text) if column not in LACKING] + ["links", "ordinal_root"]
    generated, log, macros = follow(text, columns=columns)

    var = [line for line in generated if line.startswith("var ")]
    assert var == ["var " + " ".join(column for column in the_list(text) if column not in LACKING) + ";"]
    assert len(var[0].split()) - 1 == 31
    assert log == [] and macros["syscc"] == "0"
    assert written_tables(generated) == ["_mgi_" + part for part in PARTS] + ["_mgi_baseInformation"]


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_a_model_that_was_never_published_gives_a_note_in_place_of_its_table(name):
    text = PROGRAMS[name]()
    label = "MM - Get Model Information" if name.endswith("Information") else name
    generated, log, macros = follow(text, parts=PARTS[:-1])

    assert log == [f"NOTE: {label}: the model {MODEL} has no publishedModels in SAS Model Manager, so the table "
                   "work._mgi_publishedModels was not written."]
    assert "work._mgi_publishedModels" not in "\n".join(generated) and macros["syscc"] == "0"
    assert written_tables(generated) == ["_mgi_" + part for part in PARTS[:-1]] + ["_mgi_baseInformation"]


@pytest.mark.parametrize("name", list(PROGRAMS))
@pytest.mark.parametrize("status", ["404", "403", ""], ids=["404", "403", "no answer"])
def test_a_request_that_was_not_answered_with_200_writes_no_table_and_ends_with_an_error_line(name, status):
    text = PROGRAMS[name]()
    label = "MM - Get Model Information" if name.endswith("Information") else name
    generated, log, macros = follow(text, status=status)

    assert log == [f"ERROR: {label}: no table was written, because SAS Model Manager did not answer the request for the model "
                   f"{MODEL} with HTTP 200 (status: {status})."]
    assert macros["syscc"] == "1012" and written_tables(generated) == []
    assert not [line for line in generated if line.startswith("libname _mgi_rp json")]


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_a_run_without_a_model_id_asks_for_nothing(name):
    text = PROGRAMS[name]()
    generated, log, macros = follow(text, model_id="")

    where = ("with Select ID from a table the step needs a model table and a model ID selected from it, otherwise a model ID "
             "entered by hand" if name.endswith("Information") else "the program needs the ID of a model in _mgi_model_id")
    label = "MM - Get Model Information" if name.endswith("Information") else name
    assert log == [f"ERROR: {label}: no table was written, because no model ID was chosen - {where}."]
    assert macros["syscc"] == "1012" and generated == []


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_the_tables_of_an_earlier_run_are_deleted_first_and_each_is_written_once(name):
    text = PROGRAMS[name]()

    deleted = re.search(r"proc datasets library=work noList noWarn;\n    delete ([^;]*);\nrun; quit;\n", text).group(1).split()
    assert deleted == TABLES
    assert text.index("    delete " + " ".join(TABLES)) < text.index("%macro _mgi_get_information;")
    # one DATA step for all five parts: no table is named twice
    assert text.count("data work._mgi_&_mgi_part.;") == 1 and "data work._mgi_outputVariables;" not in text
    assert "proc transpose" in text and not re.search(r"^\s*var creationTimeStamp", text, flags=re.MULTILINE)
    assert text.count("%macro _mgi_get_information;") == text.count("%sysmacdelete _mgi_get_information;") == 1


def test_the_step_and_the_program_share_one_text():
    """From the deletion of the tables to the deletion of the macro the two are one text, but for the name each writes in
    front of its lines and the sentence that says where a model ID is chosen."""
    def shared(text, label, where):
        part = text[text.index("* The six tables are deleted first"):text.index("%sysmacdelete _mgi_get_information;")]
        return part.replace(label + ":", "<name>:").replace(where, "<where>")

    step = shared(step_program(), "MM - Get Model Information",
                  "with Select ID from a table the step needs a model table and a model ID selected from it, otherwise a model ID "
                  "entered by hand")
    program = shared(script(), "MM-Get-Models-Information.sas", "the program needs the ID of a model in _mgi_model_id")
    assert step == program and step.count("<name>:") == 4 and step.count("<where>") == 1


def test_the_step_asks_for_a_control_before_it_reads_it():
    """In the state the step comes with, SAS Studio writes no macro variable for a list that holds no value (measured for
    LLM - Create LLM Call on 2026-10-13); version 1.1 resolved them all the same."""
    text = step_program()

    assert "%if &_mgi_input_selector. EQ table and %symexist(_mgi_repid_table_1) %then %do;" in text
    assert "%if &_mgi_input_selector. NE table and %symexist(_mgi_repid_manual) %then %do;" in text
    assert text.index("%let _mgi_model_id = ;") < text.index("%symexist(_mgi_repid_table_1)")


def test_the_step_says_what_it_writes():
    raw = STEP.read_bytes()
    ui = json.loads(json.loads(raw.decode("utf-8"))["ui"])
    texts = {}

    def collect(children):
        for child in children:
            if child.get("type") == "text":
                texts[child["id"]] = child["text"]
            collect(child.get("children", []))
    collect(ui["pages"])

    assert texts["_mgi_info_text"].endswith(
        "- work._mgi_publishedModels\n\nA part the model does not have gives no table, and the log says so in a NOTE: a model that "
        "was never published has no published models. The tables of an earlier run are deleted first.")
    changelog = texts["textChangelog"].split("\n")
    assert changelog[0] == "* Version: 1.2 (20OCT2026)" and changelog[3] == "* Version: 1.1 (13OCT2026)"
    assert changelog[1].startswith("      - A model is read with the properties it has. Version 1.1 transposed a fixed list of properties")
    assert "(measured on 2026-10-20)" in changelog[1]
    assert changelog[2].endswith("The table work._mgi_outputVariables is written once")
    # the file stays one line of compact JSON
    assert raw.count(b"\n") == 0 and json.dumps(json.loads(raw.decode("utf-8")), separators=(",", ":")).encode("utf-8") == raw
    # the header of the program names a model, not a project
    header = script()[:script().index("*/")]
    assert "_mgi_model_id which is the ID of a SAS Model Manager\n      model." in header and "project ID" not in script()
