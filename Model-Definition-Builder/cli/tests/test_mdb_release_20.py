# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The export, the gate and the release procedure, as round 19 left them open.

  1. The user ids of an export. The exports SAS Viya 2026.09 made on
     2026-10-12 and 2026-10-13 carry a user id in `userName` of each of
     their folder bodies; the gate counted that field and the export did not
     rewrite it, so an export in that shape ends with "Nothing was written"
     unless its differences are allowed. The packages below are fabricated
     in the SHAPE of those exports - every id in them is made up.
  2. The commit id of release.json: an abbreviated id passed the gate.
  3. The release procedure on a `main` that only takes squash merges, run in
     real git repositories with the whole command `mdb package-check
     --release` - and the order that fails the tag.
  4. The last line of a release export, and the help texts.

The tests of 1, 2 and 4 failed on the code before its fixes (the one of the
fixture aside); those of 3 hold the procedure CONTRIBUTING.md describes to
what git and the gate do.
"""
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from content_fakes import CONTENT_ROOT, b64, make_repo, package_of
from mdb import cli
from mdb.core import content
from mdb.core.content import load_manifest
from mdb.core.packages import (
    NEUTRAL_USER, USER_FIELDS, USER_FIELDS_TEXT, _body, _report_wrapper, deflate, locate, named_users,
    neutralise_users, package_differences,
)
from test_package_guards import checking, exporting, manifest, repo  # noqa: F401 - the fixtures of the gate tests

REPO_ROOT = Path(__file__).resolve().parents[3]
STAMP = "2026-10-13T09:19:08.741Z"

#: The three fields, spelled out: the test pins the list, it does not read it.
FIELDS = ("createdBy", "modifiedBy", "userName")


# ---- 1: a package in the shape of a real export -------------------------------
class Exported:
    """A fabricated package dressed the way SAS Viya 2026.09 exports a folder
    (the keys read off an export of 2026-10-13; the values are made up):

    * the package, every summary and every body carry `createdBy` and
      `modifiedBy` - a file in its `fileResource`, a folder in its `folder`,
      a report in the `object` of its wrapper behind the compression;
    * the body of every folder carries `userName` beside its `folder`
      object (in that export the id of the one user who made and exported
      everything; here `exporter`, so that a test tells the fields apart);
    * every object below the root carries `contentSourceLocation`, the path
      of the folder it sits in - for a root below /Users/<id> that path holds
      the id, and so does the `contentRoot` default of a job definition.
    """

    def __init__(self, built, root_path: str, creator: str, exporter: str):
        self.built, self.root_uri = built, built.root_uri
        self.creator, self.exporter = creator, exporter
        people = {"creationTimeStamp": STAMP, "createdBy": creator, "modifiedTimeStamp": STAMP, "modifiedBy": creator}
        _root, items = locate(built.build(), built.root_uri)
        for item in items:
            obj, summary = item.obj, item.obj["summary"]
            uri = summary["links"][0]["uri"]
            links = [{"method": "GET", "rel": "self", "href": uri, "uri": uri, "type": "application/vnd.sas.x+json"}]
            summary.update(people, version=2)
            if item.path == "" and root_path.count("/") > 1:
                obj["contentSourceLocation"] = root_path.rsplit("/", 1)[0]
            elif item.path:
                above = item.path.rpartition("/")[0]
                obj["contentSourceLocation"] = root_path + (f"/{above}" if above else "")
            wrapper = _report_wrapper(obj)
            if wrapper is not None:
                wrapper["object"].update(people)
                obj["content"] = deflate(json.dumps(wrapper, separators=(",", ":")))
                continue
            body = _body(obj)
            if item.kind == "folder":
                parent = next((c["uri"] for c in item.detail.get("connectors") or []
                               if c.get("type") == "parentFolder"), None)
                folder = {**people, "version": 1, "id": summary["id"], **body.get("folder", {}), "type": "folder",
                          "memberCount": 1, "links": links}
                if parent:
                    folder["parentFolderUri"] = parent
                body = {"folder": folder, "userName": exporter}
            elif "fileResource" in body:
                body["fileResource"].update(people, id=summary["id"], links=links, version=2)
            else:
                body.update(people, id=summary["id"], links=links, version=2)
                for parameter in body.get("parameters") or []:
                    if parameter["name"] == "contentRoot":
                        parameter["defaultValue"] = root_path
            obj["content"] = b64(body)

    def build(self) -> dict:
        return {**self.built.build(), "creationTimeStamp": STAMP, "modifiedTimeStamp": STAMP,
                "createdBy": self.creator, "modifiedBy": self.creator, "description": "", "links": []}


def decoded(package: dict) -> list:
    """Every part of a package as (kind, part, value): the package without
    its objects, and per object what stands beside the transfer object, the
    transfer object without its content, and the content decoded."""
    parts = [("package", "top", {k: v for k, v in package.items() if k != "transferDetails"})]
    for detail in package.get("transferDetails") or []:
        obj = detail.get("transferObject") or {}
        kind = (obj.get("summary") or {}).get("type") or "?"
        parts.append((kind, "detail", {k: v for k, v in detail.items() if k != "transferObject"}))
        parts.append((kind, "object", {k: v for k, v in obj.items() if k != "content"}))
        wrapper = _report_wrapper(obj)
        parts.append((kind, "content", wrapper if wrapper is not None else (_body(obj) or obj.get("content"))))
    return parts


def places(package: dict, needle: str, keys=None) -> dict:
    """{(kind, part, key path): count} of the values that hold `needle`,
    whatever their key is called - or, with `keys`, of the values at one of
    those keys."""
    found: dict = {}

    def walk(value, path, where):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, path + [key], where)
        elif isinstance(value, list):
            for item in value:
                walk(item, path + ["[]"], where)
        elif isinstance(value, str) and needle in value and (keys is None or path[-1] in keys):
            key = (*where, "/".join(path))
            found[key] = found.get(key, 0) + 1

    for kind, part, value in decoded(package):
        walk(value, [], (kind, part))
    return found


def test_the_fixture_has_the_shape_of_the_real_export(manifest):
    """What the scan of the export of 2026-10-13 found, held on the fixture:
    the id stands as a whole value in the three fields - `userName` in every
    folder body - and nowhere else of an object that ships."""
    dressed = Exported(package_of(manifest), CONTENT_ROOT, "fab.creator", "fab.exporter").build()
    folders = [d for d in dressed["transferDetails"] if d["transferObject"]["summary"]["type"] == "folder"]
    assert len(folders) == 7
    for detail in folders:
        body = _body(detail["transferObject"])
        assert sorted(body) == ["folder", "userName"] and body["userName"] == "fab.exporter"
        assert {"createdBy", "modifiedBy", "creationTimeStamp", "id", "links", "memberCount", "name"} <= set(
            body["folder"])
    assert {path.rsplit("/", 1)[-1] for (_k, _p, path) in places(dressed, "fab.")} == set(FIELDS)
    # at the root of SAS Content the root folder has no parent and no source location; a folder below it has both
    root, below = folders[0]["transferObject"], folders[1]["transferObject"]
    assert "contentSourceLocation" not in root and "parentFolderUri" not in _body(root)["folder"]
    assert below["contentSourceLocation"] == CONTENT_ROOT and _body(below)["folder"]["parentFolderUri"]


def test_one_list_of_fields_for_the_neutralising_and_for_the_gate():
    """review-19 (high): `neutralise_users` rewrote createdBy / modifiedBy,
    `named_users` - what the gate asks - also counted userName. One list, and
    the same parts of a package for both."""
    assert USER_FIELDS == FIELDS and USER_FIELDS_TEXT == "createdBy / modifiedBy / userName"
    wrapper = {"object": {"name": "R", **{field: f"object.{field}" for field in FIELDS}},
               "transferableContent": {"content": "<SASReport/>", "contentVersion": 1, "reportStates": [
                   {"content": "<SASReport/>", "reportState": {field: f"state.{field}" for field in FIELDS}}]}}
    package = {**{field: f"top.{field}" for field in FIELDS}, "transferDetails": [
        {"transferObject": {"summary": {"type": "report", "name": "R", **{f: f"summary.{f}" for f in FIELDS}},
                            "content": deflate(json.dumps(wrapper))}},
        {"transferObject": {"summary": {"type": "folder", "name": "F"},
                            "content": b64({"folder": {field: f"folder.{field}" for field in FIELDS},
                                            **{field: f"body.{field}" for field in FIELDS}})}}]}
    named = named_users(package)
    assert sorted(named) == sorted(f"{part}.{field}" for field in FIELDS
                                   for part in ("top", "summary", "object", "state", "folder", "body"))
    replaced = neutralise_users(package)
    assert replaced == len(named) == 18
    assert named_users(package) == {NEUTRAL_USER: 18}
    assert places(package, ".createdBy") == places(package, ".modifiedBy") == places(package, ".userName") == {}
    assert neutralise_users(package) == 0
    # a wrapper that is not what the transfer service writes is read as far as it goes, by both
    for odd in ({"object": "text", "transferableContent": "text"},
                {"object": {"createdBy": "odd"}, "transferableContent": {"reportStates": {"reportState": {}}}},
                {"object": None, "transferableContent": {"reportStates": ["text", {"reportState": "text"}]}}):
        package = {"transferDetails": [{"transferObject": {"summary": {"type": "report", "name": "R"},
                                                           "content": deflate(json.dumps(odd))}}]}
        named = named_users(package)
        assert named == ({"odd": 1} if isinstance(odd["object"], dict) else {})
        assert neutralise_users(package) == len(named) and set(named_users(package)) <= {NEUTRAL_USER}


def test_an_export_in_the_real_shape_is_written_and_names_no_one(exporting, manifest):
    """review-19 (high) / live-19 defect 1: with `userName` in the folder
    bodies an export of the content root ended with 'Nothing was written'; on
    2026-10-13 the export of a test root printed the --keep-user-ids wording
    without the flag, and the gate blamed createdBy / modifiedBy for it."""
    dressed = Exported(package_of(manifest), CONTENT_ROOT, "fab.creator", "fab.exporter")
    before = dressed.build()
    named = sum(places(before, "fab.", keys=FIELDS).values())
    assert named == sum(places(before, "fab.").values()) == sum(named_users(before).values())
    result, text, target, _ = exporting(dressed)
    assert result.exit_code == 0, text
    assert "Nothing was written" not in text and "--keep-user-ids" not in text and "names the user id" not in text
    assert (f"  Replaced the user id(s) fab.creator, fab.exporter in createdBy / modifiedBy / userName with "
            f"'sas-agentic-ai-accelerator' ({named} place(s)); contentSourceLocation, the path of the folder each "
            f"object was exported from, is left as it is ({CONTENT_ROOT}), and so is the contentRoot of the job "
            "definitions.\n") in text
    written = json.loads(target.read_text(encoding="utf-8"))
    assert "fab." not in target.read_text(encoding="utf-8") and places(written, "fab.") == {}
    assert named_users(written) == {NEUTRAL_USER: named}
    assert package_differences(written, manifest) == []
    # the gate of the release passes the file the export wrote
    assert CliRunner().invoke(cli.app, ["package-check", "--release"]).exit_code == 0


def test_the_gate_names_the_fields_and_fix_does_what_its_sentence_says(checking, manifest):
    """review-19: the gate said 'in createdBy / modifiedBy ... `mdb
    package-check --fix` rewrites them', and after --fix the same line stood:
    the ids were in userName."""
    dressed = Exported(package_of(manifest), CONTENT_ROOT, "fab.creator", "fab.exporter").build()
    for detail in dressed["transferDetails"]:               # as an export with only the two fields rewritten
        detail["transferObject"]["summary"].update(createdBy=NEUTRAL_USER, modifiedBy=NEUTRAL_USER)
    target = manifest.package_path
    target.write_text(json.dumps(dressed, indent=2), encoding="utf-8", newline="\n")
    left = sum(places(dressed, "fab.", keys=FIELDS).values())
    result, text, _ = checking()
    assert result.exit_code == 1
    assert (f"SAS-Agentic-AI-Accelerator.json: the package names the user id(s) fab.creator, fab.exporter in "
            f"createdBy / modifiedBy / userName ({left} place(s)) - an export of the release writes "
            "'sas-agentic-ai-accelerator' there, and `mdb package-check --fix` rewrites them\n") in text
    assert "A host, a per-user state, an option default or a user id is rewritten by --fix" in " ".join(text.split())
    result, text, _ = checking("--fix")
    assert result.exit_code == 0, text
    assert (f"  SAS-Agentic-AI-Accelerator.json: user id(s) fab.creator, fab.exporter in createdBy / modifiedBy / "
            f"userName -> sas-agentic-ai-accelerator ({left})\n") in text
    assert "fab." not in target.read_text(encoding="utf-8")
    assert places(json.loads(target.read_text(encoding="utf-8")), "fab.") == {}
    result, text, _ = checking("--release")
    assert result.exit_code == 0 and "names the user id" not in text


def test_an_export_of_a_folder_below_a_user_says_where_the_id_stays(exporting, manifest, repo):
    """The export of 2026-10-13 was made from a folder below /Users/<id>: there
    the id is also PART of two paths, `contentSourceLocation` and the
    `contentRoot` default of a job definition. The fields are rewritten, the
    paths are left and the line names the folder - and such an export is
    never the release file."""
    root = "/Users/fab.exporter/My Folder/ZZ_VERIFY_R20/root1"

    def dressed():
        built = package_of(manifest, root="root1", above=("Users", "fab.exporter", "My Folder", "ZZ_VERIFY_R20"))
        return Exported(built, root, "fab.exporter", "fab.exporter")
    elsewhere = repo / "scratch" / "r20.json"
    result, text, target, _ = exporting(dressed(), "--content-root", root, "--out", str(elsewhere),
                                        "--allow-differences")
    assert result.exit_code == 0, text
    assert "--keep-user-ids" not in text and "names the user id" not in text
    assert "job definition 'Prompt Builder/Optimize': 'parameters' differs from the parameters of its manifest entry" \
        in text
    replaced = re.search(r"  Replaced the user id\(s\) fab\.exporter in createdBy / modifiedBy / userName with "
                         r"'sas-agentic-ai-accelerator' \((\d+) place\(s\)\); contentSourceLocation, the path of the "
                         r"folder each object was exported from, is left as it is \(" + re.escape(root) + r"\), and so is the "
                         r"contentRoot of the job definitions\.\n",
                         text)
    assert replaced, text
    written = json.loads(elsewhere.read_text(encoding="utf-8"))
    assert named_users(written) == {NEUTRAL_USER: sum(places(written, NEUTRAL_USER, keys=FIELDS).values())}
    # where the id still stands: in the two paths, and nowhere else
    assert {path for (_kind, _part, path) in places(written, "fab.exporter")} == {
        "contentSourceLocation", "parameters/[]/defaultValue"}
    locations = re.findall(r'"contentSourceLocation": "([^"]*)"', elsewhere.read_text(encoding="utf-8"))
    assert len(locations) == written["transferObjectCount"]
    assert all(value.startswith("/Users/fab.exporter/My Folder/ZZ_VERIFY_R20") for value in locations)

    # --keep-user-ids: the line that says they were left, and only then
    result, text, _, _ = exporting(dressed(), "--content-root", root, "--out", str(elsewhere), "--allow-differences",
                                   "--keep-user-ids")
    assert result.exit_code == 0, text
    assert "Replaced the user id" not in text
    assert re.search(r"  The file names the user id\(s\) fab\.exporter in createdBy / modifiedBy / userName \(\d+ "
                     r"place\(s\): the package, its objects and their bodies\) - left as the export wrote them "
                     r"\(--keep-user-ids\) - and the exported folder in contentSourceLocation; the contentRoot of the "
                     r"job definitions is left as it is too\.\n", text), text

    # the release file is written from the content root of the release only, wherever --out points
    result, text, target, exported = exporting(dressed(), "--content-root", root, "--out", str(target))
    assert result.exit_code == 2 and exported == [] and not target.exists()
    assert f"{root} is not the content root of the release ({CONTENT_ROOT})" in text


# ---- 4: the last line of a release export, the help texts ----------------------
def test_the_last_line_of_a_release_export_names_what_the_commit_holds(exporting, manifest, repo):
    """review-19: 'commit this file - and nothing else with it', while the
    commit of release 2.3.0 also removes the package files of the earlier
    releases and takes --allow-missing out of the workflow; and 'Tag the
    commit that adds the file', which after a squash merge is a commit of a
    branch."""
    earlier = repo / "SAS-Viya-Integrations"
    for name in ("An Earlier Package.json", "Another-Earlier-Package.json"):
        (earlier / name).write_text('{"transferDetails": []}\n', encoding="utf-8")
    result, text, target, _ = exporting(package_of(manifest))
    assert result.exit_code == 0, text
    assert text.rstrip().splitlines()[-1] == (
        "Run `mdb package-check --release`, then commit this file in a commit that changes neither the changelog nor "
        "a source the manifest names: they stay as they were at the deploy (release.json names that commit, and the "
        "release table was made from that changelog). That commit also removes the package files of earlier "
        "releases - An Earlier Package.json, Another-Earlier-Package.json beside this file, and every other "
        "transfer package below SAS-Viya-Integrations/ - and takes --allow-missing out of the workflow. The tag goes "
        "on the commit of main that holds this file - after a squash merge the commit the merge made on main, not "
        "the commit of the branch - and the commit release.json names has to be in the history of that commit: a "
        "commit of a branch that is squash-merged later is not.")
    assert "nothing else with it" not in text and "Tag the commit that adds the file" not in text
    # once they are gone the sentence about them is too
    for name in ("An Earlier Package.json", "Another-Earlier-Package.json"):
        (earlier / name).unlink()
    result, text, _, _ = exporting(package_of(manifest))
    assert result.exit_code == 0 and "earlier releases" not in text and "--allow-missing" not in text


#: A sentence about what was not measured, tested or run - not the word "run" in what a command does.
NOT_RUN = re.compile(r"not (been )?(measured|tested)\b|untested|unmeasured|(has|have) not (been )?run\b"
                     r"|not run (in|against) SAS|never (been )?(run|measured|sent)\b", re.I)


def _help_texts(command, name: str) -> list:
    texts = [(name, " ".join((command.help or "").split()))]
    texts += [(f"{name} {param.name}", " ".join((getattr(param, "help", "") or "").split()))
              for param in command.params]
    for sub_name, sub in (getattr(command, "commands", None) or {}).items():
        texts += _help_texts(sub, f"{name} {sub_name}")
    return texts


def test_no_help_text_says_what_was_not_measured():
    """gate-19: `mdb package-export --help` ended with a sentence about what
    has not been measured. That is said in the changelog and the guide."""
    import typer
    texts = _help_texts(typer.main.get_command(cli.app), "mdb")
    assert len(texts) > 100 and any(name == "mdb package-export" for name, _ in texts)
    assert [f"{name}: {match.group(0)}" for name, text in texts for match in NOT_RUN.finditer(text)] == []
    assert NOT_RUN.search("What the transfer service does with neutral user fields has not been measured.")
    assert not NOT_RUN.search("Nothing is run. A step that is not run keeps its id.")
    export = dict(texts)["mdb package-export"]
    assert "and `userName` in the body of a folder. They are replaced with the text" in export
    assert "the release file is written from the content root of the release only" in export
    assert "user ids (`createdBy`, `modifiedBy`, `userName`)" in dict(texts)["mdb package-check"]


# ---- 2 and 3: real git repositories --------------------------------------------
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="no git on this machine")


def _git(cwd, *args) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                           "init.defaultBranch=main", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
                           "-c", "tag.gpgsign=false", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


@pytest.fixture
def gate(monkeypatch):
    """`mdb package-check` in a checkout that is a real git repository - no
    commit function is replaced. Returns run(checkout, *args) -> (exit code,
    printed text)."""
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("the check reads files and signs in nowhere"))

    def run(checkout: Path, *args):
        out.seek(0)
        out.truncate()
        monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=checkout))
        result = CliRunner().invoke(cli.app, ["package-check", *args])
        assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
        return result.exit_code, out.getvalue()
    return run


EARLIER = "SAS-Viya-Integrations/Package-Of-An-Earlier-Release.json"


def _site(tmp_path: Path) -> SimpleNamespace:
    """A hosted repository (bare), the maintainer's clone of it and the clone
    that stands for the host: `main` holds the checkout of the content tests
    with one package file of an earlier release, and a branch with the
    release work - its final texts included - is pushed and not merged."""
    origin, owner, host = tmp_path / "origin.git", tmp_path / "owner", tmp_path / "host"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    owner.mkdir()
    make_repo(owner)
    (owner / EARLIER).write_text('{"transferDetails": []}\n', encoding="utf-8")
    _git(owner, "init", "-q")
    _git(owner, "remote", "add", "origin", str(origin))
    _git(owner, "add", ".")
    _git(owner, "commit", "-q", "-m", "the release before")
    _git(owner, "push", "-q", "origin", "main")
    _git(owner, "checkout", "-q", "-b", "feature/release")
    (owner / "src/optimize.sas").write_bytes(b"%put optimize, as released;\n")
    _git(owner, "commit", "-q", "-am", "the release work")
    changelog = (owner / "CHANGELOG.md").read_bytes().replace(b"## [2.3.0] - Unreleased", b"## [2.3.0] - 2026-10-20")
    (owner / "CHANGELOG.md").write_bytes(changelog)
    _git(owner, "commit", "-q", "-am", "the final texts: the changelog carries its date")
    _git(owner, "push", "-q", "origin", "feature/release")
    _git(tmp_path, "clone", "-q", str(origin), str(host))
    return SimpleNamespace(origin=origin, owner=owner, host=host, tmp=tmp_path)


def _squash_merge(site, branch: str, message: str) -> str:
    """What the host does with a pull request on a `main` that takes squash
    merges only: one new commit on main with the changes of the branch, and
    the branch is deleted."""
    _git(site.host, "fetch", "-q", "--prune", "origin")
    _git(site.host, "checkout", "-q", "-B", "main", "origin/main")
    _git(site.host, "merge", "-q", "--squash", f"origin/{branch}")
    _git(site.host, "commit", "-q", "-m", message)
    _git(site.host, "push", "-q", "origin", "main")
    _git(site.host, "push", "-q", "origin", "--delete", branch)
    return _git(site.host, "rev-parse", "HEAD")


def _deploy_and_export(checkout: Path) -> str:
    """What `mdb content-deploy` and `mdb package-export` leave in a checkout
    whose folder equals it: the package file, with a release.json that names
    the commit that is checked out. Returns that commit."""
    listed = load_manifest(checkout)
    listed.package_path.write_text(json.dumps(package_of(listed).build(), indent=2), encoding="utf-8", newline="\n")
    return content.checkout_commit(checkout)[0]


def _tag_clone(site, tag: str) -> Path:
    """The fresh clone the workflow of a tag works in: the whole history of
    what the hosted repository still has a name for."""
    clone = site.tmp / f"clone-of-{tag}"
    _git(site.tmp, "clone", "-q", "--no-local", "--branch", tag, str(site.origin), str(clone))
    return clone


@needs_git
def test_the_release_procedure_on_a_main_that_takes_squash_merges_only(tmp_path, gate):
    """review-19: the procedure did not say which commit is deployed from and
    which one the tag names where every change reaches `main` as a squash
    merge. The four steps of CONTRIBUTING.md, with the commands named there,
    and the gate as the workflow runs it: on the pull request of the package,
    on `main` after its merge, and in the fresh clone of the tag."""
    site = _site(tmp_path)
    # 1. the release work with its final texts is squash-merged first
    merged = _squash_merge(site, "feature/release", "Release work (#1)")
    # 2. deploy and export from the commit that merge made on main
    _git(site.owner, "fetch", "-q", "origin")
    _git(site.owner, "switch", "-q", "--detach", "origin/main")
    assert _deploy_and_export(site.owner) == merged == _git(site.owner, "rev-parse", "origin/main")
    assert gate(site.owner, "--release")[0] == 0
    # 3. the package file in a second pull request, from a branch that starts at that commit
    _git(site.owner, "switch", "-q", "-c", "release/package")
    _git(site.owner, "rm", "-q", EARLIER)
    _git(site.owner, "add", "SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json")
    _git(site.owner, "commit", "-q", "-m", "the package of the release")
    on_branch = _git(site.owner, "rev-parse", "HEAD")
    _git(site.owner, "push", "-q", "origin", "release/package")
    #    the workflow of the pull request checks the merge of the branch into main
    _git(site.host, "fetch", "-q", "origin")
    _git(site.host, "checkout", "-q", "-B", "pull-request-merge", "origin/main")
    _git(site.host, "merge", "-q", "--no-ff", "-m", "the merge the workflow checks", "origin/release/package")
    code, text = gate(site.host, "--release")
    assert code == 0, text
    squashed = _squash_merge(site, "release/package", "The package of the release (#2)")
    #    ... and the workflow of the push to main checks the commit the squash made
    code, text = gate(site.host, "--release")
    assert code == 0, text
    # 4. the tag names the commit that second merge made on main - not the commit of the branch
    _git(site.owner, "fetch", "-q", "--prune", "origin")
    _git(site.owner, "tag", "2.3.0", "origin/main")
    _git(site.owner, "push", "-q", "origin", "2.3.0")
    assert _git(site.owner, "rev-parse", "2.3.0^{commit}") == squashed != on_branch
    clone = _tag_clone(site, "2.3.0")
    assert content.commit_standing(clone, merged) == content.ANCESTOR
    assert content.commit_standing(clone, on_branch) == content.NOT_HELD
    code, text = gate(clone, "--release")
    assert code == 0, text
    assert "1 package(s): every entry of the manifest is in the package and equals the checkout" in text
    assert not (clone / EARLIER).exists()
    # a tag on the commit of the branch that added the file is a tag of a commit that is not on main
    done = subprocess.run(["git", "merge-base", "--is-ancestor", on_branch, "origin/main"], cwd=site.owner)
    assert done.returncode == 1
    # CONTRIBUTING.md names these steps with these commands
    procedure = " ".join((REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").split())
    for said in ("a release is two of them, in this order", "`git switch --detach origin/main`",
                 "**The tag names the commit that second merge made on `main`**", "`git tag <version> origin/main`",
                 "not the commit of the branch that added the file, which is not on `main` after a squash merge",
                 "none of its commits is in the history of `main` afterwards",
                 "removes the four package files of the earlier releases and takes `--allow-missing` out of the "
                 "workflow"):
        assert said in procedure, said


@needs_git
def test_a_deploy_from_the_branch_of_a_pull_request_fails_the_tag(tmp_path, gate):
    """review-19, relproc.py `texts_by_pr`: the final texts and the package
    in one pull request, deployed from its branch. The check of the pull
    request passes - the commit is in the history of the branch - and the
    tag fails: the squash made another commit, and neither the maintainer's
    `main` nor the clone of the tag has the one release.json names in its
    history."""
    site = _site(tmp_path)
    _git(site.owner, "checkout", "-q", "feature/release")
    deployed = _deploy_and_export(site.owner)                    # from the tip of the branch
    _git(site.owner, "rm", "-q", EARLIER)
    _git(site.owner, "add", "SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json")
    _git(site.owner, "commit", "-q", "-m", "the package of the release")
    assert gate(site.owner, "--release")[0] == 0                 # on the branch, and so in its pull request
    _git(site.owner, "push", "-q", "origin", "feature/release")
    _squash_merge(site, "feature/release", "Release work with the package (#1)")
    _git(site.owner, "fetch", "-q", "--prune", "origin")
    _git(site.owner, "switch", "-q", "--detach", "origin/main")
    code, text = gate(site.owner, "--release")
    assert code == 1
    assert (f"file 'release.json' names the commit {deployed[:12]}, which is not in the history of this checkout "
            "(the clone holds it on another line of history)") in text
    _git(site.owner, "tag", "2.3.0", "origin/main")
    _git(site.owner, "push", "-q", "origin", "2.3.0")
    code, text = gate(_tag_clone(site, "2.3.0"), "--release")
    assert code == 1
    assert f"file 'release.json' names the commit {deployed[:12]}, which this clone does not hold" in text


def test_a_change_to_the_procedure_text_starts_the_workflow():
    """The test of the procedure reads CONTRIBUTING.md. A pull request that
    changes only that file has to start the workflow that runs the test, or
    the failure shows up on somebody else's change."""
    workflow = REPO_ROOT / ".github" / "workflows" / "verify-model-definitions.yml"
    if not workflow.is_file():
        pytest.skip(f"{workflow} is not in this checkout")
    text = workflow.read_text(encoding="utf-8").replace("\r\n", "\n")
    push, _, pull_request = text.partition("pull_request:")
    for trigger, block in (("push", push), ("pull_request", pull_request.split("jobs:", 1)[0])):
        assert '\n      - "CONTRIBUTING.md"\n' in block, trigger


def _with_commit(package_path: Path, commit: str) -> None:
    """Write another commit into the release.json of a package file - a
    release.json edited by hand."""
    package = json.loads(package_path.read_text(encoding="utf-8"))
    for detail in package["transferDetails"]:
        obj = detail["transferObject"]
        if obj["summary"]["name"] == "release.json":
            body = _body(obj)
            info = json.loads(__import__("base64").b64decode(body["content"]))
            info["commit"] = commit
            body["content"] = b64(json.dumps(info, indent=2) + "\n")
            obj["content"] = b64(body)
    package_path.write_text(json.dumps(package, indent=2), encoding="utf-8", newline="\n")


@needs_git
def test_an_abbreviated_commit_id_is_no_commit_id(tmp_path, gate):
    """review-19 / gate-19: the check took 4 to 64 hexadecimal digits while
    its message said git names a commit with 40; `head[:7]` of an ancestor
    passed --release. Only an id in full is one."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    make_repo(checkout)
    _git(checkout, "init", "-q")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "first")
    head = _deploy_and_export(checkout)
    assert re.fullmatch(r"[0-9a-f]{40}", head)
    assert content.commit_standing(checkout, head) == content.ANCESTOR
    for text in (head[:4], head[:7], head[:39], head + "0", head.upper(), "HEAD", "main", ""):
        assert content.commit_standing(checkout, text) == content.NOT_A_COMMIT, text
        assert content.commit_standing(None, text) == content.NOT_A_COMMIT, text
    # 64 digits are the id of a SHA-256 repository: a commit id in full, which this clone does not hold
    assert content.commit_standing(checkout, "0" * 64) == content.NOT_HELD
    assert content.commit_standing(checkout, "0" * 40) == content.NOT_HELD
    package_path = load_manifest(checkout).package_path
    assert gate(checkout, "--release")[0] == 0
    _with_commit(package_path, head[:7])
    for form in ((), ("--release",)):                             # wrong whatever the checkout holds: both forms
        code, text = gate(checkout, *form)
        assert code == 1
        assert (f"SAS-Agentic-AI-Accelerator.json: file 'release.json' names the commit '{head[:7]}', which is no "
                "commit id in full (git writes one with 40 hexadecimal digits, 64 in a SHA-256 repository)\n") in text
    # without a checkout to ask, the form alone is judged - the same form
    listed = load_manifest(checkout)
    entry = listed.find("release.json", "file")
    expected = content.file_bytes(listed, entry)
    short = json.dumps({**json.loads(expected), "commit": head[:12]}).encode("utf-8")
    assert content.generated_findings(entry, expected, short) == (
        [f"names the commit '{head[:12]}', which is no commit id in full (git writes one with 40 hexadecimal "
         "digits, 64 in a SHA-256 repository)"], [])
    assert content.generated_findings(entry, expected, expected) == ([], [])
