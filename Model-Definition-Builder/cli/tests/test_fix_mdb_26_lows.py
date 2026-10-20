# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""fix-mdb-26 lows: the adoption flow of `mdb import`, the provider key it
takes as no key, `mdb pull --import` refusing as `mdb import` does, what the
score code reads of a name and a value, and the refusals on one line.

- gate-26 (import): `mdb import <id>` printed "converge with: mdb import <id>
  --apply", which stopped at "already has a definition.yaml - nothing
  imported."; the hint after a render refusal asked for a rerun that stopped
  there too, and for an option of the vocabulary whose default breaks the
  rule it named a definition.yaml as the place of the repair.
- gate-26 / review-26 (API_KEY): an API_KEY whose default was 0, false, [],
  {}, "" or null, one without a default and one that was 0, "", [] or null
  was taken as no key: auth.mode none, and the regenerated options.json lost
  the API_KEY the score code read.
- gate-26 / review-26 (the rule): a name or a text value with a blank or a
  quote at its ends, a text the score code reads as true, false or a number,
  and a number the Builder sends as 1e-7 or 1e+21 were written, and the score
  code read them otherwise than options.json says.
- review-26 (pull): `mdb pull --import` printed a yellow line and ended with
  exit 0 where `mdb import` refused the folder.
- review-26 (wrap): the refusals were broken at 80 columns where the output
  goes to a pipe; V010 and the vocabulary note of generate printed a name
  with a line break over two lines.
