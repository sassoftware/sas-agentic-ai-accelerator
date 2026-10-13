# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a confined pass on SAS Viya 2026.09 (2026-10-13) and two reviews found
in `mdb content-deploy` and `mdb builders-import`, each as a test that fails on
the code before it:

  * a run of a part of the manifest wrote release.json - the record of a
    whole folder - into a folder that held nothing else;
  * `--only Data --to <root>/Data` wrote the content root of the manifest
    into the job definition, not the root the content went to;
  * a reference at a path of the manifest was listed as "not in the
    manifest" by the run that FAILED the entry for it, and beside the object
    itself it was passed over although two things of one name are never
    written;
  * the command a failed import names dropped --no-files and --out, and was
    refused its reports again after a refusal for the content root;
  * the line of a refused write cut the answer of the server in the middle,
    and no line said that the content root itself was created;
  * the line about the compute context named only the job definition a run
    creates, while one without the parameter gets it too;
  * a custom step was registered with the user id its .step file carries.

Every service is the fake of content_fakes: nothing here says how SAS Viya
answers. The answer of a refused write is made up in the SHAPE the pass
recorded.
"""
import io
import json
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

import test_builder_commands as imports
from content_fakes import CONTENT_ROOT, STEP, STEP_NAME, Response, Viya, make_repo
from mdb import cli
from mdb.core import content
from mdb.core.content import load_manifest
from mdb.viya import content as deploying
from mdb.viya.content import CREATED, EQUAL, FAILED, NOT_DEPLOYED, DeployError, deploy, deploy_to, resolve_target
# the fixtures of the two commands are those of their own test files
from test_builder_commands import (FILE_PATH, PACKAGE_FILE, TUNED, UI_PATH, Site, _built,  # noqa: F401
                                   _site_with_its_own_objects, discovery, importing, no_discovery, out, site)
from test_content_deploy import ADAPTERS, CORE, STEP_PATH, command, manifest, repo  # noqa: F401

REPOSITORY = Path(__file__).resolve().parents[3]


def _id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _statuses(result) -> dict:
    return {outcome.entry.path: outcome.status for outcome in result.outcomes}


def _parameters(viya, path) -> dict:
    body, _etag = viya.jobs[viya.at(path)[0]]
    return {p["name"]: p["defaultValue"] for p in body["parameters"]}


def _flat(text: str) -> str:
    """A text as one line, without the backticks of Markdown: what a
    sentence is compared by wherever it stands."""
    return " ".join(text.replace("`", "").split())


# ---- release.json: only by a run that covers the whole manifest ------------------
def test_a_run_of_a_part_does_not_write_the_record_of_the_whole_folder(command, manifest):
    """review-19: `--only release.json` on a fresh site wrote a release.json
    that names the release and every hash into a folder that held nothing
    else. Such a run is refused, with one line, before anything is sent."""
    for args in (("--only", "release.json"), ("--only", "RAG", "--only", "release.json"),
                 ("--only", "release.json", "--dry-run")):
        result, text, viya = command(*args)
        assert result.exit_code == 1, text
        assert text.count(deploying.RECORD_NEEDS_ALL) == 1
        assert "Done." not in text and "Nothing was written.\n" not in text
        # nothing was asked and nothing was written: not even the content root was made
        assert viya.calls == [] and viya.folders == {}
    assert deploying.RECORD_NEEDS_ALL == (
        "release.json is the record of the whole folder and is written only by a run that covers every entry of the "
        "manifest: this run covers a part of it (--only), so nothing was written - leave release.json out of "
        "--only, or run without --only")
    # the function itself holds the rule, not only the command
    with pytest.raises(DeployError, match="release.json is the record of the whole folder"):
        deploy(Viya(), manifest, only=["release.json"])
    # a part that does not name it is deployed as before, and release.json is not looked at
    result, text, viya = command("--only", "RAG")
    assert result.exit_code == 0 and "release.json" not in text and viya.at(f"{CONTENT_ROOT}/release.json") == []
    # parts that together are the whole manifest are a run of every entry
    whole = Viya()
    result = deploy(whole, manifest, only=["release.json", "Data", "RAG", "Prompt Builder"])
    assert result.failed == [] and len(whole.at(f"{CONTENT_ROOT}/release.json")) == 1


def test_the_record_is_not_written_into_a_file_a_site_has_elsewhere(command, manifest):
    """The same hole by another door: `--only release.json --to <a file>`."""
    viya = Viya()
    theirs = viya.put_member("/Team/Other/release.json", "/files/files/", b"{}\n")
    viya.calls.clear()
    outcome = deploy_to(viya, manifest, manifest.find("release.json", "file"), "/Team/Other/release.json")
    assert (outcome.status, outcome.note) == (FAILED, deploying.RECORD_NEEDS_ALL)
    assert viya.calls == [] and viya.files[theirs] == b"{}\n"
    result, text, _ = command("--only", "release.json", "--to", "/Team/Other/release.json")
    assert result.exit_code == 1 and deploying.RECORD_NEEDS_ALL in text and "Nothing was updated." in text


# ---- what a refused write answered, and the line for the content root -------------
#: A refusal in the shape the pass of 2026-10-13 recorded for a PUT of a file's content whose If-Match was
#: no longer the ETag of the file. Every value is made up.
REFUSED = {"details": ["path: /files/files/00000000-0000-4000-8000-000000000000/content"], "errorCode": 0,
           "httpStatusCode": 412, "links": [],
           "message": 'The values for the request header field "If-Match" ("etag-of-the-read") and the resource\'s '
                      'entity tag ("etag-of-another-write") are not the same.',
           "remediation": "Get the latest representation of the resource, apply the change to it and send the "
                          "request again.", "version": 2}


def test_the_answer_of_a_refused_request_is_shown_whole_or_cut_at_a_word():
    """live-19: '... "remediation":"Get the latest re))' - the first 300
    characters of the JSON, whatever stood there."""
    assert len(json.dumps(REFUSED)) > 300
    assert deploying._answer(Response(412, REFUSED)) == (
        'HTTP 412 The values for the request header field "If-Match" ("etag-of-the-read") and the resource\'s entity '
        'tag ("etag-of-another-write") are not the same. - Get the latest representation of the resource, apply the '
        "change to it and send the request again. (errorCode 0)")
    assert deploying._answer(Response(409, {"message": "An item of that name exists.", "errorCode": 11552})) == \
        "HTTP 409 An item of that name exists. (errorCode 11552)"
    assert deploying._answer(Response(403, {"message": "refused"})) == "HTTP 403 refused"
    assert deploying._answer(Response(500)) == "HTTP 500"
    # a body that is no JSON error: whole while it is short ...
    assert deploying._answer(Response(502, content=b"<html>Bad Gateway</html>")) == "HTTP 502 <html>Bad Gateway</html>"
    assert deploying._answer(Response(400, {"error": "x", "error_description": "y"})) == \
        'HTTP 400 {"error": "x", "error_description": "y"}'
    # ... and a long one ends at a word, and says that it goes on
    page = "The gateway did not get an answer in time. " * 12
    shown = deploying._answer(Response(504, content=page.encode()))
    assert len(page) > 300 and shown.endswith("...") and len(shown) <= len("HTTP 504 ") + 303
    kept = shown[len("HTTP 504 "):-3]
    assert page.startswith(kept) and page[len(kept)] == " " and not kept.endswith(" ")
    # a cut that falls on the end of a word keeps the word
    exact = ("word " * 60)[:299] + " and more words behind it"
    assert deploying._answer(Response(500, content=exact.encode())) == "HTTP 500 " + exact[:299] + "..."
    assert deploying._answer(Response(500, content=b"y" * 300 + b" and a tail")) == "HTTP 500 " + "y" * 300 + "..."
    # no blank at all: the first 300 characters
    assert deploying._answer(Response(500, content=b"x" * 400)) == "HTTP 500 " + "x" * 300 + "..."


def test_the_line_of_a_refused_update_holds_the_whole_answer(command, repo):
    _, _, viya = command()
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    real_put = viya.put

    def put(path, data=None, headers=None, **kwargs):
        if path.endswith("/content"):
            viya.calls.append(("PUT", path, None))
            return Response(412, REFUSED)
        return real_put(path, data=data, headers=headers, **kwargs)
    viya.put = put
    result, text, _ = command()
    assert result.exit_code == 1
    assert (f"  FAILED       file          {CORE}  (the file was left as it is: the update of its content was refused "
            f"(HTTP 412 {REFUSED['message']} - {REFUSED['remediation']} (errorCode 0)))\n") in text


def test_a_content_root_that_is_created_gets_a_line(command):
    """live-19: the run sent the POST for the root folder, and neither the
    dry run nor the run had a line for it."""
    root = "/Users/me/My Folder/ZZ_VERIFY_R20/root1"
    _, _, viya = command("--only", "RAG", "--dry-run")                    # the fake, before anything is on it
    viya.make_folder("/Users/me")
    result, text, _ = command("--content-root", root, "--create", "--dry-run")
    assert result.exit_code == 0, text
    assert (f"  created      folder        {root}  (the folder the content goes into, with every folder above it "
            "that is missing)\n") in text
    assert "14 entries: 13 created, 1 not deployed." in text and viya.wrote() == []
    result, text, _ = command("--content-root", root, "--create")
    assert result.exit_code == 0, text
    lines = text.splitlines()
    assert lines[1] == (f"  created      folder        {root}  (the folder the content goes into; created above it: "
                        "/Users/me/My Folder, /Users/me/My Folder/ZZ_VERIFY_R20)")
    # it is no entry: the count is the manifest's
    assert "14 entries: 13 created, 1 not deployed." in text
    # a folder that is there gets no such line
    result, text, _ = command("--content-root", root)
    assert "the folder the content goes into" not in text and "14 entries: 13 equal, 1 not deployed." in text
    # the folder form of --to, and a root whose parent is there
    result, text, _ = command("--only", "RAG", "--to", "/Users/me/Retrieval", "--create")
    assert "  created      folder        /Users/me/Retrieval  (the folder the content goes into)\n" in text


def test_the_import_prints_the_line_for_a_content_root_it_creates(importing, no_discovery):
    no_discovery()
    result, fake, text = importing(Site(), "--content-root", "/Team/Accelerator", "--create", "--no-reports",
                                   "--no-files", without_report=True)
    assert result.exit_code == 0, text
    assert ("    created      folder        /Team/Accelerator  (the folder the content goes into; created above it: "
            "/Team)\n") in text
