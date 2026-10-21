/******************************************************************************
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Load Accelerator Tables: the release table and the three fact sheets, from
 * the Data folder of the content root into CAS.
 *
 * The program of the job definition "Load-Accelerator-Tables" in the folder
 * Data of the content folder. After an import of the package it gives the
 * two reports the tables they bind without the command line:
 *
 *   ACCELERATOR_RELEASES   from Data/ACCELERATOR_RELEASES.csv - the table the
 *                          Prompt Builder report is assigned
 *   LLM_FACT_SHEET, EMBEDDING_FACT_SHEET, DECISION_FACT_SHEET
 *                          from the three fact sheet files of Data
 *
 * THE FACT SHEETS are loaded by Load-Fact-Sheets.sas, which this program
 * includes from the folder Logging and Monitoring of the content root: one
 * program loads the sheets, whether it is run on its own or from here. It is
 * handed the folder and the CAS library of this job.
 *
 * THE RELEASE TABLE is loaded here. Its columns, their order and their types
 * are the ones mdb load-releases leaves in CAS: the text columns VARCHAR, the
 * three numbers DOUBLE. CAS reads the file itself, so no value passes a SAS
 * variable and none is cut to its length. The upload names the name and the
 * type of every column and no length. Measured on 2026-10-12 against the
 * table mdb load-releases loaded from the same changelog: the same eleven
 * columns, types and lengths, and one difference - the width for display
 * that CAS keeps beside the length (the formatted length) is 16 here for the
 * four text columns whose longest value is shorter than that (version,
 * release_date, change_type, builder_part), and 6, 10, 8 and 8 there, the
 * lengths of those values. A width of 16 cuts no value of these columns.
 *
 * A TABLE THAT EXISTS is replaced only by one that loaded completely: the
 * file is uploaded beside the table as <table>_NEW, a table of the CAS
 * session, its rows are counted against the lines of the file, and only then
 * is the table dropped, the upload promoted under its name and saved. A file
 * that is not there, that has other columns or that CAS did not take leaves
 * the table as it is, with a WARNING. So does a folder that is not there:
 * the Folders service is asked for the folder before a fileref is assigned
 * to a file of it. And so does a table that has the name <table>_NEW
 * before the upload: it is no table of the job, so nothing is uploaded under
 * that name and no table of that name is dropped. Load-Fact-Sheets.sas
 * replaces each
 * fact sheet table the same way: the upload whose rows were counted is
 * promoted under the name of the table and saved, and the sheet is not
 * loaded a second time.
 *
 * THE JOB RUNS AS THE USER WHO STARTS IT where its compute context does: the
 * job definition names the context in its parameter _contextName. In a
 * context that runs under a service account the SAS session is that account,
 * and the log says so in one NOTE: the user of the session (SYSUSERID) is
 * then another one than the user SAS Job Execution started the session for
 * (SYS_COMPUTE_SESSION_OWNER) - a domain written behind @ or in front of a
 * backslash is no difference. What such a session may read depends on how
 * the system configures its context. Measured on 2026-10-12 in the SAS Job
 * Execution compute context of the test system: the log held the NOTE, and
 * the job read the folder Data and loaded the release table, as it did in
 * the SAS Studio compute context.
 *
 * THE JOB FAILS when one of the four tables is not in the CAS library at its
 * end, or when a parameter is refused: it then writes an ERROR line and sets
 * the condition code of the session to 1012. A table that was left as it is
 * and is there is a WARNING. So a run whose only message on the way is the
 * WARNING of a file or a folder that is not there still fails where the table
 * of that file is not in the CAS library: the job is there to load the four.
 * An error of SAS or of CAS on the way is an
 * error of the job as well; it stops no table behind it from being loaded.
 * Except where the program took care of it: an upload whose promote CAS
 * refused - a second run of the job at the same time had loaded a table of
 * that name, say - leaves the table that is in memory, or loads the table
 * again from its saved file, and a table that holds the upload and could not
 * be saved stays in memory. Each is a WARNING, and the ERROR line CAS wrote
 * for it no longer fails the job: the condition code is put back to 4, so
 * that SAS Job Execution shows the page of the job, not its page "Job Error".
 *
 * THE PAGE. Started from its address in SAS Job Execution the job writes a
 * short page to _webout: each of the four tables with its rows and whether
 * this run loaded it, left it as it was or did not find it in the CAS library
 * at the end - and of a table the upload could not take the place of, that it
 * was left or loaded again from its saved file. The log keeps every message. Run in SAS Studio, where a session
 * has no _webout, the program writes no page.
 *
 * Parameters of the job definition:
 *   _action       execute
 *   _output_type  html - the job writes the page itself, to _webout
 *   _contextName  the compute context the job runs in: one that runs under the
 *                 identity of the person who starts the job
 *   contentRoot   the content folder (default /SAS Agentic AI Accelerator)
 *   caslib        the CAS library of the tables (default Public)
 *   releaseTable  the name of the release table (default ACCELERATOR_RELEASES);
 *                 a table of that name that lacks a column of one is left as it
 *                 is - the table in memory, or, where none is in memory, the
 *                 file a table of that name was saved to in the CAS library
 *
 * lat is short for Load Accelerator Tables
 ******************************************************************************/

/* A parameter is taken from the lines below only where the job definition or
   the request did not set it - the pattern of Ingest-Documents.sas */
%macro _lat_default(name, value);
    %if not %symexist(&name.) %then %do;
        %global &name.;
        %let &name. = &value.;
    %end;
%mend _lat_default;