"""
import io
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mdb import cli
from mdb.core import drift, facts
from mdb.core.generator import OptionRuleError, check_options_json, read_by_score_code, sent_number
from mdb.core.manifest import MANIFEST_FILENAME, load_manifest
from mdb.core.paths import FACT_SHEETS
from test_fix_mdb_26 import DRIVEN, ONE_LINE, _files, _hand_maintained, _refusing, _relocated, no_viya  # noqa: F401

PARSER = (Path(__file__).resolve().parents[3] / "Model-Definition-Builder" / "definition-core" / "templates"
          / "partials" / "_options_parser.py.j2")


def _printed(monkeypatch, width: int = 1000) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=width, force_terminal=False, emoji=False))
    return out


def _options(folder: Path) -> dict:
    return json.loads((folder / "options.json").read_text(encoding="utf-8"))


def _write_options(folder: Path, options: dict) -> None:
    (folder / "options.json").write_text(json.dumps(options, indent=4), encoding="utf-8")


def _manifest_data(folder: Path) -> dict:
    return yaml.safe_load((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))


def _write_manifest(folder: Path, data: dict) -> None:
    (folder / MANIFEST_FILENAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _mdb(*arguments) -> object:
    return CliRunner().invoke(cli.app, list(arguments))


# -- the two commands the import prints adopt a folder -----------------------

def test_import_then_import_apply_converges_the_folder(tmp_path, monkeypatch, repo_root):
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    assert _mdb("import", "gpt_5_mini").exit_code == 0, out.getvalue()
    assert "Review definition.yaml, then converge with: mdb import gpt_5_mini --apply" in out.getvalue()
    assert not (folder / drift.LOCK_FILENAME).exists()
    reviewed = (folder / MANIFEST_FILENAME).read_bytes()

    out.truncate(0)
    out.seek(0)
    result = _mdb("import", "gpt_5_mini", "--apply")
    assert result.exit_code == 0, out.getvalue()
    lines = out.getvalue().splitlines()
    assert "gpt_5_mini: definition.yaml is not converged yet (no .mdb-lock.json) - read as it is." in lines
    assert [line for line in lines if line.startswith("Converged gpt_5_mini (")], out.getvalue()
    assert "already has a definition.yaml" not in out.getvalue()
    assert (folder / MANIFEST_FILENAME).read_bytes() == reviewed
    assert (folder / drift.LOCK_FILENAME).is_file()
    assert facts.read_row(folder.parent / FACT_SHEETS["llm"], "gpt_5_mini") is not None

    out.truncate(0)
    out.seek(0)
    assert _mdb("generate", "gpt_5_mini", "--check").exit_code == 0, out.getvalue()
    assert "gpt_5_mini: clean" in out.getvalue()

    # Converged: a folder with its lock is managed, and import leaves it.
    out.truncate(0)
    out.seek(0)
    before = _files(folder)
    assert _mdb("import", "gpt_5_mini", "--apply").exit_code == 0
    assert "gpt_5_mini already has a definition.yaml - nothing imported." in out.getvalue().splitlines()
    assert _files(folder) == before


def test_import_converges_a_reviewed_definition_as_it_was_edited(tmp_path, monkeypatch, repo_root):
    """What the reviewer changed in definition.yaml is what --apply generates."""
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    assert _mdb("import", "gpt_5_mini").exit_code == 0, out.getvalue()
    data = _manifest_data(folder)
    data["options"]["max_completion_tokens"]["default"] = 2000
    _write_manifest(folder, data)
    assert _mdb("import", "gpt_5_mini", "--apply").exit_code == 0, out.getvalue()
    assert _options(folder)["max_completion_tokens"]["default"] == 2000
    assert load_manifest(folder).options["max_completion_tokens"].default == 2000


def test_a_reviewed_definition_that_cannot_be_rendered_is_named_as_the_repair(tmp_path, monkeypatch, repo_root):
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    assert _mdb("import", "gpt_5_mini").exit_code == 0, out.getvalue()
    data = _manifest_data(folder)
    data["options"]["reasoning_effort"]["default"] = "ultra"
    _write_manifest(folder, data)
    before = _files(folder)
    out.truncate(0)
    out.seek(0)
    assert _mdb("import", "gpt_5_mini", "--apply").exit_code == 1
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("gpt_5_mini: Option 'reasoning_effort' has the default \"ultra\", which is not one "
                               "of its values"), out.getvalue()
    assert "Nothing was written. Repair definition.yaml, then rerun mdb import gpt_5_mini --apply." in lines
    assert _files(folder) == before


def test_an_option_outside_the_vocabulary_is_adopted_as_the_hint_says(tmp_path, monkeypatch, repo_root):
    """The hint after the refusal, followed step by step: the option taken
    out, the import, the option declared inline in the definition.yaml it
    wrote, and the import with --apply."""
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    options = _options(folder)
    _write_options(folder, {**options, "my_custom": {"default": "x"}})
    before = _files(folder)
    out = _printed(monkeypatch)
    assert _mdb("import", "gpt_5_mini").exit_code == 1
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("gpt_5_mini: Option 'my_custom' is not in the option vocabulary and declares no "
                               "inline type."), out.getvalue()
    assert lines[1] == (
        "Nothing was written - the folder stays fully hand-maintained. Take the option out of its options.json or "
        "add it to the vocabulary, then rerun mdb import. To keep it outside the vocabulary, declare it inline "
        "(type and description) in the definition.yaml that mdb import then writes, and converge with mdb import "
        "gpt_5_mini --apply.")
    assert _files(folder) == before

    _write_options(folder, options)
    assert _mdb("import", "gpt_5_mini").exit_code == 0, out.getvalue()
    data = _manifest_data(folder)
    data["options"]["my_custom"] = {"type": "string", "default": "x", "description": "A custom option."}
    _write_manifest(folder, data)
    out.truncate(0)
    out.seek(0)
    assert _mdb("import", "gpt_5_mini", "--apply").exit_code == 0, out.getvalue()
    assert _options(folder)["my_custom"]["default"] == "x"
    score = (folder / "gpt5MiniScore.py").read_text(encoding="utf-8")
    assert "options[\"my_custom\"]" in score


def test_an_option_of_the_vocabulary_whose_default_breaks_the_rule_names_options_json(
        tmp_path, monkeypatch, repo_root):
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    options = _options(folder)
    options["reasoning_effort"]["default"] = "ultra"
    _write_options(folder, options)
    before = _files(folder)
    out = _printed(monkeypatch)
    assert _mdb("import", "gpt_5_mini").exit_code == 1
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("gpt_5_mini: Option 'reasoning_effort' has the default \"ultra\""), out.getvalue()
    assert lines[1] == ("Nothing was written - the folder stays fully hand-maintained. Repair its options.json, "
                        "then rerun mdb import.")
    assert _files(folder) == before


# -- an API_KEY import cannot read as a key name is refused -------------------

def _key(value):
    def change(options):
        options["API_KEY"] = value
        return options
    return change


def _key_default(value):
    def change(options):
        options["API_KEY"]["default"] = value
        return options
    return change


#: Every API_KEY whose key name is no text or an empty one, with the line it is refused with.
KEY_REFUSED = {
    "default 0": (_key_default(0), "Option 'API_KEY' has the default 0 - its default is the name of the provider "
                                   "key, a non-empty text."),
    "default false": (_key_default(False), "Option 'API_KEY' has the default false - its default is the name of the "
                                           "provider key, a non-empty text."),
    "default []": (_key_default([]), "Option 'API_KEY' has the default [] - its default is the name of the provider "
                                     "key, a non-empty text."),
    "default {}": (_key_default({}), "Option 'API_KEY' has the default {} - its default is the name of the provider "
                                     "key, a non-empty text."),
    "default empty": (_key_default(""), "Option 'API_KEY' has the default \"\" - its default is the name of the "
                                        "provider key, a non-empty text."),
    "default null": (_key_default(None), "Option 'API_KEY' has the default null - its default is the name of the "
                                         "provider key, a non-empty text."),
    "no default": (_key({"description": "k"}), "Option 'API_KEY' is {\"description\": \"k\"}, not an object that "
                                               "holds a default."),
    "null": (_key(None), "Option 'API_KEY' is null, not an object that holds a default."),
    "0": (_key(0), "Option 'API_KEY' is 0, not an object that holds a default."),
    "empty text": (_key(""), "Option 'API_KEY' is \"\", not an object that holds a default."),
    "empty list": (_key([]), "Option 'API_KEY' is [], not an object that holds a default."),
}


@pytest.mark.parametrize("case", sorted(KEY_REFUSED))
def test_import_refuses_an_api_key_it_would_take_as_no_key(case, tmp_path, monkeypatch, repo_root):
    change, line = KEY_REFUSED[case]
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    _write_options(folder, change(_options(folder)))
    before = _files(folder)
    out = _printed(monkeypatch)
    assert _mdb("import", "gpt_5_mini").exit_code == 1, out.getvalue()
    assert out.getvalue().splitlines() == [
        f"gpt_5_mini: {line}",
        "Nothing was written - the folder stays fully hand-maintained. Repair its options.json, then rerun "
        "mdb import."]
    assert _files(folder) == before


def test_a_folder_without_an_api_key_is_still_imported_without_a_key(tmp_path, monkeypatch, repo_root):
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    options = _options(folder)
    del options["API_KEY"]
    _write_options(folder, options)
    out = _printed(monkeypatch)
    assert _mdb("import", "gpt_5_mini").exit_code == 0, out.getvalue()
    assert load_manifest(folder).provider.auth.mode == "none"


# -- mdb pull --import refuses as mdb import does -----------------------------

def test_pull_import_refuses_as_import_does(tmp_path, monkeypatch, repo_root, no_viya):
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    _write_options(folder, {**_options(folder), "my_stop": {"default": "a,b"}})
    before = _files(folder)
    out = _printed(monkeypatch)
    result = _mdb("pull", "gpt_5_mini", "--import", "--force")
    assert result.exit_code == 1, out.getvalue()
    lines = out.getvalue().splitlines()
    assert ("gpt_5_mini: Option 'my_stop' has the default \"a,b\" - the Builder sends the options as "
            "{name:value,name:value}, so a text default holds no comma.") in lines
    assert ("definition.yaml was not written - the pulled folder stays fully hand-maintained. Repair its "
            "options.json, then run mdb import gpt_5_mini.") in lines
    assert not (folder / MANIFEST_FILENAME).exists()
    assert _files(folder) == before


def test_pull_import_writes_definition_yaml_as_import_does(tmp_path, monkeypatch, repo_root, no_viya):
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    out = _printed(monkeypatch)
    assert _mdb("pull", "gpt_5_mini", "--import", "--force").exit_code == 0, out.getvalue()
    assert (folder / MANIFEST_FILENAME).is_file()
    assert "Review definition.yaml, then converge with: mdb import gpt_5_mini --apply" in out.getvalue()
    out.truncate(0)
    out.seek(0)
    assert _mdb("import", "gpt_5_mini", "--apply").exit_code == 0, out.getvalue()
    assert (folder / drift.LOCK_FILENAME).is_file()


def test_pull_import_that_fails_otherwise_ends_with_exit_1(tmp_path, monkeypatch, repo_root, no_viya):
    _hand_maintained(tmp_path, monkeypatch, repo_root)

    def broken(*args, **kwargs):
        raise KeyError("scoreCodeFile")

    monkeypatch.setattr(cli, "import_folder", broken)
    out = _printed(monkeypatch)
    assert _mdb("pull", "gpt_5_mini", "--import", "--force").exit_code == 1
    assert ("gpt_5_mini: pulled, but could not reverse-engineer definition.yaml ('scoreCodeFile') - the folder "
            "stays hand-maintained.") in out.getvalue().splitlines()


# -- what the score code reads of a name and a value --------------------------

def _parser():
    namespace = {"json": json}
    exec(PARSER.read_text(encoding="utf-8"), namespace)
    return namespace["_parse_options"]


#: Texts around every step of the parser: blanks and quotes at the ends,
#: true and false in any case, what int() and float() read, and texts that
#: are none of these.
TEXTS = ["a", " a ", "a ", "\ta", "a\n", "\x1ca", "a\x85", "　a", "﻿a", "\"a\"", "'a'", "\"a", "a'",
         "'", "\"\"", "a b", "a\"b", "TRUE", "True", "true", "False", "fAlSe", "truth", "5", "+5", "-5", "05",
         "1_000", "1__0", "١٢", "5.", ".5", "1.5", "1_0.5", "1.e5", "1e5", "1E5", "1.5e-7", "nan",
         "inf", "Infinity", "nan.", "1.2.3", "v1.2", "2024-10-21", "", " ", "a:b", "{x}", "x}"]


@pytest.mark.parametrize("text", TEXTS)
def test_read_by_score_code_is_what_the_parser_reads(text):
    parse = _parser()
    sent = parse("{k:" + text + "}")
    read = read_by_score_code(text)
    if text.strip().strip("\"'") == "":
        assert sent in ({"k": ""}, {}), sent
        assert read == ""
    else:
        assert sent == {"k": read} and type(sent["k"]) is type(read), (text, sent, read)


@pytest.mark.parametrize("name", ["a", " a", "a ", "'a'", "\"a", "a b", "a\nb", "　a"])
def test_a_name_is_read_without_the_blanks_and_quotes_at_its_ends(name):
    read = _parser()("{" + name + ":1}")
    assert list(read) == [name.strip().strip("\"'")]
    refused = name.strip().strip("\"'") != name
    try:
        check_options_json({name: {"default": 1}})
        assert not refused, name
    except OptionRuleError:
        assert refused, name


@pytest.mark.parametrize("number,sent", [
    (1e-07, "1e-7"), (-1e-07, "-1e-7"), (5e-324, "5e-324"), (1e21, "1e+21"), (10 ** 21, "1e+21"),
    (2e22, "2e+22"), (1e300, "1e+300"), (10 ** 400, "Infinity"),
    (1.5e-07, None), (1.5e21, None), (1e20, None), (10 ** 20, None), (1e-06, None), (0.000001, None),
    (0, None), (0.0, None), (-0.0, None), (0.5, None), (4000, None), (True, None), ("1e-7", None),
])
def test_sent_number_is_how_the_builder_writes_a_number_the_score_code_reads_as_text(number, sent):
    assert sent_number(number) == sent
    if sent is not None:
        assert isinstance(_parser()("{k:" + sent + "}")["k"], str)


#: An options.json whose option the score code would read otherwise than it says, with the line of the refusal.
READ_OTHERWISE = {
    "a name with a blank at its start": (
        {" temperature": {"type": "enum", "default": "high", "values": ["low", "high"]}},
        "Option ' temperature' has a blank or a quote at an end of its name - the score code takes them off and "
        "would read the option as \"temperature\"."),
    "a name in quotes": (
        {"'temperature'": {"default": 0.5}},
        "Option ''temperature'' has a blank or a quote at an end of its name - the score code takes them off and "
        "would read the option as \"temperature\"."),
    "a text with blanks at its ends": (
        {"stop": {"type": "string", "default": " a "}},
        "Option 'stop' has the default \" a \" - the score code would read it as \"a\", not as that text."),
    "a text in quotes": (
        {"stop": {"default": "\"a\""}},
        "Option 'stop' has the default \"\\\"a\\\"\" - the score code would read it as \"a\", not as that text."),
    "the text TRUE": (
        {"stop": {"type": "string", "default": "TRUE"}},
        "Option 'stop' has the default \"TRUE\" - the score code would read it as true, not as that text."),
    "the text true": (
        {"stop": {"default": "true"}},
        "Option 'stop' has the default \"true\" - the score code would read it as true, not as that text."),
    "the text 5": (
        {"seed_text": {"type": "string", "default": "5"}},
        "Option 'seed_text' has the default \"5\" - the score code would read it as 5, not as that text."),
    "the text 0.5 of a field of the Builder": (
        {"temperature": {"default": "0.5"}},
        "Option 'temperature' has the default \"0.5\" - the score code would read it as 0.5, not as that text."),
    "an enum of True and False": (
        {"flag": {"type": "enum", "default": "True", "values": ["True", "False"]}},
        "Option 'flag' has the value \"True\" - the score code would read it as true, not as that text."),
    "an enum value with a blank at its start": (
        {"level": {"type": "enum", "default": "high", "values": [" low", "high"]}},
        "Option 'level' has the value \" low\" - the score code would read it as \"low\", not as that text."),
    "a number sent as 1e-7": (
        {"epsilon": {"default": 1e-07}},
        "Option 'epsilon' has the default 1e-07 - the Builder sends it as 1e-7, which the score code reads as a "
        "text, not as a number."),
    "a number sent as 1e+21": (
        {"budget": {"default": 10 ** 21}},
        "Option 'budget' has the default 1000000000000000000000 - the Builder sends it as 1e+21, which the score "
        "code reads as a text, not as a number."),
}


@pytest.mark.parametrize("case", sorted(READ_OTHERWISE))
def test_an_option_the_score_code_would_read_otherwise_is_refused(case):
    entries, line = READ_OTHERWISE[case]
    with pytest.raises(OptionRuleError) as raised:
        check_options_json(entries)
    assert str(raised.value) == line


#: What the score code reads as written stays taken.
READ_AS_WRITTEN = {
    "a name with a blank inside": {"a b": {"default": "x"}},
    "a text with a blank and a quote inside": {"stop": {"type": "string", "default": "say \"hi\" now"}},
    "an empty text": {"region": {"type": "string", "default": ""}},
    "a text that only looks like a number": {"version": {"default": "2024-10-21"}},
    "the text 1e5, which int() does not read": {"stop": {"default": "1e5"}},
    "the text nan, which int() does not read": {"stop": {"default": "nan"}},
    "a number with a point in exponent notation": {"epsilon": {"default": 1.5e-07}},
    "a number below 10^21": {"budget": {"default": 10 ** 20}},
    "a bool": {"stream": {"type": "bool", "default": False}},
    "the provider key, which a run does not send": {"API_KEY": {"default": " Open AI "}},
}


@pytest.mark.parametrize("case", sorted(READ_AS_WRITTEN))
def test_an_option_the_score_code_reads_as_written_is_taken(case):
    check_options_json(READ_AS_WRITTEN[case])


def test_generate_refuses_an_informational_option_whose_name_the_score_code_strips(
        tmp_path, monkeypatch, repo_root):
    """review-26: an informational ' temperature' made the score code read
    the temperature 'high' in place of the model's."""
    folder = _relocated(tmp_path, monkeypatch, repo_root, "claude_sonnet_4_5")
    data = _manifest_data(folder)
    data["options"][" temperature"] = {"type": "enum", "default": "high", "values": ["low", "high"],
                                       "description": "X.", "informational": True}
    _write_manifest(folder, data)
    before = _files(folder)
    out = _printed(monkeypatch)
    assert _mdb("generate", "claude_sonnet_4_5").exit_code == 1
    assert ("claude_sonnet_4_5: Option ' temperature' has a blank or a quote at an end of its name - the score code "
            "takes them off and would read the option as \"temperature\".") in out.getvalue().splitlines()
    assert _files(folder) == before


