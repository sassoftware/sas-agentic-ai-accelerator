# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the second gate and the second review of 2026-10-13 found in mdb,
each with a test that fails on the code before it. Every service is a fake
(content_fakes, the fakes of the other test files) or a made-up answer:
nothing here says how SAS Viya answers.
"""
import ast
import io
import itertools
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from content_fakes import Response, Viya, package_of
from mdb import cli
from mdb.core import content
from mdb.viya import credentials
from mdb.viya import session as s
from mdb.viya import transfer
from test_builder_commands import TUNED, Site, discovery, importing, no_discovery, out, site  # noqa: F401
from test_content_deploy import command, manifest, repo  # noqa: F401
from test_credentials_commands import mdb  # noqa: F401
from test_package_guards import exporting  # noqa: F401
from test_session import env, fake_sasctl  # noqa: F401

REPOSITORY = Path(__file__).resolve().parents[3]
CLI_SOURCE = REPOSITORY / "Model-Definition-Builder/cli/src/mdb/cli.py"


# ---- a value in a text that rich reads with markup on --------------------------------
def _shown(markup: str) -> str:
    """What the console of mdb prints for a text with markup on, on one line."""
    out = io.StringIO()
    Console(file=out, width=100000, force_terminal=False, emoji=False).print(markup, highlight=False, soft_wrap=True)
    assert out.getvalue().endswith("\n")
    return out.getvalue()[:-1]


#: (a text with the place of a value, what it prints as around the value): the value alone, between tags,
#: in front of a bracket that is no tag, in front of text that closes a bracket of the value, twice in a row,
#: and in front of a bracket of mdb's own.
AROUND = [("{}", "{}"), ("[red]{}[/red]", "{}"), ("x {}) y]", "x {}) y]"), ("{}abc] z", "{}abc] z"),
          ("[bold]{}[/] more", "{} more"), ("'{}': [dim]{}[/dim]/x]", "'{}': {}/x]"), ("{}{}", "{}{}"),
          ("{}\\[literal]", "{}[literal]")]


@pytest.mark.parametrize("value", [
    "a [tag] and a \\[ escaped one",          # gate-20b: the backslash was lost
    "ends with two \\\\",                     # gate-20b: one of the two was lost
    "ends with one \\", "\\", "\\\\\\", "[", "]", "[/", "[/red]", "\\[/red]", "\\\\[/red]", "[bold", "a[b\\",
    "The folder [/folders/folders/abc] could not be exported.", "error [0] in task [export]: timeout",
    "Sure. [/INST] Here is the answer", "C:\\Temp\\[old]\\", "[link=https://example.com]here[/link]",
    "[@click=app.bell]x[/]", "[#ff0000]x", "urn:id:1 :key: x:b:y", "",
])
def test_a_value_is_printed_as_it_is_whatever_stands_around_it(value):
    """gate-20b: `rich.markup.escape` lost a backslash in front of a bracket
    that opens no tag, and one of two backslashes at the end."""
    for template, expected in AROUND:
        places = template.count("{}")
        assert _shown(template.format(*[cli._plain(value)] * places)) == expected.format(*[value] * places), template


def test_every_short_text_of_brackets_and_backslashes_is_printed_as_it_is():
    """Every text of up to four of these characters, in every place of AROUND."""
    for length in range(5):
        for characters in itertools.product("\\[]/a 0", repeat=length):
            value = "".join(characters)
            if "  " in value:               # rich keeps two blanks; a console line is not asked about them here
                continue
            for template, expected in AROUND:
                places = template.count("{}")
                assert _shown(template.format(*[cli._plain(value)] * places)) \
                    == expected.format(*[value] * places), (value, template)


def test_a_value_keeps_the_style_of_its_line_and_a_number_is_a_text():
    """The pair of tags behind backslashes styles nothing: the line is red
    to its end. And `_plain` takes what is no text."""
    text = Text.from_markup("[red]" + cli._plain("C:\\") + " and more[/red]", emoji=False)
    assert text.plain == "C:\\ and more"
    assert any(str(span.style) == "red" and (span.start, span.end) == (0, len(text.plain)) for span in text.spans)
    assert cli._plain(7) == "7" and cli._plain(None) == "None" and cli._plain(Path("a") / "b") == str(Path("a") / "b")
    assert cli._plain("no bracket, no backslash at the end: a\\b") == "no bracket, no backslash at the end: a\\b"


def test_the_console_of_mdb_prints_no_picture_for_a_name_between_colons():
    """rich replaces `:name:` with an emoji in every text it prints, with
    markup on or off: `urn:id:1` lost `:id:`. The console of mdb does not."""
    with cli.console.capture() as printed:
        cli.console.print("[dim]urn:id:1 :key: x:b:y[/dim]", highlight=False)
        cli.console.print("urn:id:1 :key: x:b:y", markup=False, highlight=False)
    assert printed.get() == "urn:id:1 :key: x:b:y\n" * 2


@pytest.mark.parametrize("asking, answer, expected", [(cli.Prompt, "typed", "typed"), (cli.Confirm, "y", True)])
def test_a_prompt_of_mdb_shows_its_question_as_it_is(asking, answer, expected):
    """A prompt of rich reads its text with emoji on, whatever the console
    says: the id in `Delete registered model 'ns:a:1' ...` was shown with a
    picture. And a bracket of the value is text."""
    out = io.StringIO()
    shown = Console(file=out, width=1000, force_terminal=False)
    question = f"Delete registered model '{cli._plain('ns:a:1 [/red] x:b:y')}' from SAS Model Manager?"
    assert asking.ask(question, console=shown, stream=io.StringIO(answer + "\n")) == expected
    assert out.getvalue().startswith("Delete registered model 'ns:a:1 [/red] x:b:y' from SAS Model Manager?")


# ---- every text cli.py hands rich: which values it may hold -----------------------------
#: The calls of cli.py that hand rich a text it reads with markup on, and where the text stands in them.
TEXT_CALLS = {"console.print": "arguments", "table.add_row": "arguments", "table.add_column": "arguments",
              "Table": "title", "Prompt.ask": "prompt", "Confirm.ask": "prompt"}
#: The names that hold markup of mdb's own: the name of a colour, and one dimmed word. A test of its own
#: holds each of them to a text mdb wrote.
OWN_MARKUP = {"color", "unknown"}
#: The tags mdb writes.
TAGS = {"red", "green", "yellow", "dim", "bold", "cyan", "bold cyan"}


def _tree():
    return ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))


def _markup_off(call) -> bool:
    return any(keyword.arg == "markup" and getattr(keyword.value, "value", None) is False
               for keyword in call.keywords)


def _a_number(node) -> bool:
    """A number by its form: len(...), a number, a sum or difference of such."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
        return _a_number(node.left) and _a_number(node.right)
    return isinstance(node, ast.Call) and ast.unparse(node.func) == "len"


def _numeric_format(part) -> bool:
    """Whether a value of an f-string is written with a format that only a number takes (`:g`, `:.2f`, `:,`)."""
    if part.format_spec is None or len(part.format_spec.values) != 1:
        return False
    spec = part.format_spec.values[0]
    return isinstance(spec, ast.Constant) and bool(re.fullmatch(r"[0-9.,]*[dgfe%]|,", spec.value))


def _value_faults(node) -> list:
    """What is wrong with one value of a text: nothing where it goes
    through `_plain`, is a number by its form, or is markup of mdb's own."""
    if isinstance(node, ast.Constant) or _a_number(node):
        return []
    if isinstance(node, ast.IfExp):
        return _value_faults(node.body) + _value_faults(node.orelse)
    if isinstance(node, ast.Call) and ast.unparse(node.func) == "_plain":
        return []
    if isinstance(node, ast.Name) and node.id in OWN_MARKUP:
        return []
    return [f"line {node.lineno}: {ast.unparse(node)}"]


def _text_faults(node, whole=("table",)) -> list:
    """What is wrong with one text: a value that is not allowed in it."""
    if isinstance(node, ast.Constant):
        return []
    if isinstance(node, ast.JoinedStr):
        faults = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue) and not _numeric_format(part):
                faults += [f"line {part.lineno}: a conversion or a format in {ast.unparse(node)}"] \
                    if part.conversion != -1 or part.format_spec is not None else _value_faults(part.value)
        return faults
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _text_faults(node.left, ()) + _text_faults(node.right, ())
    if isinstance(node, ast.IfExp):
        return _text_faults(node.body, whole) + _text_faults(node.orelse, whole)
    if isinstance(node, ast.Call) and ast.unparse(node.func) == "_plain":
        return []
    if isinstance(node, ast.Name) and node.id in (*whole, "unknown"):
        return []                               # a table is no text; `unknown` is markup of mdb's own
    return [f"line {node.lineno}: {ast.unparse(node)}"]


def _texts(tree):
    """(call, text) for every text cli.py hands rich with markup on."""
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        where = TEXT_CALLS.get(ast.unparse(call.func))
        if where is None or _markup_off(call):
            continue
        if where == "arguments":
            assert not any(isinstance(argument, ast.Starred) for argument in call.args), ast.unparse(call)
            yield from ((call, argument) for argument in call.args)
        elif where == "title":
            assert not call.args, ast.unparse(call)
            yield from ((call, keyword.value) for keyword in call.keywords if keyword.arg in ("title", "caption"))
        else:
            yield call, call.args[0]


