# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The accelerator's release history as a table: CHANGELOG.md -> rows.

The Prompt Builder object in SAS Visual Analytics needs a data assignment
before the report renders, and the tables the shipped packages used to bind to
(the retired key table, a demo table) meant every importer first had to have
one lying around. This module turns the changelog into a small table an
environment can always provide - ``ACCELERATOR_RELEASES`` - with one row per
release and one per change (a change to the Builder: one per part of it, see
below), so the assignment also tells a report author which release is deployed
and what changed for the Builder they are looking at.

Parsing follows the changelog's own conventions (keep-a-changelog style):

    ## [x.y.z] - date            a release; the paragraph(s) before the first
                                 ### heading are its intro -> one "Release" row
    ### Added|Changed|Fixed|Removed
    ### <anything else>          a themed section (2.0.0 has several); stored
                                 verbatim in `section`, normalised in `change_type`
                                 ("Breaking" / "Upgrade" for sections named so)
    - **Lead.** detail           one row; the bold lead is the summary
      - nested / 1. numbered     folded into the parent's detail
    paragraph under a heading    one row as well (the 2.0.0 breaking-change note)

Each change gets ONE component. When the section heading names a component
("RAG Builder: generating what a setup implies") every entry under it belongs
there; otherwise the entry's own wording decides, in a fixed priority so that a
Builder entry that also says "registers" is not filed under the Model
Definition Builder - and by release: the RAG runtime came with 2.0.0, so no
older entry is filed there. Nothing here needs SAS Viya; viya/cas.py uploads
the CSV.

The RAG Builder is a part of the Prompt Builder, so what names it is filed
under "Prompt Builder": that is the value the report's Changelog page filters
on, in reports already imported as much as in new ones. "RAG runtime" - the
steps, rag_core, the stores - stays a component of its own.

The Builder holds three parts in one report - prompt templates, decision
templates, RAG setups - and the last column, ``builder_part``, says which of
them an entry concerns: "Prompt", "Decision" or "RAG". It is made for a button
bar, which shows the rows of ONE value, so an entry of the component "Prompt
Builder" is written ONCE PER PART it concerns: the same row, in the order
Prompt, Decision, RAG, with only `builder_part` different. One that names no
part concerns the Builder as a whole and is written for every part its release
had. A "RAG runtime" row carries "RAG", once; the rows of every other
component and the release intros carry an empty value. Every other column
means what it meant, so (release_rank, item_rank) still counts a change once.
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
    "section", "change_type", "component", "summary", "detail", "builder_part",
)

STANDARD_SECTIONS = {"added": "Added", "changed": "Changed", "fixed": "Fixed", "removed": "Removed"}

#: Which part of the accelerator a change talks about, in PRIORITY order: the
#: first component whose keywords match wins. The Prompt Builder comes first
#: because its entries routinely mention what it generates, registers or
#: publishes - and its RAG part what it ingests and retrieves, which would
#: otherwise read as the RAG runtime; a change matching nothing is "General".
#: Short alphabetic tokens are matched on word boundaries so "scr" does not
#: match "scripts".
COMPONENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Prompt Builder", (
        # its RAG part
        "rag builder", "rag-builder", "rag setup", "test retrieval", "manifest setup",
        "browse ledger", "launch ingestion", "setup editor",
        # prompts, judging, optimization, and the embedding in Visual Analytics
        "prompt builder", "prompt-builder", "judge model", "judging", "judge button", "as-a-judge",
        "the judge", "judge this", "judge rank", "prompt experiment", "experiment tracker",
        "prompt-test", "prompt test", "optimiz", "dspy", "manifested prompt", "manifest the best",
        "call llm node", "dist/index.html", "ddc", "properties panel", "options pane",
        "best prompt", "both builders",
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
#: By release, as for the parts below: a component that did not exist yet
#: cannot be concerned. rag_core, the steps and the stores came with 2.0.0 -
#: what an older entry "retrieves" or "enriches" is something else, and it is
#: filed under the next component its wording matches.
COMPONENT_SINCE = {"RAG runtime": (2, 0, 0)}

