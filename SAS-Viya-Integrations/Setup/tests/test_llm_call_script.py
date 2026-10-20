"""SAS-Code-LLM-Calls/Create-LLM-Call.sas: the file the script writes the code to.

The script comes with _slo_fl = 1 and _slo_fl_path = temp: the code is written to a temporary file.
It then cleared the fileref _slo_LLM, which deletes a temporary file, and wrote "NOTE: The script
is available at temp" - while the comment of the setting said that the fileref is called _slo_LLM
and how to run the code with it, and the README of the folder that the setting creates a filename
with the code in it. The fileref is no longer cleared, so the comment, the README and the NOTE say
what the script does. That comment held its example in the words of the macro language, which a
scanner of the text flags (gate-19); it says the same in plain words now.

How the script reads the options of an options file is held in test_custom_steps.py, with the two
custom steps that read them the same way. The SAS code can not run here: the test reads its text.

The script is the third copy of the program of the custom steps LLM - Create LLM Call and LLM - Get
Options. The steps were given three lines on 2026-10-13 - a run without a model name and an options
file asks SAS Viya for nothing and ends with one ERROR line - and the script was not (review-20b): it
comes with both settings empty, sent its request all the same, and that request is for the address
<host>/content. The script has the three lines now, and the part of the program the three copies
share is held equal here, so that the next change to one of them is a change to all three.
"""
import re
from pathlib import Path

import pytest

import test_custom_steps as steps

FOLDER = Path(__file__).resolve().parents[2] / "SAS-Code-LLM-Calls"
SCRIPT = FOLDER / "Create-LLM-Call.sas"
NAME, URI = "%let _slo_LLM_name = ;", "%let _slo_optionsFileURI = ;"
# What a run of the script without a model says in the place of the reason
NO_MODEL = ("no model with an options file was chosen - the script needs a model name in _slo_LLM_name and the URI of its options "
            "file in _slo_optionsFileURI")


def script():
    return SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_script_comes_with_a_temporary_file():
    text = script()

    assert "%let _slo_fl = 1;" in text and "%let _slo_fl_path = temp;" in text
    assert text.index("%let _slo_fl = 1;") < text.index("%let _slo_fl_path = temp;") < text.index("proc http")


def test_the_fileref_stays_and_the_note_says_so():
    text = script()
    saved = text[text.index("* Save the result as a filename;"):text.index("* Clean up;")]

    # assigned, written to, and not cleared: clearing the fileref of a temporary file deletes the file
    assert "filename _slo_LLM &_slo_fl_path.;" in saved and "file _slo_LLM;" in saved
    assert not re.search(r"filename\s+_slo_LLM\s+clear", text, flags=re.IGNORECASE)
    assert re.findall(r"%put (NOTE: [^;]*);", saved) == [
        "NOTE: The script is available at &_slo_fl_path., and the fileref _slo_LLM names it until the SAS session ends"]
    # nothing behind the block takes the fileref away: the clean-up clears the fileref of the options file only
    behind = text[text.index("* Clean up;"):]
    assert re.findall(r"filename\s+(\w+)\s+clear", behind) == ["_slo_rsp"]


