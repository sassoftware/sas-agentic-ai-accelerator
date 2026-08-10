# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Document sources: the filesystem crawl and the SAS Content crawl (§3).

The SAS Content tests run against a stub files/folders service - the live
contract itself is verified on the server, not here.
"""
import pytest

from rag_core.extractors import ExtractorRegistry
from rag_core.sources import (ContentSource, FileSystemSource, make_source,
                              reader_for)
from rag_core.steps import COLUMN_LABELS, LEDGER_COLUMNS, column_labels, run_extract, run_list


# ---------------------------------------------------------------------------
# filesystem
# ---------------------------------------------------------------------------
@pytest.fixture()
def corpus(tmp_path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("# beta", encoding="utf-8")
    return tmp_path


def test_filesystem_source_walks_recursively(corpus):
    entries = FileSystemSource(str(corpus)).entries()
    assert len(entries) == 2
    assert all(entry["source_kind"] == "path" for entry in entries)


def test_filesystem_source_filters_suffixes(corpus):
    entries = FileSystemSource(str(corpus)).entries(include_suffixes={".md"})
    assert [e["source_uri"].endswith("b.md") for e in entries] == [True]


def test_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        FileSystemSource(str(tmp_path / "nope")).entries()


# ---------------------------------------------------------------------------
# SAS Content
# ---------------------------------------------------------------------------
class _Response:
    def __init__(self, payload=None, content=b"", status=200):
        self._payload = payload
        self.content = content
        self.status_code = status
        self.text = ""

    def json(self):
        return self._payload


class _StubService:
    """Two folders: /Docs (policy.md, guide.pdf) and /Docs/Old (old.txt).

    Mirrors the live contract probed on the server: folder MEMBERS list a
    folder's contents (files and subfolders), and per-file metadata comes
    from /files/files/{id}. `/files/files?parentFolderUri=` is deliberately
    not implemented - the real service ignores that parameter.
    """

    TREE = {
        "/Docs": ("folder-docs", [
            {"id": "f1", "name": "policy.md", "fileVersion": 1, "size": 10,
             "modifiedTimeStamp": "2026-07-01T00:00:00Z"},
            {"id": "f2", "name": "guide.pdf", "fileVersion": 1, "size": 20,
             "modifiedTimeStamp": "2026-07-02T00:00:00Z"},
        ]),
        "/Docs/Old": ("folder-old", [
            {"id": "f3", "name": "old.txt", "fileVersion": 3, "size": 5,
             "modifiedTimeStamp": "2026-06-01T00:00:00Z"},
        ]),
        # a folder that does not fit in one page, for the paging tests
        "/Big": ("folder-big", [
            {"id": f"b{n}", "name": f"doc-{n:02d}.md", "fileVersion": 1,
             "size": n, "modifiedTimeStamp": "2026-07-01T00:00:00Z"}
            for n in range(1, 13)
        ]),
    }
    BYTES = {"f1": b"# Vacation\n\nThirty days.", "f3": b"legacy note"}

    def __init__(self):
        self.headers = {}
        self.calls = []
        #: set by a test to imitate a service that ignores `start` - the shape
        #: that would turn paging into an endless loop
        self.ignore_start = False

    def _meta(self, file_id):
        for _path, (_fid, files) in self.TREE.items():
            for meta in files:
                if meta["id"] == file_id:
                    return meta
        return None

    def get(self, url, params=None, **_kwargs):
        self.calls.append(url)
        params = params or {}
        if url.endswith("/folders/folders/@item"):
            path = params["path"]
            if path not in self.TREE:
                return _Response({}, status=404)
            return _Response({"id": self.TREE[path][0]})
        if "/members" in url:
            folder_id = url.split("/folders/folders/")[1].split("/")[0]
            items = []
            for _path, (fid, files) in self.TREE.items():
                if fid == folder_id:
                    items = [{"name": f["name"],
                              "uri": "/files/files/" + f["id"]} for f in files]
            if folder_id == "folder-docs":
                items.append({"name": "Old", "uri": "/folders/folders/folder-old"})
            # the real collection is paged, and answers with the full count
            start = 0 if self.ignore_start else int(params.get("start") or 0)
            limit = int(params.get("limit") or 500)
            return _Response({"items": items[start:start + limit],
                              "start": start, "count": len(items)})
        if "/files/files/" in url and url.endswith("/content"):
            file_id = url.split("/files/files/")[1].split("/")[0]
            return _Response(content=self.BYTES.get(file_id, b""))
        if "/files/files/" in url:
            meta = self._meta(url.rsplit("/", 1)[-1])
            return _Response(meta) if meta else _Response({}, status=404)
        raise AssertionError(f"unexpected call: {url}")


def content_source(root="/Docs"):
    return ContentSource("https://sas.example", "token", root, session=_StubService())


def test_content_source_walks_subfolders():
    entries = content_source().entries()
    assert [e["source_uri"] for e in entries] == [
        "/Docs/Old/old.txt", "/Docs/guide.pdf", "/Docs/policy.md"]
    assert all(e["source_kind"] == "content" for e in entries)


def test_content_fingerprint_follows_the_file_version():
    source = content_source()
    entries = {e["source_uri"]: e for e in source.entries()}
    before = source.fingerprint(entries["/Docs/policy.md"])
    for changed in (dict(entries["/Docs/policy.md"], _version="9"),
                    dict(entries["/Docs/policy.md"], _size="999"),
                    dict(entries["/Docs/policy.md"], mtime="2026-07-09T00:00:00Z")):
        assert source.fingerprint(changed) != before
    # a second read of the unchanged file gives the same fingerprint
    assert source.fingerprint(entries["/Docs/policy.md"]) == before


def test_a_folder_listing_never_leaks_other_folders():
    """The live files service ignores parentFolderUri - members must be used."""
    source = content_source()
    assert {e["source_uri"] for e in source.entries()} == {
        "/Docs/policy.md", "/Docs/guide.pdf", "/Docs/Old/old.txt"}
    assert not any("parentFolderUri" in call for call in source.session.calls)


def test_content_source_reads_bytes():
    source = content_source()
    source.entries()
    assert source.read("/Docs/policy.md").startswith(b"# Vacation")


# -- paging -----------------------------------------------------------------
# A partial listing here is not a partial result. `run_list` decides a document
# was DELETED by not seeing it, so a crawl that stopped after one page would
# have run_load retire - or with deleted_policy='purge' delete - every chunk of
# every document on the pages nobody asked for, while those documents sit
# untouched at the source.
def test_a_folder_larger_than_one_page_is_listed_whole(monkeypatch):
    monkeypatch.setattr(ContentSource, "_PAGE", 5)
    entries = content_source("/Big").entries()
    assert len(entries) == 12
    assert entries[0]["source_uri"] == "/Big/doc-01.md"
    assert entries[-1]["source_uri"] == "/Big/doc-12.md"


def test_a_service_that_never_advances_is_refused_rather_than_looped(monkeypatch):
    monkeypatch.setattr(ContentSource, "_PAGE", 5)
    monkeypatch.setattr(ContentSource, "_MAX_PAGES", 3)
    source = content_source("/Big")
    source.session.ignore_start = True
    with pytest.raises(RuntimeError, match="repeats itself"):
        source.entries()


def test_each_folder_is_listed_once_per_visit():
    """The crawl asks two questions per folder; it must not page it twice."""
    source = content_source()
    source.entries()
    assert len([call for call in source.session.calls
                if "/members" in call]) == 2      # /Docs and /Docs/Old


def test_content_reader_resolves_by_path_without_a_crawl():
    """Extract has no document-folder field - it resolves the ledger's URI."""
    source = ContentSource("https://sas.example", "token", "/", session=_StubService())
    assert source.read("/Docs/Old/old.txt") == b"legacy note"


