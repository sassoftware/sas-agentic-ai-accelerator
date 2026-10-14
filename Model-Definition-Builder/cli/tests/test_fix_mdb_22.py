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


# ---- review[7]: an identity id that no line would show as it is -------------------------------
@pytest.mark.parametrize("who", ["a\x1bb", "a\x00b", "a\tb", "a\x7fb", "a\x9bb", "a\u200bb", "G1\ufeff", " G1", "G1 ",
                                 "G1\t", "\u00a0G1"])
def test_an_id_with_a_control_character_or_a_blank_at_an_end_is_refused(mdb, who):
    """review-22a: credentials-add group 'a\\x1bb' wrote the id and said
    'group ab'; ' G1' was added as a second identity beside G1 and printed
    as 'Added group  G1'."""
    assert mdb("credentials-add", "group", "G1")[0] == 0
    before = mdb.path.read_bytes()
    for args in (("credentials-add", "group", who), ("credentials-remove", "group", who),
                 ("credentials-apply", "--identity", who), ("credentials-report", "--identity", who)):
        code, said = mdb(*args)
        assert code == 2 and "an identity id cannot hold a control character" in said, (args, said)
        assert said.count("\n") == 1, (args, said)
    assert mdb.path.read_bytes() == before and mdb.site.calls == []
    # and in a manifest written by hand
    mdb.path.write_text(f"source: .env\nidentities:\n  - type: group\n    id: {json.dumps(who)}\n", encoding="utf-8")
    code, said = mdb("credentials-report")
    assert code == 2 and "an identity id cannot hold a control character" in said and mdb.site.calls == []


@pytest.mark.parametrize("who", ["G1", "LLM Consumers", "ada.lovelace@example.com", "Jörg", "a-b_c"])
def test_an_id_a_line_shows_as_it_is_is_taken(who):
    assert credentials.Identity(type="group", id=who).id == who


# ---- live-22a defect 1: the advice of an export that differs, for a folder of a test -----------
def test_the_advice_of_an_export_of_a_test_folder_says_what_content_deploy_leaves(exporting, manifest, monkeypatch):
    """live-22a: a test root deployed by `mdb content-deploy --create` was
    exported with 4 differences - the contentRoot of two job definitions and
    two reports - and the line advised `mdb content-deploy` to bring the
    folder to the checkout, which cannot clear one of them."""
    root = "/Users/me/My Folder/ZZ_VERIFY_R22/root"
    # the folder as `mdb content-deploy --content-root <root> --create` leaves it, read off the site in memory
    viya, held = Viya(), cli._viya_session
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)
    assert CliRunner().invoke(cli.app, ["content-deploy", "--content-root", root, "--create"]).exit_code == 0
    monkeypatch.setattr(cli, "_viya_session", held)
    optimize, = viya.at(f"{root}/Prompt Builder/Optimize")
    deployed = viya.jobs[optimize][0]
    assert {p["name"]: p["defaultValue"] for p in deployed["parameters"]}["contentRoot"] == root
    assert viya.at(f"{root}/{REPORT}") == []
    built = package_of(manifest, change={"Prompt Builder/Optimize": deployed, REPORT: None}, root="root")
    out = str(manifest.repo_root / "scratch" / "r22.json")
    result, text, _, _ = exporting(built, "--content-root", root, "--out", out)
    assert result.exit_code == 1, text
    assert "job definition 'Prompt Builder/Optimize': 'parameters' differs" in text
    assert f"report '{REPORT}' is in the manifest but not in the package" in text
    assert ("In a folder other than /SAS Agentic AI Accelerator that command writes the folder into the "
            "contentRoot of the job definitions and brings no report, so a difference in either stays: "
            "--allow-differences writes the file all the same.") in " ".join(text.split())
    result, text, _, _ = exporting(built, "--content-root", root, "--out", out, "--allow-differences")
    assert result.exit_code == 0, text
    # the content root of the release: content-deploy writes the root the manifest names, and a report is imported
    result, text, _, _ = exporting(package_of(manifest, change={REPORT: None}))
    assert result.exit_code == 1 and "In a folder other than" not in text, text


# ---- live-22a defect 2: the summary of an export names every path it leaves --------------------
def test_the_summary_of_an_export_names_the_content_root_of_the_job_definitions(exporting, manifest):
    """live-22a: a package of a folder below /Users/<id> held that id in the
    contentRoot of two job definitions, and the summary named only
    contentSourceLocation as left as it is."""
    root = "/Users/fab.exporter/My Folder/ZZ_VERIFY_R22/root"
    from mdb.core import content
    entry, = [e for e in manifest.all_entries() if e.path == "Prompt Builder/Optimize"]
    deployed = content.job_definition_body(manifest, entry)
    deployed["parameters"] = [{**p, "defaultValue": root} if p["name"] == "contentRoot" else p
                              for p in deployed["parameters"]]
    built = package_of(manifest, change={"Prompt Builder/Optimize": deployed, REPORT: None}, root="root")
    for detail in built.details:
        detail["transferObject"]["summary"]["createdBy"] = "fab.exporter"
    out = str(manifest.repo_root / "scratch" / "r22.json")
    result, text, _, _ = exporting(built, "--content-root", root, "--out", out, "--allow-differences")
    assert result.exit_code == 0, text
    assert "and so is the contentRoot of the job definitions." in text, text
    result, text, _, _ = exporting(built, "--content-root", root, "--out", out, "--allow-differences",
                                   "--keep-user-ids")
    assert result.exit_code == 0 and "the contentRoot of the job definitions is left as it is too." in text, text
