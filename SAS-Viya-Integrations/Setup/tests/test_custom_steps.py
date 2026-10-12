"""The custom step LLM - Create LLM Call, which the package ships in its folder Custom Steps.

The step comes with "Save the code to a filename?" ticked and an empty path, and the label of the path
says "if left empty it will be set to temp". Version 1.0 did the opposite for an empty path: it wrote
"ERROR: No path specified to save the script to" and ran `abort 42`. That is read from its program;
the step was not run for it.

Version 1.1 does what the label says: the code goes to a temporary file, and the fileref _slo_LLM
stays assigned so that the file can be included. For a path nothing changes: the statements that
assign the fileref and the statement that clears it are the ones of version 1.0.

The step is one line of JSON and has no code file of its own. SAS can not run here: the tests read
the program, and follow the DATA step that makes the two statements for the three kinds of path.
They sit in this folder because its tests run with the package checks, and Custom-Steps has none.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

STEPS = Path(__file__).resolve().parents[2] / "Custom-Steps"
STEP = STEPS / "LLM - Create LLM Call.step"

# The controls of the step, in their order: an id that changes loses the value a saved flow holds for it
CONTROLS = [("pageDefinition", "page"), ("_slo_info_text", "text"), ("_slo_input_selector", "radiogroup"), ("_slo_mod_input", "inputtable"),
            ("_slo_col", "columnselector"), ("_slo_mod_nm_tab", "list"), ("_slo_mod_name_manual", "textfield"),
            ("_slo_fl_uri_manual", "textfield"), ("_slo_LLMSCR", "textfield"), ("_slo_fl", "checkbox"), ("_slo_fl_path", "path"),
            ("_slo_result", "checkbox"), ("pageAbout", "page"), ("textAbout", "text"), ("sectionPrereqs", "section"), ("textPrereqs", "text"),
            ("sectionDocumentation", "section"), ("textDocumentation", "text"), ("sectionChangelog", "section"), ("textChangelog", "text")]
# The program of version 1.0 in front of the block that saves the file, and behind it: both are as they were
HEAD_OF_1_0 = "272f1e2b97ca15925888bc037969eea8ba0c1cae85b512ee0536ae82ca7fd058"
TAIL_OF_1_0 = "16638aaed760c791b0d8ddb2416dea06203c32ea08c43e755fe912dae7d8cc15"
# What version 1.0 did for a path: the lines that make the statement which assigns the fileref
FOR_A_PATH = """        else do;
            justFileName = scan("&_slo_fl_path.", -1, '/', 'MO');
            folderPath1 = scan("&_slo_fl_path.", 2, ':', 'MO');
            folderPath2 = substr(folderPath1, 1, length(folderPath1) - length(justFileName) - 1);
            mode = scan("&_slo_fl_path.", 1, ':', 'MO');

            * Handle the different required filename for SAS Content and SAS Server;
            if mode = 'sascontent' then do;
                call symput('_slo_fl_path', "filename _slo_LLM filesrvc folderPath='" || strip(folderPath2) || "' filename='" || strip(justFileName) || "'");
            end;
            else do;
                call symput('_slo_fl_path', "filename _slo_LLM '" || strip(folderPath1) || "'");
            end;
