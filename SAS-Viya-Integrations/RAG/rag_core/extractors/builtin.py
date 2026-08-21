# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Built-in extractors: stdlib-only defaults + pdf-text (pypdfium2) + markitdown.

pdf-text is the default PDF path (design §3): text-layer extraction covers
born-digital PDFs with zero LLM calls. Scanned pages simply yield little or no
text here — the page's text density is recorded so a later vlm-extract tier
can target exactly those pages.
"""
from __future__ import annotations

import csv
import io
import json
import re
from html.parser import HTMLParser

from .base import element

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


class PlaintextExtractor:
    name = "plaintext"
    formats = {".txt", ".log", ".text"}
    requires: list = []

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        text = data.decode(params.get("encoding", "utf-8"), errors="replace")
        return [element(text)] if text.strip() else []


class MarkdownExtractor:
    """Headings become heading elements and feed heading_path for every element."""
    name = "markdown"
    formats = {".md", ".markdown"}
    requires: list = []

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        text = data.decode(params.get("encoding", "utf-8"), errors="replace")
        elements: list = []
        path: list = []          # (level, title)
        buffer: list = []

        def flush():
            if buffer:
                joined = "\n".join(buffer).strip()
                if joined:
                    hp = " > ".join(t for _, t in path) or None
                    elements.append(element(joined, heading_path=hp))
                buffer.clear()

        in_code = False
        for line in text.splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code
                buffer.append(line)
                continue
            match = None if in_code else _MD_HEADING.match(line)
            if match:
                flush()
                level, title = len(match.group(1)), match.group(2).strip()
                while path and path[-1][0] >= level:
                    path.pop()
                path.append((level, title))
                hp = " > ".join(t for _, t in path)
                elements.append(element(title, "heading", level=level, heading_path=hp))
            else:
                buffer.append(line)
        flush()
        return elements


class CsvJsonExtractor:
    """CSV rows / JSON records rendered one-per-line so row boundaries survive chunking."""
    name = "csv_json"
    formats = {".csv", ".tsv", ".json", ".jsonl", ".ndjson"}
    requires: list = []

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        text = data.decode(params.get("encoding", "utf-8"), errors="replace")
        suffix = ("." + source_uri.rsplit(".", 1)[-1].lower()) if "." in source_uri else ""
        if suffix in (".csv", ".tsv"):
            dialect = "excel-tab" if suffix == ".tsv" else "excel"
            rows = list(csv.reader(io.StringIO(text), dialect=dialect))
            if not rows:
                return []
            header = rows[0]
            lines = [
                "; ".join(f"{h}: {v}" for h, v in zip(header, row) if v != "")
                for row in rows[1:]
            ]
            body = "\n".join(line for line in lines if line)
            return [element(body, "table")] if body else []
        try:
            if suffix in (".jsonl", ".ndjson"):
                records = [json.loads(l) for l in text.splitlines() if l.strip()]
            else:
                parsed = json.loads(text)
                records = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            return [element(text)] if text.strip() else []
        lines = [json.dumps(r, ensure_ascii=False, default=str) for r in records]
        return [element("\n".join(lines), "table")] if lines else []


class _HTMLToElements(HTMLParser):
    _SKIP = {"script", "style", "noscript", "head", "template"}
    _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    _BLOCK_ENDS = {"p", "div", "li", "tr", "table", "section", "article", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements: list = []
        self._path: list = []
        self._buffer: list = []
        self._skip_depth = 0
        self._heading_level = 0
        self._heading_text: list = []

    def _hp(self):
        return " > ".join(t for _, t in self._path) or None

    def _flush(self):
        joined = " ".join(self._buffer).strip()
        if joined:
            self.elements.append(element(joined, heading_path=self._hp()))
        self._buffer.clear()

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._HEADINGS and not self._skip_depth:
            self._flush()
            self._heading_level = self._HEADINGS[tag]
            self._heading_text = []

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._HEADINGS and self._heading_level:
            title = " ".join(self._heading_text).strip()
            if title:
                level = self._heading_level
                while self._path and self._path[-1][0] >= level:
                    self._path.pop()
                self._path.append((level, title))
                self.elements.append(element(title, "heading", level=level, heading_path=self._hp()))
            self._heading_level = 0
        elif tag in self._BLOCK_ENDS and not self._skip_depth:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._heading_level:
            self._heading_text.append(data.strip())
        elif data.strip():
            self._buffer.append(data.strip())


class HtmlExtractor:
    name = "html"
    formats = {".html", ".htm", ".xhtml"}
    requires: list = []

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        parser = _HTMLToElements()
        parser.feed(data.decode(params.get("encoding", "utf-8"), errors="replace"))
        parser._flush()
        return parser.elements


class PdfTextExtractor:
    """Text-layer extraction via pypdfium2 — the default PDF path (§3)."""
    name = "pdf-text"
    formats = {".pdf"}
    requires = ["pypdfium2"]

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        import pypdfium2 as pdfium  # lazy: requires is checked by the registry

        elements: list = []
        pdf = pdfium.PdfDocument(data)
        try:
            for page_index in range(len(pdf)):
                page = pdf[page_index]
                try:
                    text = page.get_textpage().get_text_bounded()
                finally:
                    page.close()
                if text and text.strip():
                    elements.append(element(text.strip(), page=page_index + 1))
        finally:
            pdf.close()
        return elements


class EmailExtractor:
    """RFC 822 mail (.eml) — headers as context, then the body.

    Email is the one corpus format where the METADATA is usually the reason
    someone is searching: who wrote it, to whom, when, about what. Those go
    in as a heading element so they are retrievable and so every body chunk
    inherits them through heading_path, rather than being discarded in
    favour of the body text alone.

    Stdlib only: the email package parses this, and multipart mail is walked
    for the first text/plain part, falling back to text/html stripped by the
    HTML extractor.
    """
    name = "email"
    formats = {".eml", ".mht", ".mhtml"}
    requires: list = []

    _HEADERS = ("From", "To", "Cc", "Date", "Subject")

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        import email
        from email import policy

        message = email.message_from_bytes(data, policy=policy.default)
        summary = []
        for name in self._HEADERS:
            value = message.get(name)
            if value:
                summary.append(f"{name}: {' '.join(str(value).split())}")
        subject = str(message.get("Subject") or "").strip()
        heading = " ".join(subject.split()) or "(no subject)"

        body, html_body = "", ""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get_filename():          # attachments are not text
                    continue
                kind = part.get_content_type()
                try:
                    text = part.get_content()
                except Exception:
                    continue
                if kind == "text/plain" and not body:
                    body = str(text)
                elif kind == "text/html" and not html_body:
                    html_body = str(text)
        else:
            try:
                content = str(message.get_content())
            except Exception:
                content = data.decode("utf-8", errors="replace")
            if message.get_content_type() == "text/html":
                html_body = content
            else:
                body = content
        if not body.strip() and html_body.strip():
            parts = HtmlExtractor().extract(html_body.encode("utf-8"),
                                            source_uri, **params)
            body = "\n\n".join(p["text"] for p in parts)

        elements = []
        if summary:
            elements.append(element("\n".join(summary), "heading", level=1,
                                    heading_path=heading))
        if body.strip():
            elements.append(element(body.strip(), "text", heading_path=heading))
        return elements


#: Source files. Ingesting a repository's code alongside its documentation is
#: almost never what someone means by "my documents" - it floods a collection
#: with build scripts and boilerplate that answer no business question - so
#: these are SKIPPED by default and only crawled when a setup opts in
#: (`run_list(..., include_code=True)`). They stay a named class rather than a
#: hard exclusion because a corpus ABOUT code is a legitimate corpus.
CODE_SUFFIXES = {
    ".py", ".sas", ".r", ".js", ".ts", ".jsx", ".tsx", ".sql", ".java", ".c",
    ".h", ".hpp", ".cpp", ".cc", ".cs", ".go", ".rb", ".php", ".swift", ".kt",
    ".scala", ".rs", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".pl",
    ".lua", ".vb", ".vbs", ".groovy", ".ipynb",
}


class CodeTextExtractor:
    """Source files as plain text (owner directive 2026-08-01).

    Code has structure worth parsing, but parsing it per language is a project
    of its own and buys little for retrieval: what a reader searches for in a
    script is usually a comment, an identifier or a literal, and all three
    survive plain text intact. So the rule is deliberately dull - decode it,
    keep it whole - and the file name rides along as heading_path so a hit can
    be attributed to its file without opening it.
    """
    name = "code-text"
    formats = CODE_SUFFIXES
    requires: list = []

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        text = data.decode(params.get("encoding", "utf-8"), errors="replace")
        if not text.strip():
            return []
        filename = source_uri.replace("\\", "/").rsplit("/", 1)[-1]
        return [element(text, heading_path=filename)]


class MarkitdownExtractor:
    """Office-family formats via markitdown (owner-accepted default, OQ7).

    .msg (Outlook) rides along here rather than in the email extractor: it is
    a compound OLE file, not RFC 822, and markitdown already carries the
    reader for it.
    """
    name = "markitdown"
    formats = {".docx", ".xlsx", ".pptx", ".epub", ".rtf", ".msg"}
    requires = ["markitdown"]

    def extract(self, data: bytes, source_uri: str, **params) -> list:
        from markitdown import MarkItDown  # lazy

        suffix = "." + source_uri.rsplit(".", 1)[-1].lower() if "." in source_uri else ""
        result = MarkItDown().convert_stream(io.BytesIO(data), file_extension=suffix)
        markdown = getattr(result, "text_content", None) or ""
        if not markdown.strip():
            return []
        return MarkdownExtractor().extract(markdown.encode("utf-8"), source_uri, **params)


BUILTINS = [
    PlaintextExtractor(),
    MarkdownExtractor(),
    CsvJsonExtractor(),
    HtmlExtractor(),
    PdfTextExtractor(),
    EmailExtractor(),
    MarkitdownExtractor(),
    CodeTextExtractor(),
]