def test_every_value_in_a_text_with_markup_on_goes_through_plain():
    """review-20b and gate-20b: the round before closed the class for
    exceptions and smoke-test details, and left the response of a model in
    a table cell of `mdb test`, the value of an options file, the state and
    the messages of an export job, the content root a user names, and the
    names typed for the two credentials commands.

    The rule holds for every value, also one mdb made itself: it is handed
    to `_plain`, or it is a number by its form, or one of the two names that
    hold markup of mdb's own.
    """
    faults = []
    for _call, text in _texts(_tree()):
        faults += _text_faults(text)
    assert faults == []
    # the places the two checks named, as they stand now
    source = CLI_SOURCE.read_text(encoding="utf-8")
    for line in ('table.add_row("response", _plain(response))', 'console.print(f"  {_plain(line)}")',
                 "'{_plain(value)}' says", "ended {_plain(job.get('state'))}", "[bold]{_plain(root)}[/bold]",
                 "{_plain(identity_type)} {_plain(identity)}: {_plain(problem)}"):
        assert line in source, line


def test_plain_is_used_for_a_text_with_markup_on_and_nowhere_else():
    """`_plain` spells a bracket `\\[`, which is the bracket only where rich
    reads markup. In a line printed with markup off, in the text of an
    exception or in a file the backslash would stand. Every call of it sits
    in one of the calls of TEXT_CALLS that has markup on."""
    tree = _tree()
    inside = {}
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and ast.unparse(call.func) in TEXT_CALLS:
            for node in ast.walk(call):
                inside.setdefault(id(node), []).append(call)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "_plain"]
    assert len(calls) > 400
    for node in calls:
        around = inside.get(id(node), [])
        assert around and not any(_markup_off(call) for call in around), node.lineno


def test_what_holds_markup_of_mdbs_own_is_a_text_mdb_wrote():
    """The two names the rule lets through are bound to nothing a service
    or a user gives: a constant, or one of the constant values of a dict."""
    tree = _tree()

    def colours(node, function) -> bool:
        if isinstance(node, ast.Name):
            bound = [a.value for a in ast.walk(function) if isinstance(a, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == node.id for t in a.targets)]
            return len(bound) == 1 and colours(bound[0], function)
        return isinstance(node, ast.Dict) and all(isinstance(v, ast.Constant) and v.value in TAGS for v in node.values)

    bound_here = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for assign in ast.walk(function):
            if not (isinstance(assign, ast.Assign) and len(assign.targets) == 1
                    and isinstance(assign.targets[0], ast.Name) and assign.targets[0].id in OWN_MARKUP):
                continue
            bound_here.append(assign.targets[0])
            value = assign.value
            if isinstance(value, ast.Constant):
                assert value.value == "[dim]unknown[/dim]", ast.unparse(assign)
            elif isinstance(value, ast.Subscript):
                assert colours(value.value, function), ast.unparse(assign)
            else:                               # <dict of colours>.get(<key>, "<colour>")
                assert (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                        and value.func.attr == "get" and colours(value.func.value, function)
                        and isinstance(value.args[1], ast.Constant) and value.args[1].value in TAGS), \
                    ast.unparse(assign)
    assert len(bound_here) == 5
    # and nothing else binds them: no loop variable, no argument, no `with ... as`
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in OWN_MARKUP and isinstance(node.ctx, ast.Store):
            assert node in bound_here, node.lineno
        assert not (isinstance(node, ast.arg) and node.arg in OWN_MARKUP), node.lineno


def test_a_bracket_mdb_writes_into_a_text_with_markup_on_is_a_tag_or_spelled_as_a_bracket():
    """`[reasoning]` behind a model of the catalog and `[y/N]` in a question
    of an adapter were never printed: rich read them as tags it does not
    know. Both are values and go through `_plain` now. In the constant part
    of a text every bracket is one of the tags mdb writes, the tag of a
    colour held in a name, or `\\[`."""
    faults = []

    def written(node):
        """The constant parts of a text, as mdb wrote them; what `_plain` is handed is a value."""
        if isinstance(node, ast.JoinedStr):
            # a value stands as {} in the text: `[{color}]` is the tag of a colour
            yield node.lineno, "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    yield from written(part.value)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.BinOp):
            yield from written(node.left)
            yield from written(node.right)
        elif isinstance(node, ast.IfExp):
            yield from written(node.body)
            yield from written(node.orelse)

    for _call, text in _texts(_tree()):
        for lineno, constant in written(text):
            for found in re.finditer(r"(\\*)\[([^\[\]]*)(\]?)", constant):
                backslashes, name, closed = found.groups()
                if len(backslashes) % 2 == 0 and not (closed and name.lstrip("/") in (*TAGS, "{}", "")):
                    faults.append(f"line {lineno}: {found.group(0)!r}")
    assert faults == []
    assert _shown("7-dimension vector \\[0.1, nan, ...]") == "7-dimension vector [0.1, nan, ...]"


def test_cli_py_hands_rich_a_text_in_no_other_way():
    """The rule above reads the calls of TEXT_CALLS. Nothing else in cli.py
    prints: no other method of the console but `print_json`, no table under
    another name, no prompt but the two, no `print`, and `typer.echo` for
    the version alone. No other module of mdb imports rich or typer."""
    tree = _tree()
    console_methods, echoed = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "console":
            console_methods.add(node.attr)
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        assert name not in ("print", "Panel", "Text", "typer.secho", "rich.print", "typer.BadParameter"), node.lineno
        if name == "typer.echo":
            echoed.append(ast.unparse(node))
        if name.endswith((".add_row", ".add_column")):
            assert name.split(".")[0] == "table", ast.unparse(node)
        if name.endswith(".ask"):
            assert name in ("Prompt.ask", "Confirm.ask"), ast.unparse(node)
        if name == "Text.from_markup":
            assert "emoji=False" in ast.unparse(node)
    assert console_methods == {"print", "print_json"}
    assert cli.Prompt.__mro__[1] is cli._EmojiOff and cli.Confirm.__mro__[1] is cli._EmojiOff
    assert echoed == ["typer.echo(f'mdb {__version__}')"]
    for assign in ast.walk(tree):
        if isinstance(assign, ast.Assign) and isinstance(assign.value, ast.Call) \
                and ast.unparse(assign.value.func) == "Table":
            assert [ast.unparse(target) for target in assign.targets] == ["table"]
    # `table` is let through as a whole text because it is a Table: in every function that prints it or
    # fills it, the name is bound to one - and to nothing else (two commands have an option of that name)
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        used = [node for node in ast.walk(function) if isinstance(node, ast.Call) and (
            ast.unparse(node.func) in ("table.add_row", "table.add_column")
            or ast.unparse(node.func) == "console.print" and any(
                isinstance(argument, ast.Name) and argument.id == "table" for argument in node.args))]
        if used:
            bound = [node.value for node in ast.walk(function) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "table" for target in node.targets)]
            assert bound and all(isinstance(value, ast.Call) and ast.unparse(value.func) == "Table"
                                 for value in bound), function.name
            assert "table" not in [argument.arg for argument in function.args.args + function.args.kwonlyargs], \
                function.name
    # no other module of mdb knows rich or typer: what one of them prints, it prints as it is
    for path in sorted(CLI_SOURCE.parent.rglob("*.py")):
        if path == CLI_SOURCE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(name.split(".")[0] in ("rich", "typer") for name in names), (path.name, node.lineno)


# ---- the same through the commands the two checks named --------------------------------
def _ended_by_itself(result) -> bool:
    """The command ended with an exit code of its own, not with a traceback."""
    return result.exception is None or isinstance(result.exception, SystemExit)


def test_the_state_and_the_messages_of_a_failed_export_job_are_printed_as_they_are(exporting, manifest, monkeypatch):
    """gate-20b: `The folder [/folders/folders/abc] could not be exported.`
    ended the command with a MarkupError, and `error [0] in task [export]:
    timeout` was printed as `error [0] in task : timeout`."""
    job = {"state": "failed [/red]", "errorCount": "[2]",
           "links": [{"rel": "self", "href": "/transfer/exportJobs/j1"}]}
    messages = ["a: failed The folder [/folders/folders/abc] could not be exported.",
                "b: failed error [0] in task [export]: timeout \\"]
    monkeypatch.setattr(transfer, "wait_for_job", lambda session, href, timeout=600: job)
    monkeypatch.setattr(transfer, "job_messages", lambda session, job: messages)
    result, text, target, _ = exporting(package_of(manifest))
    assert result.exit_code == 1 and _ended_by_itself(result), result.exception
    assert text.splitlines()[-3:] == ["The export job ended failed [/red] ([2] error(s)).",
                                      "  " + messages[0], "  " + messages[1]]
    assert not target.exists()


