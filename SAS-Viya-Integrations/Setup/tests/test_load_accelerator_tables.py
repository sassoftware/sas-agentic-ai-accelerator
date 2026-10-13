"""Load-Accelerator-Tables.sas: the release table and the three fact sheets, from the Data folder into CAS.

The release table has to be the table `mdb load-releases` leaves in CAS, so that the Prompt
Builder report binds it whichever of the two loaded it. mdb uploads the file through the
casManagement service and names no column types. What CAS made of it was read on a SAS Viya
system on 2026-10-12 (two GET requests, the table had been loaded on 2026-10-07): ten columns -
the changelog of that day had no builder_part yet -, the three numbers DOUBLE and the seven text
columns VARCHAR, each as long as the longest value of its column in the file mdb writes from
the changelog of that commit (6, 10, 58, 8, 24, 139 and 4764 bytes; 453 rows). The program
therefore lets CAS read the file as well, and names the type of every column.

Here the columns and their types are held against the columns of mdb (read from its source,
which is not changed) and against the file mdb writes from CHANGELOG.md today. The SAS code
can not run; its statements and their order are read as text.
"""
import csv
import importlib.util
import re
import sys
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1]
REPOSITORY = SETUP.parents[1]
PROGRAM = SETUP / "Load-Accelerator-Tables.sas"
RELEASES = REPOSITORY / "Model-Definition-Builder" / "cli" / "src" / "mdb" / "core" / "releases.py"

# What CAS held on 2026-10-12 for the table mdb load-releases had loaded: column -> type
MEASURED = {"version": "varchar", "release_date": "varchar", "is_current": "double", "release_rank": "double", "item_rank": "double",
            "section": "varchar", "change_type": "varchar", "component": "varchar", "summary": "varchar", "detail": "varchar"}


def program():
    return PROGRAM.read_text(encoding="utf-8").replace("\r\n", "\n")


def code():
    """The program without its block comments."""
    return re.sub(r"/\*.*?\*/", "", program(), flags=re.DOTALL)


def macro(name):
    text = code()
    return text[text.index(f"%macro {name};"):text.index(f"%mend {name};")]


def in_order(text, *markers):
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), [marker for _, marker in sorted(zip(positions, markers))]


def columns_of_the_program():
    """The columns the program gives CAS, as [(name, type)], from the one statement that lists them."""
    listed = re.findall(r"^%let _lat_vars = (.*);$", code(), flags=re.MULTILINE)
    assert len(listed) == 1
    return [tuple(part.split(":")) for part in listed[0].split()]


@pytest.fixture(scope="module")
def release_file(tmp_path_factory):
    """The file `mdb load-releases --csv` writes from CHANGELOG.md, and its rows as mdb made them.
    The module of mdb is loaded from its source file and is not changed."""
    spec = importlib.util.spec_from_file_location("mdb_releases_read_only", RELEASES)
    releases = importlib.util.module_from_spec(spec)
    sys.modules["mdb_releases_read_only"] = releases
    spec.loader.exec_module(releases)
    path = tmp_path_factory.mktemp("releases") / "ACCELERATOR_RELEASES.csv"
    rows, _ = releases.build_release_table(REPOSITORY / "CHANGELOG.md", path)
    return releases, path, rows


# ---------------------------------------------------------------------------
# The release table: the columns of mdb, with the types CAS gave them
# ---------------------------------------------------------------------------

