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
"""
import re
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[2] / "SAS-Code-LLM-Calls"
SCRIPT = FOLDER / "Create-LLM-Call.sas"


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
    assert "delete _slo_options _slo_final_macro_vars _slo_final_json_input _slo_final_http _slo_final;" in text
    readme = " ".join((FOLDER / "README.md").read_text(encoding="utf-8").split())
    assert "*_slo_fl*, setting this value to 1 will create a filename with the base LLM call code in it" in readme
    assert "You can either specify *temp*, which creates a temporary file" in readme