@pytest.mark.parametrize("root", ["/Teams/zz [two]/root", "/Teams/a[/b]/root", "/Teams/[bold]x\\"])
def test_package_export_names_the_content_root_as_it_was_given(exporting, manifest, tmp_path, root):
    """gate-20b: a folder name in brackets was dropped from the path in three
    lines of package-export, and a root that holds `[/` ended the command
    with a MarkupError. The three lines: the refusal without --out, the
    refusal of an example folder, and the line that says what is exported."""
    result, text, _target, _ = exporting(package_of(manifest), "--content-root", root)
    assert result.exit_code == 2 and _ended_by_itself(result), result.exception
    assert text.startswith(f"{root.rstrip('/')} is not the content root of the release (/SAS Agentic AI Accelerator)")

    example = root.rstrip("/") + "/Example [1]"
    result, text, _target, _ = exporting(package_of(manifest), "--content-root", example, "--out",
                                         str(tmp_path / "x.json"))
    assert result.exit_code == 2 and _ended_by_itself(result), result.exception
    assert text == f"{example}: 'Example [1]' is showcase or example content, which never ships. " \
                   "Nothing was exported.\n"

    result, text, _target, _ = exporting(package_of(manifest), "--content-root", root, "--name", "pkg [1]",
                                         "--out", str(tmp_path / "x.json"), "--allow-differences")
    assert _ended_by_itself(result), result.exception
    assert text.splitlines()[0] == f"Exporting {root.rstrip('/')} as package pkg [1] ..."


def test_mdb_test_shows_the_response_of_a_model_as_it_is(monkeypatch, tmp_path):
    """review-20b: the response is a table cell, which rich reads with
    markup on - `Sure. [/INST] Here is the answer` ended `mdb test` with a
    MarkupError."""
    response = "Sure. [/INST] Here is the [bold]answer[/bold] :ok: \\"
    (tmp_path / "score.py").write_text(
        "def scoreModel(prompt, system, options):\n"
        f"    return {response!r}, 0.25, 3, 4\n", encoding="utf-8")
    described = SimpleNamespace(kind="llm", provider=SimpleNamespace(adapter="none",
                                                                      auth=SimpleNamespace(mode="none")))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(find_folder=lambda model_id: tmp_path))
    monkeypatch.setattr(cli, "load_manifest", lambda folder: described)
    monkeypatch.setattr(cli, "load_adapters", lambda: {})
    monkeypatch.setattr(cli, "effective_score_file", lambda manifest: "score.py")
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=200, force_terminal=False, emoji=False))
    result = CliRunner().invoke(cli.app, ["test", "made_up_model"])
    assert result.exit_code == 0 and _ended_by_itself(result), result.exception
    row = next(line for line in out.getvalue().splitlines() if "response" in line)
    assert [cell.strip() for cell in row.strip("│").split("│")] == ["response", response]


def test_a_value_of_an_options_file_that_is_no_switch_is_named_as_it_stands(monkeypatch):
    """review-20b: `'[/x]' says neither on nor off` between colour tags."""
    from mdb.core import options
    from mdb.viya import reports
    value = "[/x] maybe [bold]"
    monkeypatch.setattr(reports, "find_report_at",
                        lambda session, folder, name, strict=False: {"id": "r1"} if name == "Prompt Builder" else None)
    monkeypatch.setattr(reports, "get_content", lambda session, report_id: ("<report/>", "etag"))
    monkeypatch.setattr(options, "write_options", lambda content, values: (content, SimpleNamespace(
        missing=[], rejected={"enableRag": value}, changed=False, applied=[], unchanged=[])))
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    assert cli._restore_options(object(), {"Prompt Builder": {"enableRag": value}}, dry_run=True) == 1
    assert out.getvalue() == (f"  Prompt Builder: 'enableRag' is a checkbox in this report and '{value}' says "
                              "neither on nor off - not written. Use true or false.\n")


def test_the_two_credentials_commands_name_what_was_typed_as_it_was_typed(mdb):
    """The merge of 2026-10-14 measured it: `mdb credentials-add group
    "team[/red]"`, `--only "No[/red]Such"` and `mdb credentials-remove user
    "a[/b]"` each ended with a MarkupError and exit code 1."""
    code, said = mdb("credentials-add", "group", "team[/red]")
    assert code == 0 and "with group team[/red] as its identity" in said
    code, said = mdb("credentials-add", "group", "G3 [x]", "--only", "No[/red]Such")
    assert code == 2 and "group G3 [x]: the source does not carry No[/red]Such" in said
    assert "G3" not in mdb.path.read_text(encoding="utf-8")
    code, said = mdb("credentials-remove", "user", "a[/b]")
    assert code == 0 and "Added user a[/b] to credentials.yaml with `state: absent`." in said
    code, said = mdb("credentials-add", "user", "a[/b]", "--only", "OpenAI")
    assert code == 0 and "Updated user a[/b] in credentials.yaml:" in said and "  state: absent -> present" in said
    code, said = mdb("credentials-add", "group", "RAG [team]", "--only", "OpenAI", "--manifest", "sub [1]/c.yaml")
    assert code == 0 and "  only: OpenAI\n" in said
    assert 'Next: mdb credentials-apply --manifest "sub [1]/c.yaml" --dry-run\n' in said


# ---- credentials-report: the row of a credential that cannot be read --------------------
def test_the_row_of_a_credential_that_cannot_be_read_shows_the_http_status(mdb):
    """gate-20b: the row showed the first 40 characters of mdb's sentence
    `reading the credential for <type> '<id>' failed: HTTP <status>`, so
    the status never appeared."""
    from test_credentials_commands import _Response
    assert mdb("credentials-add", "group", "LLMConsumers")[0] == 0
    assert mdb("credentials-add", "user", "ada [x]")[0] == 0
    mdb.site.get = lambda path, **_: _Response(503)
    code, said = mdb("credentials-report")
    assert code == 0
    for who in ("group 'LLMConsumers'", "user 'ada [x]'"):
        assert sum(f"reading the credential for {who} failed: HTTP 503 " in line for line in said.splitlines()) == 1
    assert "0 of 2 named identities hold a credential." in said


# ---- every hint of a FAILED line, run as it is printed -----------------------------------
ROOT = "/SAS Agentic AI Accelerator"
PREFIX = {"file": "/files/files/", "step": "/dataFlows/steps/", "jobDefinition": "/jobDefinitions/definitions/"}


@pytest.fixture
def shipped(monkeypatch):
    """`mdb content-deploy` on the manifest of the repository, against SAS
    Viya in memory. `shipped.viya` is the site, `shipped.manifest` the list."""
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("c" * 40, "2026-10-12T09:31:55+02:00"))
    monkeypatch.setattr(content, "commit_standing", lambda root, commit: content.ANCESTOR)
    out, viya = io.StringIO(), Viya()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPOSITORY))
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)

    def run(*args):
        out.seek(0)
        out.truncate()
        result = CliRunner().invoke(cli.app, ["content-deploy", *args])
        assert _ended_by_itself(result), result.exception
        return result.exit_code, out.getvalue()

    run.viya, run.manifest = viya, content.load_manifest(REPOSITORY)
    return run


def _failed_line(text: str, path: str) -> str:
    line, = [line for line in text.splitlines() if line.lstrip().startswith("FAILED") and f" {path}  (" in line]
    return line


def _as_printed(line: str, **filled) -> list:
    """The arguments of the one command a FAILED line names, with what it
    leaves open (`<its URI>`, `<id>`, the path of a folder) filled in."""
    command, = re.findall(r"`(mdb content-deploy [^`]*)`", line)
    for placeholder, value in filled.items():
        assert command.count(placeholder) == 1, (placeholder, command)
        command = command.replace(placeholder, value)
    assert "<" not in command, command
    words = shlex.split(command)
    assert words[:2] == ["mdb", "content-deploy"]
    return words[2:]


def _taken_out(viya, path: str, kind: str):
    """Take the object of an entry out of its folder; returns (the member, a function that puts everything
    of that folder back as it was)."""
    above, _, name = path.rpartition("/")
    folder = viya.folder_id(above)
    before = list(viya.members[folder])
    member, = [m for m in before if m["name"] == name and m["uri"].startswith(_URI[kind])]
    viya.members[folder] = [m for m in before if m is not member]
    return member, lambda: viya.members.__setitem__(folder, list(before))


_URI = {**PREFIX, "folder": "/folders/folders/"}


def _a_copy_elsewhere(viya, member, kind: str, path: str) -> str:
    """An object of the same kind and content under another path; returns its URI."""
    held = {"file": lambda: viya.files[member["uri"]], "step": lambda: dict(viya.steps[member["uri"]]),
            "jobDefinition": lambda: dict(viya.jobs[member["uri"]][0])}[kind]()
    if kind != "file":
        held["name"] = path.rsplit("/", 1)[-1]
    return viya.put_member(path, PREFIX[kind], held)


def _objects_to_try(manifest, part: str) -> list:
    """Of the manifest, or of one folder of it: every job definition, the first and the last custom step,
    the first file with a source and the file mdb generates beside release.json - the kinds of the manifest
    that a hint names a command for."""
    below = [e for e in manifest.objects() if e.path.startswith(part + "/" if part else "")]
    listed = {kind: [e for e in below if e.kind == kind] for kind in PREFIX}
    files = [e for e in listed["file"] if e.source][:1] + [e for e in listed["file"] if e.generated == "release-table"]
    return listed["jobDefinition"] + listed["step"][:1] + listed["step"][1:][-1:] + files