def test_the_comments_and_the_readme_say_what_the_script_does():
    text = script()
    comments = [comment.strip() for comment in re.findall(r"^\s*\*[^;]*;", text, flags=re.MULTILINE)]
    setting = [comment for comment in comments if comment.startswith("* Save the code to a file?")]

    assert setting == ["* Save the code to a file? 1 = Yes, 0 = No. The fileref _slo_LLM then names the file, and an INCLUDE statement "
                       "with that fileref runs the code;"]
    assert text.index(setting[0]) < text.index("%let _slo_fl = 1;")
    # no comment of the script holds a word of the macro language
    assert [comment for comment in comments if "%" in comment or "&" in comment] == []
    # the header says what the script leaves: the code on the Results tab and in the fileref, and no table
    header = text[:text.index("*/")]
    assert "written to the\n      fileref _slo_LLM" in header and "No table is left." in header and "_slo_models" not in text
    assert "delete _slo_options _slo_final_macro_vars _slo_final_key _slo_final_json_input _slo_final_http _slo_final;" in text
    readme = " ".join((FOLDER / "README.md").read_text(encoding="utf-8").split())
    assert "*_slo_fl*, setting this value to 1 will create a filename with the base LLM call code in it" in readme
    assert "You can either specify *temp*, which creates a temporary file" in readme
    # the header says what the code does with the option API_KEY
    assert ("The option API_KEY of a model names the entry of the credential domain agentic-ai-keys that holds the provider key, "
            "not the key. The code reads the key of that entry when it runs, under the identity of its SAS session, and sends it "
            "in the place of the name; the log says so in a NOTE.") in " ".join(header.split())


def test_the_readme_says_that_a_file_of_sas_content_is_written():
    """live-27 (2026-10-20). The README said of _slo_fl_path that paths in the SAS Content are currently not supported -
    while LLM - Create LLM Call 1.5 wrote its code to SAS Content and read it from there, and the script takes a FILESRVC
    file the same way: it puts the value behind FILENAME _slo_LLM. Measured the same day with the script and the value
    filesrvc folderPath='/Users/<user>/My Folder/<folder>' filename='<name>.sas': the NOTE named that file, the fileref
    _slo_LLM was assigned, and the code included from it called gpt_5_mini with HTTP 200."""
    readme = " ".join((FOLDER / "README.md").read_text(encoding="utf-8").split())
    text = script()

    assert "not supported" not in readme
    assert ("A file in the *SAS Content* is set with the access method FILESRVC, the folder and the file name each between quotes: "
            "*filesrvc folderPath='/Users/\\<you\\>/My Folder' filename='\\<llmName\\>Call.sas'*. The script writes the code to that "
            "file, and the fileref *_slo_LLM* names it. The custom steps take a path on the SAS Server and a path in the SAS Content "
            "alike, from their path control.") in readme
    # what the script does with the value: the statement that assigns the fileref, then the file is written through it
    assert "filename _slo_LLM &_slo_fl_path.;" in text and "file _slo_LLM;" in text


def test_the_readme_says_where_the_key_comes_from():
    readme = " ".join((FOLDER / "README.md").read_text(encoding="utf-8").split())

    assert "### The provider key" in (FOLDER / "README.md").read_text(encoding="utf-8")
    for said in ("The code keeps that name in `%let API_KEY=<entry name>;` and reads the key of the entry when it runs",
                 "`%let API_KEY_DOMAIN=agentic-ai-keys;`", "The key is not written to the log, and the macro variable *options*",
                 "`NOTE: The key of the entry OpenAI was read from the credential domain agentic-ai-keys.`",
                 "`WARNING: No key was read for the entry ...`", "the provider answers 401 - the container answers 422"):
        assert said in readme, said


# ---------------------------------------------------------------------------
# A run without a model asks for nothing (the three lines of the two steps)
# ---------------------------------------------------------------------------

def followed(text, status="404"):
    """Follows the open code of the script from its settings to the reason of a run without code: (the SAS code it
    generates, its ERROR line, the address it asked for or None, the macro variables at its end). A PROC HTTP step
    is stood in for and leaves `status`; the statement that reads the host is left out and the host is handed in,
    as in test_custom_steps."""
    settings = text[text.index("* Provide the LLM name;"):text.index("* Get the Viya Host URL;")]
    asked = text[text.index("* The code is written only where"):text.index("* One row per option.")]
    said = text[text.index("%if &_slo_ok. NE 1 %then %do;"):text.index("%symdel _slo_LLM_name")]
    step = {"http": None, "asked": None}

    def on_text(line, macros):
        if line == "proc http":
            step["http"] = []
        elif step["http"] is not None:
            step["http"].append(line)
            if line == "run; quit;":
                step["asked"] = next(part[len('url="'):-1] for part in step["http"] if part.startswith('url="'))
                macros.symbols["sys_prochttp_status_code"] = status
                step["http"] = None

    macros = steps._OpenCode({"_slo_viyaHost": "https://viya.example.com", "SYSCC": "0"},
                             {"libref": lambda arguments: 70006, "max": lambda arguments: max(int(argument) for argument in arguments),
                              "getoption": lambda arguments: "https://viya.example.com"})
    generated, log = macros.run("%macro script;\n" + settings + asked + said + "%mend script;\n", "script", on_text)
    return generated, log, step["asked"], macros


