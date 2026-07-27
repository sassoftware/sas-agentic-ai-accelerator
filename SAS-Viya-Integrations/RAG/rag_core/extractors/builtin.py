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


class MarkitdownExtractor:
    """Office-family formats via markitdown (owner-accepted default, OQ7)."""
    name = "markitdown"
    formats = {".docx", ".xlsx", ".pptx", ".epub", ".rtf"}
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
    MarkitdownExtractor(),
]