def test_every_options_json_of_the_repository_is_read_as_written(repo_root):
    files = sorted(path for folder in ("LLM-Definitions", "Embedding-Definitions")
                   for path in (repo_root / folder).glob("*/options.json"))
    assert len(files) > 40
    for path in files:
        check_options_json(json.loads(path.read_text(encoding="utf-8")))


# -- every refusal is one line, also where the output goes to a pipe ----------

@pytest.mark.parametrize("case", sorted(DRIVEN))
def test_a_refusal_is_one_line_at_80_columns(case, tmp_path, monkeypatch, repo_root, no_viya):
    arguments, refusing = DRIVEN[case]
    folder = _relocated(tmp_path, monkeypatch, repo_root, "gpt_5_mini")
    if arguments[0] == "import":
        (folder / MANIFEST_FILENAME).unlink()
        (folder / drift.LOCK_FILENAME).unlink()
    for module, name in refusing:
        monkeypatch.setattr(module, name, _refusing)
    out = _printed(monkeypatch, width=80)
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert [line for line in out.getvalue().splitlines() if ONE_LINE in line], out.getvalue()


def test_the_hint_of_an_import_is_one_line_at_80_columns(tmp_path, monkeypatch, repo_root):
    folder = _hand_maintained(tmp_path, monkeypatch, repo_root)
    _write_options(folder, {**_options(folder), "my_stop": {"default": "a,b"}})
    out = _printed(monkeypatch, width=80)
    assert _mdb("import", "gpt_5_mini").exit_code == 1
    assert out.getvalue().splitlines() == [
        "gpt_5_mini: Option 'my_stop' has the default \"a,b\" - the Builder sends the options as "
        "{name:value,name:value}, so a text default holds no comma.",
        "Nothing was written - the folder stays fully hand-maintained. Repair its options.json, then rerun "
        "mdb import."]


