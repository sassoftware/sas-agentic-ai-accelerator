# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a text loses before mdb prints it. The console of the command line
(cli.py) hands every text through `printable`, and so does every other
print of mdb and of the scripts of its skills - a module that does not know
rich, the audit script of the model-audit skill. Imports nothing of mdb."""
from __future__ import annotations

import re

#: The control characters a terminal may act on that rich leaves in a text: every C0 character but the tab and
#: the line end, DEL and the C1 range. rich itself takes out the carriage return, the bell, the backspace, the
#: vertical tab and the form feed; an ESC, the C1 CSI byte or a NUL of an answer of a server or of a model went
#: to the terminal, which acted on it (gate-21a N1, review-21a).
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def printable(text) -> str:
    """A text - or what `str` makes of a value - without the control
    characters of `CONTROL`."""
    return CONTROL.sub("", str(text))
