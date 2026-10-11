# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The summary of RAG - Extract Text counts a document under its status.

The step extracts the new and the changed documents and passes every other
row of the inventory on. Its summary named all of those "skipped as
unchanged": the count was what is left of the inventory after the extracted
and the failed ones. Found on a live system with one source-code file in the
folder - RAG - List Documents said `4 documents in scope (excluded=1,
unchanged=3)` and `3 skipped as unchanged`, and RAG - Extract Text of the
same run `0 documents extracted, 0 failed, 4 skipped as unchanged`.

The Python of the step is run here as PROC PYTHON would run it, against a
stub of SAS, of CAS and of SAS Viya: the rag_core folder of the stub is
empty, so the step imports the rag_core of this repository. No SAS runs.
"""
import json
import pathlib
import sys
import tempfile
import types

RAG = pathlib.Path(__file__).resolve().parents[1]
STEP = RAG.parent / "Custom-Steps" / "RAG - Extract Text.step"
BASE = "https://viya.example"


def _python():
    sas = json.loads(STEP.read_text(encoding="utf-8"))["templates"]["SAS"]
    return sas.split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


class _Reply:
    status_code = 200
    content = b""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _Sas:
    """PROC PYTHON's SAS object: macro variables, the log, the work path."""

    def __init__(self, workpath, macros):
        self.workpath = str(workpath)
        self.macros = dict(macros)
        self.log = []

    def symget(self, name):
        return self.macros.get(name, "")

    def symput(self, name, value):
        self.macros[name] = value

    def logMessage(self, message, *_):
        self.log.append(message)


class _Cas:
    """The CAS session of the step: it takes the staged elements."""

    def __init__(self, **_):
        self.uploaded = []
        self.table = types.SimpleNamespace(alterTable=lambda **_: None)

    def upload_frame(self, frame, **_):
        self.uploaded.append(frame)

    def close(self, **_):
        pass


def _row(name, status, folder):
    return {"doc_id": name, "source_uri": str(folder / name),
            "source_kind": "path", "status": status, "content_hash": "h",
            "pipeline_version": "v1", "config_hash": "cfg-1", "chunk_count": 0,
            "run_id": "run-1791724976-344", "rag_project": "RAG_HR",
            "tables_caslib": "casuser"}


def _run_step(monkeypatch, tmp_path, statuses):
    """Run the step on an inventory with one row per entry of `statuses`
    (file name -> status). Returns the parts of its summary."""
    import requests

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in statuses:
        (corpus / name).write_text("The last shuttle leaves at 19:10.\n",
                                   encoding="utf-8")
    (tmp_path / "rgex_inventory_in.json").write_text(json.dumps(
        [_row(name, status, corpus) for name, status in statuses.items()]),
        encoding="utf-8")
    sas = _Sas(tmp_path, {
        "_rgex_state": "run", "_rgex_viyaHost": BASE,
        "_rgex_ragProject": "RAG_HR", "_rgex_tablesCaslib": "casuser",
        "_rgex_extractor": "auto", "_rgex_cashost": "cas.example",
        "_rgex_casport": "5570", "_rgex_casuuid": "session-1",
    })

    def _get(url, **kwargs):
        assert url.startswith(BASE), url
        if url.endswith("/folders/folders/@item"):
            return _Reply({"id": "rag-core"})
        assert url.endswith("/members"), url
        return _Reply({"items": []})

    monkeypatch.setitem(sys.modules, "swat", types.SimpleNamespace(CAS=_Cas))
    monkeypatch.setattr(requests, "get", _get)
    # the step puts its download of rag_core first on sys.path
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(tempfile, "mkdtemp",
                        lambda **_: str(tmp_path / "rag_core_staging"))

    exec(compile(_python(), "RAG - Extract Text.step", "exec"), {"SAS": sas})

    assert sas.macros.get("_rgex_rc") == "0", sas.macros.get("_rgex_error")
    return [part.strip() for part in sas.macros["_rgex_summary"].split("|")]


def _counts(summary):
    return next(part for part in summary if "documents extracted" in part)


def test_an_excluded_document_is_not_counted_as_unchanged(monkeypatch, tmp_path):
    """The run that was measured: three unchanged documents and one code
    file the setup excludes."""
    summary = _run_step(monkeypatch, tmp_path, {
        "zz_lockers.md": "unchanged", "zz_printer.txt": "unchanged",
        "zz_shuttle.txt": "unchanged", "ZZ_VERIFY_code_sample.py": "excluded"})
    assert _counts(summary) == ("0 documents extracted, 0 failed, "
                                "3 skipped as unchanged, 1 excluded")


def test_every_document_is_counted_once_under_what_it_is(monkeypatch, tmp_path):
    summary = _run_step(monkeypatch, tmp_path, {
        "new.txt": "new", "changed.txt": "changed", "same.txt": "unchanged",
        "gone.txt": "deleted", "script.py": "excluded",
        # a format nothing reads: List passes it on as new, this step skips it
        "picture.zzimage": "new"})
    assert _counts(summary) == ("2 documents extracted, 0 failed, "
                                "1 skipped as unchanged, 1 deleted, "
                                "1 excluded, 1 skipped")


def test_a_run_of_unchanged_documents_reads_as_before(monkeypatch, tmp_path):
    summary = _run_step(monkeypatch, tmp_path, {
        "a.txt": "unchanged", "b.txt": "unchanged"})
    assert _counts(summary) == ("0 documents extracted, 0 failed, "
                                "2 skipped as unchanged")
