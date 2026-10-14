# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate, the review and the live pass of round 22 found in mdb at
fb65e09, each with the input that showed it. Nothing signs in anywhere: the
service is a fake in memory and sasctl a stand-in."""
import io
import json
import shutil
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from typer.testing import CliRunner

from content_fakes import Viya, package_of
from mdb import cli
from mdb.core.generator import CoreAssets
from mdb.core.paths import core_dir, fact_sheet_path
from mdb.viya import credentials
from mdb.viya import session as s
from test_credentials_commands import mdb  # noqa: F401
from test_package_guards import REPORT, exporting, manifest, repo  # noqa: F401

# ---- gate[1]: mdb retire names what kept it from SAS Viya --------------------------------------
@pytest.fixture
def retiring(tmp_path, monkeypatch, repo_root):
    """`mdb retire` on a copy of one definition and of its fact sheet, with
    no SAS Viya variable set and no SAS Viya CLI login."""
    definitions = tmp_path / "LLM-Definitions"
    shutil.copytree(repo_root / "LLM-Definitions" / "smollm_135m", definitions / "smollm_135m")
    sheet = fact_sheet_path(repo_root, "llm")
    shutil.copy(sheet, definitions / sheet.name)
    for name in (s.URL_ENV, s.TOKEN_ENV, s.USER_ENV, s.PASSWORD_ENV, s.CLI_PROFILE_ENV, "SAS_VIYA_VERIFY_SSL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    core = CoreAssets.load(core_dir(repo_root))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(
        repo=tmp_path, core=core, find_folder=lambda model_id: definitions / model_id,
        fact_sheet=lambda kind: definitions / sheet.name))
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))

    def run():
        result = CliRunner().invoke(cli.app, ["retire", "smollm_135m"])
        assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
        return result.exit_code, out.getvalue()
    return run


def _no_answer_sasctl(monkeypatch):
    class Session:
        def __init__(self, *args, **kwargs):
            raise requests.exceptions.ConnectionError("HTTPConnectionPool(host='127.0.0.1', port=1): Max retries "
                                                      "exceeded")
    monkeypatch.setitem(sys.modules, "sasctl", types.SimpleNamespace(Session=Session))


@pytest.mark.parametrize("setting, said", [
    ({}, "Missing Viya configuration: SAS_VIYA_URL"),
    ({s.URL_ENV: "https://h x", s.USER_ENV: "u", s.PASSWORD_ENV: "p"},
     "SAS_VIYA_URL is 'https://h x', which mdb cannot use"),
    ({s.URL_ENV: "http://127.0.0.1:1", s.USER_ENV: "u", s.PASSWORD_ENV: "p"},
     "SAS Viya at http://127.0.0.1:1 could not be asked for the sign-in of u with SAS_VIYA_USER + "
     "SAS_VIYA_PASSWORD: ConnectionError"),
])
def test_retire_says_what_kept_it_from_sas_viya(retiring, monkeypatch, setting, said):
    """gate-22a: every sign-in that failed and every address mdb refuses
    was printed as 'SAS Viya is not configured', and the reason was dropped."""
    _no_answer_sasctl(monkeypatch)
    for name, value in setting.items():
        monkeypatch.setenv(name, value)
    code, text = retiring()
    assert code == 0 and "smollm_135m: tagged deprecated and regenerated." in text, text
    assert "is not configured" not in text, text
    line, = [line for line in text.splitlines() if line.startswith("The registered model's status was not updated: ")]
    assert said in line, line