%_lat_default(contentRoot, /SAS Agentic AI Accelerator);
%_lat_default(caslib, Public);
%_lat_default(releaseTable, ACCELERATOR_RELEASES);
%sysmacdelete _lat_default;

/* An error for one table must not leave a batch session checking syntax
   only, in which no further step runs and the tables behind it are not
   loaded. The option that decides it is switched off while the program runs
   and put back to what it was - as the ingestion job of a RAG setup does it
   while it publishes its run history. */
%let _lat_syntaxcheck = %sysfunc(getoption(syntaxcheck));
options nosyntaxcheck;

/* The columns of the release table in the order of the file, each with the
   type it has in CAS, as name:type */
%let _lat_vars = version:varchar release_date:varchar is_current:double release_rank:double item_rank:double section:varchar change_type:varchar component:varchar summary:varchar detail:varchar builder_part:varchar;

/* 1 until a parameter is refused. A refusal writes its ERROR line and sets
   this to 0: nothing is read and nothing is written behind it */
%let _lat_ready = 1;
/* 1 once the program has written an ERROR line. The last statement reads it */
%let _lat_failed = 0;
/* the tables that are not in the CAS library at the end of the run */
%let _lat_missing = ;
/* <table>:<outcome> of each table whose upload was to take its place - the release table here, the fact sheets in
   Load-Fact-Sheets.sas, which adds to _lfs_outcomes where it is there. The page names a table that was left or
   loaded again from its file (kept, restored) and one that was not saved (save) */
%let _lat_outcomes = ;
%let _lfs_outcomes = ;

/* The parameters stand in statements below, so each is looked at as text
   first: a quotation mark, a percent sign, an ampersand or a semicolon in one
   of them would be read as code. A parameter that is refused is set to its
   default, so that no statement below resolves it as it was. */