def test_list_then_extract_over_sas_content():
    source = content_source()
    inventory = run_list(source, [], "run-1", "v1", "", log=lambda *_: None)
    assert {row["status"] for row in inventory} == {"new"}
    elements, updated = run_extract(inventory, ExtractorRegistry(),
                                    source=source, log=lambda *_: None)
    by_uri = {row["source_uri"]: row for row in updated}
    assert by_uri["/Docs/policy.md"]["status"] == "new"
    assert any("Thirty days" in el["text"] for el in elements)
    # guide.pdf has no stub bytes -> per-document failure, never an exception
    assert by_uri["/Docs/guide.pdf"]["status"] == "failed"


def test_second_run_sees_no_change():
    source = content_source()
    first = run_list(source, [], "run-1", "v1", "", log=lambda *_: None)
    second = run_list(content_source(), first, "run-2", "v1", "", log=lambda *_: None)
    assert {row["status"] for row in second} == {"unchanged"}


def test_extract_without_a_content_reader_fails_the_document():
    inventory = run_list(content_source(), [], "run-1", "v1", "", log=lambda *_: None)
    _elements, updated = run_extract(inventory, ExtractorRegistry(),
                                     log=lambda *_: None)
    assert {row["status"] for row in updated} == {"failed"}
    assert "not available in this step" in updated[0]["error_text"]


# ---------------------------------------------------------------------------
# selector values and labels
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("sasserver:/data/docs", FileSystemSource),
    ("/data/docs", FileSystemSource),
    ("sascontent:/Docs", ContentSource),
])
def test_make_source_reads_the_selector_scheme(value, expected):
    source = make_source(value, "https://sas.example", "token")
    assert isinstance(source, expected)


def test_make_source_strips_the_server_scheme():
    assert make_source("sasserver:/data/docs").root == "/data/docs"


def test_reader_for_only_builds_what_needs_a_service():
    assert reader_for("path") is None
    assert isinstance(reader_for("content", "https://sas.example", "t"), ContentSource)


def test_every_ledger_column_is_labelled():
    labels = column_labels(LEDGER_COLUMNS + ["extractor"])
    assert set(labels) == set(LEDGER_COLUMNS + ["extractor"])
    assert all(label and label[0].isupper() for label in COLUMN_LABELS.values())
