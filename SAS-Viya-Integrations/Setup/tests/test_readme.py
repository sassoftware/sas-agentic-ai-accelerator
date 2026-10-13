"""README.md of this folder: how the two jobs are opened, and what it says of the programs.

The page holds sentences that are true of a program only as long as nobody changes the program,
and numbers that were measured once. This file holds the ones a reader acts on: the two addresses
with the parameter _contextName, the parameters of the two job definitions, what the page says of
a custom step, of Load-Fact-Sheets.sas and of the width of four columns of the release table.
"""
import re
from pathlib import Path

SETUP = Path(__file__).resolve().parents[1]
REPOSITORY = SETUP.parents[1]
MANIFEST = REPOSITORY / "SAS-Viya-Integrations" / "package-manifest.yaml"


def readme():
    return (SETUP / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def section(title):
    text = readme()
    return text[text.index(f"\n## {title}\n"):].split("\n## ")[1]


def parameters(title):
    """The table of the parameters of a section, as {name: value}; a row that names several gives each its value."""
    table = section(title).split("| Parameter | Value | |\n| --- | --- | --- |\n")[1].split("\n\n")[0]
    found = {}
    for row in table.split("\n"):
        names, values = [cell.strip() for cell in row.replace("\\|", "\x00").strip("|").split("|")[:2]]
        for name, value in zip(re.findall(r"`([^`]*)`", names), re.findall(r"`([^`]*)`", values)):
            found[name] = value.replace("\x00", "|")
    return found


def defaults_of(program, macro):
    text = (SETUP / program).read_text(encoding="utf-8").replace("\r\n", "\n")
    return dict(re.findall(r"^%" + macro + r"\((\w+), (.*)\);$", text, flags=re.MULTILINE))


def context_of(entry):
    """The default of the parameter _contextName of an entry of the manifest, None where it has none."""
    text = MANIFEST.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text.count(f"  - path: {entry}\n") == 1, entry
    block = text.split(f"  - path: {entry}\n")[1].split("\n  - path: ")[0]
    found = re.search(r"\{name: _contextName, type: character, default: ([^,}]*)", block)
    return found.group(1).strip().strip('"') if found else None


def test_both_jobs_are_opened_in_a_compute_context_that_runs_as_the_user():
    """Measured on 2026-10-12 in the SAS Job Execution compute context: on one run SAS Viya refused every
    request of the page, on a later run the page was built completely. The README gives each address with
    the parameter, says what was seen on which run, and claims of no context that it always refuses."""
    start, loader = section("Start Here"), section("Load-Accelerator-Tables")

    assert ("https://<your SAS Viya host>/SASJobExecution/?_program=/SAS Agentic AI Accelerator/Start Here"
            "&_contextName=<a compute context that runs as you>\n") in start
    assert ("https://<your SAS Viya host>/SASJobExecution/?_program=/SAS Agentic AI Accelerator/Data/Load-Accelerator-Tables"
            "&_contextName=<a compute context that runs as you>\n") in loader
    # what was measured on each of the two runs, with the numbers of their pages
    assert "What the session of a compute context may read depends on how the system configures that context." in start
    assert "the page read `13 ok, 0 missing, 15 not checked` - fifteen lines, twelve of them with the advice to ask an administrator" in start
    assert "opened with `&_contextName=SAS Studio compute context` the same page had 97 lines that were ok." in start
    assert "On a later run the same day the page was built completely in that context - `97 ok, 2 missing, 1 not checked`" in start
    assert "runs its sessions under a service account" not in start and "always" not in start
    assert "the page has one line at its top in place of those lines" in start
    assert "The program compares the two - a domain written behind `@` or in front of a backslash is no difference - and writes neither" in start
    assert "the log then holds one NOTE that the session does not run as the user who started the job" in loader.replace("The log", "the log")


def test_the_parameters_are_the_ones_of_the_programs_and_of_the_manifest():
    start, loader = parameters("Start Here"), parameters("Load-Accelerator-Tables")

    for listed, program, macro in ((start, "Start-Here.sas", "_sh_default"), (loader, "Load-Accelerator-Tables.sas", "_lat_default")):
        assert {name: value for name, value in listed.items() if not name.startswith("_")} == defaults_of(program, macro)
        assert listed["_action"] == "execute"
    assert start["_output_type"] == "html"
    # the compute context is the one the job definition of the Prompt Builder has in the manifest
    builder = context_of("Prompt Builder/SAS-LLM-Prompt-Builder-UI")
    assert builder == "SAS Studio compute context" and start["_contextName"] == loader["_contextName"] == builder
    # and where the manifest gives one of the two jobs the parameter, it is that context
    for entry in ("Start Here", "Data/Load-Accelerator-Tables"):
        assert context_of(entry) in (None, builder), entry
    assert "`_contextName` and `contentRoot` are settings of a site" in section("Start Here")


def test_what_the_page_says_of_a_custom_step_names_no_upload_as_a_way():
    start = section("Start Here")

    assert "A custom step is registered - by the import of the package or by `mdb content-deploy` -" in start
    assert "**No advice is an upload.**" in start
    assert not re.search(r"\bupload (it|the|a|its)\b", readme(), flags=re.IGNORECASE)


def test_what_the_page_says_of_the_fact_sheet_program_and_of_the_four_widths():
    loader = section("Load-Accelerator-Tables")

    assert "does the same for each sheet" not in readme()
    assert ("*Load-Fact-Sheets.sas* replaces each fact sheet table the same way: the upload of the sheet whose rows were counted is "
            "promoted under the name of the table and saved, and the sheet is not loaded a second time.") in loader
    assert "second load leaves the site without that table" not in loader
    # the formatted length: what was measured, and why it changes nothing
    assert ("is 16 where the table of `mdb` has 6, 10, 8 and 8, for the four text columns whose longest value is shorter than 16 bytes "
            "(`version`, `release_date`, `change_type`, `builder_part`)") in loader
    assert "a width of 16 shows every value of these columns whole, and the page of the Prompt Builder reads nothing from the table it is assigned" in loader


def test_the_page_says_of_no_program_that_it_has_not_run():
    """What has not run in SAS is said in the changelog and in the guide, not here. The one sentence of
    the kind this page has is about its tests, which hold the text of the SAS code."""
    text = " ".join(readme().split())

    for phrase in ("not run in SAS", "not been run", "has not run", "was not run", "untested", "not measured", "not run against"):
        assert phrase.lower() not in text.lower(), phrase
    assert text.count("does not run in these tests") == 1
