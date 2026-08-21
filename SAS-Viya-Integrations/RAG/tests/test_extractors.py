# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest

from rag_core.extractors import ExtractorRegistry


@pytest.fixture()
def registry():
    return ExtractorRegistry()


def test_plaintext(registry):
    elements, used = registry.extract(b"hello\nworld", "note.txt")
    assert used == "plaintext"
    assert elements[0]["text"] == "hello\nworld"


def test_markdown_heading_path(registry):
    md = b"# Title\n\nIntro text.\n\n## Section A\n\nBody of A.\n"
    elements, used = registry.extract(md, "doc.md")
    assert used == "markdown"
    body = [e for e in elements if e["type"] == "text"]
    assert body[0]["heading_path"] == "Title"
    assert body[1]["heading_path"] == "Title > Section A"


def test_markdown_code_fence_not_parsed_as_heading(registry):
    md = b"```\n# not a heading\n```\ntext after\n"
    elements, _ = registry.extract(md, "doc.md")
    assert not any(e["type"] == "heading" for e in elements)


def test_csv_rows_labelled(registry):
    csv_bytes = b"name,role\nAda,Engineer\nGrace,Admiral\n"
    elements, used = registry.extract(csv_bytes, "people.csv")
    assert used == "csv_json"
    assert elements[0]["type"] == "table"
    assert "name: Ada; role: Engineer" in elements[0]["text"]


def test_json_records(registry):
    elements, _ = registry.extract(b'[{"a": 1}, {"a": 2}]', "data.json")
    assert elements[0]["type"] == "table"
    assert '"a": 1' in elements[0]["text"]


def test_html_headings_and_skip_script(registry):
    html = (b"<html><head><script>var x=1;</script></head><body>"
            b"<h1>Main</h1><p>Hello there.</p>"
            b"<h2>Sub</h2><p>Deep text.</p></body></html>")
    elements, used = registry.extract(html, "page.html")
    assert used == "html"
    assert not any("var x" in e["text"] for e in elements)
    text = [e for e in elements if e["type"] == "text"]
    assert text[0]["heading_path"] == "Main"
    assert text[1]["heading_path"] == "Main > Sub"


def _minimal_pdf(text: str) -> bytes:
    """Handcraft a one-page PDF with a real text layer (no authoring API needed)."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def test_pdf_text_roundtrip(registry):
    pytest.importorskip("pypdfium2")
    elements, used = registry.extract(_minimal_pdf("Verified ground truth"), "t.pdf")
    assert used == "pdf-text"
    assert any("Verified ground truth" in e["text"] for e in elements)
    assert elements[0]["page"] == 1


def test_unknown_format_raises(registry):
    with pytest.raises(LookupError):
        registry.extract(b"data", "movie.mp4")


def test_unavailable_extractor_has_clear_error():
    registry = ExtractorRegistry()

    class Fake:
        name = "needs-nothing-real"
        formats = {".xyz"}
        requires = ["package_that_does_not_exist_xyz"]

        def extract(self, data, source_uri, **params):
            return []

    registry.register(Fake())
    with pytest.raises(LookupError, match="package_that_does_not_exist_xyz"):
        registry.get("needs-nothing-real")