@pytest.mark.parametrize("part", ["", "RAG", "Data", "Logging and Monitoring"])
def test_the_hint_for_a_reference_and_for_two_objects_runs_as_printed_for_every_kind(shipped, part):
    """gate-20b ran the hints as printed. In a run of the whole manifest
    they worked; in a run of a part that goes somewhere else - `--only RAG
    --to <folder>`, what the deploy-rag-content scripts run - the line named
    the entry by its path below that part, which `--only` does not take:
    `--only "rag_core/tokens.py"` ended with exit code 2."""
    viya, root = shipped.viya, "/Team/A Site/Part" + ("/" + part if part else "")
    entries = _objects_to_try(shipped.manifest, part)
    assert {e.kind for e in entries} == {"": {"jobDefinition", "step", "file"}, "RAG": {"step", "file"},
                                         "Data": {"jobDefinition", "file"},
                                         "Logging and Monitoring": {"step", "file"}}[part]
    first = ("--only", part, "--to", root, "--create") if part else ("--content-root", root, "--create")
    assert shipped(*first)[0] == 0
    for number, entry in enumerate(entries):
        # the run that meets the entry: the part again, or - in the content root - the entry alone
        again = ("--only", part, "--to", root) if part else ("--content-root", root, "--only", entry.path)
        shown = entry.path[len(part) + 1:] if part else entry.path          # the path as the lines of this run show it
        # a reference of the name, alone: to an object of another name elsewhere
        member, put_back = _taken_out(viya, f"{root}/{shown}", entry.kind)
        theirs = _a_copy_elsewhere(viya, member, entry.kind, f"/Team/Own {number}/of the site {entry.name}")
        viya.put_reference(f"{root}/{shown}", theirs)
        code, text = shipped(*again)
        line = _failed_line(text, shown)
        assert code == 1 and f"--only \"{entry.path}\" --to <its URI>`" in line, line
        printed = _as_printed(line, **{"<its URI>": theirs})
        code, text = shipped(*printed)
        assert code == 1 and "pass --force to update it all the same" in text, text    # as the hint says: another name
        code, text = shipped(*printed, "--force")
        assert code == 0 and re.search(r"^  (equal|updated) ", text, re.M), text
        put_back()
        # two objects of the name: one of them is updated by its id
        second = _a_copy_elsewhere(viya, member, entry.kind, f"{root}/{shown}")
        code, text = shipped(*again)
        line = _failed_line(text, shown)
        assert code == 1 and f"--only \"{entry.path}\" --to <id>`" in line, line
        code, text = shipped(*_as_printed(line, **{"<id>": second.rsplit("/", 1)[-1]}))
        assert code == 0 and re.search(r"^  (equal|updated) ", text, re.M), text
        put_back()
        assert shipped(*again)[0] == 0


def test_the_hint_for_a_reference_in_the_place_of_a_folder_runs_as_printed_for_every_folder(shipped):
    """gate-20b: for the entry Data the hint `--only "Data" --to <the SAS
    Content path of that folder>` ended with exit code 2 where the folder
    the reference names is not called Data: a job definition below Data
    reads the content root it sits in, and the command takes that folder of
    a content root only. The hint says so where it is so."""
    viya = shipped.viya
    assert shipped()[0] == 0
    folders = shipped.manifest.folders()
    reading = {f.path for f in folders for e in shipped.manifest.objects()
               if e.path.startswith(f.path + "/") and content.reads_content_root(e)}
    assert reading == {"Data"} and len(folders) > 8
    for number, folder in enumerate(folders):
        member, put_back = _taken_out(viya, f"{ROOT}/{folder.path}", "folder")
        other = f"/Team/Own {number}/Another name"
        fitting = f"/Team/Own {number}/A root/{folder.path}"
        for target in (other, fitting):
            viya.make_folder(target)
        viya.put_reference(f"{ROOT}/{folder.path}", f"/folders/folders/{viya.folder_id(other)}")
        code, text = shipped("--only", folder.path)
        line = _failed_line(text, folder.path)
        assert code == 1 and "--force" not in line
        says_where = (f" - a path that ends with /{folder.path}, with the content root in front of it, because the "
                      "job definition Data/Load-Accelerator-Tables reads the content root it sits in)")
        assert line.endswith(says_where) == (folder.path in reading), line
        placeholder = "<the SAS Content path of that folder>"
        # as printed, with a path of the kind the line asks for
        code, text = shipped(*_as_printed(line, **{placeholder: f'"{fitting}"'}))
        assert code == 0 and "FAILED" not in text and viya.tree(fitting), text
        # ... and with the folder of another name: every folder but the one the line says it of
        code, text = shipped(*_as_printed(line, **{placeholder: f'"{other}"'}))
        assert (code, bool(viya.tree(other))) == ((2, False) if folder.path in reading else (0, True)), text
        put_back()
    assert shipped()[0] == 0


def test_no_hint_names_a_command_for_release_json(shipped):
    """The record of the folder is written by a run of the whole manifest
    alone: `--only release.json --to ...` is refused, so the line for a
    reference of its name, or for two files of its name, names no command."""
    viya = shipped.viya
    assert shipped()[0] == 0
    member, put_back = _taken_out(viya, f"{ROOT}/release.json", "file")
    theirs = _a_copy_elsewhere(viya, member, "file", "/Team/Own/another release.json")
    viya.put_reference(f"{ROOT}/release.json", theirs)
    code, text = shipped()
    line = _failed_line(text, "release.json")
    assert code == 1 and "`" not in line and line.endswith(
        "it is not followed, and nothing was written - remove the reference: release.json is written only by a run "
        "that covers every entry of the manifest)"), line
    put_back()
    _a_copy_elsewhere(viya, member, "file", f"{ROOT}/release.json")
    code, text = shipped()
    line = _failed_line(text, "release.json")
    assert code == 1 and "`" not in line and line.endswith(
        "none was changed, because nothing says which one is meant - remove the other: release.json is written only "
        "by a run that covers every entry of the manifest)"), line


# ---- what is left alone: called by the word the other lines use -----------------------------
def test_what_is_left_alone_is_called_by_the_word_of_its_kind(shipped):
    """gate-20b: `reference to a jobDefinition`, `reference to a
    casManagement`, `not the jobDefinition that is listed under this name` -
    the word of the manifest and a part of an address in a line for people,
    where the FAILED lines say `job definition` and `registered step`."""
    viya = shipped.viya
    assert shipped()[0] == 0
    table = "/casManagement/servers/cas-shared-default/caslibs/Public/tables/SOME_TABLE"
    step, job = "RAG/steps/RAG - List Documents.step", "Data/Load-Accelerator-Tables"
    viya.put_reference(f"{ROOT}/RAG/rag_core/tokens.py", "/jobDefinitions/definitions/elsewhere")
    viya.put_reference(f"{ROOT}/{step}", "/files/files/elsewhere")
    viya.put_reference(f"{ROOT}/{job}", "/dataFlows/steps/elsewhere")
    viya.put_reference(f"{ROOT}/RAG/rag_core/env.py", table)
    viya.put_reference(f"{ROOT}/RAG/of the site", table)
    viya.members[viya.folder_id(f"{ROOT}/RAG")].append({"name": "a picture", "uri": "/images/images/1",
                                                         "type": "reference", "contentType": "image"})
    viya.put_member(f"{ROOT}/RAG/a step of the site.step", "/dataFlows/steps/", {"name": "a step of the site.step"})
    viya.put_member(f"{ROOT}/RAG/a job of the site", "/jobDefinitions/definitions/", {"code": "x"})
    viya.put_member(f"{ROOT}/RAG/a table of the site", "/casManagement/servers/x/caslibs/y/tables/", None)
    code, text = shipped()
    assert code == 0 and "FAILED" not in text, text
    left = text.split("Not in the manifest and left alone (9) - ")[1].splitlines()[1:10]
    assert sorted(left) == sorted([
        f"  {job} (reference to a registered step - not the job definition that is listed under this name)",
        "  RAG/a job of the site (job definition)",
        "  RAG/a picture (reference to an image)",
        "  RAG/a step of the site.step (registered step)",
        f"  RAG/a table of the site (object at {viya.at(ROOT + '/RAG/a table of the site')[0]})",
        f"  RAG/of the site (reference to an object at {table})",
        f"  RAG/rag_core/env.py (reference to an object at {table} - not the file that is listed under this name)",
        "  RAG/rag_core/tokens.py (reference to a job definition - not the file that is listed under this name)",
        f"  {step} (reference to a file - not the registered step that is listed under this name)"])
    for raw in ("jobDefinition", "casManagement", "dataFlows"):
        assert all(raw not in line.split(" (", 1)[1].split(" at ")[0] for line in left), raw


# ---- an empty value where a path is expected ---------------------------------------------
@pytest.mark.parametrize("args", [("--only", ""), ("--only", "/"), ("--only", "//", "--dry-run"),
                                  ("--only", "RAG", "--only", ""), ("--only", "", "--to", "/Team/Other")])