def with_settings(name="", uri=""):
    text = script()
    assert text.count(NAME) == 1 and text.count(URI) == 1
    return text.replace(NAME, f"%let _slo_LLM_name = {name};").replace(URI, f"%let _slo_optionsFileURI = {uri};")


def without_the_three_lines(text):
    """The script as it was before: the request stands in open code, and no line says that no model was chosen."""
    for old, new in (("%if &_slo_chosen. EQ 1 %then %do;\nproc http\n", "proc http\n"),
                     ("    headers 'Accept' = 'application/json';\nrun; quit;\n%end;\n", "    headers 'Accept' = 'application/json';\nrun; quit;\n"),
                     (f"* A run without a model has asked for nothing;\n%if &_slo_chosen. NE 1 %then %do;\n    %let _slo_why = {NO_MODEL};\n%end;\n", "")):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def test_the_script_comes_with_both_settings_empty():
    text = script()

    assert text.count(NAME) == 1 and text.count(URI) == 1 and text.index(NAME) < text.index(URI) < text.index("%let _slo_chosen = ")


@pytest.mark.parametrize("name, uri", [("", ""), ("gpt_5_mini", ""), ("", steps.OPTIONS_URI)],
                         ids=["as it comes", "a name and no URI", "a URI and no name"])
def test_the_script_without_a_model_or_an_options_file_asks_for_nothing(name, uri):
    """review-20b. Run as it comes, the script asked for `<host>/content` and ended with `did not answer the request
    for the options file  with HTTP 200` - an empty file name, the line the pass of 2026-10-13 objected to in the two
    steps. With the three lines it sends no request and its one ERROR line says what it needs. A URI without a name
    is such a run as well, as manual entry without a name is in the steps: the code the script writes calls
    <endpoint>/<name>/<name>."""
    generated, log, asked, _ = followed(with_settings(name, uri))

    assert asked is None and "proc http" not in generated
    assert log == [f"ERROR: Create-LLM-Call.sas: no code was written, because {NO_MODEL}."]
    # the script of before, for the same settings: the request is sent, also for no file at all
    generated, log, asked, _ = followed(without_the_three_lines(with_settings(name, uri)))
    assert asked == "https://viya.example.com" + uri + "/content" and "proc http" in generated
    assert log == [f"ERROR: Create-LLM-Call.sas: no code was written, because SAS Viya did not answer the request for the options file {uri} "
                   "with HTTP 200 (status: 404)."]


def test_the_script_with_a_model_and_an_options_file_asks_for_that_file():
    generated, log, asked, macros = followed(with_settings("gpt_5_mini", steps.OPTIONS_URI), status="200")

    assert asked == "https://viya.example.com" + steps.OPTIONS_URI + "/content" and macros["_slo_chosen"] == "1"
    # the request was answered with 200, so the reason is the one of a file that is no JSON - until the file is read
    assert log == [f"ERROR: Create-LLM-Call.sas: no code was written, because the options file {steps.OPTIONS_URI} is no JSON that SAS can read."]
    # and a request that is refused is named with its file and its status, as before
    assert followed(with_settings("gpt_5_mini", steps.OPTIONS_URI), status="403")[1] == [
        f"ERROR: Create-LLM-Call.sas: no code was written, because SAS Viya did not answer the request for the options file {steps.OPTIONS_URI} "
        "with HTTP 200 (status: 403)."]