"""
NOTE = "%put NOTE: No path was chosen, so the code was written to a temporary file. The fileref _slo_LLM names it until the SAS session ends"


def step():
    return json.loads(STEP.read_bytes().decode("utf-8"))


def ui():
    return json.loads(step()["ui"])


def program():
    return step()["templates"]["SAS"]


def controls(children):
    found = []
    for child in children:
        found.append((child["id"], child["type"]))
        found.extend(controls(child.get("children", [])))
    return found


def control(control_id):
    def look(children):
        for child in children:
            if child["id"] == control_id:
                return child
            found = look(child.get("children", []))
            if found:
                return found
        return None
    return look(ui()["pages"])


def saving():
    """The block of the program that saves the code to a file."""
    text = program()
    return text[text.index("* Save the result as a filename;"):text.index("* Print the result for the user;")]


def statements_for(path):
    """Follows the DATA step of the block for a value of the path control, the way its statements
    say: (the statement that assigns the fileref, the statement that stands behind the file)."""
    block = saving()
    for_an_empty_path = block[block.index("if \"&_slo_fl_path.\" EQ '' then do;"):block.index("        else do;")]
    for_a_path = block[block.index("        else do;"):block.index("    run;")]
    if path == "":
        made = dict(re.findall(r"call symput\('(\w+)', '([^']*)'\);", for_an_empty_path))
        return made["_slo_fl_path"], made["_slo_fl_end"]
    assert for_a_path.startswith(FOR_A_PATH)
    file_name = path.split("/")[-1]                                  # scan(path, -1, '/', 'MO')
    behind_the_colon = path.split(":")[1]                            # scan(path, 2, ':', 'MO')
    folder = behind_the_colon[:len(behind_the_colon) - len(file_name) - 1]
    mode = path.split(":")[0]                                        # scan(path, 1, ':', 'MO')
    assigned = (f"filename _slo_LLM filesrvc folderPath='{folder}' filename='{file_name}'" if mode == "sascontent"
                else f"filename _slo_LLM '{behind_the_colon}'")
    behind = dict(re.findall(r"call symput\('(\w+)', '([^']*)'\);", for_a_path[len(FOR_A_PATH):]))
    return assigned, behind["_slo_fl_end"]


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------

def test_the_step_is_one_line_of_json_with_the_controls_it_had():
    raw = STEP.read_bytes()

    assert raw.count(b"\n") == 0 and raw.count(b"\r") == 0 and not raw.startswith(b"\xef\xbb\xbf")
    assert json.dumps(step(), separators=(",", ":")).encode("utf-8") == raw
    assert controls(ui()["pages"]) == CONTROLS
    assert "\r" not in program() and set(step()["templates"]) == {"SAS"}
    assert step()["name"] == "LLM - Create LLM Call.step" and step()["type"] == "code"


def test_the_step_comes_with_a_ticked_box_and_an_empty_path():
    """The state the step is in when it is dropped into a flow: the values of 1.0, which 1.1 keeps."""
    assert ui()["values"] == {"_slo_input_selector": {"value": "table", "label": "Select name from a table"},
                              "_slo_LLMSCR": "%sysfunc(getoption(SERVICESBASEURL))/llm", "_slo_fl": True, "_slo_fl_path": "", "_slo_result": True}
    assert control("_slo_fl_path")["label"] == "Choose a path, where to store the filename, if left empty it will be set to temp:"
    assert control("_slo_fl_path")["required"] is False and control("_slo_fl_path")["visible"] == "$_slo_fl"


# ---------------------------------------------------------------------------
# An empty path
# ---------------------------------------------------------------------------

def test_an_empty_path_is_a_temporary_file_and_no_abort():
    text = program()

    assert not re.search(r"\babort\b|%abort|endsas", text, flags=re.IGNORECASE)
    assert "No path specified" not in text and "ERROR" not in text.upper().replace("NOWARN", "")
    assigned, behind = statements_for("")
    assert assigned == "filename _slo_LLM temp"
    # the fileref is not cleared, which would delete the file: a NOTE names the fileref in its place
    assert behind == NOTE and "clear" not in behind
    # the statement behind the file is a statement of the macro language that ends at the semicolon of its line
    assert behind.startswith("%put NOTE: ") and not set(behind[1:]) & set("%&;'\"") and behind.count("(") == behind.count(")")


@pytest.mark.parametrize("path, assigned", [
    ("sasserver:/tmp/llm/call.sas", "filename _slo_LLM '/tmp/llm/call.sas'"),
    ("sasserver:/call.sas", "filename _slo_LLM '/call.sas'"),
    ("sascontent:/Public/LLM Calls/call.sas", "filename _slo_LLM filesrvc folderPath='/Public/LLM Calls' filename='call.sas'"),
], ids=["a file of the SAS server", "a file at the root", "a file of SAS Content"])
def test_a_path_gives_the_statements_of_version_1_0(path, assigned):
    """For a path the step assigns the fileref, writes the file and clears the fileref, as it did."""
    assert statements_for(path) == (assigned, "filename _slo_LLM clear")


def test_the_two_statements_stand_around_the_step_that_writes_the_file():
    block = saving()
    positions = [block.index(marker) for marker in (
        "%if &_slo_fl. EQ 1 %then %do;", "    data _null_;\n        if \"&_slo_fl_path.\" EQ '' then do;", "    run;\n\n\t&_slo_fl_path.;\n",
        "    data _null_;\n        file _slo_LLM;\n        set work._slo_final;\n        put _slo_code;\n    run;\n", "\t&_slo_fl_end.;\n%end;\n")]

    assert positions == sorted(positions)
    assert block.count("&_slo_fl_end.") == 1 and block.count("call symput('_slo_fl_end',") == 2
    # each branch of the DATA step sets the two macro variables once
    assert block.count("call symput('_slo_fl_path',") == 3 and block.count("then do;") == 2 and block.count("else do;") == 2
    # no condition stands inside the condition of the block: open code takes none
    assert len(re.findall(r"%if\b", block)) == 1 and len(re.findall(r"%do\b", block)) == len(re.findall(r"%end\b", block)) == 1
    # a quoted text of more than 262 bytes makes SAS warn of a quotation mark that is not closed
    for literal in re.findall(r"'[^'\n]*'|\"[^\"\n]*\"", block):
        assert len(literal.encode("utf-8")) < 262, literal
    # the comment inside the block is read by the macro processor: it holds nothing that it takes as code
    comments = re.findall(r"^\s*\*[^;]*;", block, flags=re.MULTILINE)
    assert len(comments) == 3 and all(not set(comment) & set("%&'\"") for comment in comments)


def test_the_rest_of_the_program_is_the_program_of_version_1_0():
    text = program()
    head = text[:text.index("* Save the result as a filename;")]
    tail = text[text.index("* Print the result for the user;"):]

    assert hashlib.sha256(head.encode("utf-8")).hexdigest() == HEAD_OF_1_0
    # behind the block one statement was added: the macro variable of the statement behind the file is deleted
    added = "\n%symdel _slo_fl_end / nowarn;"
    assert tail.endswith("%symdel _slo_LLM_name _slo_optionsFileURI _slo_viyaHost;" + added)
    assert hashlib.sha256(tail[:-len(added)].encode("utf-8")).hexdigest() == TAIL_OF_1_0
    assert FOR_A_PATH in saving()


# ---------------------------------------------------------------------------
# What the step says of itself
# ---------------------------------------------------------------------------

def test_the_about_tab_and_the_changelog_say_what_the_step_does():
    about = control("textAbout")["text"]
    changelog = control("textChangelog")["text"]

    assert ("It is possible to save the resulting code to a file or printing it to the Results pane. Without a path the code is saved to a "
            "temporary file, which the fileref _slo_LLM names until the SAS session ends.") in about
    assert changelog.split("\n") == [
        "* Version: 1.1 (12OCT2026)",
        "      - Without a path the code is saved to a temporary file that the fileref _slo_LLM names, as the label of the path says. "
        "The program of version 1.0 held an ERROR line and an ABORT statement for an empty path, which is the value the step comes with",
        "* Version: 1.0 (07AUG2024)",
        "      - Initial version"]
    # no text of the step says what has or has not run
    texts = " ".join(child.get("text", "") for child in [control(control_id) for control_id, kind in CONTROLS if kind == "text"])
    assert not re.search(r"not (been )?run|untested|not measured", texts, flags=re.IGNORECASE)