def test_an_only_that_names_nothing_is_refused_and_nothing_is_deployed(command, args):
    """gate-20b: `mdb content-deploy --only ""` - what `--only "$PART"`
    gives where the variable is not set - deployed every entry of the
    manifest, release.json with them; `--only /` did the same."""
    result, text, viya = command(*args)
    assert result.exit_code == 2 and _ended_by_itself(result), result.exception
    assert viya.calls == []
    value = "" if "" in args else args[1]
    assert text == (f'--only "{value}" names no path of the manifest: an empty value - what a variable of the shell '
                    "that is not set gives - or nothing but slashes. Nothing was deployed. Without --only every "
                    "entry is deployed.\n")


def test_a_path_that_is_named_is_still_taken_with_its_slashes(command):
    result, text, viya = command("--only", "/RAG/", "--dry-run")
    assert result.exit_code == 0 and "(only RAG):" in text and viya.wrote() == []


@pytest.mark.parametrize("words", [("content-deploy",), ("content-deploy", "--dry-run"), ("package-export",),
                                   ("builders-import",), ("builders-import", "--dry-run"), ("options-save",),
                                   ("options-restore",)])
def test_an_empty_content_root_is_refused_by_every_command_that_takes_one(monkeypatch, repo, tmp_path, words):
    """The same for --content-root: read as not given, an empty value is
    the content root of the release - a command typed for a test folder,
    `--content-root "$ROOT"` with the variable not set, read or wrote the
    folder a site works in."""
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repo))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("the command signed in"))
    monkeypatch.chdir(tmp_path)
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    for value in ("", "  "):
        out.seek(0)
        out.truncate()
        result = CliRunner().invoke(cli.app, [*words, "--content-root", value])
        assert result.exit_code == 2 and _ended_by_itself(result), result.exception
        assert out.getvalue() == ("--content-root is empty - what a variable of the shell that is not set gives. "
                                  "Nothing was done. Without --content-root the command takes the content root "
                                  "the manifest names.\n")
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


# ---- the command package-export prints for a test folder --------------------------------
@pytest.mark.parametrize("folder, there", [("/Team/A test [1]/root", False), ("/Team/Is there/root", True)])
def test_the_command_for_a_test_folder_runs_as_printed_for_a_folder_that_is_not_there(
        exporting, manifest, repo, monkeypatch, folder, there):
    """gate-20b: the line carried no --create. Run as printed for a test
    folder that is not there - the usual case - it ended with exit code 1
    and the sentence that says to pass --create."""
    from test_package_guards import REPORT
    elsewhere = repo / "scratch" / "r21.json"
    result, text, _, _ = exporting(package_of(manifest, change={REPORT: None}, root="root"), "--content-root",
                                   "/Users/me/My Folder/ZZ_VERIFY_R21/root", "--out", str(elsewhere),
                                   "--allow-differences")
    assert result.exit_code == 0, text
    printed, = re.findall(r"`(mdb builders-import [^`]*)`", text.rstrip().splitlines()[-1])
    assert printed.endswith(' --content-root "<folder>" --no-releases --no-reports --create --no-files')
    viya, out = Viya(), io.StringIO()
    if there:
        viya.make_folder(folder)
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setenv("SAS_VIYA_URL", "https://site.example")
    words = shlex.split(printed.replace("<folder>", folder))
    result = CliRunner().invoke(cli.app, words[1:])
    assert result.exit_code == 0 and _ended_by_itself(result), (result.exception, out.getvalue())
    assert "Release table: not reloaded (--no-releases)" in out.getvalue()
    assert ("created      folder" in out.getvalue()) and viya.tree(folder)
    assert not [path for method, path in viya.wrote() if path.startswith("/transfer")]
    assert not (repo / cli.CONTENT_RULES_FILE).exists()


# ---- the address of SAS Viya, and what the token address answers ------------------------
ADDRESS = ("which mdb cannot use: {}. Give the address of SAS Viya as https://<host>, or https://<host>:<port> "
           "where it listens on a port of its own. Nothing was asked of SAS Viya.")
NO_SCHEME = "it holds a colon, and no scheme (https://) stands in front of the host"
NO_PORT = "its port is no number from 1 to 65535"


@pytest.mark.parametrize("url, problem", [
    ("https://127.0.0.1:4983x", NO_PORT),             # gate-20b: asked at port 80, ConnectionError
    ("https://viya.example.com:99999", NO_PORT), ("https://viya.example.com:0", NO_PORT),
    ("https://viya.example.com:-1", NO_PORT), ("https://viya.example.com:8443:1", NO_PORT),
    ("viya.example.com:8443", NO_SCHEME),             # review-20b: POST viya.example.com://viya.example.com:8443/...
    ("localhost:8443", NO_SCHEME), ("[::1]:8443", NO_SCHEME), ("https:/viya.example.com", NO_SCHEME),
    ("viya.example.com/path:1", NO_SCHEME),
    ("https://[::1]:8443", "its host is an IPv6 address, which sasctl - what mdb signs in with - does not ask"),
    ("https://", "it names no host"), ("https://:8443", "it names no host"),
    ("ftp://viya.example.com", "its scheme is neither https nor http"),
    ("https://[::1", "it cannot be read as an address"),
])
def test_an_address_that_cannot_be_used_is_refused_before_any_request(env, fake_sasctl, monkeypatch, url, problem):
    """gate-20b and review-20b: a port that is no number was dropped and the
    default port asked; `host:8443` made sasctl ask `host://host:8443/...`.
    Both ended with a traceback. The address is judged before sasctl sees
    it, for a password and for a token."""
    env.setenv(s.URL_ENV, url)
    for credentials in ({s.USER_ENV: "u", s.PASSWORD_ENV: "p"}, {s.TOKEN_ENV: "not-a-jwt"}):
        for name in (s.USER_ENV, s.PASSWORD_ENV, s.TOKEN_ENV):
            env.delenv(name, raising=False)
        for name, value in credentials.items():
            env.setenv(name, value)
        with pytest.raises(s.ViyaConfigError) as caught:
            s.create_session()
        assert str(caught.value) == f"SAS_VIYA_URL is {url!r}, " + ADDRESS.format(problem)
    assert fake_sasctl == []
    # through a command: the one line, exit code 2
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=80, force_terminal=False, emoji=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPOSITORY))
    result = CliRunner().invoke(cli.app, ["content-deploy", "--dry-run"])
    assert result.exit_code == 2 and _ended_by_itself(result), result.exception
    assert out.getvalue() == f"SAS_VIYA_URL is {url!r}, " + ADDRESS.format(problem) + "\n"


@pytest.mark.parametrize("url, port", [
    ("https://viya.example.com", None), ("viya.example.com", None), ("https://viya.example.com:8443", 8443),
    ("https://viya.example.com:8443/", 8443), ("HTTPS://viya.example.com:443", 443), ("http://127.0.0.1:4983", 4983),
    ("https://viya.example.com:", None), ("https://viya.example.com/SASLogon", None), ("http://localhost:65535", 65535),
])
def test_an_address_that_can_be_used_reaches_sasctl_as_it_did(env, fake_sasctl, url, port):
    env.setenv(s.URL_ENV, url)
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    s.create_session()
    assert fake_sasctl == [((url,), {"token": "not-a-jwt", "verify_ssl": True, **({"port": port} if port else {})})]


def test_the_address_of_a_cli_profile_is_judged_the_same_way(env, tmp_path):
    from test_session import _cli_login
    _cli_login(tmp_path, endpoint="viya.example.com:8443", profile="Site")
    env.setenv(s.CLI_PROFILE_ENV, "Site")
    with pytest.raises(s.ViyaConfigError) as caught:
        s.resolve_auth()
    assert str(caught.value) == ("The sas-endpoint of the SAS Viya CLI profile Site is 'viya.example.com:8443', "
                                 + ADDRESS.format(NO_SCHEME))


@pytest.fixture
def token_address(env, monkeypatch):
    """The installed sasctl with the transport of requests replaced: each
    POST is answered with the next of `answers` - (status, body) -, the last
    one again and again. Skipped where sasctl is not installed."""
    sasctl = pytest.importorskip("sasctl")
    import requests

    state = SimpleNamespace(answers=[(401, "")], posted=[], waited=[])

    def post(self, url, **kwargs):
        status, body = state.answers[min(len(state.posted), len(state.answers) - 1)]
        state.posted.append(url)
        response = requests.Response()
        response.status_code, response.url = status, url
        response._content = body.encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    monkeypatch.setitem(sys.modules, "sasctl", sasctl)
    monkeypatch.setattr("time.sleep", state.waited.append)
    env.setenv(s.URL_ENV, "https://viya.example.com")
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "s3cret-p")
    env.setenv("SAS_VIYA_VERIFY_SSL", "false")
    return state


