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
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text

from mdb import cli

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
