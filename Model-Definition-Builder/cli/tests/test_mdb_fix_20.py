# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate and the review of the round after the pass of 2026-10-13
found in mdb, each as a test that fails on the code before it. Every service
is a fake (content_fakes, the fakes of test_builder_commands) or a made-up
answer: nothing here says how SAS Viya answers.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_fakes import Response
from mdb.viya import answer as answering
from mdb.viya import content as deploying


# ---- the answer of a refused request: one function, one line ----------------------
def test_an_answer_is_one_line_and_an_object_is_shown_as_json():
    """gate-20: a message with a line break made the FAILED line two lines,
    and a message that is an object was printed as a Python dict."""
    assert deploying._answer is answering.answer
    broken = {"message": "line one\nline two", "remediation": "do\tthis", "errorCode": 0}
    assert answering.answer(Response(412, broken)) == "HTTP 412 line one line two - do this (errorCode 0)"
    assert answering.answer(Response(500, content=b"first\r\nsecond\n\nthird")) == "HTTP 500 first second third"
    assert answering.answer(Response(400, {"message": {"a": 1}})) == 'HTTP 400 {"a": 1}'
    assert answering.answer(Response(400, {"message": "m", "errorCode": {"code": 7}})) == \
        'HTTP 400 m (errorCode {"code": 7})'
    # a body printed over several lines is still read as the JSON error it is
    pretty = json.dumps({"message": "refused", "remediation": "ask"}, indent=2)
    assert "\n" in pretty and answering.answer(SimpleNamespace(status_code=403, text=pretty)) == \
        "HTTP 403 refused - ask"
    # something that has no text at all
    assert answering.answer(SimpleNamespace(status_code=502)) == "HTTP 502"
