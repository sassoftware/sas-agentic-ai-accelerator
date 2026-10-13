"""The custom step LLM - Create LLM Call, which the package ships in its folder Custom Steps.

The step comes with "Save the code to a filename?" ticked and an empty path, and the label of the path
says "if left empty it will be set to temp". Version 1.0 did the opposite for an empty path: it wrote
"ERROR: No path specified to save the script to" and ran `abort 42`. That is read from its program;
the step was not run for it.

Version 1.1 does what the label says: the code goes to a temporary file, and the fileref _slo_LLM
stays assigned so that the file can be included. For a path nothing changes: the statements that
assign the fileref and the statement that clears it are the ones of version 1.0.

Version 1.2 reads every option of the options file. Measured on 2026-10-12 with version 1.1 and the
options file of gpt_5_mini, whose option reasoning_effort lists the values it can take: PROC TRANSPOSE
wrote "ERROR: The ID value "col_values" occurs twice in the same BY group." four times, its table had
2 of the 3 options, the job ended with SYSCC 108, and the code file was written without that option
and with the NOTE of a good run. The options are now read with one PROC SQL step that takes default,
range and description of each option, and a run that could not read the default of every option
writes no code and no file and ends with an ERROR line. LLM - Get Options, an earlier copy of the
step, read the options the same way and is changed the same way (1.1), and so is the script
SAS-Code-LLM-Calls/Create-LLM-Call.sas.

The steps are one line of JSON and have no code file of their own. SAS can not run here: the tests read
the program, follow the DATA step that makes the two statements for the three kinds of path, and
follow the reading of the options on the table the JSON engine makes of each options.json of the
repository (ALLDATA: one line per value, with the names of its path in P1, P2, ... and V = 1, and one
line with V = 0 for an object or a list; the entries of a list have the path of the list). That table
is held against what was measured with version 1.1.
They sit in this folder because its tests run with the package checks, and Custom-Steps has none.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

STEPS = Path(__file__).resolve().parents[2] / "Custom-Steps"
STEP = STEPS / "LLM - Create LLM Call.step"
GET_OPTIONS = STEPS / "LLM - Get Options.step"
SCRIPT = Path(__file__).resolve().parents[2] / "SAS-Code-LLM-Calls" / "Create-LLM-Call.sas"
REPOSITORY = Path(__file__).resolve().parents[3]

# The controls of the step, in their order: an id that changes loses the value a saved flow holds for it
CONTROLS = [("pageDefinition", "page"), ("_slo_info_text", "text"), ("_slo_input_selector", "radiogroup"), ("_slo_mod_input", "inputtable"),
            ("_slo_col", "columnselector"), ("_slo_mod_nm_tab", "list"), ("_slo_mod_name_manual", "textfield"),
            ("_slo_fl_uri_manual", "textfield"), ("_slo_LLMSCR", "textfield"), ("_slo_fl", "checkbox"), ("_slo_fl_path", "path"),
            ("_slo_result", "checkbox"), ("pageAbout", "page"), ("textAbout", "text"), ("sectionPrereqs", "section"), ("textPrereqs", "text"),
            ("sectionDocumentation", "section"), ("textDocumentation", "text"), ("sectionChangelog", "section"), ("textChangelog", "text")]
# The steps that write the code from the options, and the block that saves it, as versions 1.0 and 1.1 have them
GENERATION_OF_1_1 = "bcdd949ca58f2f809b8dff8b40256d6134eae6ed6dc043cbca47c087c7a25013"
SAVING_OF_1_1 = "205ec3d3d3e60ed8b7ff1b93fbcccda867d79cfbedf27036184d6bef7f16a65d"
SAVING_OF_GET_OPTIONS_1_0 = "df65d2a73fbbeb80531c81346f535d901ad26b397c719c45be401270a1e3d147"
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
    # the one ERROR line of the program is the one of a run that could not read an option
    assert "No path specified" not in text and "ERROR" not in saving().upper().replace("NOWARN", "")
    assert text.upper().count("ERROR") == 1
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
        "%if &_slo_ok. EQ 1 and &_slo_fl. EQ 1 %then %do;","    data _null_;\n        if \"&_slo_fl_path.\" EQ '' then do;", "    run;\n\n\t&_slo_fl_path.;\n",
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


def test_the_macro_variables_of_the_step_are_deleted_at_its_end():
    text = program()

    assert text.endswith("%symdel _slo_LLM_name _slo_optionsFileURI _slo_viyaHost;\n%symdel _slo_fl_end _slo_ok _slo_why / nowarn;")
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
        "* Version: 1.2 (13OCT2026)",
        "      - An option of the options file that lists the values it can take (values, as reasoning_effort of gpt_5_mini has it) is read. "
        "Version 1.1 stopped in PROC TRANSPOSE with four ERROR lines at such an option and wrote the code without it. A run that can not "
        "read the default of every option writes no code and no file and ends with an ERROR line",
        "* Version: 1.1 (12OCT2026)",
        "      - Without a path the code is saved to a temporary file that the fileref _slo_LLM names, as the label of the path says. "
        "The program of version 1.0 held an ERROR line and an ABORT statement for an empty path, which is the value the step comes with",
        "* Version: 1.0 (07AUG2024)",
        "      - Initial version"]
    # no text of the step says what has or has not run
    texts = " ".join(child.get("text", "") for child in [control(control_id) for control_id, kind in CONTROLS if kind == "text"])
    assert not re.search(r"not (been )?run|untested|not measured", texts, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# The options of the options file (version 1.2, and LLM - Get Options 1.1)
# ---------------------------------------------------------------------------

def all_data(document):
    """The table ALLDATA the JSON engine makes of a JSON text: (P, P1, P2, ..., V, Value) per line. A
    member that holds an object or a list is a line with V = 0; a value is a line with V = 1 and its
    text; the entries of a list are lines with the path of the list."""
    rows = []

    def walk(value, path):
        if isinstance(value, dict):
            if path:
                rows.append({"P": len(path), "path": list(path), "V": 0, "Value": None})
            for key, member in value.items():
                walk(member, path + [key])
        elif isinstance(value, list):
            rows.append({"P": len(path), "path": list(path), "V": 0, "Value": None})
            for entry in value:
                walk(entry, path)
        else:
            text = value if isinstance(value, str) else json.dumps(value)
            rows.append({"P": len(path), "path": list(path), "V": 1, "Value": text})

    walk(document, [])
    for row in rows:
        for level in range(1, 4):
            row[f"P{level}"] = row["path"][level - 1] if len(row["path"]) >= level else None
    return rows


def transposed_as_in_1_1(rows):
    """What versions 1.0 and 1.1 did: the lines with V = 1, transposed by P1 with P2 as the id. Returns
    (the options with their columns, the ERROR lines PROC TRANSPOSE writes)."""
    groups, errors = defaultdict(list), []
    for row in rows:
        if row["V"] == 1:
            groups[row["P1"]].append(row)
    options = {}
    for option, members in sorted(groups.items()):
        counts = Counter(row["P2"] for row in members)
        twice = [key for key, count in counts.items() if count > 1]
        if twice:
            errors.extend(f'ERROR: The ID value "col_{key}" occurs twice in the same BY group.' for key in twice for _ in range(counts[key] - 1))
            continue                     # the BY group is left out
        options[option] = {row["P2"]: row["Value"] for row in members}
    return options, errors


def read_options(text, rows):
    """Follows the PROC SQL step of a program that reads the options: every condition of a CASE
    expression and the WHERE clause, on the lines of ALLDATA. Returns ({option: {column: value}}, the columns)."""
    start = text.index("create table work._slo_options as")
    step = text[start:text.index("quit;", start)]
    columns = re.findall(r"max\(case when P EQ 2 and V EQ 1 and P2 EQ '(\w+)' then Value end\) as (\w+)", step)
    assert "select P1 as option_name," in step and "where P GE 1" in step and "group by P1" in step and "order by P1" in step
    options = {}
    for row in rows:
        if row["P"] >= 1:
            entry = options.setdefault(row["P1"], {name: None for _, name in columns})
            for key, name in columns:
                if row["P"] == 2 and row["V"] == 1 and row["P2"] == key:
                    # max() of one value is that value: a key with more than one is no key that is read
                    assert entry[name] is None, (row["P1"], key)
                    entry[name] = row["Value"]
    return dict(sorted(options.items())), [name for _, name in columns]


def unread(options):
    """What the DATA step behind it keeps: the options without a default, in their order."""
    return [option for option, values in options.items() if values["default"] is None]


OPTION_FILES = sorted((REPOSITORY / "LLM-Definitions").glob("*/options.json"))
PROGRAMS = {"LLM - Create LLM Call": lambda: program(),
            "LLM - Get Options": lambda: json.loads(GET_OPTIONS.read_bytes().decode("utf-8"))["templates"]["SAS"],
            "Create-LLM-Call.sas": lambda: SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")}


def test_the_model_of_alldata_gives_what_was_measured_for_gpt_5_mini():
    """The table of the test, held against the run of version 1.1 on 2026-10-12: 17 lines with V = 1,
    four ERROR lines of PROC TRANSPOSE, a table of 2 of the 3 options, the code without reasoning_effort."""
    rows = all_data(json.loads((REPOSITORY / "LLM-Definitions" / "gpt_5_mini" / "options.json").read_text(encoding="utf-8")))
    options, errors = transposed_as_in_1_1(rows)

    assert sum(row["V"] == 1 for row in rows) == 17
    assert errors == ['ERROR: The ID value "col_values" occurs twice in the same BY group.'] * 4
    assert sorted(options) == ["API_KEY", "max_completion_tokens"]


def test_the_options_files_of_the_repository_have_the_shapes_the_programs_read():
    """Every key an option of an LLM definition holds: default, range and description, and label, type and
    values of some. values is the one that is a list - three definitions have it."""
    keys, lists, options = Counter(), set(), 0
    for path in OPTION_FILES:
        for option, spec in json.loads(path.read_text(encoding="utf-8")).items():
            assert isinstance(spec, dict), (path, option)
            options += 1
            keys.update(list(spec))
            lists.update(key for key, value in spec.items() if isinstance(value, (list, dict)))
    assert set(keys) == {"default", "range", "description", "label", "type", "values"}
    assert lists == {"values"} and keys["default"] == keys["range"] == keys["description"] == options
    assert sum('"values"' in path.read_text(encoding="utf-8") for path in OPTION_FILES) == 3


@pytest.mark.parametrize("name", list(PROGRAMS))
@pytest.mark.parametrize("path", OPTION_FILES, ids=[path.parent.name for path in OPTION_FILES])
def test_every_option_of_every_llm_definition_is_read_with_its_default(name, path):
    """Each option once, with the default, range and description of the file - the option that lists its
    values as well. For a file without a list the table is the one version 1.1 made, so the code is the same."""
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = all_data(document)
    options, columns = read_options(PROGRAMS[name](), rows)

    assert columns == ["default", "range", "description"]
    assert list(options) == sorted(document) and unread(options) == []
    for option, spec in document.items():
        expected = {key: (spec[key] if isinstance(spec[key], str) else json.dumps(spec[key])) for key in columns}
        assert options[option] == expected, (option, options[option])
    old, errors = transposed_as_in_1_1(rows)
    if not errors:
        assert {option: {key: values.get(key) for key in columns} for option, values in old.items()} == options


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_an_option_without_a_default_is_named_and_no_code_is_written(name):
    """A file the programs can not read completely: an option without default, an option that is a value."""
    text = PROGRAMS[name]()
    document = {"temperature": {"range": "0 - 2", "description": "no default"}, "plain": 7,
                "top_p": {"default": 1, "range": "0 - 1", "description": "d"}}
    options, _ = read_options(text, all_data(document))

    assert unread(options) == ["plain", "temperature"]
    # the DATA step behind the read sets _slo_ok only where no option lacks its default, and names the others in _slo_why
    start = text.index("* Every option needs its default")
    check = text[start:text.index("%end;", start)]
    assert "if missing(default) then _slo_unread = catx(' ', _slo_unread, option_name);" in check
    assert "if missing(_slo_unread) then call symputx('_slo_ok', 1);" in check
    assert "else call symputx('_slo_why', catx(' ', 'no default could be read for the option(s)', _slo_unread," in check


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_a_run_that_could_not_read_every_option_writes_no_code_and_ends_with_an_error_line(name):
    text = PROGRAMS[name]()

    assert not re.search(r"^\s*proc transpose", text, flags=re.IGNORECASE | re.MULTILINE) and "_slo_transposed" not in text
    # _slo_ok starts at 0, and only the DATA step that found a default for every option sets it to 1
    assert text.count("%let _slo_ok = 0;") == 1 and text.count("call symputx('_slo_ok', 1);") == 1
    assert text.index("%let _slo_ok = 0;") < text.index("proc http") < text.index("call symputx('_slo_ok', 1);")
    # the steps that make the code, the file and the print stand behind _slo_ok
    generate = text.index("%if &_slo_ok. EQ 1 %then %do;")
    assert generate < text.index("* Create code that generates the option inputs;") < text.index("* Combine the datasets;")
    assert "%if &_slo_ok. EQ 1 and &_slo_fl. EQ 1 %then %do;" in text and "%if &_slo_ok. EQ 1 and &_slo_result. EQ 1 %then %do;" in text
    assert not re.search(r"%if &_slo_fl\. EQ 1", text) and not re.search(r"%if &_slo_result\. EQ 1", text)
    # one ERROR line, for that run, and the condition code of a failed job
    assert re.findall(r"%put (ERROR: [^;]*);", text) == [f"ERROR: {name}: no code was written, because %superq(_slo_why)."]
    failure = text[text.index("%if &_slo_ok. NE 1 %then %do;"):]
    assert failure.index("%put ERROR:") < failure.index("%let SYSCC = %sysfunc(max(&SYSCC., 1012));") < failure.index("%end;")
    assert text.index("* Clean up;") < text.index("%if &_slo_ok. NE 1 %then %do;")
    # a request that was not answered with 200 is no file that is read
    assert '%if "&SYS_PROCHTTP_STATUS_CODE." EQ "200" %then %do;' in text and "%let SYS_PROCHTTP_STATUS_CODE = ;" in text
    # open code takes no condition inside a condition
    depth = 0
    for statement in re.findall(r"%if\b|%do\b|%end\b", text):
        if statement == "%if":
            assert depth == 0, name
        depth += {"%do": 1, "%end": -1}.get(statement, 0)
    assert depth == 0
    # every statement comment from the read on is free of what the macro processor takes as code
    for comment in re.findall(r"^\s*\*[^;]*;", text[text.index("%let _slo_ok = 0;") - 200:], flags=re.MULTILINE):
        assert not set(comment) & set("%&'\""), comment


def test_the_code_the_step_writes_is_the_code_of_version_1_1():
    """The steps that make the code from the options and the block that saves it are the ones of 1.1 (and of
    LLM - Get Options 1.0): for an options file version 1.1 read, the code is the same."""
    def generation(text):
        return text[text.index("* Create code that generates the option inputs;\n"):text.index("run;\n", text.index("* Combine the datasets;\n")) + 4]

    def saved(text):
        start = text.index("\n", text.index("\n", text.index("* Save the result as a filename;\n")) + 1) + 1
        return text[start:text.index("* Print the result for the user;")]

    text, get = program(), PROGRAMS["LLM - Get Options"]()
    assert hashlib.sha256(generation(text).encode("utf-8")).hexdigest() == GENERATION_OF_1_1 and generation(get) == generation(text)
    assert hashlib.sha256(saved(text).encode("utf-8")).hexdigest() == SAVING_OF_1_1
    assert hashlib.sha256(saved(get).encode("utf-8")).hexdigest() == SAVING_OF_GET_OPTIONS_1_0


def test_the_two_steps_say_what_they_read():
    get_texts = {}

    def collect(children):
        for child in children:
            if child.get("type") == "text":
                get_texts[child["id"]] = child["text"]
            collect(child.get("children", []))
    collect(json.loads(json.loads(GET_OPTIONS.read_bytes().decode("utf-8"))["ui"])["pages"])
    sentence = ("Every option of the options file is read with its default, also an option that lists the values it can take. A run that "
                "can not read the default of every option writes no code and no file: it ends with an ERROR line that says why.")
    assert sentence in control("textAbout")["text"] and sentence in get_texts["textAbout"]
    assert control("textChangelog")["text"].startswith("* Version: 1.2 (13OCT2026)\n")
    assert get_texts["textChangelog"].startswith("* Version: 1.1 (13OCT2026)\n")
    assert get_texts["textChangelog"].endswith("* Version: 1.0 (04AUG2024)\n      - Initial version")
    raw = GET_OPTIONS.read_bytes()
    assert raw.count(b"\n") == 0 and raw.count(b"\r") == 0 and not raw.startswith(b"\xef\xbb\xbf")
    assert json.dumps(json.loads(raw.decode("utf-8")), separators=(",", ":")).encode("utf-8") == raw