@pytest.mark.parametrize("status, body, said", [
    (500, '{"error":"server_error","error_description":"Something [/red]\\nbroke"}',
     'with error "server_error" and error_description "Something [/red] broke"'),
    (500, "<html>Internal Server Error</html>", "(no error_description could be read from the answer)"),
    (503, "", "(no error_description could be read from the answer)"),
    (400, '{"error":"invalid_request"}', 'with error "invalid_request"'),
    (403, '["no"]', "(no error_description could be read from the answer)"),
    (429, '{"error_description":"Too many requests"}', 'with error_description "Too many requests"'),
])
def test_another_status_of_the_token_address_ends_with_one_line(token_address, monkeypatch, status, body, said):
    """gate-20b and review-20b: a 500 of the token address - and a 400, 403,
    429 or 503 - ended the command with a traceback and exit code 1. It is
    one line and exit code 2 now; the sign-in is not repeated."""
    token_address.answers = [(status, body)]
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=80, force_terminal=False, emoji=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPOSITORY))
    result = CliRunner().invoke(cli.app, ["content-deploy", "--dry-run"])
    assert result.exit_code == 2 and _ended_by_itself(result), result.exception
    assert len(token_address.posted) == 1 and token_address.waited == []
    assert out.getvalue() == (
        "SAS Viya at https://viya.example.com did not answer the sign-in of u with SAS_VIYA_USER + "
        f"SAS_VIYA_PASSWORD (1 sign-in made) with a token: HTTP {status} from /SASLogon/oauth/token {said}. mdb "
        "reads only HTTP 401 as a refusal of the credentials, and asks no other answer again: run the command "
        "again, and where the answer stays the same, look at SAS Logon.\n")
    assert "s3cret-p" not in out.getvalue()


def test_a_500_behind_a_refusal_counts_both_sign_ins(token_address):
    token_address.answers = [(401, '{"error":"unauthorized","error_description":"Full authentication is required"}'),
                             (500, "")]
    with pytest.raises(s.ViyaSignInError, match=r"\(2 sign-ins made\) with a token: HTTP 500 from "):
        s.create_session()
    assert len(token_address.posted) == 2 and token_address.waited == [2]


@pytest.mark.parametrize("attempts", [None, "1", "7"])
def test_a_grant_the_client_may_not_use_is_asked_once(token_address, monkeypatch, attempts):
    """gate-20b and review-20b: `Unauthorized grant type: password` was asked
    three times, 7 seconds of waiting in all, although no repeat changes
    what a client may do. MDB_LOGIN_ATTEMPTS is as it was: it sets how often
    a refusal that can change is asked."""
    if attempts is None:
        monkeypatch.delenv(s.LOGIN_ATTEMPTS_ENV, raising=False)
    else:
        monkeypatch.setenv(s.LOGIN_ATTEMPTS_ENV, attempts)
    token_address.answers = [(401, '{"error":"invalid_client","error_description":"Unauthorized grant type: password"}')]
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert len(token_address.posted) == 1 and token_address.waited == []
    assert "(1 sign-in made): HTTP 401 from /SASLogon/oauth/token with error \"invalid_client\" and " \
           "error_description \"Unauthorized grant type: password\". Check the credentials" in str(caught.value)


@pytest.mark.parametrize("attempts, posted", [(None, 3), ("1", 1), ("5", 5), ("0", 1), ("many", 3)])
def test_a_refusal_that_can_change_is_asked_as_often_as_before(token_address, monkeypatch, attempts, posted):
    if attempts is None:
        monkeypatch.delenv(s.LOGIN_ATTEMPTS_ENV, raising=False)
    else:
        monkeypatch.setenv(s.LOGIN_ATTEMPTS_ENV, attempts)
    token_address.answers = [(401, '{"error":"unauthorized","error_description":"Full authentication is required"}')]
    with pytest.raises(s.ViyaSignInError, match=rf"\({posted} sign-ins? made\): HTTP 401 from "):
        s.create_session()
    assert len(token_address.posted) == posted


# ---- the credentials commands: what the side stream of 2026-10-13 left open -----------------
CREDENTIAL_COMMANDS = ("credentials-init", "credentials-add", "credentials-remove", "credentials-apply",
                       "credentials-report")
COMMENT = tuple("  " + line for line in credentials.PLACEHOLDER_COMMENT)


def test_the_five_credentials_commands_name_the_manifest_with_one_option(mdb, tmp_path):
    """`credentials-init` took its file as --file / -f, the four other
    commands as --manifest / -m. All five take --manifest / -m; --file / -f
    is still read by `credentials-init`, where it was the name."""
    for command in CREDENTIAL_COMMANDS:
        result = CliRunner().invoke(cli.app, [command, "--help"])
        shown = " ".join(result.output.replace("│", " ").split())           # without the frame of the help
        assert result.exit_code == 0 and "--manifest" in shown and re.search(r"(?<![\w-])-m\b", shown), command
        assert ("--file" in shown) == (command == "credentials-init"), command
        assert ("--file / -f is the earlier name of this option and is still read" in shown) \
            == (command == "credentials-init")
    for number, option in enumerate(("--manifest", "-m", "--file", "-f")):
        target = tmp_path / f"records {number}" / "c.yaml"
        code, said = mdb("credentials-init", option, str(target))
        assert code == 0 and target.is_file(), said
        # the two commands the line names take the file as it is printed: in quotes, for its blank
        named = cli._shell_word(target)
        assert named.startswith('"') and f"mdb credentials-add group <id> --manifest {named} " in said
        assert f"mdb credentials-apply --manifest {named} --dry-run" in said
    assert not mdb.path.exists()


def test_a_command_the_credentials_commands_print_runs_as_printed_for_a_path_with_a_blank(mdb, tmp_path):
    """`Next: mdb credentials-apply --manifest my records/c.yaml --dry-run`
    is two arguments where one was meant. The file is named as a shell
    takes it - by the function that spells every other printed command."""
    target = tmp_path / "my records" / "team creds.yaml"
    code, said = mdb("credentials-add", "group", "LLMConsumers", "--manifest", str(target))
    assert code == 0
    printed, = re.findall(r"^Next: (mdb credentials-apply .*)$", said, re.M)
    assert shlex.split(printed) == ["mdb", "credentials-apply", "--manifest", target.as_posix(), "--dry-run"]
    code, said = mdb(*shlex.split(printed)[1:])
    assert code == 0 and "Dry run" in said
    code, said = mdb("credentials-remove", "group", "LLMConsumers", "--manifest", str(target))
    assert code == 0
    for printed in re.findall(r"(mdb credentials-apply --manifest .*?)(?: --dry-run|;)", said):
        assert shlex.split(printed)[-1] == target.as_posix(), printed
    # the head of the manifest names the file the same way
    head = target.read_text(encoding="utf-8").split("domain:")[0]
    assert head.count('--manifest "team creds.yaml"') == 5 and "--manifest team creds.yaml" not in head
    # and a name without a blank stands as it stood
    mdb("credentials-init")
    assert mdb.path.read_text(encoding="utf-8").count("--manifest credentials.yaml") == 5


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_the_comment_about_the_placeholder_leaves_with_the_placeholder(mdb, eol):
    """After the first `credentials-add` on a manifest of `credentials-init`
    the line `The first mdb credentials-add replaces this placeholder.` stood
    above a real identity. It leaves with the placeholder - and only it."""
    assert mdb("credentials-init")[0] == 0
    before = mdb.path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", eol.encode())
    mdb.path.write_bytes(before)
    comment = "".join(line + eol for line in COMMENT).encode()
    assert before.count(comment) == 1
    assert mdb("credentials-add", "group", "LLMConsumers", "--only", "OpenAI")[0] == 0
    after = mdb.path.read_bytes()
    assert after == before.replace(comment, b"").replace(
        f"    id: {credentials.PLACEHOLDER_ID}{eol}".encode(),
        f"    id: LLMConsumers{eol}    only: [OpenAI]{eol}".encode())
    assert b"replaces this placeholder" not in after and credentials.PLACEHOLDER_ID.encode() not in after
    assert after.count(b"# Narrow an identity to some of the entries:") == 1
    # the head of the file says so, where it says that the two commands leave the comments alone
    assert b"leave its comments alone, but for the one" + eol.encode() + \
        b"# about the placeholder, which leaves with the placeholder:" in after


@pytest.mark.parametrize("above", [
    ("  # Replace with the groups and users this deployment should equip.",),      # one of the two lines
    ("  # Our own note about who is equipped here.", COMMENT[1]),                   # a line its author changed
    (COMMENT[0], COMMENT[1], ""),                                                   # not right above the identity
    (),
])
def test_a_comment_that_is_not_the_scaffolds_own_stays_where_it_is(tmp_path, above):
    text = "\n".join(["domain: d", "source: .env", "identities:", *above, "  - type: group",
                      f"    id: {credentials.PLACEHOLDER_ID}", ""])
    edit = credentials.edit_identity(text, tmp_path / "c.yaml", "group", "LLMConsumers")
    assert edit.outcome == "placeholder"
    assert edit.text == text.replace(credentials.PLACEHOLDER_ID, "LLMConsumers")


ONE_PER_LINE = ("domain: d\nsource: .env\nidentities:\n  - type: group\n    id: RAGEngineers\n    only:\n"
                "      # the stores\n      - PGVECTOR_RAG_USER{}\n      - PGVECTOR_RAG_PW{}\n")
IN_BRACKETS = ("domain: d\nsource: .env\nidentities:\n  - type: group\n    id: RAGEngineers\n"
               "    only: [PGVECTOR_RAG_USER,{}\n           PGVECTOR_RAG_PW]{}\n")