def test_a_custom_option_whose_name_holds_a_line_break_is_noted_on_one_line(tmp_path, monkeypatch, repo_root):
    """review-26: V010 of validate and the vocabulary note of generate
    printed the name over two lines."""
    folder = _relocated(tmp_path, monkeypatch, repo_root, "claude_sonnet_4_5")
    data = _manifest_data(folder)
    data["options"]["x\ny"] = {"type": "string", "default": "q", "description": "Info.", "informational": True}
    _write_manifest(folder, data)
    out = _printed(monkeypatch, width=80)
    assert _mdb("generate", "claude_sonnet_4_5").exit_code == 0, out.getvalue()
    assert [line for line in out.getvalue().splitlines()
            if line.startswith("claude_sonnet_4_5: option 'x y' is not in the standardized vocabulary - it is sent "
                               "to the provider as-is")], out.getvalue()
    out.truncate(0)
    out.seek(0)
    _mdb("validate", "claude_sonnet_4_5")
    assert [line for line in out.getvalue().splitlines()
            if line.startswith("[V010] claude_sonnet_4_5: Option 'x y' is not in the standardized option "
                               "vocabulary. It is passed to the provider as-is")], out.getvalue()
    assert not [line for line in out.getvalue().splitlines() if line.startswith("y'")], out.getvalue()
