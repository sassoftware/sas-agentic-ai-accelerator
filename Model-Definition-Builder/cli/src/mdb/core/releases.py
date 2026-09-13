# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The accelerator's release history as a table: CHANGELOG.md -> rows.

The Prompt Builder and RAG Builder objects in SAS Visual Analytics need a data
assignment before the report renders, and the tables the shipped packages used
to bind to (the retired key table, a demo table) meant every importer first had
to have one lying around. This module turns the changelog into a small table an
environment can always provide - ``ACCELERATOR_RELEASES`` - with one row per
release and one per change, so the assignment also tells a report author which
release is deployed and what changed for the Builder they are looking at.

Parsing follows the changelog's own conventions (keep-a-changelog style):

    ## [x.y.z] - date            a release; the paragraph(s) before the first
                                 ### heading are its intro -> one "Release" row
    ### Added|Changed|Fixed|Removed
    ### <anything else>          a themed section (2.0.0 has several); stored
                                 verbatim in `section`, normalised in `change_type`
    - **Lead.** detail           one row; the bold lead is the summary
      - nested / 1. numbered     folded into the parent's detail
    paragraph under a heading    one row as well (the 2.0.0 breaking-change note)

Each change gets ONE component. When the section heading names a component
("RAG Builder: generating what a setup implies") every entry under it belongs
there; otherwise the entry's own wording decides, in a fixed priority so that a
RAG Builder entry that also says "registers" is not filed under the Model
Definition Builder. Nothing here needs SAS Viya; viya/cas.py uploads the CSV.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TABLE = "ACCELERATOR_RELEASES"
TABLE_ENV = "SAS_RELEASES_TABLE"

COLUMNS = (
    "version", "release_date", "is_current", "release_rank", "item_rank",
    "section", "change_type", "component", "summary", "detail",
)

STANDARD_SECTIONS = {"added": "Added", "changed": "Changed", "fixed": "Fixed", "removed": "Removed"}

#: Which part of the accelerator a change talks about, in PRIORITY order: the
#: first component whose keywords match wins. The two Builders come first
#: because their entries routinely mention what they generate, register or
#: publish; a change matching nothing is "General". Short alphabetic tokens
#: are matched on word boundaries so "scr" does not match "scripts".
COMPONENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("RAG Builder", (
        "rag builder", "rag-builder", "rag setup", "test retrieval", "manifest setup",
        "browse ledger", "launch ingestion", "setup editor",
    )),
    ("Prompt Builder", (
        "prompt builder", "prompt-builder", "judge model", "judging", "judge button", "as-a-judge",
        "the judge", "judge this", "judge rank", "prompt experiment", "experiment tracker",
        "prompt-test", "prompt test", "optimiz", "dspy", "manifested prompt", "manifest the best",
        "call llm node", "dist/index.html", "ddc", "properties panel", "options pane",
    )),
    ("RAG runtime", (
        "rag_core", "rag -", "rag ingestion", "rag cost", "ingest", "chunk", "vector store",
        "pgvector", "singlestore", "retriev", "corpus", "enrich", "ledger", "extractor",
    )),
    ("Credentials", ("credential", "api key", "api_key", ".env", "secret")),
    ("Model Definition Builder", (
        "mdb", "model definition builder", "definition.yaml", "adapter", "wizard", "fact sheet",
        "fact-sheet", "register", "publish", "scr", "score code", "scorer", "template", "drift",
        "options.json", "manifest ", "requirements.json",
    )),
    ("Definitions", (
        "llm-definitions", "embedding-definitions", "llm definitions", "embedding model",
        "gpt", "claude", "gemini", "llama", "phi", "qwen", "mistral", "voyage", "bedrock",
        "azure", "openrouter", "ollama", "vllm", "hugging face",
    )),
    ("Documentation", ("documentation", "guide", "readme", "changelog", "docs")),
)
GENERAL = "General"

_RELEASE = re.compile(r"^## \[(?P<version>[^\]]+)\]\s*-\s*(?P<date>.+?)\s*$")
_SECTION = re.compile(r"^### (?P<title>.+?)\s*$")
_BULLET = re.compile(r"^- (?P<text>.*)$")
_NESTED = re.compile(r"^\s{2,}- (?P<text>.*)$")
_NUMBERED = re.compile(r"^\s*\d+\.\s+(?P<text>.*)$")
_LEAD = re.compile(r"^\*\*(?P<lead>.+?)\*\*\s*(?P<rest>.*)$", re.S)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_EMOJI = re.compile(r"[☀-➿\U0001F300-\U0001FAFF️]")


@dataclass
class Release:
    version: str
    date: str
    intro: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)  # section, change_type, text


