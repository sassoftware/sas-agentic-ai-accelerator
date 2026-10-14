# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate and the review of round 21 found in mdb at 844b922, each
with the input that showed it. The service is a fake in memory and the token
address a stand-in for the transport of requests: no test signs in anywhere,
and every key is made up.

The tests of the first part run `mdb` in a process of its own, with the
typer this interpreter has: a traceback is printed by typer only there. Run
them with the lowest typer pyproject.toml allows (0.12.0) as well - the one
that prints the locals of every frame unless it is told not to."""
import ast
import base64
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mdb import cli
from mdb.viya import credentials
from mdb.viya import session as s
from test_credentials_commands import _Response, mdb  # noqa: F401

SOURCE = Path(cli.__file__).resolve().parent
REPOSITORY = Path(__file__).resolve().parents[3]

#: Made up, and distinctive enough that finding one in an output is a leak - in plain text and in base64.
OPENAI, PGPW = "sk-made-up-OPENAIVALUE", "pgpw-made-up-SECRET"
LEAKS = (OPENAI, PGPW, *(base64.b64encode(value.encode()).decode() for value in (OPENAI, PGPW)))

#: Runs mdb's credentials commands against a service whose requests raise where MODE says.
RUN = r'''
import os, sys
import requests
from mdb import cli
from mdb.viya import credentials

os.chdir(sys.argv[1])
MODE = sys.argv[2]


class Answer:
    def __init__(self, status):
        self.status_code, self.text = status, ""

    def json(self):
        raise ValueError("no body")


class Site:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, path, **_):
        if MODE == "get":
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        return Answer(404)

    def put(self, path, data=None, headers=None):
        if MODE == "put" and "/groups/" in path:
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)")
        return Answer(200)

    def delete(self, path, **_):
        if MODE == "delete":
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        return Answer(204)


cli._viya_session = lambda: Site()
if MODE == "uncaught":
    # a defect of mdb inside the frame that holds the values: nothing catches it
    def encode(entries):
        raise RuntimeError("a defect of mdb")
    credentials.encode = encode
sys.argv = ["mdb", *sys.argv[3:]]
cli.main()
'''


def _run(tmp_path: Path, mode: str, *args: str):
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={OPENAI}\nPGVECTOR_RAG_USER=u\nPGVECTOR_RAG_PW={PGPW}\n",
                                   encoding="utf-8")
    (tmp_path / "credentials.yaml").write_text(
        "source: .env\nidentities:\n  - {type: group, id: G}\n  - {type: user, id: gone, state: absent}\n",
        encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE.parent), os.environ.get("PYTHONPATH", "")]),
                       COLUMNS="200", PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, str(script), str(tmp_path), mode, *args], capture_output=True,
                          text=True, encoding="utf-8", env=environment, timeout=120)
    return done.returncode, done.stdout, done.stderr


# ---- M1: a request of the credentials commands that raises -----------------------------------
def test_no_typer_app_of_mdb_prints_the_locals_of_a_traceback():
    """review-21b (medium): typer 0.12.0, which pyproject.toml allows, prints
    the locals of every frame of a traceback by default - those of
    put_credential hold every value in plain text and in base64."""
    import typer
    apps = [value for value in vars(cli).values() if isinstance(value, typer.Typer)]
    assert len(apps) == 2
    assert all(app.pretty_exceptions_show_locals is False for app in apps)


def test_a_defect_in_the_frame_that_holds_the_values_prints_none_of_them(tmp_path):
    """The traceback of an exception nothing catches, under the typer of this
    interpreter: the frames of put_credential and encode hold the values."""
    code, out, err = _run(tmp_path, "uncaught", "credentials-apply")
    assert code == 1 and "RuntimeError" in err and "a defect of mdb" in err, (out, err)
    assert not [leak for leak in LEAKS if leak in out + err], (out, err)


@pytest.mark.parametrize("mode, args, code, line", [
    ("put", ["credentials-apply"], 1, "storing the credential for group 'G' failed: no answer (ReadTimeout)"),
    ("delete", ["credentials-apply"], 1, "removing the credential for user 'gone' failed: no answer (ConnectionError)"),
    ("get", ["credentials-apply"], 2, "reading the credential for group 'G' failed: no answer (ConnectionError)"),
    ("get", ["credentials-report"], 0, "reading the credential for group 'G' failed: no answer (ConnectionError)"),
])
def test_a_request_that_raises_ends_with_one_line_that_names_no_value(tmp_path, mode, args, code, line):
    """review-21b: a connection reset or a timeout of a request of
    credentials-apply ended in a traceback - with typer 0.12.0 one that
    printed every value the write carried."""
    done, out, err = _run(tmp_path, mode, *args)
    assert done == code and line in out and "Traceback" not in out + err, (out, err)
    assert not [leak for leak in LEAKS if leak in out + err], (out, err)


# ---- M2: the guard of a refused write compares as answer() prints ----------------------------
class _Answer:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text


@pytest.mark.parametrize("value, text", [
    ("pg  two  blanks", json.dumps({"message": "bad value pg  two  blanks", "errorCode": 1})),
    ("pg\ttab\nline", json.dumps({"message": "bad value pg\ttab\nline here", "errorCode": 1})),
    # a body that is no JSON is ended at 300 characters, in front of the word the cut falls into: here behind
    # `pg cut`, the first two words of the value
    ("pg cut " + "x" * 30, "y" * 285 + " pg cut " + "x" * 30 + " and more"),
    ("pg cut " + "x" * 30, "y" * 285 + " pg cut " + "x" * 30),
    ("pg/slash+plus", '{"message": "bad value pg\\/slash+plus"}'),
])
def test_a_refused_write_whose_answer_holds_a_value_prints_no_part_of_it(value, text):
    """gate-21b B5, review-21b: the guard compared the value as it is with
    the answer after one_line() had collapsed every run of blanks, and after
    answer() had cut a body that is no JSON at 300 characters."""
    session = type("S", (), {"put": lambda self, path, **_: _Answer(404, text)})()
    who = credentials.Identity(type="group", id="G")
    with pytest.raises(credentials.CredentialError) as caught:
        credentials.put_credential(session, "d", who, {"PGVECTOR_RAG_PW": value})
    assert str(caught.value) == "storing the credential for group 'G' failed: HTTP 404"


def test_a_refused_write_whose_answer_holds_no_value_still_shows_it():
    session = type("S", (), {"put": lambda self, path, **_: _Answer(
        404, json.dumps({"message": "the group  identity G does not exist", "errorCode": 12310}))})()
    with pytest.raises(credentials.CredentialError) as caught:
        credentials.put_credential(session, "d", credentials.Identity(type="group", id="G"), {"X": "pg  two"})
    assert str(caught.value) == ("storing the credential for group 'G' failed: HTTP 404 the group identity G does "
                                 "not exist (errorCode 12310)")


def test_a_refused_read_or_removal_of_another_status_names_the_message_of_the_service():
    """live-21b notRun: a refused read or removal with a status other than
    404, which shows the message of the service, could not be staged."""
    body = {"message": "Access denied for the domain zz.", "errorCode": 1234}
    answer = _Response(403, body)
    answer.text = json.dumps(body)
    session = type("S", (), {"get": lambda self, path, **_: answer, "delete": lambda self, path, **_: answer})()
    who = credentials.Identity(type="group", id="G")
    for call, verb in ((credentials.fetch_credential, "reading"), (credentials.delete_credential, "removing")):
        with pytest.raises(credentials.CredentialError) as caught:
            call(session, "zz", who)
        assert str(caught.value) == (f"{verb} the credential for group 'G' failed: HTTP 403 Access denied for the "
                                     "domain zz. (errorCode 1234)")
