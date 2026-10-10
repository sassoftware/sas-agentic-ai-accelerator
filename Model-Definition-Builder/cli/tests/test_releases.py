# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CHANGELOG.md -> the ACCELERATOR_RELEASES table.

The table is what a report author assigns to the Prompt Builder object, and
its `component` column is what the report's Changelog page filters on - so the
parser must survive the changelog's real shapes (themed sections, nested and
numbered lists, paragraphs under a heading, "None" placeholders) and the
component tagging must put Builder changes where that filter finds them. The
filter is `component = 'Prompt Builder'`, and the RAG Builder is a part of the
Prompt Builder: a change to it must land under that value too.
"""
import csv
from pathlib import Path

import pytest

from mdb.core.releases import (
    COLUMNS, COMPONENT_KEYWORDS, GENERAL, build_release_table, component_for, components_of,
    parse_changelog, rows_from,
)

REPO = Path(__file__).resolve().parents[3]

SAMPLE = """# Changelog

Intro line that is not a release.

## [2.1.0] - Unreleased

Work in progress.

### Upgrading from 2.0.x

Save the options, then import:

1. `mdb options-save`
2. `mdb builders-import --options builder-options.json`

### Added

- **A new thing for the Prompt Builder.** Judges rank runs.

## [2.0.0] - 2026-09-03

Provider secrets move to **credential domains** - see [the guide](x.md).

### ⚠️ Breaking change — migrate before upgrading

**The key-table pattern is removed.** The Prompt Builder no longer reads API keys from the table.

1. Sign in with the CLI.
2. Run the script.

The helper is gone; the report needs no assigned data.

### RAG Builder: generating what a setup implies

- **One button makes a setup real.** *Manifest setup* saves it and registers the retrieval model.
- **Three ways for content to leave a collection**, kept apart:
  - a policy on `RAG - Load Vector Store`
  - `RAG - Purge Documents` for erasure

### Fixed

- A plain bullet without a bold lead. It has a second sentence.

### Removed

- None

## [1.0.0] - 2026-07-15

First release.

### Changed

- **mdb generates definitions.** Every folder regenerates from `definition.yaml`.
"""


def test_releases_sections_and_items_are_read():
    releases = parse_changelog(SAMPLE)
    assert [r.version for r in releases] == ["2.1.0", "2.0.0", "1.0.0"]
    one = releases[0]
    assert [i["change_type"] for i in one.items] == ["Upgrade", "Added"]
    assert one.items[0]["section"] == "Upgrading from 2.0.x"
    assert "options-save" in one.items[0]["text"] and "builders-import" in one.items[0]["text"]
    two = releases[1]
    assert two.date == "2026-09-03"
    assert two.intro == ["Provider secrets move to **credential domains** - see [the guide](x.md)."]
    assert [i["change_type"] for i in two.items] == ["Breaking", "Breaking", "Changed", "Changed", "Fixed"]
    assert two.items[0]["section"] == "Breaking change — migrate before upgrading"
    # the numbered steps fold into the paragraph they belong to; the next
    # paragraph is an entry of its own
    assert "1. Sign in" not in " ".join(i["text"][:2] for i in two.items)
    assert "Sign in with the CLI" in two.items[0]["text"] and "Run the script" in two.items[0]["text"]
    assert two.items[1]["text"].startswith("The helper is gone")
    assert two.items[2]["section"] == "RAG Builder: generating what a setup implies"
    # nested bullets fold into their parent
    assert "Purge Documents" in two.items[3]["text"] and "Load Vector Store" in two.items[3]["text"]
    # "- None" is a placeholder, not a change
    assert all("None" != i["text"] for i in two.items)


def test_rows_carry_ranks_current_flag_and_plain_text():
    rows = rows_from(parse_changelog(SAMPLE))
    # Unreleased is listed but the newest DATED release is current
    assert {r["version"] for r in rows if r["is_current"]} == {"2.0.0"}
    assert [r["release_rank"] for r in rows if r["item_rank"] == 0] == [1, 2, 3]
    intro = next(r for r in rows if r["version"] == "2.0.0" and r["change_type"] == "Release")
    assert intro["summary"] == "Provider secrets move to credential domains - see the guide"
    assert "**" not in intro["detail"] and "](" not in intro["detail"]
    manifest = next(r for r in rows if r["summary"] == "One button makes a setup real")
    assert manifest["detail"] == "Manifest setup saves it and registers the retrieval model."
    plain = next(r for r in rows if r["change_type"] == "Fixed")
    assert plain["summary"] == "A plain bullet without a bold lead"
    assert plain["detail"].endswith("second sentence.")
    # one row per entry, item ranks consecutive within a release
    two = [r for r in rows if r["version"] == "2.0.0"]
    assert [r["item_rank"] for r in two] == [0, 1, 2, 3, 4, 5]


def test_components_put_builder_changes_where_a_filter_finds_them():
    rows = rows_from(parse_changelog(SAMPLE))
    by_summary = {r["summary"]: r["component"] for r in rows}
    assert by_summary["A new thing for the Prompt Builder"] == "Prompt Builder"
    assert by_summary["mdb generates definitions"] == "Model Definition Builder"
    # a section that names a component files everything under it there, even
    # an entry that says "registers" - which alone would read as mdb, and one
    # that names only steps - which alone would read as the RAG runtime. The
    # RAG Builder is a part of the Prompt Builder, so that is the component.
    assert by_summary["One button makes a setup real"] == "Prompt Builder"
    assert by_summary["Three ways for content to leave a collection"] == "Prompt Builder"
    assert components_of("RAG - Purge Documents for erasure")[0] == "RAG runtime"
    # priority: a Prompt Builder entry that also mentions keys stays Prompt Builder
    assert by_summary["The key-table pattern is removed"] == "Prompt Builder"
    assert components_of("Nothing recognisable here.") == [GENERAL]
    assert component_for("Fixed", "Nothing recognisable here.") == GENERAL
    # "RAG Builder" is not a component: no text can be filed under it
    assert "RAG Builder" not in [name for name, _ in COMPONENT_KEYWORDS]
    assert components_of("The RAG Builder loads a setup.")[0] == "Prompt Builder"
    assert components_of("rag_core retries a failed embedding call.")[0] == "RAG runtime"
    # short tokens match whole words only: "scr" is not in "scripts"
    assert "Model Definition Builder" not in components_of("The shell scripts were quoted.")


# The shape the 2.3.0 entry has: the headline of the release, the upgrade steps
# and the removals all talk about the RAG Builder moving into the Prompt
# Builder. Every one of them is what a site looks for on the report's
# Changelog page.
MERGE_RELEASE = """# Changelog