def test_the_columns_are_the_columns_of_mdb_in_their_order(release_file):
    releases, path, _ = release_file
    columns = columns_of_the_program()

    assert [name for name, _ in columns] == list(releases.COLUMNS)
    with open(path, encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == [name for name, _ in columns]
    assert {kind for _, kind in columns} == {"varchar", "double"}
    # the name of the table is the one mdb loads, and of the file the one the manifest generates
    assert releases.DEFAULT_TABLE == "ACCELERATOR_RELEASES"
    assert "%_lat_default(releaseTable, ACCELERATOR_RELEASES);" in code()
    assert 'filename _latsrc filesrvc folderPath="&contentRoot./Data" filename="ACCELERATOR_RELEASES.csv" recfm=n lrecl=32767;' in code()


def test_the_types_are_the_ones_cas_gave_the_table_of_mdb(release_file):
    """The ten columns that were measured have the measured type, and the one that came since is text."""
    columns = dict(columns_of_the_program())

    assert {name: columns[name] for name in MEASURED} == MEASURED
    assert set(columns) - set(MEASURED) == {"builder_part"} and columns["builder_part"] == "varchar"


def test_every_value_of_the_file_fits_the_type_of_its_column(release_file):
    """A DOUBLE column holds numbers in every row, a VARCHAR column holds text that is no number in at least one."""
    _, path, rows = release_file
    with open(path, encoding="utf-8", newline="") as handle:
        read = list(csv.reader(handle))
    header, lines = read[0], read[1:]

    assert len(lines) == len(rows) > 600
    for position, (name, kind) in enumerate(columns_of_the_program()):
        cells = [line[position] for line in lines]
        numbers = [bool(re.fullmatch(r"\d+", cell)) for cell in cells]
        if kind == "double":
            assert all(numbers), name
        else:
            assert not all(numbers) and header[position] == name, name


def test_the_longest_cell_reaches_cas_as_it_is(release_file):
    """A SAS variable holds 32,767 bytes at most, and a text column that PROC IMPORT makes is as long as
    the longest value it saw. The file never becomes a SAS table: CAS reads it, and the one DATA
    step that looks at it counts its lines and reads 400 bytes of each for the first line."""
    _, path, rows = release_file
    longest = max((len(str(value).encode("utf-8")) for row in rows for value in row.values()), default=0)
    longest_line = max(len(line) for line in path.read_bytes().split(b"\n"))
    release = macro("_lat_release_table")

    assert longest > 4764                         # the changelog has grown since the table was measured
    assert longest_line < 1048576 and "infile \"&_lat_file.\" lrecl=1048576 truncover end=_lat_last;" in release
    # four DATA steps, none of which makes a table: the parameters, the address the folder is asked for with, the
    # first line of the file, and the file of one comment
    assert re.findall(r"^\s*data\s+([^;]+);", code(), flags=re.MULTILINE | re.IGNORECASE) == ["_null_"] * 4
    assert "proc import" not in release.lower() and "_infile_" not in release
    assert "input _lat_first $char400.;" in release
    assert 'upload path = "&_lat_file."' in release
    # the file is copied from SAS Content byte for byte
    in_order(release, 'filename _latsrc filesrvc folderPath="&contentRoot./Data" filename="ACCELERATOR_RELEASES.csv" recfm=n lrecl=32767;',
             'filename _latdst "&_lat_file." recfm=n lrecl=32767;', "%let _lat_copy = %sysfunc(fcopy(_latsrc, _latdst));",
             "%if &_lat_copy. NE 0 %then %do;", "data _null_;", "proc cas;")


def test_the_lines_of_the_file_are_the_rows_of_the_table(release_file):
    """The program counts the lines of the file and takes one off for the first: that is the number of
    rows only while no value holds a line break. mdb writes none - and the first line is the list of
    the columns, which the program compares before it uploads."""
    _, path, rows = release_file
    raw = path.read_bytes()
    lines = raw.split(b"\n")
    assert lines[-1] == b"" and len(lines) - 2 == len(rows)
    assert not [value for row in rows for value in row.values() if "\n" in str(value) or "\r" in str(value)]
    # the first line as the DATA step compares it: mdb writes every value in quotation marks, and a file
    # written on Windows ends its lines with a carriage return - both are taken out
    assert lines[0].startswith(b'"version","release_date",') and len(lines[0]) < 400
    first = lines[0].replace(b"\r", b"").replace(b'"', b"").decode("utf-8")
    assert first == ",".join(name for name, _ in columns_of_the_program())

    release = macro("_lat_release_table")
    assert "_lat_expected = catx(',', _lat_expected, scan(scan(\"&_lat_vars.\", _lat_i, ' '), 1, ':'));" in release
    assert """call symputx('_lat_header', ifn(compress(_lat_first, '"' || '0D'x) = _lat_expected, 1, 0), 'L');""" in release
    assert "if _lat_last then call symputx('_lat_rows', _n_ - 1, 'L');" in release
    in_order(release, "%if &_lat_header. NE 1 %then %do;", "%if &_lat_rows. LE 0 %then %do;", "proc cas;")


def test_cas_reads_the_file_with_the_options_of_the_ingestion_job():
    """The upload statement is the one Ingest-Documents.sas publishes its run history with, which ran on
    SAS Viya on 2026-10-12: the same options, the columns as name and type."""
    job = (REPOSITORY / "SAS-Viya-Integrations" / "RAG-Ingestion" / "Ingest-Documents.sas").read_text(encoding="utf-8").replace("\r\n", "\n")
    theirs = " ".join(re.search(r"importOptions = \{.*?vars = \{", job, flags=re.DOTALL).group(0).split())
    release = macro("_lat_release_table")
    ours = " ".join(re.search(r"importOptions = \{.*?vars = \{", release, flags=re.DOTALL).group(0).split())

    assert ours == theirs == 'importOptions = {fileType = "csv", allowEmbeddedNewLines = true, locale = "EN-us", vars = {'
    assert '{name = "%scan(&_lat_v., 1, %str(:))", type = "%scan(&_lat_v., 2, %str(:))"}' in release
    assert '{name = "%scan(&_rag_hv., 1, %str(:))", type = "%scan(&_rag_hv., 2, %str(:))"}' in job
    assert "%let _lat_n = %sysfunc(countw(&_lat_vars., %str( )));" in release and "%do _lat_i = 1 %to &_lat_n.;" in release


def test_the_width_cas_keeps_for_display_cuts_no_value(release_file):
    """Measured on 2026-10-12, the table of the job against the table `mdb load-releases` loaded from the
    same file: 707 rows, eleven columns with the same types and lengths, and one difference - the
    formatted length, the width CAS keeps for display beside the length, was 16 in the table of the job
    for version, release_date, change_type and builder_part, and 6, 10, 8 and 8 in the table of mdb.
    The upload names no length, so CAS decides both. Why the difference changes nothing: the four are
    the text columns whose longest value is shorter than 16 bytes, so a width of 16 shows every value
    whole - and the page of the Prompt Builder reads nothing from the table it is assigned."""
    _, _, rows = release_file
    columns = columns_of_the_program()
    longest = {name: max(len(str(row[name]).encode("utf-8")) for row in rows) for name, kind in columns if kind == "varchar"}
    short = {name: length for name, length in longest.items() if length < 16}

    assert short == {"version": 6, "release_date": 10, "change_type": 8, "builder_part": 8}
    assert all(length >= 16 for name, length in longest.items() if name not in short)
    # the upload names the name and the type of a column, and nothing else of it
    release = macro("_lat_release_table")
    upload = release[release.index("upload path ="):release.index("table.tableExists", release.index("upload path ="))]
    assert re.findall(r"(\w+) = \"%scan", upload) == ["name", "type"]
    assert not re.search(r"length|format", upload, flags=re.IGNORECASE)
    # the header says what was measured
    header = " ".join(program().split("*/")[0].replace("\n *", " ").split())
    assert ("the width for display that CAS keeps beside the length (the formatted length) is 16 here for the four text columns whose "
            "longest value is shorter than that (version, release_date, change_type, builder_part), and 6, 10, 8 and 8 there, the lengths "
            "of those values. A width of 16 cuts no value of these columns.") in header
    # and the page of the Prompt Builder is assigned the table and reads nothing from it
    builder = (REPOSITORY / "LLM-Prompt-Builder" / "README.md").read_text(encoding="utf-8")
    assert "a DDC object renders only with a data assignment; the app reads nothing" in " ".join(builder.split())


# ---------------------------------------------------------------------------
# A table that exists is replaced only by one that loaded completely
# ---------------------------------------------------------------------------

def test_what_is_said_of_the_fact_sheet_program_is_what_it_does():
    """The header and the README said of the swap of the release table that Load-Fact-Sheets.sas does the
    same for each sheet, while it dropped its trial and loaded the sheet a second time. Now it
    does the same: the counted upload is promoted under the name of the table. The sentence says that -
    and this test reads the program, so that the sentence is looked at again when the program changes."""
    loader = re.sub(r"/\*.*?\*/", "", (REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring" / "Load-Fact-Sheets.sas")
                    .read_text(encoding="utf-8").replace("\r\n", "\n"), flags=re.DOTALL)
    trial = loader[loader.index("%macro _lfs_read("):loader.index("%mend _lfs_read;")]

    # the trial is loaded beside the table and counted, and dropped only where it does not hold every row
    in_order(trial, 'load data=work.&_lfs_work. casOut="&_lfs_table._NEW" replace;',
             'if upcase(entry.Name) == upcase("&_lfs_table._NEW") then staged = entry.Rows;',
             'if staged != &_lfs_rows. then do;',
             'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;')
    swap = loader[loader.index("%macro _lfs_swap("):loader.index("%mend _lfs_swap;")]
    # the counted upload is promoted under the name of the table, as this job does it; nothing loads the sheet again
    in_order(swap, 'table.dropTable status = dropped / caslib = "&_lfs_caslib." name = "&_lfs_table." quiet = true;',
             'table.promote status = promoted / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW"',
             'targetLib = "&_lfs_caslib." target = "&_lfs_table.";', 'table.save status = stored')
    assert "promote" not in trial.lower() and "casData=" not in loader and loader.count("load data=") == 1
    for table in ("LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET"):
        assert f"%_lfs_swap({table});" in loader

    said = ("Load-Fact-Sheets.sas replaces each fact sheet table the same way: the upload whose rows were counted is promoted under "
            "the name of the table and saved, and the sheet is not loaded a second time.")
    header = " ".join(program().split("*/")[0].replace("\n *", " ").split())
    assert said in header and "drops that trial again" not in header

def test_the_table_is_dropped_only_behind_an_upload_that_holds_every_row():
    release = macro("_lat_release_table")

    in_order(release,
             'casOut = {caslib = "&caslib.", name = "&releaseTable._NEW", replace = true}',
             'table.tableExists result = found / caslib = "&caslib." name = "&releaseTable._NEW";',
             'if upcase(entry.Name) == upcase("&releaseTable._NEW") then staged = entry.Rows;',
             "if staged == &_lat_rows. then do;",
             'table.dropTable status = dropped / caslib = "&caslib." name = "&releaseTable." quiet = true;',
             'table.promote status = promoted / caslib = "&caslib." name = "&releaseTable._NEW"',
             'targetLib = "&caslib." target = "&releaseTable.";',
             "if promoted.severity == 0 then do;",
             'table.save status = stored / table = {caslib = "&caslib.", name = "&releaseTable."}',
             'if stored.severity == 0 then outcome = "loaded";',
             'table.dropTable / caslib = "&caslib." name = "&releaseTable._NEW" quiet = true;')
    # the upload is dropped in the ELSE group of the statement that replaces
    replace = release[release.index('if staged == &_lat_rows. and outcome == "rows" then do;'):release.index('call symputx("_lat_outcome"')]
    assert replace.count("else do;") == 1 and replace.index("else do;") < replace.index('name = "&releaseTable._NEW" quiet = true;')
    # the table itself is dropped in one statement, and promoted and saved in one each
    assert release.count('name = "&releaseTable." quiet = true') == 1 and release.count("table.promote") == 1 and release.count("table.save") == 1
    # every way the upload can end is one line of the log, and only the one that loaded is a NOTE
    outcomes = re.findall(r"%if &_lat_outcome\. EQ (\w+) %then", release)
    assert outcomes == ["foreign", "foreignfile", "loaded", "upload", "rows", "replace", "save"]
    said = re.findall(r"%put (\w+): Load-Accelerator-Tables: ([^;]*);", release)
    assert [kind for kind, _ in said] == ["WARNING"] * 5 + ["ERROR", "ERROR", "NOTE"] + ["WARNING"] * 5
    for kind, text in said:
        assert not set(text) & set("%'\"") and text.count("(") == text.count(")"), text
        assert set(re.findall(r"&(\w+)", text)) <= {"contentRoot", "caslib", "releaseTable", "_lat_rows", "_lat_staged", "_lat_taken"}, text
        if kind == "WARNING" and "may not be loaded" not in text and "could not be saved" not in text and "holds what it held before" not in text:
            assert text.endswith("was left as it is.") or "was left as it is. " in text, text


def test_a_file_that_is_not_there_or_holds_other_columns_leaves_the_table():
    release = macro("_lat_release_table")

    missing = release[release.index("%let _lat_there = %sysfunc(fexist(_latsrc));"):release.index("%let _lat_copy = %sysfunc(fcopy(_latsrc, _latdst));")]
    in_order(missing, "%if &_lat_there. NE 1 %then %do;", "filename _latsrc clear;", "was not found in the folder &contentRoot./Data", "%return;")
    # asked twice: to clear the fileref of a file that is not there, and - outside the group of the request - to say so and leave
    assert missing.count("%if &_lat_there. NE 1 %then %do;") == 2
    # one sentence for a file that is not there, whether its folder is there or not
    assert release.count("was not found in the folder &contentRoot./Data") == 1
    assert release.count("%return;") == 8
    assert release.index('outcome = "upload";') > release.split("%if &_lat_outcome. EQ foreign")[0].rindex("%return;")
    # the last of them in front of the upload: CAS is asked for a table of the name the file is uploaded under
    in_order(release[release.index("%let _lat_taken = unknown;"):], "%let _lat_taken = unknown;",
             'table.tableExists result = taken / caslib = "&caslib." name = "&releaseTable._NEW";',
             'call symputx("_lat_taken", taken.exists, "L");', "%if &_lat_taken. NE 0 %then %do;", "%return;", "upload path =")
    assert "_lat_taken" in release[release.index("%local "):release.index(";", release.index("%local "))]
    assert "The file and this job definition are of different releases." in release


# ---------------------------------------------------------------------------
# The fact sheets: Load-Fact-Sheets.sas, included
# ---------------------------------------------------------------------------

def test_the_fact_sheets_are_loaded_by_the_program_of_the_logging_folder():
    """One program loads the sheets, run on its own or from the job. The job hands it its folder and its
    CAS library by the two settings the program takes when they are set."""
    text = code()
    loader = (REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring" / "Load-Fact-Sheets.sas").read_text(encoding="utf-8")

    assert 'filename _latlfs filesrvc folderPath="&contentRoot./Logging and Monitoring" filename="Load-Fact-Sheets.sas";' in text
    in_order(text, "%_lat_find_fact_sheets;", "%let _lfs_path = &contentRoot./Data;", "%let _lfs_caslib = &caslib.;", "%include _latlfs;",
             "filename _latlfs clear;", "%macro _lat_look;")
    for setting in ("_lfs_path", "_lfs_caslib"):
        assert f"%_lfs_default({setting}," in loader
    # nothing of the three sheets is written into the job: no file name, no table that is dropped, loaded or promoted
    for name in ("llm_fact_sheet.csv", "embedding_fact_sheet.csv", "decision_fact_sheet.csv", "proc import", "proc casutil"):
        assert name not in text.lower(), name
    assert text.count("LLM_FACT_SHEET") == text.count("DECISION_FACT_SHEET") == 2      # in the look at the end, and in its NOTE


def test_the_include_stands_in_open_code():
    """Load-Fact-Sheets.sas holds statements of the macro language in open code, which SAS does not take
    inside another such statement. The include is no part of a macro and of no %if block: where the
    program is not there, or the run is refused, the fileref names a file of one comment."""
    text = code()
    lines = text.split("\n")
    position = lines.index("%include _latlfs;")
    depth = macros = 0
    for line in lines[:position]:
        stripped = line.strip()
        macros += stripped.startswith("%macro ") - stripped.startswith("%mend ")
        depth += len(re.findall(r"%do\b", stripped)) - len(re.findall(r"%end\b", stripped))
    assert macros == 0 and depth == 0
    assert text.count("%include") == 1

    find = macro("_lat_find_fact_sheets")
    in_order(find, "%if &_lat_ready. EQ 1 %then %do;", "%if %sysfunc(fexist(_latlfs)) EQ 1 %then %let _lat_found = 1;",
             "%if &_lat_found. NE 1 %then %do;", "filename _latlfs temp;", "file _latlfs;")
    stub = re.search(r"put '(.*)';", find).group(1)
    assert stub.startswith("* ") and stub.endswith(";") and not set(stub[:-1]) & set("%&'\";")
    assert text.index("filename _latlfs temp;") < text.index("%macro _lat_find_fact_sheets;")


# ---------------------------------------------------------------------------
# Parameters, the CAS session and the state of the job
# ---------------------------------------------------------------------------

def test_the_parameters_of_the_job_and_what_the_program_takes_without_them():
    text = code()
    defaults = dict(re.findall(r"^%_lat_default\((\w+), (.*)\);$", text, flags=re.MULTILINE))

    assert defaults == {"contentRoot": "/SAS Agentic AI Accelerator", "caslib": "Public", "releaseTable": "ACCELERATOR_RELEASES"}
    assert ("%macro _lat_default(name, value);\n    %if not %symexist(&name.) %then %do;\n        %global &name.;\n"
            "        %let &name. = &value.;\n    %end;\n%mend _lat_default;") in text
    header = program().split("*/")[0]
    for parameter in list(defaults) + ["_action", "_contextName"]:
        assert re.search(rf"\b{parameter}\b", header), parameter
    # the folder of the content root the two files are read from is the folder Load-Fact-Sheets.sas reads by default
    assert defaults["contentRoot"] + "/Data" == "/SAS Agentic AI Accelerator/Data"


def test_a_session_that_does_not_run_as_the_user_who_started_the_job_is_one_note():
    """Measured on 2026-10-12 in the SAS Job Execution compute context: the NOTE, and then the job read the folder
    Data and loaded the release table. The job says in one NOTE when its session runs as another user than the
    one SAS Job Execution started it for, and how it is run as oneself. One person written with a domain behind
    @ or in front of a backslash is one person (review-18): the two are compared as the persons they name."""
    text = code()
    check = text[text.index("data _null_;"):text.index("run;") + 4]

    in_order(check, "if symexist('SYS_COMPUTE_SESSION_OWNER') then do;",
             "_lat_value = lowcase(scan(scan(strip(symget('SYS_COMPUTE_SESSION_OWNER')), -1, '\\'), 1, '@'));",
             "_lat_user = lowcase(scan(scan(strip(symget('SYSUSERID')), -1, '\\'), 1, '@'));",
             "if lengthn(_lat_value) > 0 and strip(_lat_value) ne strip(_lat_user) then do;",
             "putLog 'NOTE: Load-Accelerator-Tables: this SAS session does not run as the user who started the job,",
             "putLog 'NOTE- To run the job as yourself, start it with the parameter _contextName and the name of a compute context that runs as you.';")
    # the session is asked for the user only where it names one, and no statement resolves either user
    assert check.count("symget('SYS_COMPUTE_SESSION_OWNER')") == 1 and check.index("symexist(") < check.index("symget('SYS_COMPUTE_SESSION_OWNER')")
    assert not re.search(r"&sysuserid|&SYS_COMPUTE_SESSION_OWNER", text, flags=re.IGNORECASE)
    # neither user is written: every line of the log is a text of the program, and one a SAS statement takes
    written = re.findall(r"putLog (.*);", check)
    assert len(written) == 5 and all(re.fullmatch(r"'[^']*'", line) for line in written)
    assert all(len(line) < 262 and not set(line[1:-1]) & set("%&;\"") for line in written)
    # it is a NOTE: the job is the job it was, with its state decided by the tables at its end
    assert [line[1:6] for line in written] == ["ERROR", "ERROR", "ERROR", "NOTE:", "NOTE-"]
    assert check.count("call symputx('_lat_ready', '0');") == 3 and "_lat_failed" not in check


def test_a_parameter_that_would_be_read_as_code_is_refused_before_it_stands_in_a_statement():
    text = code()
    check = text[text.index("data _null_;"):text.index("run;") + 4]

    assert text.index("%let _lat_ready = 1;") < text.index("data _null_;") < text.index("&contentRoot.")
    assert text.index("data _null_;") < text.index("&caslib.") and text.index("data _null_;") < text.index("&releaseTable.")
    # each parameter is read as text, and one that is refused is set to its default
    for parameter, default in (("contentRoot", "/SAS Agentic AI Accelerator"), ("caslib", "Public"), ("releaseTable", "ACCELERATOR_RELEASES")):
        assert f"_lat_value = symget('{parameter}');" in check and f"call symputx('{parameter}', '{default}');" in check
    assert check.count("""findc(_lat_value, '"''%&;') > 0""") == 2
    assert "nvalid(strip(_lat_value), 'v7') = 0" in check and "lengthn(_lat_value) > 28" in check
    errors = re.findall(r"putLog '(ERROR: [^']*)';", check)
    assert len(errors) == 3 and all(error.endswith("nothing was read or written.") for error in errors)
    assert check.count("call symputx('_lat_ready', '0');") == 3
    # every macro of the program leaves at once for a run that was refused
    for name in ("_lat_release_table", "_lat_look"):
        assert macro(name).split("\n")[1 if name == "_lat_look" else 2].strip() == "%if &_lat_ready. NE 1 %then %return;", name


def test_the_cas_session_and_the_syntax_check_option():
    text = code()

    assert ("%if &_lat_ready. EQ 1 and %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;\n"
            "    %let _lat_clean_cas_session = 1;\n    cas _latSess;\n%end;\n%else %do;\n    %let _lat_clean_cas_session = 0;\n%end;") in text
    assert "%if &_lat_clean_cas_session. %then %do;\n    cas _latSess terminate;\n%end;" in text
    assert re.findall(r"^\s*cas\s+(\w+)([^;]*);", text, flags=re.MULTILINE) == [("_latSess", ""), ("_latSess", " terminate")]
    in_order(text, "%let _lat_syntaxcheck = %sysfunc(getoption(syntaxcheck));\noptions nosyntaxcheck;", "data _null_;", "cas _latSess;",
             "%_lat_release_table;", "%include _latlfs;", "%_lat_look;", "cas _latSess terminate;", "options &_lat_syntaxcheck.;")


def test_the_job_fails_for_a_refusal_and_for_a_table_that_is_not_there_at_the_end():
    text = code()
    look = macro("_lat_look")

    assert 'do name over {"&releaseTable.", "LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET"};' in look
    in_order(look, 'table.tableExists result = found / caslib = "&caslib." name = name;', 'if found.exists == 0 then missing = missing || " " || name;',
             'call symputx("_lat_missing", missing);', "%if %length(&_lat_missing.) GT 0 %then %do;", "%put ERROR: Load-Accelerator-Tables:",
             "%let _lat_failed = 1;")
    # the statement that sets the condition code is the last one, behind the session and the option
    block = "%if &_lat_ready. NE 1 or &_lat_failed. EQ 1 %then %do;\n    %let SYSCC = 1012;\n%end;\n"
    assert text.count(block) == 1 and len(re.findall(r"syscc\s*=", text, flags=re.IGNORECASE)) == 1
    behind = [statement.strip() for statement in text.split(block)[1].split(";") if statement.strip()]
    assert behind and all(statement.startswith("%symdel ") for statement in behind)
    in_order(text, "cas _latSess terminate;", "options &_lat_syntaxcheck.;", block)
    comment = text.split(block)[0].rstrip().split("\n")[-1]
    assert comment.startswith("* A run that wrote an ERROR line ends with the condition code of a failed job") and comment.endswith(";")
    assert not re.search(r"[%&'\";]", comment[:-1])
    # nothing but an ERROR line sets what the statement reads
    assert re.findall(r"%let _lat_failed\s*=\s*(\d);", text) == ["0", "1", "1", "1"]
    assert not re.search(r"\babort\b|%abort|endsas", text, flags=re.IGNORECASE)
    # the two settings of the included program are deleted where it did not run and delete them itself
    assert text.rstrip().endswith("%symdel _lfs_path _lfs_caslib / nowarn;")


def test_a_table_of_that_name_that_is_no_release_table_is_left_as_it_is():
    """Review-17/18 (open item 14): the parameter releaseTable could name any table of the CAS library, which the job
    then dropped and replaced - a fact sheet, the log table. A table of that name that exists is replaced only where it
    has the columns of a release table - all but builder_part, which the release table of 2.2.0 lacks (measured on 2026-10-12);
    otherwise it is left, the upload is dropped, and the job ends with an ERROR
    line and SYSCC 1012."""
    release = macro("_lat_release_table")

    in_order(release,
             "if staged == &_lat_rows. then do;",
             'lacking = "foreign";',
             'table.tableExists result = present / caslib = "&caslib." name = "&releaseTable.";',
             'if present.exists != 0 then look = "&releaseTable.";',
             'table.columnInfo result = described / table = {caslib = "&caslib.", name = look};',
             "have = have || upcase(column.Column) || \" \";",
             '%let _lat_v = %upcase(%scan(%scan(&_lat_vars., &_lat_i., %str( )), 1, %str(:)));',
             '%if &_lat_v. NE BUILDER_PART %then %do;',
             'if index(have, " &_lat_v. ") == 0 then outcome = lacking;',
             'if staged == &_lat_rows. and outcome == "rows" then do;',
             'table.dropTable status = dropped / caslib = "&caslib." name = "&releaseTable." quiet = true;',
             'table.dropTable / caslib = "&caslib." name = "&releaseTable._NEW" quiet = true;')
    # every column the job loads is looked for, by the names the upload gives them
    names = [entry.split(":")[0].upper() for entry in re.search(r"%let _lat_vars = ([^;]*);", code()).group(1).split()]
    assert len(names) == 11 and "BUILDER_PART" in names and "DETAIL" in names
    # the refusal is an ERROR line that makes the job fail, and nothing behind it says that the table was loaded:
    # one for a table in memory, one for a table that is only the file it was saved to (test_loader_followed.py)
    refused = release[release.index("%if &_lat_outcome. EQ foreign %then %do;"):release.index("%if &_lat_outcome. EQ loaded %then")]
    in_memory, in_file = refused.split("%if &_lat_outcome. EQ foreignfile %then %do;")
    in_order(in_memory, "%put ERROR: Load-Accelerator-Tables: the table &caslib..&releaseTable. is no release table", "%let _lat_failed = 1;",
             "%return;", "%end;")
    in_order(in_file, "%put ERROR: Load-Accelerator-Tables: no table &caslib..&releaseTable. is in memory, and the file a table of that name "
             "was saved to", "%let _lat_failed = 1;", "%return;", "%end;")
    # the two refusals are the only ERROR lines of the macro
    assert release.count("%put ERROR:") == 2 and refused.count("%let _lat_failed = 1;") == 2
