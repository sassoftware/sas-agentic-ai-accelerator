# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a text loses before mdb prints it. The console of the command line
(cli.py) hands every text through `printable`, and so does every other
print of mdb and of the scripts of its skills - a module that does not know
rich, the audit script of the model-audit skill. Imports nothing of mdb."""
from __future__ import annotations

import re
import traceback

#: The control characters a terminal may act on that rich leaves in a text: every C0 character but the tab and
#: the line end, DEL and the C1 range. rich itself takes out the carriage return, the bell, the backspace, the
#: vertical tab and the form feed; an ESC, the C1 CSI byte or a NUL of an answer of a server or of a model went
#: to the terminal, which acted on it (gate-21a N1, review-21a).
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def printable(text) -> str:
    """A text - or what `str` makes of a value - without the control
    characters of `CONTROL`."""
    return CONTROL.sub("", str(text))


def traceback_text(exc: BaseException) -> str:
    """The traceback Python prints for `exc`, with the text of every
    exception in it - of `exc`, of its cause and context, of the members of
    a group, the message of a SyntaxError - on the one line that names the
    exception. That text can be an
    answer of SAS Viya (sasctl raises its body as an HTTPError), and a line
    break in it split that line (live-23b B2): every run of blanks, tabs and
    line breaks in it becomes one blank, as in every other line of mdb that
    shows an answer (viya/answer.py, one_line). The frames are Python's own
    lines and stay as they are."""
    shown = traceback.TracebackException.from_exception(exc, compact=True)     # as traceback.format_exception
    todo, seen = [shown], set()
    while todo:
        each = todo.pop()
        if each is None or id(each) in seen:
            continue
        seen.add(id(each))
        # _str is the text the line of an exception shows, msg that of a SyntaxError (Python 3.10 to 3.14)
        each._str = " ".join(str(each._str).split())
        if isinstance(getattr(each, "msg", None), str):
            each.msg = " ".join(each.msg.split())
        todo += [each.__cause__, each.__context__, *(getattr(each, "exceptions", None) or ())]
    return "".join(shown.format())