@pytest.mark.parametrize("text", [
    ONE_PER_LINE.format("  # the user of the store", ""), ONE_PER_LINE.format("\n      # and its password", ""),
    ONE_PER_LINE.format("", "  # rotated in March"), IN_BRACKETS.format("  # the user of the store", ""),
])
def test_an_only_list_with_a_comment_between_its_entries_is_not_rewritten(mdb, text):
    """A list written one entry per line was rewritten whole, and a comment
    between its entries was gone without a word. Nothing says which entry
    of the new list such a comment belongs to, so the edit is refused, like
    the other layouts it does not handle - and the file is as it was."""
    mdb.path.write_text(text, encoding="utf-8")
    code, said = mdb("credentials-add", "group", "RAGEngineers", "--only", "PGVECTOR_RAG_PW,OpenAI")
    assert code == 2 and mdb.path.read_text(encoding="utf-8") == text
    assert " ".join(said.split()) == (
        "credentials.yaml: the `only:` list of group 'RAGEngineers' holds a comment, which a rewrite of the list "
        "would drop or leave beside another entry — nothing was written; edit the list by hand")
    # what is not about the list is still done, and leaves the list alone
    code, said = mdb("credentials-remove", "group", "RAGEngineers")
    assert code == 0 and mdb.path.read_text(encoding="utf-8") == text + "    state: absent\n"


@pytest.mark.parametrize("text, rewritten", [
    # a comment in front of the first entry, and one behind a list in brackets, are about the list: they stay
    (ONE_PER_LINE.format("", ""),
     ONE_PER_LINE.format("", "").replace("      - PGVECTOR_RAG_USER\n      - PGVECTOR_RAG_PW\n",
                                         "      - PGVECTOR_RAG_PW\n      - OpenAI\n")),
    (IN_BRACKETS.format("", "  # the store"),
     IN_BRACKETS.format("", "  # the store").replace("[PGVECTOR_RAG_USER,\n           PGVECTOR_RAG_PW]",
                                                     "[PGVECTOR_RAG_PW, OpenAI]")),
    ("domain: d\nsource: .env\nidentities:\n  - {type: group, id: RAGEngineers, only: ['a#b', \"c # d\"]}\n",
     "domain: d\nsource: .env\nidentities:\n  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_PW, OpenAI]}\n"),
])
def test_an_only_list_without_such_a_comment_is_rewritten_as_before(mdb, text, rewritten):
    mdb.path.write_text(text, encoding="utf-8")
    code, said = mdb("credentials-add", "group", "RAGEngineers", "--only", "PGVECTOR_RAG_PW,OpenAI")
    assert code == 0 and mdb.path.read_text(encoding="utf-8") == rewritten, said


def test_a_new_manifest_of_credentials_add_takes_the_domain_it_is_given(mdb, tmp_path):
    """`credentials-init` has --domain; a `credentials-add` that writes the
    manifest wrote it for agentic-ai-keys and took no other."""
    code, said = mdb("credentials-add", "group", "ZZ_GROUP", "--domain", "zz_made_up_keys")
    assert code == 0 and credentials.Manifest.load(mdb.path).domain == "zz_made_up_keys"
    code, said = mdb("credentials-apply", "--dry-run")
    assert code == 0 and "Credential domain 'zz_made_up_keys'" in said
    # an existing manifest names its own domain: said, and the file keeps it
    before = mdb.path.read_bytes()
    code, said = mdb("credentials-add", "user", "ada", "--domain", "another")
    assert code == 0 and "--domain is read only when the manifest is created; credentials.yaml exists and names " \
                         "its own domain." in " ".join(said.split())
    assert credentials.Manifest.load(mdb.path).domain == "zz_made_up_keys"
    assert mdb.path.read_bytes().startswith(before.split(b"identities:")[0])
    # without the option: the domain of the accelerator, as before
    other = tmp_path / "other.yaml"
    assert mdb("credentials-add", "group", "G", "--manifest", str(other))[0] == 0
    assert credentials.Manifest.load(other).domain == "agentic-ai-keys"


@pytest.mark.parametrize("words", [
    ("credentials-init",), ("credentials-add", "group", "ZZ_GROUP"), ("credentials-apply", "--identity", "ZZ_GROUP"),
    ("credentials-apply", "--identity", "ZZ_GROUP", "--dry-run"), ("credentials-report", "--identity", "ZZ_GROUP"),
])
def test_an_empty_domain_is_refused_and_does_not_become_the_domain_of_the_accelerator(mdb, monkeypatch, words):
    """`--domain "$DOMAIN"` with the variable not set: `credentials-init` and
    `credentials-add` wrote a manifest that names no domain, which is read as
    one for agentic-ai-keys - the next apply went to the domain a site works
    with. An empty --domain ends each of the four commands that take it."""
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("the command signed in"))
    for value in ("", " "):
        out = io.StringIO()
        monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
        result = CliRunner().invoke(cli.app, [*words, "--domain", value])
        assert result.exit_code == 2 and _ended_by_itself(result), result.exception
        assert out.getvalue() == ("--domain is empty - what a variable of the shell that is not set gives - and "
                                  "names no credential domain. Nothing was done.\n")
    assert not mdb.path.exists() and mdb.site.calls == []


@pytest.mark.parametrize("readme", ["Model-Definition-Builder/README.md", "SAS-Viya-Integrations/README.md"])
def test_a_readme_that_names_the_credentials_commands_names_all_five(readme):
    """The two READMEs named `mdb credentials-apply` or none of the
    commands; `credentials-add`, `credentials-remove` and `state: absent`
    were in the changelog, the guide and the help alone."""
    text = (REPOSITORY / readme).read_text(encoding="utf-8")
    for command in CREDENTIAL_COMMANDS:
        assert f"`mdb {command}`" in text, command
    assert "`state: absent`" in text


# ---- the modeler of a definition that ships -------------------------------------------------
def test_no_definition_of_the_repository_names_a_person_as_its_modeler():
    """gate-20b: five decision definitions named the user id of the person
    who ran `mdb add` as their modeler - `mdb add` takes the modeler from
    SAS_RESPONSIBLE_PARTY and, where that is not set, from the user of the
    machine. Every definition that ships names the accelerator, in its
    definition.yaml and in the modelConfiguration.json generated from it.
    The value is one of the model attributes mdb registers; the LLM and
    embedding definitions have carried it since they were written."""
    import json

    import yaml
    neutral, seen = "SAS Agentic AI Accelerator", {"definition.yaml": 0, "modelConfiguration.json": 0}
    for kind in ("LLM-Definitions", "Embedding-Definitions", "Decision-Definitions"):
        # every folder, the base definition a hand-written one starts from included
        for folder in sorted(path for path in (REPOSITORY / kind).iterdir() if path.is_dir()):
            for name, load in (("definition.yaml", yaml.safe_load), ("modelConfiguration.json", json.loads)):
                if (folder / name).is_file():
                    seen[name] += 1
                    assert load((folder / name).read_text(encoding="utf-8"))["modeler"] == neutral, folder / name
    assert seen["definition.yaml"] >= 51 and seen["modelConfiguration.json"] >= 53, seen


# ---- what the second pass of 2026-10-13 could not run, and a test can ------------------------
@pytest.mark.parametrize("width", [40, 80, 132])
def test_the_line_of_a_refused_update_is_one_line_at_the_width_of_a_terminal(command, monkeypatch, width):
    """The pass ran mdb at 2,000 columns, so it did not show what the line
    of an HTTP 412 looks like in a terminal. And the answer of that pass
    held no bracket and no line break; this one holds both."""
    _, _, viya = command()
    core, = viya.at(f"{ROOT}/RAG/rag_core/__init__.py")
    viya.files[core] = b"CHANGED = 1\n"
    said = ('The values for the request header field "If-Match" ("mv75s7d6") and the resource\'s entity tag '
            '("mv75trhn") are not the same [/red] [see\nthe log].')
    monkeypatch.setattr(viya, "put", lambda path, data=None, headers=None, **kw: Response(
        412, {"message": said, "remediation": "Get the latest representation of the resource.", "errorCode": 0}))
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=width, force_terminal=False, emoji=False))
    result = CliRunner().invoke(cli.app, ["content-deploy"])
    assert result.exit_code == 1 and _ended_by_itself(result), result.exception
    line, = [line for line in out.getvalue().splitlines() if line.lstrip().startswith("FAILED")]
    assert len(line) > width and line == (
        "  FAILED       file          RAG/rag_core/__init__.py  (the file was left as it is: the update of its "
        "content was refused (HTTP 412 " + " ".join(said.split()) + " - Get the latest representation of the "
        "resource. (errorCode 0)))")
    assert viya.files[core] == b"CHANGED = 1\n"


class _TransferService:
    """A session that answers what `mdb package-export` asks, and keeps every request."""

    def __init__(self, package: dict, root_id: str):
        self.package, self.root_id, self.asked = package, root_id, []
        self.job = {"id": "j1", "state": "completed", "packageUri": "/transfer/packages/p1",
                    "links": [{"rel": "self", "href": "/transfer/exportJobs/j1"}]}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, path, params=None, headers=None, **kw):
        self.asked.append(("GET", path))
        return {"/folders/folders/@item": Response(200, {"id": self.root_id}),
                "/transfer/exportJobs/j1": Response(200, self.job),
                "/transfer/packages/p1": Response(200, self.package)}.get(path, Response(404, {"message": "no"}))

    def post(self, path, data=None, headers=None, **kw):
        self.asked.append(("POST", path))
        return Response(201, self.job)

    def delete(self, path, **kw):
        self.asked.append(("DELETE", path))
        return Response(204)