#: The three parts of the Prompt Builder, in the order a shared entry is
#: written, and the component whose entries are told apart by them.
PARTS = ("Prompt", "Decision", "RAG")
BUILDER = "Prompt Builder"
#: A component that is one part by itself: its rows carry that part, once.
PART_OF_COMPONENT = {"RAG runtime": "RAG"}

#: What names a part. Unlike the components these are not a priority list:
#: every part an entry names gets a row. "decision" alone names nothing - a
#: SAS Intelligent Decisioning decision uses a prompt as much as a RAG setup -
#: and neither does "embedding": `mdb setup` creates an embedding project. A
#: keyword never starts inside a word ("prompt" is not in "unprompted"), and a
#: short one is a whole word, as for the components.
PART_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Prompt", (
        "prompt", "judge", "judging", "council", "optimiz", "dspy", "best response",
        "llm run", "llm call", "call llm",
    )),
    ("Decision", (
        "decision template", "decision mode", "decision view", "decision kind", "decision model",
        "decision run", "decision project", "decision definition", "decision manifest",
        "decision-template",
    )),
    ("RAG", (
        "rag", "retrieval", "ingestion", "vector", "ledger", "chunk", "manifest setup",
    )),
)
#: One flow serves both kinds of template: a prompt-test is the object behind
#: a prompt template as much as behind a decision template, and both are run
#: and saved as experiments. A headline about the flow names both parts.
FLOW_KEYWORDS = ("prompt-test", "prompt test", "prompt flow", "experiment", "manifest best prompt")
FLOW_PARTS = ("Prompt", "Decision")
#: An entry that says so itself concerns every part, whatever else it names.
#: "Both builders" are the Prompt Builder and the RAG Builder of 2.0.0 to
#: 2.2.x, which is every part those releases had.
EVERY_PART_PHRASES = ("three kinds", "every kind", "all four create dialogs", "both builders")

#: By release: what did not exist yet cannot be concerned.
#:  - BUILDER_SINCE is the Builder as a whole, which is what an entry that
#:    names no part is about: prompts alone until the RAG Builder came into
#:    this repository (2.0.0), no decision templates before the decision mode
#:    (2.2.0).
#:  - NAMED_SINCE is the release from which a keyword can mean the part at
#:    all. The RAG Builder has none: 0.1.14 names it, when it lived in another
#:    repository, and an entry that names it is about it.
BUILDER_SINCE = {"Prompt": (0,), "Decision": (2, 2, 0), "RAG": (2, 0, 0)}
NAMED_SINCE = {"Decision": (2, 2, 0)}
#: Since 2.3.0 "Prompt Builder" is the name of the whole: it names no part,
#: and a headline that names it and no part is about all of it. Before, it
#: was the UI for prompts beside the RAG Builder, so there the name counts as
#: the keyword "prompt" that it contains.
MERGED_SINCE = (2, 3, 0)
_BUILDER_NAME = re.compile(r"prompt[- ]builder")
#: What is named without being concerned: "RAG runtime" is the other
#: component, and a comparison ("as in the Prompt Builder") names the Builder
#: an entry is measured against.
_NOT_CONCERNED = re.compile(r"rag[- ]runtime|\bas (?:in |of )?the (?:prompt|rag)[- ]builder")

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
    # "Upgrading from 2.0.x": the steps a site follows, not a change it gets
    if "upgrad" in key:
        return "Upgrade"
    return "Changed"


def _matches(keyword: str, text: str) -> bool:
    if len(keyword) <= 4 and keyword.isalpha():
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def _release_key(version: str) -> tuple:
    """The version as numbers, 2.3.0 -> (2, 3, 0), to tell older from newer.
    A heading without a number ("Unreleased") is the newest there can be."""
    numbers = re.findall(r"\d+", version)
    return tuple(int(n) for n in numbers[:3]) if numbers else (float("inf"),)


def components_of(text: str, version: str = "") -> list[str]:
    """Every component the text names, in priority order; [General] if none.
    With a version, only the components its release had."""
    lowered = text.lower()
    release = _release_key(version)
    found = [name for name, words in COMPONENT_KEYWORDS
             if release >= COMPONENT_SINCE.get(name, (0,)) and any(_matches(w, lowered) for w in words)]
    return found or [GENERAL]


