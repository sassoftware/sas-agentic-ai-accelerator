"""Load-Accelerator-Tables.sas, followed: a folder that is not there, and a table that is only a saved file.

live-19, defect 6. Measured on 2026-10-13 with a content root that had no folder "Logging and
Monitoring": the FILENAME statement for the program of that folder wrote "ERROR: Folder denoted by
path ... was not found." and "ERROR: Error in the FILENAME statement." in front of the WARNING the
job means. The job now asks the Folders service for a folder before it assigns a fileref to a file
of it, and leaves the statement out where the answer is 404.

review-19: the job replaced a table of the name of its parameter releaseTable only where that
table is a release table - and looked for the table in memory only. A table that is only the file
it was saved to, which is how a restart of the CAS server leaves a saved table, was not looked at,
and the save of the new table wrote over that file. Where no table of that name is in memory the
job now looks for the file, loads it as a table of its session, reads its columns and drops it.

SAS and CAS can not run here. The macros are followed with macro_harness, and the CASL of their
PROC CAS step runs against a CAS library that is made up (casl_harness, both in the tests of
Logging-Monitoring): the tests say what the program does IF the FILENAME statement, PROC HTTP and
the CAS actions behave as that model has them. What was measured is named where it is used.
"""
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

SETUP = Path(__file__).resolve().parents[1]
REPOSITORY = SETUP.parents[1]
sys.path.insert(0, str(REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring" / "tests"))

import casl_harness  # noqa: E402

PROGRAM = SETUP / "Load-Accelerator-Tables.sas"
FACT_SHEETS = REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring" / "Load-Fact-Sheets.sas"
ROOT = "/Site, Root (2026)"
RELEASES = "ACCELERATOR_RELEASES"
NOT_FOUND = ("WARNING: Load-Accelerator-Tables: the file ACCELERATOR_RELEASES.csv was not found in the folder " + ROOT
             + "/Data, the table Public.ACCELERATOR_RELEASES was left as it is.")
NO_PROGRAM = ("WARNING: Load-Accelerator-Tables: the program Load-Fact-Sheets.sas was not found in the folder " + ROOT
              + "/Logging and Monitoring, the three fact sheet tables were left as they are.")
# The columns of other tables of a site
LOG_COLUMNS = ["timestamp", "model", "transaction_id", "prompt_length", "output_length", "response"]
SHEET_COLUMNS = ["model", "provider", "input_cost", "output_cost"]


def program():
    return PROGRAM.read_text(encoding="utf-8").replace("\r\n", "\n")


def columns():
    return [part.split(":")[0] for part in re.search(r"^%let _lat_vars = (.*);$", program(), flags=re.MULTILINE).group(1).split()]


def symbols(**more):
    found = {"contentRoot": ROOT, "caslib": "Public", "releaseTable": RELEASES, "_lat_ready": "1", "_lat_failed": "0",
             "SYS_PROCHTTP_STATUS_CODE": "an answer of an earlier request",
             "_lat_vars": re.search(r"^%let _lat_vars = (.*);$", program(), flags=re.MULTILINE).group(1)}
    found.update(more)
    return found


def follow(name, status, there, cas=None, copied=0, rows=3, **more):
    """Follows a macro of the job for an answer of the Folders service (the status PROC HTTP leaves, '' for no
    answer), for what FEXIST says of the file, and for a CAS library. Returns (the SAS code it generates, the lines
    it writes, the folders it asked for, the macro variables at its end)."""
    asked = []

    def called(text, macros):
        asked.append(text)
        macros.symbols["sys_prochttp_status_code"] = status

    def data_step(lines, macros):
        # the DATA step that reads the first line of the file and counts its lines
        if any("_lat_header" in line for line in lines):
            macros.symbols["_lat_header"], macros.symbols["_lat_rows"] = "1", str(rows)

    functions = {"pathname": lambda arguments: "/saswork", "fexist": lambda arguments: there, "fcopy": lambda arguments: copied}
    generated, log, macros = casl_harness.follow(program(), name, symbols(**more), cas, functions, called, data_step)
    return [line for line in generated if not line.startswith(casl_harness.CALLED)], log, asked, macros


def assigned(generated):
    """The FILENAME statements of the access method FILESRVC a run generated."""
    return [line for line in generated if re.match(r"filename \w+ filesrvc ", line)]


# ---------------------------------------------------------------------------
# A folder that is not there is a WARNING, and no statement that fails
# ---------------------------------------------------------------------------

def test_a_content_root_without_the_logging_folder_is_the_warning_only():
    generated, log, asked, macros = follow("_lat_find_fact_sheets", "404", 0)

    assert asked == ["_lat_ask_folder(/Logging and Monitoring);"]
    assert assigned(generated) == [] and log == [NO_PROGRAM]
    # the include still has something to read: the file of one comment
    assert generated == ["filename _latlfs temp;", "data _null_;", "file _latlfs;",
                         "put '* Load-Accelerator-Tables: the fact sheets are not loaded in this run;';", "run;"]
    assert macros["_lat_failed"] == "0"


@pytest.mark.parametrize("status, there, found", [("200", 1, True), ("200", 0, False), ("401", 0, False), ("403", 0, False),
                                                  ("500", 0, False), ("", 0, False), ("", 1, True)],
                         ids=["there", "the folder without the program", "401", "403", "500", "no answer", "no answer, and the file is there"])
def test_every_other_answer_leaves_the_statement_to_say_what_it_says(status, there, found):
    """Only the answer 404 says that the folder is not there. A folder that is there, a session that may not ask
    and a request without an answer assign the fileref as before: where the program is there it is included."""
    generated, log, asked, _ = follow("_lat_find_fact_sheets", status, there)

    assert asked == ["_lat_ask_folder(/Logging and Monitoring);"]
    assert assigned(generated) == ['filename _latlfs filesrvc folderPath="' + ROOT + '/Logging and Monitoring" filename="Load-Fact-Sheets.sas";']
    assert log == ([] if found else [NO_PROGRAM])
    assert ("filename _latlfs temp;" in generated) == (not found)


def test_a_run_that_was_refused_asks_for_no_folder():
    generated, log, asked, _ = follow("_lat_find_fact_sheets", "404", 0, _lat_ready="0")

    assert asked == [] and assigned(generated) == [] and log == [] and generated[0] == "filename _latlfs temp;"
    generated, log, asked, _ = follow("_lat_release_table", "404", 0, _lat_ready="0")
    assert (generated, log, asked) == ([], [], [])


def test_a_content_root_without_the_data_folder_is_the_warning_only():
    generated, log, asked, macros = follow("_lat_release_table", "404", 0)

    assert asked == ["_lat_ask_folder(/Data);"]
    assert generated == [] and log == [NOT_FOUND] and macros["_lat_failed"] == "0"


@pytest.mark.parametrize("status", ["200", "401", "500", ""])
def test_a_data_folder_without_the_file_is_the_same_warning(status):
    generated, log, asked, _ = follow("_lat_release_table", status, 0)

    assert asked == ["_lat_ask_folder(/Data);"]
    assert generated == ['filename _latsrc filesrvc folderPath="' + ROOT + '/Data" filename="ACCELERATOR_RELEASES.csv" recfm=n lrecl=32767;',
                         "filename _latsrc clear;"]
    assert log == [NOT_FOUND]


def test_the_folder_is_asked_for_before_a_fileref_is_assigned_to_a_file_of_it():
    """Every FILENAME statement of the access method FILESRVC of the job and of the program it includes stands
    behind the request, in the group of an answer that is not 404."""
    for text, prefix, count in ((program(), "_lat", 2), (FACT_SHEETS.read_text(encoding="utf-8").replace("\r\n", "\n"), "_lfs", 1)):
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        statements = [match.start() for match in re.finditer(r"filename \w+ filesrvc ", code)]
        assert len(statements) == count
        for position in statements:
            before = code[:position].rstrip()
            assert before.endswith('%if "&SYS_PROCHTTP_STATUS_CODE." NE "404" %then %do;')
            assert re.search(r"%" + prefix + r"_ask_folder\([^)]*\);\s*$", before[:before.rindex("%if")])
        assert code.count("%sysmacdelete " + prefix + "_ask_folder;") == 1
        assert code.index("%mend " + prefix + "_ask_folder;") < min(statements) < code.index("%sysmacdelete " + prefix + "_ask_folder;")


def ask_macro(text, prefix):
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return code[code.index(f"%macro {prefix}_ask_folder("):code.index(f"%mend {prefix}_ask_folder;")]


def test_the_request_is_the_one_start_here_asks_with():
    """Start Here asks GET /folders/folders/@item?path=<the folder>, which this SAS Viya answered with 404 for a
    folder that is not there (measured on 2026-10-13 with mdb, which sends the same request). The address is made
    in a DATA step from the text of the parameter: SYMGET reads it and URLENCODE writes it, so that no statement
    of the macro language resolves the path - a comma or a parenthesis of a folder name ends no argument."""
    ask = ask_macro(program(), "_lat")
    start_here = (SETUP / "Start-Here.sas").read_text(encoding="utf-8")

    assert "status, folder = read_json(get, '/folders/folders/@item?path=' + segment(root))" in start_here
    assert ("_lat_ask = cats(getoption('servicesbaseurl'), '/folders/folders/@item?path=', "
            "urlencode(cats(symget('contentRoot'), \"&_lat_below.\")));") in ask
    assert "call symputx('_lat_ask', _lat_ask, 'L');" in ask and "%local _lat_ask;" in ask
    assert 'proc http method = "GET" url = "%superq(_lat_ask)" oauth_bearer = sas_services out = _latask;' in ask
    # the one reference with an ampersand is the name of the folder below the root, a text of this program
    assert re.findall(r"&\w+", ask) == ["&_lat_below"] and re.findall(r"%_lat_ask_folder\(([^)]*)\);", program()) == ["/Data", "/Logging and Monitoring"]
    # the answer of an earlier request is taken away first, in the global symbol table PROC HTTP writes to
    assert ask.index("call symputx('SYS_PROCHTTP_STATUS_CODE', ' ', 'G');") < ask.index("proc http")
    # what the DATA step makes for a root with a blank, a comma and parentheses names that folder and nothing else
    address = "https://viya.example.com" + "/folders/folders/@item?path=" + quote(ROOT + "/Data", safe="")
    assert unquote(address.split("?path=")[1]) == ROOT + "/Data" and not set(address.split("?path=")[1]) & set(" ,()/")


def test_the_fact_sheet_program_asks_the_same_way():
    """One way to ask, written twice because the job has to ask before it can include the program: the two macros
    are the same statements, but for their prefix and for the macro variable that holds the folder."""
    ours = ask_macro(program(), "_lat")
    theirs = ask_macro(FACT_SHEETS.read_text(encoding="utf-8").replace("\r\n", "\n"), "_lfs")

    assert theirs.replace("_lfs", "_lat").replace("symget('_lat_path')", "symget('contentRoot')") == ours
    assert "symget('_lfs_path')" in theirs


# ---------------------------------------------------------------------------
# The release table takes the place of a release table only - in memory, or saved
# ---------------------------------------------------------------------------

def loaded(cas, name=RELEASES):
    return cas.promoted.get(cas.key("Public", name))


def saved(cas, name=RELEASES):
    return cas.files.get(("public", name + ".sashdat"))


def release(cas, **more):
    generated, log, asked, macros = follow("_lat_release_table", "200", 1, cas, rows=cas.upload_rows, **more)
    return log, macros


def left_alone(cas, log, macros, name, sentence):
    """A run that was refused: one ERROR line, the job fails, and no table of the run is left in the session."""
    assert log == [sentence] and macros["_lat_failed"] == "1"
    assert cas.session == {} and loaded(cas, name) is None
    assert not [action for action, _ in cas.calls if action in ("table.promote", "table.save")]


FOREIGN = "ERROR: Load-Accelerator-Tables: the table Public.{0} is no release table - it lacks columns of one - and was left as it is: " \
          "the parameter releaseTable names another table."
FOREIGN_FILE = "ERROR: Load-Accelerator-Tables: no table Public.{0} is in memory, and the file a table of that name was saved to in the " \
               "CAS library Public could not be loaded or lacks columns of a release table. The job loads that file as a table " \
               "_LAT_SAVED of its session to read its columns, and only where no table of that name is in the CAS library. The file " \
               "was left as it is, the upload of ACCELERATOR_RELEASES.csv was dropped again and no table Public.{0} was loaded: the " \
               "parameter releaseTable names another table, or that file is to be removed first."
# The line of a saved file that was not looked at, because a table has the name the job would load it under ({1}: what CAS answered)
SAVED_TAKEN = "ERROR: Load-Accelerator-Tables: no table Public.{0} is in memory, and the file a table of that name was saved to in the " \
              "CAS library Public was not looked at. The job loads that file as a table _LAT_SAVED of its session to read its columns, " \
              "and CAS did not answer that there is no table Public._LAT_SAVED - tableExists said {1}, and 0 is no table. The job loads " \
              "over and drops no table it did not load: where a table of that name is there, rename or drop it and run the job again. " \
              "The file was left as it is, the upload of ACCELERATOR_RELEASES.csv was dropped again and no table Public.{0} was loaded."
TAKEN = "WARNING: Load-Accelerator-Tables: CAS did not answer that there is no table Public.{0}_NEW - tableExists said {1}, and 0 is no " \
        "table. The file is uploaded under that name first, as a table of this session, and the job drops and replaces no table it did " \
        "not load: where a table of that name is there, rename or drop it and run the job again. Nothing was uploaded, the table " \
        "Public.{0} was left as it is."
LOADED = "NOTE: Load-Accelerator-Tables: the release table Public.ACCELERATOR_RELEASES was loaded with 755 rows, promoted and saved."


@pytest.mark.parametrize("no_table_when_empty", [True, False], ids=["an empty listing has no table", "an empty listing is a table of no rows"])
def test_a_first_run_loads_the_table_and_writes_no_error(no_table_when_empty):
    """No table and no file: the library is listed, nothing is found, the table is loaded. Whether CAS answers the
    listing of a library without files with no table or with one of no rows, the CASL reads both."""
    cas = casl_harness.FakeCAS(upload_rows=755, lists_no_table_when_empty=no_table_when_empty)
    log, macros = release(cas)

    assert log == [LOADED] and macros["_lat_failed"] == "0" and cas.errors == []
    assert loaded(cas) == {"columns": columns(), "rows": 755, "of": "upload"} and saved(cas)["of"] == "upload" and cas.session == {}
    assert [action for action, _ in cas.calls] == ["table.tableExists", "upload", "table.tableExists", "table.tableInfo", "table.tableExists",
                                                   "table.fileInfo", "table.dropTable", "table.promote", "table.save"]
    # the first action asks for a table of the name the file is uploaded under
    assert cas.calls[0][1] == {"caslib": "Public", "name": RELEASES + "_NEW"}


def test_a_release_table_in_memory_is_replaced_also_the_one_of_2_2_0():
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.table("Public", RELEASES, [name for name in columns() if name != "builder_part"], rows=453, of="the table of 2.2.0")
    cas.file("Public", RELEASES + ".sashdat", [name for name in columns() if name != "builder_part"], rows=453, of="the table of 2.2.0")
    log, macros = release(cas)

    assert log == [LOADED] and cas.errors == [] and loaded(cas)["of"] == "upload" and saved(cas)["rows"] == 755
    # a table in memory is the one that is looked at: the files are not listed and nothing is loaded for it
    assert not [action for action, _ in cas.calls if action in ("table.fileInfo", "table.loadTable")]


def test_another_table_in_memory_is_left_as_it_is():
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.table("Public", "LLM_LOGS", LOG_COLUMNS, rows=1040, of="the log")
    cas.file("Public", "LLM_LOGS.sashdat", LOG_COLUMNS, rows=1040, of="the log")
    log, macros = release(cas, releaseTable="LLM_LOGS")

    assert log == [FOREIGN.format("LLM_LOGS")] and macros["_lat_failed"] == "1"
    assert loaded(cas, "LLM_LOGS") == {"columns": LOG_COLUMNS, "rows": 1040, "of": "the log"} and saved(cas, "LLM_LOGS")["of"] == "the log"
    assert cas.session == {} and cas.errors == []


@pytest.mark.parametrize("file_name", ["LLM_LOGS.sashdat", "llm_logs.sashdat"], ids=["the name as it was saved", "the name in another case"])
def test_another_table_that_is_only_its_saved_file_is_left_as_it_is(file_name):
    """review-19. LLM_LOGS after a restart of the CAS server: no table in memory, and the file it was saved to.
    tableExists says 0, and the save of the release table wrote over the file. The file is loaded as a table of
    the session, it lacks the columns of a release table, and nothing is replaced."""
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.file("Public", file_name, LOG_COLUMNS, rows=1040, of="the log of every run")
    log, macros = release(cas, releaseTable="LLM_LOGS")

    left_alone(cas, log, macros, "LLM_LOGS", FOREIGN_FILE.format("LLM_LOGS"))
    assert cas.files == {("public", file_name): {"columns": LOG_COLUMNS, "rows": 1040, "of": "the log of every run", "loadable": True}}
    assert cas.errors == []
    # the file was loaded under a name of the session and dropped again
    load = [parameters for action, parameters in cas.calls if action == "table.loadTable"]
    assert load == [{"caslib": "Public", "path": file_name, "casOut": {"caslib": "Public", "name": "_LAT_SAVED", "replace": True}}]


def test_a_table_of_the_site_that_has_the_name_of_the_table_of_the_session_is_not_dropped():
    """The saved file is loaded as _LAT_SAVED. A site can have a table of that name, and the parameter can name it:
    what this step drops is the table it loaded, and it loaded none."""
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.table("Public", "_LAT_SAVED", LOG_COLUMNS, rows=7, of="a table of the site")
    log, macros = release(cas, releaseTable="_LAT_SAVED")

    assert log == [FOREIGN.format("_LAT_SAVED")] and macros["_lat_failed"] == "1"
    assert loaded(cas, "_LAT_SAVED") == {"columns": LOG_COLUMNS, "rows": 7, "of": "a table of the site"} and cas.session == {}
    assert [parameters["name"] for action, parameters in cas.calls if action == "table.dropTable"] == ["_LAT_SAVED_NEW"]


@pytest.mark.parametrize("scope, answer", [("global", 2), ("session", 1)], ids=["a table of the site", "a table of the session"])
def test_a_table_that_has_the_name_the_file_is_uploaded_under_is_not_touched(scope, answer):
    """gate-20. The file is uploaded as ACCELERATOR_RELEASES_NEW, and that table is dropped again or promoted. A site
    can have a table of that name. What CAS does with a table of the session that has the name of a global one has
    not been measured, so the job does not rest on it: it asks before the upload, and where a table of the name is
    there it uploads nothing under it and drops nothing of it."""
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.table("Public", RELEASES, columns(), rows=453, of="the table of before")
    cas.table("Public", RELEASES + "_NEW", LOG_COLUMNS, rows=7, of="a table of the site", scope=scope)
    log, macros = release(cas)

    assert log == [TAKEN.format(RELEASES, answer)] and macros["_lat_failed"] == "0"
    assert [action for action, _ in cas.calls] == ["table.tableExists"]
    held = cas.promoted if scope == "global" else cas.session
    assert held[cas.key("Public", RELEASES + "_NEW")] == {"columns": LOG_COLUMNS, "rows": 7, "of": "a table of the site"}
    assert loaded(cas)["of"] == "the table of before"


@pytest.mark.parametrize("scope, answer", [("global", 2), ("session", 1)], ids=["a table of the site", "a table of the session"])
def test_a_table_that_has_the_name_the_saved_file_is_loaded_under_is_not_touched(scope, answer):
    """The same for the second name the job loads a table of its session under: the file a table was saved to is
    loaded as _LAT_SAVED to read its columns, and dropped again. With a table of that name in the library the file
    is not loaded: it is left as it is, with an ERROR line of its own that names the table and what CAS answered."""
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.table("Public", "_LAT_SAVED", SHEET_COLUMNS, rows=7, of="a table of the site", scope=scope)
    cas.file("Public", "LLM_LOGS.sashdat", LOG_COLUMNS, rows=1040, of="the log of every run")
    log, macros = release(cas, releaseTable="LLM_LOGS")

    assert log == [SAVED_TAKEN.format("LLM_LOGS", answer)] and macros["_lat_failed"] == "1" and cas.errors == []
    held = cas.promoted if scope == "global" else cas.session
    assert held == {cas.key("Public", "_LAT_SAVED"): {"columns": SHEET_COLUMNS, "rows": 7, "of": "a table of the site"}}
    assert (cas.session == {}) == (scope == "global")
    assert saved(cas, "LLM_LOGS")["of"] == "the log of every run" and loaded(cas, "LLM_LOGS") is None
    assert not [action for action, _ in cas.calls if action in ("table.loadTable", "table.columnInfo", "table.promote", "table.save")]
    # the one table that is dropped is the upload of this run
    assert [parameters["name"] for action, parameters in cas.calls if action == "table.dropTable"] == ["LLM_LOGS_NEW"]


@pytest.mark.parametrize("scope, answer", [("global", 2), ("session", 1)], ids=["a table of the site", "a table of the session"])
def test_a_good_release_table_behind_a_taken_name_is_not_called_a_file_that_could_not_be_loaded(scope, answer):
    """live-20b, review-20b. Measured on 2026-10-13 with a table _LAT_SAVED of the session (tableExists said 1) and a
    release table that was only its saved file: the job wrote the ERROR line of a file that `could not be loaded or
    lacks columns of a release table`, with the remedies `the parameter releaseTable names another table, or that
    file is to be removed first` - and the next run, without the table, read the same file and loaded 796 rows. The
    line also said `nothing was loaded` behind the upload of the CSV file. The cause now has a line of its own: the
    table that is in the way, what CAS answered, and the remedy that fits. The run fails as before: the release
    table is not in memory at its end."""
    cas = casl_harness.FakeCAS(upload_rows=796)
    cas.table("Public", "_LAT_SAVED", SHEET_COLUMNS, rows=19, of="a table of somebody", scope=scope)
    cas.file("Public", RELEASES + ".sashdat", columns(), rows=755, of="the table of before")
    log, macros = release(cas)

    assert log == [SAVED_TAKEN.format(RELEASES, answer)] and macros["_lat_failed"] == "1" and cas.errors == []
    for wrong in ("could not be loaded", "lacks columns", "names another table", "to be removed first", "nothing was loaded"):
        assert wrong not in log[0], wrong
    for said in ("Public._LAT_SAVED", f"tableExists said {answer}", "rename or drop it and run the job again",
                 "the upload of ACCELERATOR_RELEASES.csv was dropped again"):
        assert said in log[0], said
    # what the line says is what happened: the file as it was, the upload gone, the table of somebody untouched
    assert saved(cas)["of"] == "the table of before" and loaded(cas) is None
    actions = [(action, parameters.get("name")) for action, parameters in cas.calls]
    assert actions.count(("upload", None)) == 1 and actions[-1] == ("table.dropTable", RELEASES + "_NEW")
    assert cas.key("Public", RELEASES + "_NEW") not in cas.session
    held = cas.promoted if scope == "global" else cas.session
    assert held[cas.key("Public", "_LAT_SAVED")] == {"columns": SHEET_COLUMNS, "rows": 19, "of": "a table of somebody"}
    # the same file without that table is loaded, as the run of the pass behind it did
    again = casl_harness.FakeCAS(upload_rows=796)
    again.file("Public", RELEASES + ".sashdat", columns(), rows=755, of="the table of before")
    assert release(again)[0] == [LOADED.replace("755", "796")]


class _Look:
    """Stands in for the PROC CAS step of the macro that looks at the end of the job, whose statements
    test_load_accelerator_tables.py holds: the names of its list for which tableExists answers 0."""

    def __init__(self, cas):
        self.cas = cas

    def run(self, casl):
        assert 'if found.exists == 0 then missing = missing || " " || name;' in casl and 'call symputx("_lat_missing", missing);' in casl
        names = re.findall(r'"([^"]*)"', re.search(r"do name over \{(.*?)\};", casl).group(1))
        # CALL SYMPUTX leaves out the blanks in front of and behind the value: the line of the pass named the three
        # tables with one blank in front of the first
        return {"_lat_missing": " ".join(name for name in names if self.cas.exists("Public", name) == 0)}


@pytest.mark.parametrize("sheets", [True, False], ids=["the four tables are there", "the fact sheets are not there"])
def test_the_warning_of_a_taken_name_alone_does_not_fail_the_job(sheets):
    """live-20b could not show what the condition code of the job is where the WARNING for <table>_NEW is its only
    message: its test root held no fact sheet table, so every run ended with the ERROR line of the look at the end
    and SYSCC 1012. Followed here: the WARNING sets nothing the last statement reads, and with the four tables in
    the CAS library the look at the end writes its NOTE. With the fact sheets missing it is the ERROR line the pass
    saw. That SAS then leaves SYSCC as it was is what the last statement of the program says, and is not run here."""
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.table("Public", RELEASES, columns(), rows=453, of="the table of before")
    cas.table("Public", RELEASES + "_NEW", LOG_COLUMNS, rows=19, of="a table of the site")
    for name in ("LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET") if sheets else ():
        cas.table("Public", name, SHEET_COLUMNS, rows=30, of="a fact sheet")
    log, macros = release(cas)
    assert log == [TAKEN.format(RELEASES, 2)] and macros["_lat_failed"] == "0"

    generated, looked, end = casl_harness.follow(program(), "_lat_look", symbols(_lat_failed=macros["_lat_failed"], _lat_missing=""), _Look(cas))
    if sheets:
        assert looked == ["NOTE: Load-Accelerator-Tables: ACCELERATOR_RELEASES, LLM_FACT_SHEET, EMBEDDING_FACT_SHEET and DECISION_FACT_SHEET are "
                          "in the CAS library Public."]
    else:
        assert looked == ["ERROR: Load-Accelerator-Tables: not in the CAS library Public at the end of the run: LLM_FACT_SHEET "
                          "EMBEDDING_FACT_SHEET DECISION_FACT_SHEET. The lines above say why."]
    assert end["_lat_failed"] == ("0" if sheets else "1") and end["_lat_ready"] == "1"
    # the one statement that sets the condition code reads these two, and nothing else of the program sets it
    code = re.sub(r"/\*.*?\*/", "", program(), flags=re.DOTALL)
    assert code.count("%if &_lat_ready. NE 1 or &_lat_failed. EQ 1 %then %do;\n    %let SYSCC = 1012;\n%end;\n") == 1
    assert len(re.findall(r"syscc\s*=", code, flags=re.IGNORECASE)) == 1


def test_the_two_causes_of_a_saved_file_that_is_not_replaced_have_a_line_each():
    """A file that can not be loaded or lacks columns, and a file that was not looked at: two outcomes of the CASL,
    two ERROR lines, and neither says that nothing was loaded - the upload ran in front of both."""
    code = re.sub(r"/\*.*?\*/", "", program(), flags=re.DOTALL)
    errors = re.findall(r"%if &_lat_outcome\. EQ (\w+) %then %do;\s*%put (ERROR: [^;]*);\s*%let _lat_failed = 1;\s*%return;", code)

    assert [outcome for outcome, _ in errors] == ["foreign", "foreignfile", "savedtaken"]
    assert code.count('outcome = "savedtaken";') == 1 and code.count('lacking = "foreignfile";') == 1
    # the outcome of a name that is taken is set where CAS did not answer 0, with what it answered
    assert ('                    else do;\n'
            '                        \n'
            '                        outcome = "savedtaken";\n'
            '                        call symputx("_lat_helper", helper.exists, "L");\n'
            '                    end;\n') in code
    assert "%let _lat_helper = unknown;" in code and " _lat_helper;" in code.split("%macro _lat_release_table;")[1].split("\n")[1]
    lines = dict(errors)
    assert "could not be loaded or lacks columns of a release table" in lines["foreignfile"] and "was not looked at" in lines["savedtaken"]
    for line in (lines["foreignfile"], lines["savedtaken"]):
        assert "nothing was loaded" not in line
        assert "the upload of ACCELERATOR_RELEASES.csv was dropped again and no table &caslib..&releaseTable. was loaded" in line
    # the line of a name that is taken says what the WARNING for <table>_NEW says of the answer and of the remedy
    warning = re.search(r"%put (WARNING: Load-Accelerator-Tables: CAS did not answer [^;]*);", code).group(1)
    for shared in ("tableExists said &_lat_{0}., and 0 is no table", "where a table of that name is there, rename or drop it and run the job again"):
        assert shared.format("taken") in warning and shared.format("helper") in lines["savedtaken"]


def test_a_saved_file_that_can_not_be_loaded_is_left_as_it_is():
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.file("Public", "LLM_FACT_SHEET.sashdat", SHEET_COLUMNS, rows=30, of="the fact sheet", loadable=False)
    log, macros = release(cas, releaseTable="LLM_FACT_SHEET")

    left_alone(cas, log, macros, "LLM_FACT_SHEET", FOREIGN_FILE.format("LLM_FACT_SHEET"))
    assert saved(cas, "LLM_FACT_SHEET")["of"] == "the fact sheet"
    # no column is asked for of a table that was not loaded, and no table of the site is dropped for it
    assert not [action for action, _ in cas.calls if action == "table.columnInfo"]
    assert [parameters["name"] for action, parameters in cas.calls if action == "table.dropTable"] == ["LLM_FACT_SHEET_NEW"]


@pytest.mark.parametrize("old", [True, False], ids=["without builder_part", "with every column"])
def test_a_release_table_that_is_only_its_saved_file_is_loaded_again(old):
    """The release table after a restart of the CAS server: the job is how a site gets it back into memory."""
    cas = casl_harness.FakeCAS(upload_rows=755)
    cas.file("Public", RELEASES + ".sashdat", [name for name in columns() if not (old and name == "builder_part")], rows=453, of="the table of before")
    log, macros = release(cas)

    assert log == [LOADED] and macros["_lat_failed"] == "0" and cas.errors == []
    assert loaded(cas) == {"columns": columns(), "rows": 755, "of": "upload"} and saved(cas)["of"] == "upload"
    assert cas.session == {} and list(cas.files) == [("public", RELEASES + ".sashdat")]


def test_a_library_whose_files_can_not_be_listed_goes_on_as_without_a_file():
    """What is not known is not guessed: CAS has written its ERROR line for the listing, and the run goes on as
    it does where the library has no such file - as it did before the files were asked for."""
    cas = casl_harness.FakeCAS(upload_rows=755, refuse={"table.fileInfo"})
    log, macros = release(cas)

    assert log == [LOADED] and cas.errors == ["ERROR: table.fileInfo: the files of the CAS library could not be listed"]
    assert loaded(cas)["of"] == "upload"


def test_an_upload_that_does_not_hold_every_row_asks_for_no_file_and_replaces_nothing():
    cas = casl_harness.FakeCAS(upload_rows=700)
    cas.file("Public", RELEASES + ".sashdat", columns(), rows=453, of="the table of before")
    generated, log, asked, macros = follow("_lat_release_table", "200", 1, cas, rows=755)

    assert log == ["WARNING: Load-Accelerator-Tables: the upload of ACCELERATOR_RELEASES.csv holds 700 rows where the file has 755, "
                   "the table Public.ACCELERATOR_RELEASES was left as it is."]
    assert saved(cas)["of"] == "the table of before" and cas.session == {} and loaded(cas) is None
    assert not [action for action, _ in cas.calls if action in ("table.fileInfo", "table.loadTable", "table.promote", "table.save")]


def test_the_statements_that_look_at_the_saved_file():
    """The CASL as text: the listing is the one of the ingestion job of a RAG setup, which has run on SAS Viya, and
    the saved file is loaded without PROMOTE under a name of the session."""
    code = re.sub(r"/\*.*?\*/", "", program(), flags=re.DOTALL)
    job = (REPOSITORY / "SAS-Viya-Integrations" / "RAG-Ingestion" / "Ingest-Documents.sas").read_text(encoding="utf-8").replace("\r\n", "\n")

    assert 'table.fileInfo result = files status = listed / caslib = "&caslib.";' in code
    assert 'table.fileInfo result = listing status = listed / caslib = "&caslib.";' in job
    for ours, theirs in (('if savedEntry.Name == "&releaseTable..sashdat" then saved = savedEntry.Name;',
                          'if entry.Name == "&table..sashdat" then'),
                         ('else if saved == "" and upcase(savedEntry.Name) == upcase("&releaseTable..sashdat") then saved = savedEntry.Name;',
                          'else if saved == "" and upcase(entry.Name) == upcase("&table..sashdat") then')):
        assert ours in code and theirs in job
    assert ('table.tableExists result = helper / caslib = "&caslib." name = "_LAT_SAVED";\n'
            '                    if helper.exists == 0 then do;\n'
            '                        table.loadTable result = ignored status = opened / caslib = "&caslib." path = saved\n'
            '                            casOut = {caslib = "&caslib.", name = "_LAT_SAVED", replace = true};') in code
    # the name: asked for, loaded, looked at, dropped - and named in the ERROR line of a file that could not be loaded or
    # lacks columns, and twice in the one of a file that was not looked at
    assert code.count("_LAT_SAVED") == 7 and "promote = true" not in code
    # the table of the session is dropped by the flag of the load, not by its name
    assert "if fromFile == 1 then do;" in code and code.count("fromFile = 1;") == 1 and code.count("fromFile = 0;") == 1
    # the header and the parameter say which table is looked at
    header = " ".join(program().split("*/")[0].replace("\n *", " ").split())
    assert ("a table of that name that lacks a column of one is left as it is - the table in memory, or, where none is in memory, the "
            "file a table of that name was saved to in the CAS library") in header