def shared(text):
    """The part of the program the two steps and the script have in common, from the line that says whether there
    is a model to the step that puts the code together, with the one sentence that names where a model is chosen
    taken out. What stands in front of it reads the model from the controls of a step or from the settings of the
    script; what stands behind it saves and prints the code, which each of the three does its own way."""
    start = text.index("* 1 where there is a model name and an options file to ask for;\n")
    end = text.index("%end;\n", text.index("* Combine the datasets;\n")) + len("%end;\n")
    part = text[start:end]
    sentences = re.findall(r"^    %let _slo_why = no model with an options file was chosen - ([^;]*);$", part, flags=re.MULTILINE)
    assert len(sentences) == 1
    return part.replace(sentences[0], "<where a model is chosen>"), sentences[0]


def test_the_three_copies_of_the_program_share_one_text():
    """LLM - Create LLM Call, LLM - Get Options and this script: the line that says whether there is a model, the
    request that is sent only then, the reasons of a run without code, the reading of the options and the steps that
    write the code are one text in the three. They differ in the sentence that says where a model is chosen - the
    controls of a step, the settings of the script - and in nothing else of that part."""
    programs = {name: read() for name, read in steps.PROGRAMS.items()}
    parts = {name: shared(text) for name, text in programs.items()}

    assert list(programs) == ["LLM - Create LLM Call", "LLM - Get Options", "Create-LLM-Call.sas"]
    assert parts["LLM - Create LLM Call"][0] == parts["LLM - Get Options"][0] == parts["Create-LLM-Call.sas"][0]
    assert len(parts["Create-LLM-Call.sas"][0]) > 5000 and parts["Create-LLM-Call.sas"][0].count("\n") > 150
    assert parts["LLM - Create LLM Call"][1] == parts["LLM - Get Options"][1] == steps.NO_MODEL.split(" - ", 1)[1]
    assert parts["Create-LLM-Call.sas"][1] == NO_MODEL.split(" - ", 1)[1]
    first = programs["LLM - Create LLM Call"]
    for name, text in programs.items():
        # the three lines, once each, and the macro variable is deleted at the end
        assert text.count("%let _slo_chosen = %eval(%length(%superq(_slo_LLM_name)) GT 0 and %length(%superq(_slo_optionsFileURI)) GT 0);") == 1
        assert text.count("%if &_slo_chosen. EQ 1 %then %do;\nproc http\n") == 1 and text.count("\nproc http\n") == 1
        assert text.count("* A run without a model has asked for nothing;\n%if &_slo_chosen. NE 1 %then %do;\n") == 1
        assert text.count("&_slo_chosen.") == 2 and "_slo_chosen " in text.split("\n")[-1], name
        # what follows the code ends the same way in the three: the clean-up, and the one ERROR line with the name of the program
        tail = text[text.index("* Clean up;\n"):text.index("%symdel _slo_LLM_name")]
        assert tail.replace(name, "<name>") == first[first.index("* Clean up;\n"):first.index("%symdel _slo_LLM_name")].replace(
            "LLM - Create LLM Call", "<name>"), name


def test_the_header_and_the_readme_say_what_the_script_needs():
    header = " ".join(script()[:script().index("*/")].split())
    readme = " ".join((FOLDER / "README.md").read_text(encoding="utf-8").split())

    assert ("This script requires you to set the macro variables _slo_LLM_name and _slo_optionsFileURI, which can be retrieved using the "
            "script Get-List-of-available-LLMs.sas. Without both it asks SAS Viya for nothing and ends with one ERROR line.") in header
    assert ("The script comes with *_slo_LLM_name* and *_slo_optionsFileURI* empty. Without a value for both it asks SAS Viya for nothing "
            "and writes no code: it ends with one ERROR line that says that no model with an options file was chosen.") in readme
