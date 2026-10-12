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

The page also has a button bar on `builder_part` - Prompt, Decision, RAG. A
button bar shows the rows of one value, so a Builder entry is one row per part
it concerns, and the tests below pin which parts that is: by what the entry
names, and by release - a part that did not exist yet cannot be concerned.
"""
import csv
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from mdb.core.releases import (
    BUILDER, COLUMNS, COMPONENT_KEYWORDS, GENERAL, PARTS, build_release_table, component_for,
    components_of, parse_changelog, parts_for, rows_from,
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
    # every entry is exactly one row per part (the key includes the date: the
    # changelog once reused a version number for two releases)
    assert len({(r["version"], r["release_date"], r["item_rank"], r["builder_part"]) for r in rows}) == len(rows)
    # ... and one entry however many rows it is: what a release holds is
    # still counted by release and item rank
    releases = parse_changelog((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert summary["entries"] == sum(len(r.items) + bool(r.intro) for r in releases)
    assert summary["entries"] == len({(r["release_rank"], r["item_rank"]) for r in rows}) < summary["rows"]
    assert sum(summary["by_part"].values()) == len([r for r in rows if r["builder_part"]])
    assert tuple(summary["by_part"]) == PARTS and all(summary["by_part"].values())
    # no numbered-list fragment became a headline
    assert not [r for r in rows if r["summary"].isdigit()]
    with (tmp_path / "releases.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames) == COLUMNS
        first = next(reader)
    assert first["release_rank"] == "1" and first["change_type"] == "Release"
    # the release intro belongs to no part
    assert COLUMNS[-1] == "builder_part" and first["builder_part"] == ""


# Entries in the wording 2.3.0 uses: since the merge "Prompt Builder" is the
# name of the whole, so a headline says which part it means - or names none.
PARTS_RELEASE = """# Changelog

## [2.3.0] - unreleased

One report, three kinds.

### Upgrading from 2.2.x

Save the options while the RAG Builder report still exists, then run `mdb builders-import`.

### Added

- **The Administration Guide says how to recognise an execution that SAS Micro Analytic Service ended after 30 seconds, for a decision with a retrieval model.** Measured with a decision of three model nodes that were all manifested in the Prompt Builder - a retrieval model, a prompt template and a decision template.

### Changed

- **The RAG Builder is part of the Prompt Builder.** One bundle, one report and one Options pane serve all three kinds.
- **LLMCONTAINERSSLVERIFY switches TLS verification on as well as off.** The score code of a prompt template with the included LLM call and of a decision template manifested in the Prompt Builder read the variable in one direction only.

### Fixed

