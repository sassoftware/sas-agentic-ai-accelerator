# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a refused request answered, for one line of a message.

One function for every module that names the answer of SAS Viya in an error:
the deploy (content.py), the transfer service, the reports, CAS and the model
repository. Imports nothing of mdb and no HTTP library - a response is
anything with `status_code` and `text`.
"""
from __future__ import annotations

import json

#: How much of an answer that is no JSON error body a line shows.
ANSWER_LIMIT = 300


def one_line(value) -> str:
    """A value of an answer as text on one line: a text as it is, anything
    else (an object, a list, a number) as JSON; every run of blanks, tabs and
    line breaks becomes one blank."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return " ".join(text.split())


def answer(response) -> str:
    """What a request that was refused answered, on one line: `HTTP <status>`
    and, of a JSON error body, the fields a reader needs - `message`,
    `remediation` and `errorCode` - each whole. A body of any other shape is
    shown whole up to ANSWER_LIMIT characters; a longer one ends at the last
    whole word in front of that, with `...`."""
    text = one_line(str(getattr(response, "text", "") or ""))
    try:
        body = json.loads(text) if text.startswith("{") else None
    except ValueError:
        body = None
    if isinstance(body, dict) and (body.get("message") or body.get("remediation")):
        shown = " - ".join(one_line(body[key]) for key in ("message", "remediation") if body.get(key))
        if body.get("errorCode") is not None:
            shown += f" (errorCode {one_line(body['errorCode'])})"
    elif len(text) > ANSWER_LIMIT:
        head = text[:ANSWER_LIMIT]
        if not (text[ANSWER_LIMIT].isspace() or head[-1].isspace()) and len(head.split()) > 1:
            head = head.rsplit(None, 1)[0]                     # the cut fell into a word: end in front of it
        shown = head.rstrip() + "..."
    else:
        shown = text
    return f"HTTP {response.status_code}" + (f" {shown}" if shown else "")