def test_package_export_asks_the_transfer_service_for_an_export_and_nothing_else(monkeypatch, manifest, repo):
    """The report of the pass said `no request to /transfer/importJobs in any
    log`, which its checker could not confirm for the scripts around mdb.
    What mdb itself sends for an export, request by request: no import job,
    no upload of a package - and the package and the job record are deleted."""
    built = package_of(manifest)
    service = _TransferService(built.build(), built.root_uri.rsplit("/", 1)[-1])
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repo))
    monkeypatch.setattr(cli, "_viya_session", lambda: service)
    result = CliRunner().invoke(cli.app, ["package-export"])
    assert result.exit_code == 0 and _ended_by_itself(result), (result.exception, out.getvalue())
    assert service.asked == [("GET", "/folders/folders/@item"), ("POST", "/transfer/exportJobs"),
                             ("GET", "/transfer/exportJobs/j1"), ("GET", "/transfer/packages/p1"),
                             ("DELETE", "/transfer/packages/p1"), ("DELETE", "/transfer/exportJobs/j1")]


def test_content_deploy_does_not_ask_the_transfer_service(command):
    _, _, viya = command()
    assert viya.wrote() and not [path for _method, path, _params in viya.calls if "/transfer" in path]


def test_an_import_without_reports_does_not_ask_the_transfer_service(exporting, manifest, repo, monkeypatch):
    """`--no-reports`: "the transfer service is not called at all" - held
    here for every request, the GET requests too."""
    elsewhere = repo / "scratch" / "r21.json"
    result, text, _, _ = exporting(package_of(manifest), "--out", str(elsewhere))
    assert result.exit_code == 0, text
    other, out = Viya(), io.StringIO()
    monkeypatch.setattr(cli, "_viya_session", lambda: other)
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setenv("SAS_VIYA_URL", "https://site.example")
    result = CliRunner().invoke(cli.app, ["builders-import", str(elsewhere), "--no-reports", "--no-releases",
                                          "--no-files"])
    assert result.exit_code == 0 and _ended_by_itself(result), (result.exception, out.getvalue())
    assert other.wrote() and not [path for _method, path, _params in other.calls if "/transfer" in path]


# ---- a command a line names: with the file, the folder and the domain of the run ---------------
def _named_command(text: str, command: str) -> list:
    """The words of the one `mdb <command> ...` a text names between backticks."""
    printed, = re.findall(rf"`(mdb {command}[^`]*)`", " ".join(text.split()))
    return shlex.split(printed)


def test_the_restore_that_options_save_names_reads_the_file_and_the_root_of_the_save(importing, no_discovery, site,
                                                                                    out, tmp_path):
    """Found in the review of this round: the line of `mdb options-save
    --file <file> --content-root <root>` named `mdb options-restore`, which
    reads builder-options.json and the content root of the release - another
    file, and the report of another folder."""
    no_discovery()
    fake = site(Site(**{"Prompt Builder": TUNED}))
    kept, root = tmp_path / "my records" / "options of the site.json", "/Team/A root"
    result = CliRunner().invoke(cli.app, ["options-save", "--reports-only", "--file", str(kept), "--content-root",
                                          root])
    assert result.exit_code == 0 and kept.is_file(), out.getvalue()
    words = _named_command(out.getvalue(), "options-restore")
    assert words == ["mdb", "options-restore", "--file", kept.as_posix(), "--content-root", root]
    del fake.looked_in[:]
    result = CliRunner().invoke(cli.app, [*words[1:], "--dry-run"])
    assert result.exit_code == 0 and f"{root}/Prompt Builder/Prompt Builder" in fake.looked_in, out.getvalue()
    # without the two options the command stands alone, as it did
    out.seek(0)
    out.truncate()
    assert CliRunner().invoke(cli.app, ["options-save", "--reports-only"]).exit_code == 0
    assert _named_command(out.getvalue(), "options-restore") == ["mdb", "options-restore"]
    # and a file that is not there is to be saved under its name, for its root
    (tmp_path / "a package.json").write_text("{}", encoding="utf-8")
    for command in (["options-restore", "--file", "not there.json", "--content-root", root],
                    ["builders-import", "a package.json", "--options", "not there.json", "--content-root", root]):
        out.seek(0)
        out.truncate()
        assert CliRunner().invoke(cli.app, command).exit_code in (1, 2)
        assert _named_command(out.getvalue(), "options-save") == ["mdb", "options-save", "--file", "not there.json",
                                                                  "--content-root", root]


def test_the_deploy_that_package_export_names_goes_to_the_folder_that_was_exported(exporting, manifest, repo,
                                                                                  monkeypatch):
    """The same for an export that differs from the checkout: `Run mdb
    content-deploy to bring the folder to the checkout` named a command that
    deploys into the content root of the release, whatever folder was
    exported."""
    from test_package_guards import REPORT
    root = "/Users/me/My Folder/ZZ_VERIFY_R21/root"
    built = package_of(manifest, change={REPORT: None}, root="root")
    result, text, _, _ = exporting(built, "--content-root", root, "--out", str(repo / "scratch" / "r21.json"))
    assert result.exit_code == 1, text
    words = _named_command(text, "content-deploy")
    assert words == ["mdb", "content-deploy", "--content-root", root]
    # the content root of the release needs no option
    result, text, _, _ = exporting(package_of(manifest, change={REPORT: None}))
    assert result.exit_code == 1 and _named_command(text, "content-deploy") == ["mdb", "content-deploy"]
    # as printed, for the folder that was exported
    viya, out = Viya(), io.StringIO()
    viya.make_folder(root)
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    result = CliRunner().invoke(cli.app, words[1:])
    assert result.exit_code == 0 and viya.tree(root) and viya.folder_id(ROOT) is None, out.getvalue()


def test_the_commands_the_credentials_commands_name_take_the_manifest_of_the_run(mdb, tmp_path):
    """And for the credentials: after `mdb credentials-apply --manifest
    <file>` the line said `Check it with mdb credentials-report`, which
    reads ./credentials.yaml - another manifest, or none."""
    target = tmp_path / "my records" / "c.yaml"
    assert mdb("credentials-add", "group", "LLMConsumers", "--manifest", str(target))[0] == 0
    code, said = mdb("credentials-apply", "--manifest", str(target))
    assert code == 0
    words = _named_command(said, "credentials-report")
    assert words == ["mdb", "credentials-report", "--manifest", target.as_posix()]
    code, said = mdb(*words[1:])
    assert code == 0 and "1 of 1 named identities hold a credential." in said
    # a credential the manifest marks absent and that is still there: the apply of that manifest
    assert mdb("credentials-remove", "group", "LLMConsumers", "--manifest", str(target))[0] == 0
    code, said = mdb("credentials-report", "--manifest", str(target))
    assert f'Run mdb credentials-apply --manifest "{target.as_posix()}" to remove them.' in " ".join(said.split())
    # the identities of a run without a manifest, in their domain
    code, said = mdb("credentials-apply", "--identity", "ada", "--identity", "bo b", "--identity-type", "user",
                     "--domain", "zz_made_up_keys")
    assert code == 0, said
    words = _named_command(said, "credentials-report")
    assert words == ["mdb", "credentials-report", "--identity", "ada", "--identity", "bo b", "--identity-type",
                     "user", "--domain", "zz_made_up_keys"]
    code, said = mdb(*words[1:])
    assert code == 0 and "Credential domain 'zz_made_up_keys'" in said and "2 of 2 named identities" in said
    # the default manifest needs no option
    assert mdb("credentials-add", "group", "LLMConsumers")[0] == 0
    code, said = mdb("credentials-apply")
    assert code == 0 and _named_command(said, "credentials-report") == ["mdb", "credentials-report"]
    # no manifest where one was named: the two commands that write one write it there
    missing = tmp_path / "not there" / "c.yaml"
    code, said = mdb("credentials-apply", "--manifest", str(missing))
    flat = " ".join(said.split())
    assert code == 2 and f'mdb credentials-init --manifest "{missing.as_posix()}" or' in flat
    assert f'mdb credentials-add <type> <id> --manifest "{missing.as_posix()}", or' in flat
    code, said = mdb("credentials-remove", "group", "G", "--manifest", str(missing))
    assert code == 2 and f'mdb credentials-init --manifest "{missing.as_posix()}" first.' in " ".join(said.split())
    code, said = mdb("credentials-remove", "group", "G", "--manifest", "credentials.yaml")
    mdb.path.unlink()
    code, said = mdb("credentials-remove", "group", "G")
    assert code == 2 and "write one with mdb credentials-init first." in " ".join(said.split())


def test_no_path_of_the_manifest_holds_a_character_double_quotes_do_not_keep():
    """The hints of a FAILED line spell the path of an entry in double quotes - `--only "<path>"` -, which
    PowerShell and bash read as it stands unless the path holds a character double quotes do not keep. `--only`
    takes a path of this manifest and nothing else, so the hints are right while no path of it holds one."""
    paths = [entry.path for entry in content.load_manifest(REPOSITORY).all_entries()]
    assert len(paths) == 72 and not [path for path in paths if cli._NOT_KEPT_IN_DOUBLE_QUOTES.intersection(path)]