def component_for(section: str, text: str, version: str = "") -> str:
    """The one component an entry is filed under: the section's, when the
    heading names one, else the first the entry's wording matches."""
    if section and section.lower() not in STANDARD_SECTIONS:
        by_section = components_of(section, version)
        if by_section != [GENERAL]:
            return by_section[0]
    return components_of(text, version)[0]


def _names(keyword: str, text: str) -> bool:
    whole_word = r"\b" if len(keyword) <= 4 and keyword.isalpha() else ""
    return re.search(rf"\b{re.escape(keyword)}{whole_word}", text) is not None


def _named_parts(text: str, release: tuple, flow: bool = True) -> list[str]:
    """The parts the text names, in the order of PARTS; [] if it names none.
    `flow` is off where only a part's own name may count."""
    lowered = _NOT_CONCERNED.sub(" ", text.lower())
    if release >= MERGED_SINCE:
        lowered = _BUILDER_NAME.sub("builder", lowered)
    named = {part for part, words in PART_KEYWORDS if any(_names(w, lowered) for w in words)}
    if flow and any(_names(w, lowered) for w in FLOW_KEYWORDS):
        named.update(FLOW_PARTS)
    return [part for part in PARTS if part in named and release >= NAMED_SINCE.get(part, (0,))]


def parts_for(version: str, section: str, change_type: str, text: str) -> list[str]:
    """The parts of the Builder an entry of the component "Prompt Builder"
    concerns, in the order of PARTS - one row is written for each.

    The section decides when its heading names a part, as it does for the
    component; the steps of an upgrade are everybody's, and so is an entry
    that says it holds for every kind. Otherwise the headline decides: the
    parts it names, or the whole Builder when it names that and no part. A
    headline that settles nothing leaves it to the text, and an entry that
    names no part anywhere concerns the Builder as a whole: every part its
    release had.
    """
    release = _release_key(version)
    whole = [part for part in PARTS if release >= BUILDER_SINCE[part]]
    if section and section.lower() not in STANDARD_SECTIONS:
        by_section = _named_parts(section, release)
        if by_section:
            return by_section
    if change_type == "Upgrade":
        return whole
    headline, detail = _split(text)
    if any(phrase in _plain(text).lower() for phrase in EVERY_PART_PHRASES):
        return whole
    named = _named_parts(headline, release)
    if named == ["Prompt"] and "Decision" in _named_parts(detail, release, flow=False):
        # a headline about prompts alone, and a text that says the decision
        # templates behave the same
        return ["Prompt", "Decision"]
    if named:
        return named
    if release >= MERGED_SINCE and _BUILDER_NAME.search(headline.lower()):
        return whole
    return _named_parts(detail, release) or whole


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
                         "component": GENERAL, "summary": summary, "detail": intro, "builder_part": ""})
        for item in release.items:
            item_rank += 1
            summary, detail = _split(item["text"])
            component = component_for(item["section"], item["text"], release.version)
            if component == BUILDER:
                parts = parts_for(release.version, item["section"], item["change_type"], item["text"])
            else:
                parts = [PART_OF_COMPONENT.get(component, "")]
            for part in parts:
                rows.append({**base, "item_rank": item_rank, "section": item["section"],
                             "change_type": item["change_type"], "component": component,
                             "summary": summary, "detail": detail, "builder_part": part})
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
    by_part = {part: 0 for part in PARTS}
    for row in rows:
        by_component[row["component"]] = by_component.get(row["component"], 0) + 1
        if row["builder_part"]:
            by_part[row["builder_part"]] += 1
    current = next((r for r in rows if r["is_current"]), None)
    return rows, {
        "releases": len(releases),
        "rows": len(rows),
        # what the changelog holds, each counted once: a Builder entry that
        # concerns two parts is two rows and one entry
        "entries": len({(r["release_rank"], r["item_rank"]) for r in rows}),
        "current": current["version"] if current else "",
        "by_component": dict(sorted(by_component.items(), key=lambda kv: -kv[1])),
        "by_part": by_part,
    }