## [2.3.0] - unreleased

The **RAG Builder is part of the Prompt Builder**: one report, one object, three kinds.

### Upgrading from 2.2.x

Save the options while the RAG Builder report still exists:

1. `mdb options-save`
2. `mdb builders-import --options builder-options.json`
3. Delete the report `RAG Builder` and the job definition `RAG-Builder-UI`.

### Changed

- **The RAG Builder is part of the Prompt Builder.** One report, one object, one set of options.

### Added

- **A RAG setup is the third kind on the Setup card.** Choosing it swaps the step flow.
- **Switches for decisions, optimization and RAG.** Each one hides what it does not offer in the options pane.

### Fixed

- **A manifested decision template is valid Python.** The Prompt Builder indents the per-question lines.
- **rag_core records the run history again.** The column was missing from the DDL.

### Removed

- **The standalone RAG build.** `npm run build:rag`, `rag.html` and the RAG Builder report are gone.
- **The `rag-builder.json` seed.** `mdb setup` writes one seed file.
"""


def test_the_merge_release_lands_where_the_report_filters():
    rows = rows_from(parse_changelog(MERGE_RELEASE))
    by_summary = {r["summary"]: r for r in rows}
    upgrade = next(r for r in rows if r["change_type"] == "Upgrade")
    assert upgrade["component"] == "Prompt Builder"
    assert "options-save" in upgrade["detail"] and "RAG-Builder-UI" in upgrade["detail"]
    for headline in ("The RAG Builder is part of the Prompt Builder",
                     "A RAG setup is the third kind on the Setup card",
                     "Switches for decisions, optimization and RAG",
                     "A manifested decision template is valid Python",
                     "The standalone RAG build",
                     "The rag-builder.json seed"):
        assert by_summary[headline]["component"] == "Prompt Builder", headline
    assert by_summary["The standalone RAG build"]["change_type"] == "Removed"
    # the runtime keeps its own component: this row is not a UI change
    assert by_summary["rag_core records the run history again"]["component"] == "RAG runtime"
    assert "RAG Builder" not in {r["component"] for r in rows}
    # an entry dated "unreleased" is listed but is not the current release
    assert not any(r["is_current"] for r in rows)


def test_the_real_changelog_parses(tmp_path):
    rows, summary = build_release_table(REPO / "CHANGELOG.md", tmp_path / "releases.csv")
    assert summary["releases"] >= 12 and summary["rows"] > 400
    assert summary["current"]  # a dated release exists
    assert {"Prompt Builder", "RAG runtime", "Model Definition Builder"} <= set(summary["by_component"])
    assert "RAG Builder" not in summary["by_component"]
    # every entry is exactly one row (the key includes the date: the changelog
    # once reused a version number for two releases)
    assert len({(r["version"], r["release_date"], r["item_rank"]) for r in rows}) == len(rows)
    # no numbered-list fragment became a headline
    assert not [r for r in rows if r["summary"].isdigit()]
    with (tmp_path / "releases.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames) == COLUMNS
        first = next(reader)
    assert first["release_rank"] == "1" and first["change_type"] == "Release"


def test_a_file_without_releases_is_refused(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# Nothing here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="release headings"):
        build_release_table(tmp_path / "CHANGELOG.md", tmp_path / "out.csv")