- **A decision template manifested in the Prompt Builder can be scored.** The manifest of a prompt template uses the same escaping.
- **The optimization switch of the Prompt Builder shows what is stored.** The switch is a checkbox now.
- **An LLM run in the Prompt Builder keeps the results of the models that answered.** Decision runs behave the same.
- **"Judge this run" in the Prompt Builder can be used right after a run.** After an experiment the button stayed disabled until the prompt-test was loaded again.
- **Prompt-tests made in the Prompt Builder record their modeler.** A misspelt field name meant that every prompt-test was created without one.
- **A RAG setup embeds its documents and its questions at one endpoint.** The retrieval model was written with the host whatever the SCR endpoint option of the Prompt Builder said.
- **The create dialogs of the Prompt Builder show the whole name.** In the dialogs that create a project, a prompt-test or a RAG setup the field was too narrow.
- **The Prompt Builder sends no Referer to another site.** The user-guide link of the Optimize step goes to another site.
- **mdb builders-import knows the Prompt Builder package from the RAG runtime package.** A report RAG Builder that is still on the site is reported.
- **A manifest of a RAG setup that fails half way says so beside the button.** As the prompt flow does.
- **rag_core records the run history again.** The column was missing from the DDL.
- **mdb register keeps the model version.** Nothing of the Builder.
"""


def _parts_by_summary(rows):
    parts: dict[str, list[str]] = {}
    for row in rows:
        parts.setdefault(row["summary"], []).append(row["builder_part"])
    return parts


def test_a_builder_entry_is_one_row_per_part_it_concerns():
    rows = rows_from(parse_changelog(PARTS_RELEASE))
    parts = _parts_by_summary(rows)
    every = list(PARTS)
    # the headline names the part
    assert parts["A decision template manifested in the Prompt Builder can be scored"] == ["Decision"]
    assert parts["The optimization switch of the Prompt Builder shows what is stored"] == ["Prompt"]
    assert parts["A RAG setup embeds its documents and its questions at one endpoint"] == ["RAG"]
    # ... and what the text mentions on the way does not add one: a judge is
    # for prompts although its text says "experiment", and a decision in SAS
    # Intelligent Decisioning is not a decision template
    assert parts['"Judge this run" in the Prompt Builder can be used right after a run'] == ["Prompt"]
    assert parts["The Administration Guide says how to recognise an execution that SAS Micro Analytic Service "
                 "ended after 30 seconds, for a decision with a retrieval model"] == ["RAG"]
    assert parts["A manifest of a RAG setup that fails half way says so beside the button"] == ["RAG"]
    # one flow serves both kinds of template: a prompt-test is either, and a
    # headline about prompts is joined by Decision when the text says so
    assert parts["Prompt-tests made in the Prompt Builder record their modeler"] == ["Prompt", "Decision"]
    assert parts["An LLM run in the Prompt Builder keeps the results of the models that answered"] == ["Prompt", "Decision"]
    # a headline that names no part: the Builder as a whole when it names
    # that, else the parts the text names
    assert parts["The create dialogs of the Prompt Builder show the whole name"] == every
    assert parts["The Prompt Builder sends no Referer to another site"] == every
    assert parts["LLMCONTAINERSSLVERIFY switches TLS verification on as well as off"] == ["Prompt", "Decision"]
    # "RAG runtime" is the other component, not the RAG part
    assert parts["mdb builders-import knows the Prompt Builder package from the RAG runtime package"] == every
    # an entry that says "all three kinds" is about all three, whatever it names
    assert parts["The RAG Builder is part of the Prompt Builder"] == every
    # the steps of an upgrade are everybody's
    upgrade = [r["builder_part"] for r in rows if r["change_type"] == "Upgrade"]
    assert upgrade == every
    # the runtime is the RAG part, once; nothing else belongs to a part
    assert parts["rag_core records the run history again"] == ["RAG"]
    assert parts["mdb register keeps the model version"] == [""]
    assert [r["builder_part"] for r in rows if r["change_type"] == "Release"] == [""]


def test_the_rows_of_one_entry_differ_in_the_part_only():
    rows = rows_from(parse_changelog(PARTS_RELEASE))
    shared = [r for r in rows if r["summary"] == "The create dialogs of the Prompt Builder show the whole name"]
    assert [r["builder_part"] for r in shared] == ["Prompt", "Decision", "RAG"]
    assert len({tuple(r[c] for c in COLUMNS if c != "builder_part") for r in shared}) == 1
    # the ranks count entries, not rows: the next entry is the next rank
    ranks = [r["item_rank"] for r in rows]
    assert sorted(set(ranks)) == list(range(0, 17)) and ranks == sorted(ranks)
    # every Builder row has a part, every part is one of the three
    assert all(r["builder_part"] in PARTS for r in rows if r["component"] == BUILDER)
    assert {r["builder_part"] for r in rows} == {"", *PARTS}


NO_PART = "**Disabled buttons explain themselves on hover.** A disabled button fires no hover events."


@pytest.mark.parametrize("version, expected", [
    ("0.1.35", ["Prompt"]),                     # the Builder was prompts
    ("1.9.0", ["Prompt"]),
    ("2.0.0", ["Prompt", "RAG"]),               # the RAG Builder came into the repository
    ("2.1.0", ["Prompt", "RAG"]),
    ("2.2.0", ["Prompt", "Decision", "RAG"]),   # the decision mode
    ("2.3.0", ["Prompt", "Decision", "RAG"]),
    ("3.0.0", ["Prompt", "Decision", "RAG"]),
    ("Unreleased", ["Prompt", "Decision", "RAG"]),
])
def test_an_entry_that_names_no_part_concerns_the_builder_of_its_release(version, expected):
    assert parts_for(version, "Changed", "Changed", NO_PART) == expected
    # the steps of an upgrade likewise, whatever they name
    assert parts_for(version, "Upgrading", "Upgrade", "Import the RAG Builder package.") == expected


def test_what_did_not_exist_yet_cannot_be_concerned():
    # before the decision mode a "decision model" was something else, and the
    # Call LLM node of SAS Intelligent Decisioning is how a prompt is used
    old = "**The run table names the decision model.** For the Call LLM node in SAS Intelligent Decisioning."
    assert parts_for("2.1.0", "Fixed", "Fixed", old) == ["Prompt"]
    assert parts_for("2.2.0", "Fixed", "Fixed", old) == ["Decision"]
    flow = "**Prompt-tests record their modeler.** Every prompt-test was created without one."
    assert parts_for("1.4.0", "Fixed", "Fixed", flow) == ["Prompt"]
    assert parts_for("2.2.0", "Fixed", "Fixed", flow) == ["Prompt", "Decision"]
    # until 2.3.0 "the Prompt Builder" was the UI for prompts, beside the RAG
    # Builder: its name names the prompt part there, and the whole since
    named = "**The Prompt Builder keeps its width.** On a narrow report object the columns stack."
    assert parts_for("1.9.0", "Fixed", "Fixed", named) == ["Prompt"]
    assert parts_for("2.1.0", "Fixed", "Fixed", named) == ["Prompt"]
    assert parts_for("2.2.0", "Fixed", "Fixed", named) == ["Prompt"]
    assert parts_for("2.3.0", "Fixed", "Fixed", named) == ["Prompt", "Decision", "RAG"]
    both = "**The Prompt Builder and the RAG Builder are step flows.** Both were one long page."
    assert parts_for("2.1.0", "Changed", "Changed", both) == ["Prompt", "RAG"]
    # the RAG Builder is older than its place in this repository: an entry
    # that names it is about it, in every release
    assert parts_for("0.1.14", "Added", "Added", "rag-builder.json is used for the RAG Builder UI") == ["RAG"]
    # a section that names a part files everything under it there
    assert parts_for("2.0.0", "RAG Builder: generating what a setup implies", "Changed",
                     "**Deleting a setup says what depends on it first.** The Prompt Builder has asked since 1.3.0.") == ["RAG"]
    # a comparison names the Builder an entry is measured against
    assert parts_for("2.0.0", "Added", "Added",
                     "**Save documentation** in the RAG Builder, as in the Prompt Builder: the fields save on their own.") == ["RAG"]
    # a keyword never starts inside a word
    assert parts_for("2.0.0", "Fixed", "Fixed",
                     "**The job and the flow were not the same pipeline.** The ledger re-ingests that document unprompted.") == ["RAG"]


def test_a_template_without_its_kind_names_both_kinds_in_a_headline():
    every = list(PARTS)
    note = ("**The note about decisions that still use a prompt, a template or a RAG setup appears on the first "
            "delete in the Prompt Builder.** Before Delete Prompt and Delete setup ask for confirmation.")
    assert parts_for("2.3.0", "Fixed", "Fixed", note) == every
    assert parts_for("2.3.0", "Fixed", "Fixed", "**Templates keep their names.** As typed.") == ["Prompt", "Decision"]
    # with its kind it is that kind
    assert parts_for("2.3.0", "Fixed", "Fixed", "**A prompt template keeps its name.** As typed.") == ["Prompt"]
    assert parts_for("2.3.0", "Fixed", "Fixed", "**A decision-template keeps its name.** As typed.") == ["Decision"]
    # in a headline only: in the text the word is as often what a retrieval
    # model is manifested from, or a score template of a definition
    text = "**A RAG setup is manifested again.** The retrieval model needs the template of this release."
    assert parts_for("2.3.0", "Fixed", "Fixed", text) == ["RAG"]
    assert parts_for("2.3.0", "Fixed", "Fixed", "**The page keeps its width.** A score template is not concerned.") == every
    # there have been two kinds since the decision mode; before, the word in
    # a headline names nothing and the entry is read as any other
    plain = "**A template keeps its name.** As typed."
    assert parts_for("2.1.0", "Fixed", "Fixed", plain) == ["Prompt", "RAG"]
    assert parts_for("2.2.0", "Fixed", "Fixed", plain) == ["Prompt", "Decision"]


def test_a_headline_is_read_whole_where_the_column_shows_it_cut():
    # an entry without a bold lead: its first sentence is the headline, and
    # the summary column holds 140 characters of it
    entry = ("The Python samples outside SAS Viya set an HTTP timeout and lost their typos; the Prompt Builder README "
             "describes the whole source tree, RAG Builder included.")
    rows = rows_from(parse_changelog(f"# Changelog\n\n## [2.0.4] - 2026-09-13\n\n### Fixed\n\n- {entry}\n"))
    assert len(rows[0]["summary"]) <= 140 and "RAG Builder" not in rows[0]["summary"]
    assert [r["builder_part"] for r in rows] == ["Prompt", "RAG"]


def test_a_component_that_did_not_exist_yet_cannot_be_concerned():
    # rag_core, the steps and the stores came with 2.0.0: what an older entry
    # "enriches" or "retrieves" is filed under the next component it names
    old = "**Live catalogs no longer lose the kind.** The static-snapshot enrichment carries it over for `mdb add`."
    assert component_for("Fixed", old, "1.8.3") == "Model Definition Builder"
    assert component_for("Fixed", old, "2.0.0") == "RAG runtime"
    assert component_for("Fixed", old) == "RAG runtime"  # no release given: the newest there can be
    assert components_of("Get-All-Prompts.sas retrieves all prompting projects.", "0.1.15") == [GENERAL]
    # a section heading is read by release as well
    assert component_for("RAG cost", "A new view.", "1.9.0") == GENERAL
    assert component_for("RAG cost", "A new view.", "2.0.0") == "RAG runtime"
    # Manifest Best Prompt is a button of the Prompt Builder: an entry about
    # the best prompt is filed there, not under what its text registers
    best = "**The manifested best prompt inherits its winning LLM's attributes.** As the registered LLMs do (mdb-enriched)."
    assert components_of(best, "1.7.0")[:2] == ["Prompt Builder", "Model Definition Builder"]
    assert parts_for("1.7.0", "Changed", "Changed", best) == ["Prompt"]


def test_an_entry_about_both_builders_is_a_builder_entry_for_every_part():
    # from 2.0.0 to 2.2.x there were two: the Prompt Builder and the RAG
    # Builder. What is fixed in both is no change to the runtime, whatever it
    # was found with, and concerns every part the release had
    both = ("**Long progress messages were being read as their first 132 characters.** It went unnoticed until "
            "a retrieval hit arrived truncated. The fragments are now rejoined, in both builders' live run displays.")
    assert components_of(both, "2.0.0")[:2] == ["Prompt Builder", "RAG runtime"]
    assert parts_for("2.0.0", "Fixed", "Fixed", both) == ["Prompt", "RAG"]
    assert parts_for("2.2.0", "Fixed", "Fixed", both) == ["Prompt", "Decision", "RAG"]
    # without the phrase the text decides: a retrieval hit is the RAG part
    assert parts_for("2.0.0", "Fixed", "Fixed", both.replace("in both builders' live", "in the live")) == ["RAG"]


def test_an_entry_about_the_release_table_is_an_mdb_entry():
    # `mdb load-releases` makes the table. An entry about it names components
    # and parts as the values the table holds, and none of them is what changed
    files = ("**The release table files RAG Builder changes under the Prompt Builder.** The component column no "
             "longer has the value RAG Builder; RAG runtime stays a component of its own.")
    assert components_of(files, "2.3.0")[0] == "Prompt Builder"
    assert component_for("Changed", files, "2.3.0") == "Model Definition Builder"
    # ... under each of its names
    column = "**ACCELERATOR_RELEASES says which part of the Prompt Builder a change concerns.** A new last column."
    command = "**A table for the Builder objects: `mdb load-releases`.** The Prompt Builder object is assigned it."
    assert component_for("Added", column, "2.3.0") == component_for("Added", command, "2.0.4") == "Model Definition Builder"
    # it is no Builder entry, so it is one row and has no part
    rows = rows_from(parse_changelog(f"# Changelog\n\n## [2.3.0] - unreleased\n\n### Changed\n\n- {files}\n"))
    assert [(r["component"], r["builder_part"]) for r in rows] == [("Model Definition Builder", "")]
    # in a headline only: in the text the table is what a report object is
    # assigned, and the entry is about what its headline names
    assigned = ("**The RAG Builder object in the shipped package has a data assignment.** It is bound to "
                "ACCELERATOR_RELEASES, the release table that `mdb load-releases` loads.")
    assert component_for("Fixed", assigned, "2.0.5") == "Prompt Builder"
    assert parts_for("2.0.5", "Fixed", "Fixed", assigned) == ["RAG"]
    # a section heading that names a component still files every entry under it
    assert component_for("RAG cost", files, "2.3.0") == "RAG runtime"


def test_a_headline_about_definitions_is_filed_under_the_definitions_not_the_tool():
    # mdb makes the definitions, so an entry that adds some says how they were
    # generated, and by its wording it is the tool's. The headline says which
    # of the two it is about
    added = ("Open-source CPU embedding definitions informed by a leaderboard: granite_embedding_small_r2 joins "
             "the BGE family. The generated sentence-transformers scorer was verified end-to-end on CPU.")
    assert components_of(added, "1.1.0")[0] == "Model Definition Builder"
    assert component_for("Added", added, "1.1.0") == "Definitions"
    shipped = "**Shipped definitions.** jev_1_13 and von_1_2 - `mdb deploy` renders the persistent-volume variant."
    assert component_for("Added", shipped, "2.2.0") == "Definitions"
    assert [r["builder_part"] for r in rows_from(parse_changelog(
        f"# Changelog\n\n## [2.2.0] - 2026-10-07\n\n### Added\n\n- {shipped}\n"))] == [""]
    # a headline that names the tool as well is the tool's: what mdb does
    # with the definitions is a change to mdb
    for tool in ("**`mdb sync --rebuild` regenerates the fact sheets from the definitions.** Idempotent.",
                 "Embedding definitions are fully supported: generated embedding scorers return all three outputs.",
                 "**Environment-neutral definitions: one published image serves every region.** Resolved at scoring time."):
        assert component_for("Added", tool, "1.8.0") == "Model Definition Builder", tool
    # between those two only: definitions that another component reads stay there
    read = "**Decision definitions carry what a model takes.** `mdb register` writes it, the Prompt Builder's cards read it."
    assert component_for("Added", read, "2.2.0") == "Prompt Builder"
    # in a headline only, and "definition" alone names a kind or a file as
    # often as the definitions
    text = "**The generator emits toolVersion 3.11.** `mdb generate` rewrites all definitions."
    assert component_for("Changed", text, "1.1.0") == "Model Definition Builder"
    kind = "**Decision models are a definition kind of their own.** `mdb add` asks for the kind."
    assert component_for("Added", kind, "2.2.0") == "Model Definition Builder"


def test_the_parts_of_the_real_changelog_follow_the_releases(tmp_path):
    rows, summary = build_release_table(REPO / "CHANGELOG.md", tmp_path / "releases.csv")
    key = lambda r: tuple(int(n) for n in r["version"].split("."))  # noqa: E731
    builder = [r for r in rows if r["component"] == BUILDER]
    # every Builder row has a part; the runtime is RAG; nothing else has one
    assert {r["builder_part"] for r in builder} == set(PARTS)
    assert {r["builder_part"] for r in rows if r["component"] == "RAG runtime"} == {"RAG"}
    assert {r["builder_part"] for r in rows if r["component"] not in (BUILDER, "RAG runtime")} == {""}
    # no runtime entry before the runtime: an older one is filed elsewhere
    assert not [r for r in rows if r["component"] == "RAG runtime" and key(r) < (2, 0, 0)]
    component = {(r["version"], r["summary"]): r["component"] for r in rows}
    assert component[("1.8.3", "Live catalogs no longer lose the kind")] == "Model Definition Builder"
    assert component[("1.6.0", "Richer model attributes on register")] == "Model Definition Builder"
    assert component[("1.7.0", "The manifested best prompt inherits its winning LLM's attributes")] == BUILDER
    assert component[("1.0.0", '"Save Experiments" and "Manifest Best Prompt" are real buttons now, so they are '
                               "keyboard-accessible and properly disabled while busy")] == BUILDER
    # an entry about the release table is mdb's, whichever Builder it names
    about_the_table = [r for r in rows if "release table" in r["summary"].lower()]
    assert {r["version"] for r in about_the_table} >= {"2.0.4", "2.1.0"}
    assert {r["component"] for r in about_the_table} == {"Model Definition Builder"}
    assert component[("2.0.4", "A release table for the Builder objects: mdb load-releases")] == "Model Definition Builder"
    assert component[("2.0.5", "The RAG Builder object in the shipped package has a data assignment")] == BUILDER
    # definitions that were added are filed under the definitions, although
    # their entries say what mdb generated or renders; what mdb does with the
    # definitions is mdb's
    granite = next(k for k in component if k[0] == "1.1.0" and k[1].startswith("Open-source CPU embedding definitions"))
    assert component[granite] == component[("2.2.0", "Shipped definitions")] == "Definitions"
    assert component[("1.8.0", "mdb sync --rebuild regenerates the fact sheets from the definitions")] == "Model Definition Builder"
    # only a Builder entry is more than one row
    by_entry: dict[tuple, list[dict]] = {}
    for r in rows:
        by_entry.setdefault((r["release_rank"], r["item_rank"]), []).append(r)
    assert all(len(group) == 1 or group[0]["component"] == BUILDER for group in by_entry.values())
    for group in by_entry.values():
        assert [r["builder_part"] for r in group] == [p for p in ("", *PARTS) if p in {r["builder_part"] for r in group}]
        assert len({tuple(r[c] for c in COLUMNS if c != "builder_part") for r in group}) == 1
    # no decision template before the decision mode
    assert not [r for r in rows if r["builder_part"] == "Decision" and key(r) < (2, 2, 0)]
    # before the RAG Builder came into the repository a Builder entry is RAG
    # only where it names it
    early = [r for r in builder if r["builder_part"] == "RAG" and key(r) < (2, 0, 0)]
    assert early and all("rag-buil" in f'{r["summary"]} {r["detail"]}'.lower() for r in early)
    # entries of released versions, as they were read when the column was made
    parts = {(r["version"], r["summary"]): [] for r in builder}
    for r in builder:
        parts[(r["version"], r["summary"])].append(r["builder_part"])
    assert parts[("2.2.0", "The Prompt Builder has a decision mode")] == ["Prompt", "Decision"]
    assert parts[("2.2.0", "The designer guides the decision template")] == ["Decision"]
    assert parts[("2.1.0", "The Prompt Builder and the RAG Builder are step flows")] == ["Prompt", "RAG"]
    assert parts[("2.1.0", "The Builder packages no longer carry the exporting environment's configuration")] == ["Prompt", "RAG"]
    assert parts[("2.0.5", "The RAG Builder object in the shipped package has a data assignment")] == ["RAG"]
    # a headline that names the RAG Builder beyond what the column shows of it
    readme = next(k for k in parts if k[0] == "2.0.4" and k[1].startswith("The Python samples outside SAS Viya"))
    assert "RAG Builder" not in readme[1] and parts[readme] == ["Prompt", "RAG"]
    assert parts[("2.0.0", "Save documentation")] == ["RAG"]
    assert parts[("2.0.0", "One button makes a setup real")] == ["RAG"]
    assert parts[("2.0.0", "Model Manager names were rendered as HTML in every picker")] == ["Prompt", "RAG"]
    assert parts[("2.0.0", "A carriage return would have made every progress milestone unreadable")] == ["Prompt", "RAG"]
    assert parts[("2.0.0", "Long progress messages were being read as their first 132 characters")] == ["Prompt", "RAG"]
    assert parts[("2.0.0", "Per-user model availability")] == ["Prompt"]
    assert parts[("1.9.0", "An Optimize section in the Prompt Builder")] == ["Prompt"]
    assert parts[("1.7.0", "The manifested best prompt inherits its winning LLM's attributes")] == ["Prompt"]
    assert parts[("1.4.0", "Disabled buttons now explain themselves on hover")] == ["Prompt"]


def test_load_releases_prints_the_rows_per_part(tmp_path, monkeypatch):
    from rich.console import Console
    from typer.testing import CliRunner

    from mdb import cli

    (tmp_path / "CHANGELOG.md").write_text(PARTS_RELEASE, encoding="utf-8")
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=tmp_path))
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=400, force_terminal=False))
    result = CliRunner().invoke(cli.app, ["load-releases", "--csv", str(tmp_path / "out.csv")])
    assert result.exit_code == 0, result.output
    rows = rows_from(parse_changelog(PARTS_RELEASE))
    counts = {part: len([r for r in rows if r["builder_part"] == part]) for part in PARTS}
    assert (f"rows per Builder part (builder_part): Prompt {counts['Prompt']}, Decision {counts['Decision']}, "
            f"RAG {counts['RAG']} - {len(rows)} rows for 17 changelog entries") in out.getvalue()
    with (tmp_path / "out.csv").open(encoding="utf-8", newline="") as fh:
        assert next(csv.reader(fh))[-1] == "builder_part"


def test_a_file_without_releases_is_refused(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# Nothing here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="release headings"):
        build_release_table(tmp_path / "CHANGELOG.md", tmp_path / "out.csv")


# ---- the shape of "Upgrading from ..." ---------------------------------------
# The parser reads that section by its shape: the paragraph under the heading
# and every paragraph with a bold lead is an entry, and a numbered step is
# added to the entry in front of it. A shape it does not know still parses and
# no other test fails for it - a "####" heading, a table row or a link
# definition becomes a row of the release table that starts with "#", "|" or
# "[", and steps directly under a heading all fold into one paragraph.
import re  # noqa: E402

#: The blocks of the upgrade section of 2.3.0, in their order: what every site
#: does, then one block for each thing a site may use.
UPGRADE_BLOCKS = ("Everyone", "Prompt building", "Decision models", "RAG", "Logging")
_STEP = re.compile(r"^(\d+)\.\s+\S")
_STEP_NAMED = re.compile(r"\bsteps?\s+(\d+)(?:\s*(?:,|and|to|or)\s*(\d+))*", re.I)


def _upgrade_sections(text: str) -> dict:
    """{version: [lines of its "### Upgrading ..." section]} of a changelog."""
    sections, version, inside = {}, None, False
    for line in text.splitlines():
        if line.startswith("## "):
            version, inside = line.split("]")[0].lstrip("# ["), False
        elif line.startswith("### "):
            inside = "upgrad" in line.lower()
            if inside:
                assert version not in sections, f"{version}: two upgrade headings"
                sections[version] = []
        elif inside:
            sections[version].append(line)
    return sections


def _release_block(text: str, version: str) -> list:
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.startswith(f"## [{version}]")
        elif inside:
            lines.append(line)
    return lines


def test_the_upgrade_steps_of_every_release_are_numbered_without_a_gap():
    sections = _upgrade_sections((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert "2.3.0" in sections
    for version, lines in sections.items():
        numbers = [int(m.group(1)) for m in map(_STEP.match, lines) if m]
        assert numbers == list(range(1, len(numbers) + 1)), f"{version}: the steps are numbered {numbers}"
        # a step under a heading with no paragraph in front of it is added to
        # nothing, or to the entry of another section
        first_step = next((i for i, line in enumerate(lines) if _STEP.match(line)), None)
        if first_step is not None:
            assert any(line.strip() for line in lines[:first_step]), f"{version}: steps without a paragraph above them"
        for line in lines:
            assert not line.startswith(("#", "|", "- ", "* ")) and not re.match(r"^\[[^\]]+\]:", line), \
                f"{version}: a line of the upgrade section is a heading, a table row, a bullet or a link definition: {line[:60]!r}"


def test_the_upgrade_section_of_2_3_0_has_each_block_once_and_in_order():
    lines = _upgrade_sections((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))["2.3.0"]
    leads = [m.group(1) for m in (re.match(r"^\*\*([^*]+?)\.\*\*", line) for line in lines) if m]
    assert leads == list(UPGRADE_BLOCKS)
    # a step is one or two lines of the page, not a chapter: the long text is in the guides
    steps = [line for line in lines if _STEP.match(line)]
    assert steps and max(len(step) for step in steps) < 1000, max(len(step) for step in steps)
    # every step of the blocks after the first is for the sites that use that part
    position = {lead: next(i for i, line in enumerate(lines) if line.startswith(f"**{lead}.**")) for lead in leads}
    assert all(line.strip() == "" or not _STEP.match(line) for line in lines[:position["Everyone"]])


def test_no_upgrade_row_of_the_release_table_is_a_fragment_of_markdown(tmp_path):
    rows, _ = build_release_table(REPO / "CHANGELOG.md", tmp_path / "releases.csv")
    upgrade = [r for r in rows if r["change_type"] == "Upgrade"]
    assert upgrade
    for row in upgrade:
        assert row["summary"] and not row["summary"].startswith(("#", "|", "[")), (row["version"], row["summary"])
        assert not row["summary"][:1].isdigit() or not row["summary"].split(".")[0].isdigit(), (row["version"], row["summary"])
    current = [r for r in upgrade if r["version"] == "2.3.0"]
    summaries = list(dict.fromkeys(r["summary"] for r in current))
    # the paragraph under the heading, the five blocks, and the closing paragraph about a first install
    assert summaries[1:6] == list(UPGRADE_BLOCKS), summaries
    assert len(summaries) == 7, summaries
    # the steps are in the rows of their block, and no step made a row of its own
    by_summary = {r["summary"]: r["detail"] for r in current}
    assert "mdb builders-import" in by_summary["Everyone"]
    assert "Manifest setup" in by_summary["RAG"] and "log collection script" in by_summary["Logging"]
    assert "Manifest each of those decision templates again" in by_summary["Decision models"]


def test_a_step_of_the_upgrade_that_an_entry_names_exists():
    """An entry of 2.3.0 says "step 7 of the upgrade" or "Upgrading from 2.2.x:
    steps 2 and 7"; the numbers have to be steps of the section as it is."""
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    steps = [line for line in _upgrade_sections(text)["2.3.0"] if _STEP.match(line)]
    last = len(steps)
    named = []
    for line in _release_block(text, "2.3.0"):
        for sentence in re.findall(r"Upgrading from 2\.2\.x:[^.]*\.|[^.]*of the upgrade[^.]*\.", line):
            for match in _STEP_NAMED.finditer(sentence):
                named += [int(n) for n in re.findall(r"\d+", match.group(0))]
    assert named, "no entry names a step of the upgrade - the pattern of this test no longer finds them"
    assert all(1 <= n <= last for n in named), (sorted(set(named)), last)
    # inside the section a step names another one by its number as well
    for step in steps:
        own = int(_STEP.match(step).group(1))
        for match in re.finditer(r"\bsteps? (\d+)\b", step):
            assert 1 <= int(match.group(1)) <= last and int(match.group(1)) != own, step[:80]