def _plain(text: str) -> str:
    """Markdown to readable text: links to their label, emphasis and code
    markers dropped, whitespace collapsed."""
    text = _LINK.sub(r"\1", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"(?<!\w)\*(?!\s)([^*]+?)\*(?!\w)", r"\1", text)  # *italic*
    text = _EMOJI.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _split(text: str) -> tuple[str, str]:
    """(summary, detail): the bold lead when the entry has one, else the
    first sentence, capped so a column stays a headline."""
    m = _LEAD.match(text.strip())
    if m:
        return _plain(m.group("lead")).rstrip(".:"), _plain(m.group("rest"))
    plain = _plain(text)
    sentence = re.split(r"(?<=[.!?])\s+", plain, maxsplit=1)
    summary = sentence[0]
    if len(summary) > 140:
        summary = summary[:137].rstrip() + "..."
    detail = plain if len(sentence) > 1 or summary != plain else ""
    return summary.rstrip("."), detail


def _change_type(title: str) -> str:
    key = _plain(title).lower().strip()
    if key in STANDARD_SECTIONS:
        return STANDARD_SECTIONS[key]
    if "breaking" in key:
        return "Breaking"
    return "Changed"


def _matches(keyword: str, text: str) -> bool:
    if len(keyword) <= 4 and keyword.isalpha():
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def components_of(text: str) -> list[str]:
    """Every component the text names, in priority order; [General] if none."""
    lowered = text.lower()
    found = [name for name, words in COMPONENT_KEYWORDS if any(_matches(w, lowered) for w in words)]
    return found or [GENERAL]


def component_for(section: str, text: str) -> str:
    """The one component an entry is filed under: the section's, when the
    heading names one, else the first the entry's wording matches."""
    if section and section.lower() not in STANDARD_SECTIONS:
        by_section = components_of(section)
        if by_section != [GENERAL]:
            return by_section[0]
    return components_of(text)[0]


def parse_changelog(text: str) -> list[Release]:
    releases: list[Release] = []
    current: Release | None = None
    section = change_type = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _RELEASE.match(line)
        if m:
            current = Release(m.group("version").strip(), m.group("date").strip())
            releases.append(current)
            section = change_type = ""
            continue
        if current is None or line.startswith("## ") or line.startswith("# "):
            continue
        m = _SECTION.match(line)
        if m:
            section = _plain(m.group("title"))
            change_type = _change_type(m.group("title"))
            continue
        if not line.strip():
            continue
        m = _NESTED.match(line) or _NUMBERED.match(line)
        if m and section and current.items:
            current.items[-1]["text"] += " " + m.group("text").strip()
            continue
        m = _BULLET.match(line)
        if m:
            body = m.group("text").strip()
            if not section or _plain(body).lower() in ("none", "none."):
                continue
            current.items.append({"section": section, "change_type": change_type, "text": body})
            continue
        if not section:
            current.intro.append(line.strip())
        elif current.items and raw[:1].isspace():
            current.items[-1]["text"] += " " + line.strip()  # wrapped continuation
        else:
            current.items.append({"section": section, "change_type": change_type, "text": line.strip()})
    return releases


def rows_from(releases: list[Release]) -> list[dict]:
    """Flatten to table rows. Rank 1 is the newest release; the current
    release is the newest one that carries a date (an Unreleased section on a
    development checkout is listed but not marked current)."""
    current_version = next((r.version for r in releases if not r.date.lower().startswith("unreleased")), "")
    rows: list[dict] = []
    for rank, release in enumerate(releases, start=1):
        base = {
            "version": release.version,
            "release_date": release.date,
            "is_current": 1 if release.version == current_version else 0,
            "release_rank": rank,
        }
        item_rank = 0
        if release.intro:
            intro = " ".join(_plain(p) for p in release.intro)
            summary, _ = _split(intro)
            rows.append({**base, "item_rank": item_rank, "section": "Release", "change_type": "Release",
                         "component": GENERAL, "summary": summary, "detail": intro})
        for item in release.items:
            item_rank += 1
            summary, detail = _split(item["text"])
            rows.append({**base, "item_rank": item_rank, "section": item["section"],
                         "change_type": item["change_type"],
                         "component": component_for(item["section"], item["text"]),
                         "summary": summary, "detail": detail})
    return rows


def write_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})
    return path


def build_release_table(changelog: Path, csv_path: Path) -> tuple[list[dict], dict]:
    """Parse the changelog and write the CSV; returns (rows, summary) where the
    summary carries counts for the command's report."""
    releases = parse_changelog(changelog.read_text(encoding="utf-8"))
    if not releases:
        raise ValueError(f"{changelog} contains no '## [version] - date' release headings")
    rows = rows_from(releases)
    write_csv(rows, csv_path)
    by_component: dict[str, int] = {}
    for row in rows:
        by_component[row["component"]] = by_component.get(row["component"], 0) + 1
    current = next((r for r in rows if r["is_current"]), None)
    return rows, {
        "releases": len(releases),
        "rows": len(rows),
        "current": current["version"] if current else "",
        "by_component": dict(sorted(by_component.items(), key=lambda kv: -kv[1])),
    }