data _null_;
    length _lat_value $ 1024;
    _lat_value = symget('contentRoot');
    if lengthn(_lat_value) = 0 or findc(_lat_value, '"''%&;') > 0 or substr(_lat_value, 1, 1) ne '/' then do;
        putLog 'ERROR: Load-Accelerator-Tables: the parameter contentRoot is no folder path, nothing was read or written.';
        call symputx('contentRoot', '/SAS Agentic AI Accelerator');
        call symputx('_lat_ready', '0');
    end;
    _lat_value = symget('caslib');
    if lengthn(_lat_value) = 0 or findc(_lat_value, '"''%&;') > 0 then do;
        putLog 'ERROR: Load-Accelerator-Tables: the parameter caslib is no name of a CAS library, nothing was read or written.';
        call symputx('caslib', 'Public');
        call symputx('_lat_ready', '0');
    end;
    _lat_value = symget('releaseTable');
    if lengthn(_lat_value) = 0 or lengthn(_lat_value) > 28 or nvalid(strip(_lat_value), 'v7') = 0 then do;
        putLog 'ERROR: Load-Accelerator-Tables: the parameter releaseTable is no SAS name of 28 characters at most, nothing was read or written.';
        call symputx('releaseTable', 'ACCELERATOR_RELEASES');
        call symputx('_lat_ready', '0');
    end;
    /* Whose session this is. SAS Job Execution names the user a session was
       started for; a session without that name - the program run in SAS
       Studio - is not asked. The two users are compared as the persons they
       name - a domain behind @ or in front of a backslash is dropped, the
       case does not count - and neither is written to the log. */
    if symexist('SYS_COMPUTE_SESSION_OWNER') then do;
        _lat_value = lowcase(scan(scan(strip(symget('SYS_COMPUTE_SESSION_OWNER')), -1, '\'), 1, '@'));
        _lat_user = lowcase(scan(scan(strip(symget('SYSUSERID')), -1, '\'), 1, '@'));
        if lengthn(_lat_value) > 0 and strip(_lat_value) ne strip(_lat_user) then do;
            putLog 'NOTE: Load-Accelerator-Tables: this SAS session does not run as the user who started the job, but under the account of its compute context. A file that is reported as not found below may be one that account may not read.';
            putLog 'NOTE- To run the job as yourself, start it with the parameter _contextName and the name of a compute context that runs as you.';
        end;
    end;
run;

* Check if a CAS session exists;
* The option SESSREF names the session the CAS actions run in and keeps the name of a session that was ended, so SESSFOUND is asked whether that session is connected. A session that is connected is used as it is, and only a session this program started is ended at its end;
* Where the session the option SESSREF names is not connected but _latSess is, the option is set to _latSess, which is then used and left as it is. A second cas _latSess only makes SAS warn that the session exists and leaves the option on the session that was ended, and the end of the program would end a session it did not start;
%if &_lat_ready. EQ 1 and %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 and %sysfunc(sessfound(_latSess)) EQ 1 %then %do;
    options sessref=_latSess;
%end;
%if &_lat_ready. EQ 1 and %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    %let _lat_clean_cas_session = 1;
    cas _latSess;
%end;
%else %do;
    %let _lat_clean_cas_session = 0;
%end;

/* ---- A folder that is not there ------------------------------------------------
   A FILENAME statement of the access method FILESRVC writes two ERROR lines
   for a folder of SAS Content that is not there (measured on 2026-10-13),
   where this program means a WARNING. So the Folders service is asked for the
   folder first, with the request Start Here asks for the content root with,
   and the statement is left out where the answer is 404. Every other answer
   - the folder is there, the session may not ask, there is no answer - leaves
   the statement to say what it says. The address is made in a DATA step from
   the text of the parameter, so that no statement resolves the path on the
   way, and SYS_PROCHTTP_STATUS_CODE is emptied first: it keeps the answer of
   an earlier request where PROC HTTP gets none. Load-Fact-Sheets.sas asks
   the same way for its folder. */
%macro _lat_ask_folder(_lat_below);
    %local _lat_ask;
    filename _latask temp;
    data _null_;
        length _lat_ask $ 8192;
        _lat_ask = cats(getoption('servicesbaseurl'), '/folders/folders/@item?path=', urlencode(cats(symget('contentRoot'), "&_lat_below.")));
        call symputx('_lat_ask', _lat_ask, 'L');
        call symputx('SYS_PROCHTTP_STATUS_CODE', ' ', 'G');
    run;
    proc http method = "GET" url = "%superq(_lat_ask)" oauth_bearer = sas_services out = _latask;
    run;
    filename _latask clear;
%mend _lat_ask_folder;

/* ---- Which table this run loads ---------------------------------------------
   The page the job writes for SAS Job Execution says of each of the four
   tables whether this run loaded it. A table an upload takes the place of,
   and a table that is loaded where there was none, is a table CAS made in
   this run, with a time of its own at which it was made. So the rows of each
   table and that time are read before the tables are loaded and again at the
   end: a table with another time, or one that was not there before, is a
   table this run loaded. CAS is asked for the tables of the CAS library, so a
   table that is not there is no ERROR line. _lat_before and _lat_after hold
   rows/time for each of the four, in their order: none for a table that is
   not there, unknown where CAS did not list the tables. */
%let _lat_before = ;
%let _lat_after = ;
%macro _lat_tables(_lat_when);
    %local _lat_i _lat_name _lat_count _lat_made _lat_list;
    %if &_lat_ready. NE 1 %then %return;
    %let _lat_list = ;
    %do _lat_i = 1 %to 4;
        %let _lat_name = %scan(&releaseTable. LLM_FACT_SHEET EMBEDDING_FACT_SHEET DECISION_FACT_SHEET, &_lat_i., %str( ));
        /* unknown until the step below answers: a step that halts sets nothing */
        %let _lat_count = unknown;
        %let _lat_made = ;
        proc cas;
            table.tableInfo result = listing status = listed / caslib = "&caslib.";
            if listed.severity == 0 then do;
                call symputx("_lat_count", "none", "L");
                if exists(listing, "TableInfo") then do;
                    do entry over listing.TableInfo;
                        if upcase(entry.Name) == upcase("&_lat_name.") then do;
                            call symputx("_lat_count", entry.Rows, "L");
                            call symputx("_lat_made", entry.CreateTime, "L");
                        end;
                    end;
                end;
            end;
        quit;
        %let _lat_list = &_lat_list. &_lat_count./&_lat_made.;
    %end;
    %if &_lat_when. EQ before %then %do;
        %let _lat_before = &_lat_list.;
    %end;
    %else %do;
        %let _lat_after = &_lat_list.;
    %end;
%mend _lat_tables;
%_lat_tables(before);

/* ---- The release table -------------------------------------------------------
   The file is copied from SAS Content to WORK as it is, its lines are counted
   - mdb writes one row per line, no value holds a line break - and its first
   line is compared with the columns above: with other columns the upload
   would put values under the wrong names. CAS then reads the file with the
   options the ingestion job of a RAG setup uploads its run history with. */
%macro _lat_release_table;
    %local _lat_dropped _lat_handled _lat_cc _lat_file _lat_copy _lat_rows _lat_header _lat_outcome _lat_staged _lat_i _lat_n _lat_v _lat_there _lat_taken _lat_helper;
    %if &_lat_ready. NE 1 %then %return;
    %let _lat_file = %sysfunc(pathname(work))/accelerator_releases.csv;
    %let _lat_rows = -1;
    %let _lat_header = 0;

    /* the fileref is assigned only where the Folders service did not say that the folder is not there */
    %let _lat_there = 0;
    %_lat_ask_folder(/Data);
    %if "&SYS_PROCHTTP_STATUS_CODE." NE "404" %then %do;
        filename _latsrc filesrvc folderPath="&contentRoot./Data" filename="ACCELERATOR_RELEASES.csv" recfm=n lrecl=32767;
        %let _lat_there = %sysfunc(fexist(_latsrc));
        %if &_lat_there. NE 1 %then %do;
            filename _latsrc clear;
        %end;
    %end;
    %if &_lat_there. NE 1 %then %do;
        %put WARNING: Load-Accelerator-Tables: the file ACCELERATOR_RELEASES.csv was not found in the folder &contentRoot./Data, the table &caslib..&releaseTable. was left as it is.;
        %return;
    %end;
    filename _latdst "&_lat_file." recfm=n lrecl=32767;
    %let _lat_copy = %sysfunc(fcopy(_latsrc, _latdst));
    filename _latsrc clear;
    filename _latdst clear;
    %if &_lat_copy. NE 0 %then %do;
        %put WARNING: Load-Accelerator-Tables: the file ACCELERATOR_RELEASES.csv of the folder &contentRoot./Data could not be copied to the SAS session, the table &caslib..&releaseTable. was left as it is.;
        %return;
    %end;

    data _null_;
        infile "&_lat_file." lrecl=1048576 truncover end=_lat_last;
        length _lat_expected _lat_first $ 400;
        input _lat_first $char400.;
        if _n_ = 1 then do;
            do _lat_i = 1 to countw("&_lat_vars.", ' ');
                _lat_expected = catx(',', _lat_expected, scan(scan("&_lat_vars.", _lat_i, ' '), 1, ':'));
            end;
            /* mdb writes every value in quotation marks, the names of the first line as well */
            call symputx('_lat_header', ifn(compress(_lat_first, '"' || '0D'x) = _lat_expected, 1, 0), 'L');
        end;
        if _lat_last then call symputx('_lat_rows', _n_ - 1, 'L');
    run;
    %if &_lat_header. NE 1 %then %do;
        %put WARNING: Load-Accelerator-Tables: the first line of ACCELERATOR_RELEASES.csv does not name the columns this program loads, the table &caslib..&releaseTable. was left as it is. The file and this job definition are of different releases.;
        %return;
    %end;
    %if &_lat_rows. LE 0 %then %do;
        %put WARNING: Load-Accelerator-Tables: the file ACCELERATOR_RELEASES.csv holds no rows, the table &caslib..&releaseTable. was left as it is.;
        %return;
    %end;

    /* The file is uploaded as <table>_NEW, a table of this session, which the step below drops again or promotes.
       A table of that name that is there before the upload is no table of this run - a table of the site can have
       the name -, so CAS is asked first. Only where it answers that there is none is the file uploaded under the
       name, and only then is a table of the name dropped or promoted: the one this run loaded. A macro variable that
       a PROC CAS step sets starts at the value of a question CAS did not answer: a step that halts - its session
       ended, say - sets none */
    %let _lat_taken = refused;
    proc cas;
        table.tableExists result = taken status = takenAsked / caslib = "&caslib." name = "&releaseTable._NEW";
        if takenAsked.severity != 0 then call symputx("_lat_taken", "refused", "L");
        else call symputx("_lat_taken", taken.exists, "L");
    quit;
    %if &_lat_taken. EQ refused %then %do;
        %put ERROR: Load-Accelerator-Tables: CAS did not say whether there is a table &caslib..&releaseTable._NEW, the name the file is uploaded under first. Nothing was uploaded, the table &caslib..&releaseTable. was left as it is.;
        %let _lat_failed = 1;
        %return;
    %end;
    %if &_lat_taken. NE 0 %then %do;
        %put WARNING: Load-Accelerator-Tables: CAS did not answer that there is no table &caslib..&releaseTable._NEW - tableExists said &_lat_taken., and 0 is no table. The file is uploaded under that name first, as a table of this session, and the job drops and replaces no table it did not load: where a table of that name is there, rename or drop it and run the job again. Nothing was uploaded, the table &caslib..&releaseTable. was left as it is.;
        %return;
    %end;

    %let _lat_outcome = unknown;
    %let _lat_staged = -1;
    %let _lat_helper = unknown;
    %let _lat_dropped = unknown;
    %let _lat_handled = 0;
    /* the condition code before the step: a refused promote or save that the step took care of puts it back below */
    %let _lat_cc = &syscc.;
    %let _lat_n = %sysfunc(countw(&_lat_vars., %str( )));
    proc cas;
        outcome = "upload";
        staged = -1;
        handled = 0;
        upload path = "&_lat_file."
            casOut = {caslib = "&caslib.", name = "&releaseTable._NEW", replace = true}
            importOptions = {fileType = "csv", allowEmbeddedNewLines = true,
                             locale = "EN-us", vars = {
                %do _lat_i = 1 %to &_lat_n.;
                    %let _lat_v = %scan(&_lat_vars., &_lat_i., %str( ));
                    %if &_lat_i. GT 1 %then %str(,);
                    {name = "%scan(&_lat_v., 1, %str(:))", type = "%scan(&_lat_v., 2, %str(:))"}
                %end;
            }};
        /* the rows the upload holds, against the lines of the file. An action of CAS the job goes by that CAS
           refused - here and below - is no answer: the outcome is the refusal, and the upload takes the place of no table */
        table.tableExists result = found status = foundAsked / caslib = "&caslib." name = "&releaseTable._NEW";
        if foundAsked.severity != 0 then outcome = "refused";
        else if found.exists != 0 then do;
            staged = 0;
            outcome = "rows";
            table.tableInfo result = listing status = counted / caslib = "&caslib.";
            if counted.severity != 0 then outcome = "refused";
            if exists(listing, "TableInfo") then do;
                do entry over listing.TableInfo;
                    if upcase(entry.Name) == upcase("&releaseTable._NEW") then staged = entry.Rows;
                end;
            end;
        end;
        /* A table of that name that exists is replaced only where it is a release table, with the columns of one:
           the parameter releaseTable can name any table of the CAS library, and another table of the site - a fact
           sheet, the log table - is left as it is. builder_part is not asked for: the release table of 2.2.0 and
           earlier does not have it (measured on 2026-10-12: Public.ACCELERATOR_RELEASES of the test system had the
           ten other columns), and the job replaces it */
        if staged == &_lat_rows. then do;
            /* What is there under that name: the table in memory, or - where none is - the file a table of that
               name was saved to, which the save below writes over. tableExists sees a table in memory only, and a
               restart of the CAS server leaves a saved table as its file. The files of the CAS library are listed
               as the ingestion job of a RAG setup lists them: the whole library, so that a file that is not there
               is no error, and the exact name before the same name in another case. The file is loaded as a table
               of this session, its columns are read, and it is dropped again - under the name _LAT_SAVED, and only
               where CAS answers that no table of that name is there: a table of the site can have it, and the
               table this step drops is then the one it loaded. A listing that CAS refused says nothing about such a
               file, so the upload then takes the place of no table and nothing is saved over a file */
            look = "";
            lacking = "foreign";
            fromFile = 0;
            table.tableExists result = present status = presentAsked / caslib = "&caslib." name = "&releaseTable.";
            if presentAsked.severity != 0 then outcome = "refused";
            else if present.exists != 0 then look = "&releaseTable.";
            else do;
                saved = "";
                table.fileInfo result = files status = listed / caslib = "&caslib.";
                if listed.severity != 0 then outcome = "unlisted";
                if listed.severity == 0 then do;
                    if exists(files, "FileInfo") then do;
                        do savedEntry over files.FileInfo;
                            if savedEntry.Name == "&releaseTable..sashdat" then saved = savedEntry.Name;
                            else if saved == "" and upcase(savedEntry.Name) == upcase("&releaseTable..sashdat") then saved = savedEntry.Name;
                        end;
                    end;
                end;
                if saved != "" then do;
                    /* a saved file that can not be loaded is no release table either: nothing writes over it */
                    lacking = "foreignfile";
                    outcome = lacking;
                    table.tableExists result = helper status = helperAsked / caslib = "&caslib." name = "_LAT_SAVED";
                    if helperAsked.severity != 0 then outcome = "refused";
                    else if helper.exists == 0 then do;
                        table.loadTable result = ignored status = opened / caslib = "&caslib." path = saved
                            casOut = {caslib = "&caslib.", name = "_LAT_SAVED", replace = true};
                        if opened.severity == 0 then do;
                            outcome = "rows";
                            look = "_LAT_SAVED";
                            fromFile = 1;
                        end;
                    end;
                    else do;
                        /* a table of that name is there, so the file was not looked at: an outcome of its own,
                           with what CAS answered, as the WARNING for <table>_NEW has it */
                        outcome = "savedtaken";
                        call symputx("_lat_helper", helper.exists, "L");
                    end;
                end;
            end;
            if look != "" then do;
                /* a listing of the columns that CAS refused names none, so every column below is lacking: the outcome is the refusal */
                table.columnInfo result = described status = columnsListed / table = {caslib = "&caslib.", name = look};
                if columnsListed.severity != 0 then lacking = "refused";
                have = " ";
                if exists(described, "ColumnInfo") then do;
                    do column over described.ColumnInfo;
                        have = have || upcase(column.Column) || " ";
                    end;
                end;
                %do _lat_i = 1 %to &_lat_n.;
                    %let _lat_v = %upcase(%scan(%scan(&_lat_vars., &_lat_i., %str( )), 1, %str(:)));
                    %if &_lat_v. NE BUILDER_PART %then %do;
                    if index(have, " &_lat_v. ") == 0 then outcome = lacking;
                    %end;
                %end;
            end;
            /* only the table this step loaded from the file is dropped: a table of the site can have that name */
            if fromFile == 1 then do;
                table.dropTable / caslib = "&caslib." name = "_LAT_SAVED" quiet = true;
            end;
        end;
        /* The swap of Load-Fact-Sheets.sas: what the drop did is kept for the WARNING of a table that is left, and a
           refused promote drops the upload and leaves a table of the name that is in memory - one a second run of the
           job at the same time loaded, say - or loads the table again from the file it was saved to */
        if staged == &_lat_rows. and outcome == "rows" then do;
            outcome = "replace";
            there = 1;
            table.tableExists result = held status = heldAsked / caslib = "&caslib." name = "&releaseTable.";
            if heldAsked.severity == 0 then there = held.exists;
            table.dropTable status = dropped / caslib = "&caslib." name = "&releaseTable." quiet = true;
            if dropped.severity != 0 then drop = "refused";
            else if there == 0 then drop = "none";
            else drop = "dropped";
            table.promote status = promoted / caslib = "&caslib." name = "&releaseTable._NEW"
                targetLib = "&caslib." target = "&releaseTable.";
            if promoted.severity == 0 then do;
                outcome = "save";
                table.save status = stored / table = {caslib = "&caslib.", name = "&releaseTable."}
                    caslib = "&caslib." name = "&releaseTable." replace = true;
                if stored.severity == 0 then outcome = "loaded";
                else handled = 1;
            end;
            else do;
                table.dropTable / caslib = "&caslib." name = "&releaseTable._NEW" quiet = true;
                table.tableExists result = still status = stillAsked / caslib = "&caslib." name = "&releaseTable.";
                if stillAsked.severity != 0 then outcome = "replace";
                else if still.exists != 0 then do;
                    outcome = "kept";
                    handled = 1;
                end;
                else do;
                    table.loadTable status = restored / caslib = "&caslib." path = "&releaseTable..sashdat"
                        casOut = {caslib = "&caslib.", name = "&releaseTable.", promote = true};
                    if restored.severity == 0 then do;
                        outcome = "restored";
                        handled = 1;
                    end;
                end;
            end;
            call symputx("_lat_dropped", drop, "L");
        end;
        else do;
            /* an upload that does not hold every row takes the place of no table */
            table.dropTable / caslib = "&caslib." name = "&releaseTable._NEW" quiet = true;
        end;
        /* "L": local to this macro - without a scope CASL writes to the global symbol table */
        call symputx("_lat_outcome", outcome, "L");
        call symputx("_lat_staged", staged, "L");
        call symputx("_lat_handled", handled, "L");
    quit;

    /* A refused promote or save is an ERROR line of CAS, and the condition code of the session is then 1012: SAS Job
       Execution shows its page "Job Error" in place of the page of the job (measured on 2026-10-20, two runs of the
       job started 3 seconds apart). Where the step took care of it - the table that is in memory was left, the table
       was loaded again from its file, or it holds the upload and was not saved - the condition code is put back to
       what it was before the step, and to 4 at least, as a WARNING leaves it; any other outcome leaves it as CAS did */
    %if &_lat_handled. EQ 1 %then %do;
        %let syscc = %sysfunc(max(&_lat_cc., 4));
    %end;
    %if %symexist(_lat_outcomes) %then %do;
        %let _lat_outcomes = &_lat_outcomes. &releaseTable.:&_lat_outcome.;
    %end;

    %if &_lat_outcome. EQ foreign %then %do;
        %put ERROR: Load-Accelerator-Tables: the table &caslib..&releaseTable. is no release table - it lacks columns of one - and was left as it is: the parameter releaseTable names another table.;
        %let _lat_failed = 1;
        %return;
    %end;
    %if &_lat_outcome. EQ foreignfile %then %do;
        %put ERROR: Load-Accelerator-Tables: no table &caslib..&releaseTable. is in memory, and the file a table of that name was saved to in the CAS library &caslib. could not be loaded or lacks columns of a release table. The job loads that file as a table _LAT_SAVED of its session to read its columns, and only where no table of that name is in the CAS library. The file was left as it is, the upload of ACCELERATOR_RELEASES.csv was dropped again and no table &caslib..&releaseTable. was loaded: the parameter releaseTable names another table, or that file is to be removed first.;
        %let _lat_failed = 1;
        %return;
    %end;
    %if &_lat_outcome. EQ unlisted %then %do;
        %put ERROR: Load-Accelerator-Tables: no table &caslib..&releaseTable. is in memory, and CAS refused to list the files of the CAS library &caslib. (the ERROR lines above), so it is not known whether a file a table of that name was saved to is there. The upload of ACCELERATOR_RELEASES.csv was dropped again and no table &caslib..&releaseTable. was loaded.;
        %let _lat_failed = 1;
        %return;
    %end;
    %if &_lat_outcome. EQ refused %then %do;
        %put ERROR: Load-Accelerator-Tables: CAS refused an action the job goes by to decide whether the upload takes the place of the table &caslib..&releaseTable. - the ERROR lines above. The upload of ACCELERATOR_RELEASES.csv was dropped again and no table &caslib..&releaseTable. was loaded.;
        %let _lat_failed = 1;
        %return;
    %end;
    %if &_lat_outcome. EQ savedtaken %then %do;
        %put ERROR: Load-Accelerator-Tables: no table &caslib..&releaseTable. is in memory, and the file a table of that name was saved to in the CAS library &caslib. was not looked at. The job loads that file as a table _LAT_SAVED of its session to read its columns, and CAS did not answer that there is no table &caslib.._LAT_SAVED - tableExists said &_lat_helper., and 0 is no table. The job loads over and drops no table it did not load: where a table of that name is there, rename or drop it and run the job again. The file was left as it is, the upload of ACCELERATOR_RELEASES.csv was dropped again and no table &caslib..&releaseTable. was loaded.;
        %let _lat_failed = 1;
        %return;
    %end;
    %if &_lat_outcome. EQ loaded %then
        %put NOTE: Load-Accelerator-Tables: the release table &caslib..&releaseTable. was loaded with &_lat_rows. rows, promoted and saved.;
    %else %if &_lat_outcome. EQ upload %then
        %put WARNING: Load-Accelerator-Tables: the &_lat_rows. rows of ACCELERATOR_RELEASES.csv could not be uploaded to the CAS library &caslib. (messages above), the table &caslib..&releaseTable. was left as it is.;
    %else %if &_lat_outcome. EQ rows %then
        %put WARNING: Load-Accelerator-Tables: the upload of ACCELERATOR_RELEASES.csv holds &_lat_staged. rows where the file has &_lat_rows., the table &caslib..&releaseTable. was left as it is.;
    %else %if &_lat_outcome. EQ kept and &_lat_dropped. EQ refused %then
        %put WARNING: Load-Accelerator-Tables: the upload could not take the place of the table &caslib..&releaseTable. (messages above). The table of that name that is in memory was left as it is: it could not be dropped.;
    %else %if &_lat_outcome. EQ kept and &_lat_dropped. EQ none %then
        %put WARNING: Load-Accelerator-Tables: the upload could not take the place of the table &caslib..&releaseTable. (messages above). The table of that name that is in memory was left as it is: none was in memory when this run went to drop it, and one was loaded before the promote - by a second run of the job at the same time, say.;
    %else %if &_lat_outcome. EQ kept %then
        %put WARNING: Load-Accelerator-Tables: the upload could not take the place of the table &caslib..&releaseTable. (messages above). The table of that name that is in memory was left as it is: this run had dropped the table, and one was loaded again before the promote - by a second run of the job at the same time, say.;
    %else %if &_lat_outcome. EQ restored %then
        %put WARNING: Load-Accelerator-Tables: the upload could not take the place of the table &caslib..&releaseTable. (messages above). The table was loaded again from the file it was saved to before.;
    %else %if &_lat_outcome. EQ replace %then
        %put WARNING: Load-Accelerator-Tables: the upload could not take the place of the table &caslib..&releaseTable. (messages above), and the table was not loaded again from the file it was saved to before: load that file, or run the job again.;
    %else %if &_lat_outcome. EQ save %then
        %put WARNING: Load-Accelerator-Tables: the release table &caslib..&releaseTable. holds its &_lat_rows. rows in memory and could not be saved (messages above).;
    %else
        %put WARNING: Load-Accelerator-Tables: nothing said what became of the upload of ACCELERATOR_RELEASES.csv (messages above), the table &caslib..&releaseTable. may not be loaded.;
%mend _lat_release_table;
%_lat_release_table;
%sysmacdelete _lat_release_table;

/* ---- The three fact sheets ----------------------------------------------------
   Load-Fact-Sheets.sas of the content folder loads them. It holds statements
   of the macro language in open code, so it is included here, in open code
   as well, and not from inside a macro. The fileref names the program where
   it is there and this run is not refused, and otherwise a file of one
   comment, so that the include below has something to read in every run. */
filename _latlfs temp;
%macro _lat_find_fact_sheets;
    %local _lat_found;
    %let _lat_found = 0;
    %if &_lat_ready. EQ 1 %then %do;
        /* the fileref is assigned only where the Folders service did not say that the folder is not there */
        %_lat_ask_folder(/Logging and Monitoring);
        %if "&SYS_PROCHTTP_STATUS_CODE." NE "404" %then %do;
            filename _latlfs filesrvc folderPath="&contentRoot./Logging and Monitoring" filename="Load-Fact-Sheets.sas";
            %if %sysfunc(fexist(_latlfs)) EQ 1 %then %let _lat_found = 1;
        %end;
        %if &_lat_found. NE 1 %then %put WARNING: Load-Accelerator-Tables: the program Load-Fact-Sheets.sas was not found in the folder &contentRoot./Logging and Monitoring, the three fact sheet tables were left as they are.;
    %end;
    %if &_lat_found. NE 1 %then %do;
        filename _latlfs temp;
        data _null_;
            file _latlfs;
            put '* Load-Accelerator-Tables: the fact sheets are not loaded in this run;';
        run;
    %end;
%mend _lat_find_fact_sheets;
%_lat_find_fact_sheets;
%sysmacdelete _lat_find_fact_sheets;
%sysmacdelete _lat_ask_folder;

/* The two settings of Load-Fact-Sheets.sas, which takes a setting that is set */
%let _lfs_path = &contentRoot./Data;
%let _lfs_caslib = &caslib.;
%include _latlfs;
filename _latlfs clear;

/* ---- What is there at the end -------------------------------------------------
   The job is a failed job when one of the four tables is not in the CAS
   library now - whatever this run loaded, left as it was or could not load. */
%macro _lat_look;
    %if &_lat_ready. NE 1 %then %return;
    /* every table, until the step below answered for each: a step that halts sets nothing */
    %let _lat_missing = &releaseTable. LLM_FACT_SHEET EMBEDDING_FACT_SHEET DECISION_FACT_SHEET;
    proc cas;
        missing = "";
        do name over {"&releaseTable.", "LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET"};
            table.tableExists result = found status = asked / caslib = "&caslib." name = name;
            if asked.severity != 0 then missing = missing || " " || name;
            else if found.exists == 0 then missing = missing || " " || name;
        end;
        call symputx("_lat_missing", missing);
    quit;
    %if %length(&_lat_missing.) GT 0 %then %do;
        %put ERROR: Load-Accelerator-Tables: not in the CAS library &caslib. at the end of the run, or CAS did not say whether they are: &_lat_missing.. The lines above say why.;
        %let _lat_failed = 1;
    %end;
    %else %put NOTE: Load-Accelerator-Tables: &releaseTable., LLM_FACT_SHEET, EMBEDDING_FACT_SHEET and DECISION_FACT_SHEET are in the CAS library &caslib..;
%mend _lat_look;
%_lat_look;
%sysmacdelete _lat_look;
%_lat_tables(after);
%sysmacdelete _lat_tables;

/* ---- The page ---------------------------------------------------------------------
   Started from its address in SAS Job Execution, the job writes a short page
   to the fileref _webout, as Start Here does: each of the four tables with its
   rows and whether this run loaded it, left it as it was or did not find it in
   the CAS library at the end. The log keeps every message. SAS Job Execution
   shows what a job writes there as the page with the parameter _output_type =
   html, which the job definition holds; for a job definition without it SAS
   Job Execution has its own output on _webout, and no page is written, with a
   NOTE. A session without _webout - the program run in SAS Studio - writes no
   page and no NOTE. */
%macro _lat_page;
    %local _lat_html;
    %if %sysfunc(fileref(_webout)) GT 0 %then %return;
    %let _lat_html = 0;
    %if %symexist(_output_type) %then %do;
        %if %upcase(%superq(_output_type)) EQ HTML %then %let _lat_html = 1;
    %end;
    %if &_lat_html. NE 1 %then %do;
        %put NOTE: Load-Accelerator-Tables: no page was written to _webout, because the job definition has no parameter _output_type with the value html. The lines above say what was loaded.;
        %return;
    %end;
    data _null_;
        file _webout lrecl=32767;
        length _lat_line $ 2000 _lat_lib _lat_name $ 300 _lat_before _lat_after _lat_class _lat_state $ 200 _lat_rows $ 40
            _lat_all $ 1000 _lat_outcome $ 40;
        _lat_lib = htmlencode(strip(symget('caslib')));
        put '<!DOCTYPE html>';
        put '<html lang="en"><head><meta charset="utf-8"><title>SAS Agentic AI Accelerator - Load Accelerator Tables</title>';
        put '<style>body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f5f6f7;color:#1b1d21;line-height:1.45}';
        put 'main{max-width:60rem;margin:0 auto;padding:1.5rem 1rem 3rem}h1{font-size:1.6rem;margin:0 0 .5rem}p{margin:.4rem 0}';
        put 'table{border-collapse:collapse;width:100%;background:#fff;margin:.8rem 0}th,td{text-align:left;padding:.35rem .6rem}';
        put 'th,td{border-bottom:1px solid #d9dce1;overflow-wrap:anywhere}th{font-size:.85rem;background:#e9ebee}';
        put 'td.ok{color:#1c6b2a;font-weight:bold}td.kept{color:#6a5300;font-weight:bold}td.missing{color:#a1260d;font-weight:bold}';
        put '.summary{background:#fff;border-left:.3rem solid #0b5cab;padding:.6rem .8rem}.note{font-size:.9rem;color:#4a4f57}';
        put '@media (prefers-color-scheme:dark){body{background:#17191c;color:#e8eaed}table,.summary{background:#22252a}';
        put 'th{background:#2c3036}th,td{border-bottom-color:#3a3f47}td.ok{color:#7fd18b}td.kept{color:#e3c35a}';
        put 'td.missing{color:#ff9c8a}.note{color:#b4b9c0}}</style></head><body><main>';
        put '<h1>Load Accelerator Tables</h1>';
        if symget('_lat_ready') NE '1' then do;
            put '<p class="summary">A parameter of the job was refused, so nothing was read or written. The ERROR line of the log names it.</p>';
        end;
        else do;
            if lengthn(symget('_lat_missing')) = 0 then
                _lat_line = '<p class="summary">The four tables are in the CAS library ' || strip(_lat_lib) || '.</p>';
            else _lat_line = '<p class="summary">The job failed: not in the CAS library ' || strip(_lat_lib)
                || ' at the end of the run, or CAS did not say whether they are: ' || strip(htmlencode(strip(symget('_lat_missing')))) || '.</p>';
            put _lat_line;
            put '<table><thead><tr><th>Table</th><th>Rows</th><th>This run</th></tr></thead><tbody>';
            _lat_all = upcase(catx(' ', symget('_lat_outcomes'), symget('_lfs_outcomes')));
            do _lat_i = 1 to 4;
                _lat_name = scan(catx(' ', symget('releaseTable'), 'LLM_FACT_SHEET EMBEDDING_FACT_SHEET DECISION_FACT_SHEET'), _lat_i, ' ');
                _lat_before = scan(symget('_lat_before'), _lat_i, ' ');
                _lat_after = scan(symget('_lat_after'), _lat_i, ' ');
                _lat_rows = scan(_lat_after, 1, '/');
                /* what the swap of this table ended with, where it got to one */
                _lat_outcome = '';
                do _lat_j = 1 to countw(_lat_all, ' ');
                    if scan(scan(_lat_all, _lat_j, ' '), 1, ':') = upcase(strip(_lat_name)) then _lat_outcome = lowcase(scan(scan(_lat_all, _lat_j, ' '), 2, ':'));
                end;
                _lat_class = 'missing';
                if _lat_rows = 'unknown' then _lat_state = 'CAS did not list the tables of the CAS library';
                else if _lat_rows = 'none' then _lat_state = 'Not in the CAS library';
                else if _lat_outcome = 'kept' then do;
                    _lat_class = 'kept';
                    _lat_state = 'Left as it was: the upload could not take its place - the WARNING in the log says why';
                end;
                else if _lat_outcome = 'restored' then do;
                    _lat_class = 'kept';
                    _lat_state = 'Loaded again from its saved file: the upload could not take its place - the WARNING in the log says why';
                end;
                else if _lat_outcome = 'save' then do;
                    _lat_class = 'kept';
                    _lat_state = 'Loaded by this run and not saved: its saved file is the one of before';
                end;
                else if scan(_lat_before, 1, '/') = 'unknown' then do;
                    _lat_class = 'ok';
                    _lat_state = 'In the CAS library';
                end;
                else if _lat_after NE _lat_before then do;
                    _lat_class = 'ok';
                    _lat_state = 'Loaded by this run';
                end;
                else do;
                    _lat_class = 'kept';
                    _lat_state = 'Left as it was';
                end;
                if _lat_rows in ('unknown', 'none') then _lat_rows = '-';
                _lat_line = '<tr><td>' || strip(_lat_lib) || '.' || strip(htmlencode(strip(_lat_name))) || '</td><td>' || strip(_lat_rows)
                    || '</td><td class="' || strip(_lat_class) || '">' || strip(_lat_state) || '</td></tr>';
                put _lat_line;
            end;
            put '</tbody></table>';
            put '<p class="note">The log of the job says why a table was left as it was or is not there: its WARNING and ERROR lines name the file, the folder or the step.</p>';
        end;
        put '</main></body></html>';
    run;
%mend _lat_page;
%_lat_page;
%sysmacdelete _lat_page;

* Clean up;
%if &_lat_clean_cas_session. %then %do;
    cas _latSess terminate;
%end;
options &_lat_syntaxcheck.;
* A run that wrote an ERROR line ends with the condition code of a failed job, as a SAS error leaves it: a parameter that was refused, and a table that is not in the CAS library at the end. A run with a WARNING for a table that was left as it is ends as before. Nothing follows but the macro variables that are deleted;
%if &_lat_ready. NE 1 or &_lat_failed. EQ 1 %then %do;
    %let SYSCC = 1012;
%end;
%symdel _lat_vars _lat_ready _lat_failed _lat_missing _lat_clean_cas_session _lat_syntaxcheck _lat_outcomes _lfs_outcomes _lat_before _lat_after / nowarn;
%symdel _lfs_path _lfs_caslib / nowarn;
