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
 * variable and none is cut to its length.
 *
 * A TABLE THAT EXISTS is replaced only by one that loaded completely: the
 * file is uploaded beside the table as <table>_NEW, a table of the CAS
 * session, its rows are counted against the lines of the file, and only then
 * is the table dropped, the upload promoted under its name and saved. A file
 * that is not there, that has other columns or that CAS did not take leaves
 * the table as it is, with a WARNING. Load-Fact-Sheets.sas does the same for
 * each sheet.
 *
 * THE JOB FAILS when one of the four tables is not in the CAS library at its
 * end, or when a parameter is refused: it then writes an ERROR line and sets
 * the condition code of the session to 1012. A table that was left as it is
 * and is there is a WARNING. An error of SAS or of CAS on the way is an
 * error of the job as well; it stops no table behind it from being loaded.
 *
 * Parameters of the job definition:
 *   _action       execute
 *   contentRoot   the content folder (default /SAS Agentic AI Accelerator)
 *   caslib        the CAS library of the tables (default Public)
 *   releaseTable  the name of the release table (default ACCELERATOR_RELEASES)
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
run;

* Check if a CAS session exists;
* The option SESSREF names the session the CAS actions run in and keeps the name of a session that was ended, so SESSFOUND is asked whether that session is connected. A session that is connected is used as it is, and only a session this program started is ended at its end;
%if &_lat_ready. EQ 1 and %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    %let _lat_clean_cas_session = 1;
    cas _latSess;
%end;
%else %do;
    %let _lat_clean_cas_session = 0;
%end;

/* ---- The release table -------------------------------------------------------
   The file is copied from SAS Content to WORK as it is, its lines are counted
   - mdb writes one row per line, no value holds a line break - and its first
   line is compared with the columns above: with other columns the upload
   would put values under the wrong names. CAS then reads the file with the
   options the ingestion job of a RAG setup uploads its run history with. */
%macro _lat_release_table;
    %local _lat_file _lat_copy _lat_rows _lat_header _lat_outcome _lat_staged _lat_i _lat_n _lat_v;
    %if &_lat_ready. NE 1 %then %return;
    %let _lat_file = %sysfunc(pathname(work))/accelerator_releases.csv;
    %let _lat_rows = -1;
    %let _lat_header = 0;

    filename _latsrc filesrvc folderPath="&contentRoot./Data" filename="ACCELERATOR_RELEASES.csv" recfm=n lrecl=32767;
    %if %sysfunc(fexist(_latsrc)) NE 1 %then %do;
        %put WARNING: Load-Accelerator-Tables: the file ACCELERATOR_RELEASES.csv was not found in the folder &contentRoot./Data, the table &caslib..&releaseTable. was left as it is.;
        filename _latsrc clear;
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

    %let _lat_outcome = unknown;
    %let _lat_staged = -1;
    %let _lat_n = %sysfunc(countw(&_lat_vars., %str( )));
    proc cas;
        outcome = "upload";
        staged = -1;
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
        /* the rows the upload holds, against the lines of the file */
        table.tableExists result = found / caslib = "&caslib." name = "&releaseTable._NEW";
        if found.exists != 0 then do;
            staged = 0;
            table.tableInfo result = listing / caslib = "&caslib.";
            if exists(listing, "TableInfo") then do;
                do entry over listing.TableInfo;
                    if upcase(entry.Name) == upcase("&releaseTable._NEW") then staged = entry.Rows;
                end;
            end;
            outcome = "rows";
        end;
        if staged == &_lat_rows. then do;
            outcome = "replace";
            table.dropTable status = dropped / caslib = "&caslib." name = "&releaseTable." quiet = true;
            table.promote status = promoted / caslib = "&caslib." name = "&releaseTable._NEW"
                targetLib = "&caslib." target = "&releaseTable.";
            if promoted.severity == 0 then do;
                outcome = "save";
                table.save status = stored / table = {caslib = "&caslib.", name = "&releaseTable."}
                    caslib = "&caslib." name = "&releaseTable." replace = true;
                if stored.severity == 0 then outcome = "loaded";
            end;
        end;
        else do;
            /* an upload that does not hold every row takes the place of no table */
            table.dropTable / caslib = "&caslib." name = "&releaseTable._NEW" quiet = true;
        end;
        /* "L": local to this macro - without a scope CASL writes to the global symbol table */
        call symputx("_lat_outcome", outcome, "L");
        call symputx("_lat_staged", staged, "L");
    quit;

    %if &_lat_outcome. EQ loaded %then
        %put NOTE: Load-Accelerator-Tables: the release table &caslib..&releaseTable. was loaded with &_lat_rows. rows, promoted and saved.;
    %else %if &_lat_outcome. EQ upload %then
        %put WARNING: Load-Accelerator-Tables: the &_lat_rows. rows of ACCELERATOR_RELEASES.csv could not be uploaded to the CAS library &caslib. (messages above), the table &caslib..&releaseTable. was left as it is.;
    %else %if &_lat_outcome. EQ rows %then
        %put WARNING: Load-Accelerator-Tables: the upload of ACCELERATOR_RELEASES.csv holds &_lat_staged. rows where the file has &_lat_rows., the table &caslib..&releaseTable. was left as it is.;
    %else %if &_lat_outcome. EQ replace %then
        %put WARNING: Load-Accelerator-Tables: the upload could not take the place of the table &caslib..&releaseTable. (messages above). The table holds what it held before or is no longer in memory.;
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
        filename _latlfs filesrvc folderPath="&contentRoot./Logging and Monitoring" filename="Load-Fact-Sheets.sas";
        %if %sysfunc(fexist(_latlfs)) EQ 1 %then %let _lat_found = 1;
        %else %put WARNING: Load-Accelerator-Tables: the program Load-Fact-Sheets.sas was not found in the folder &contentRoot./Logging and Monitoring, the three fact sheet tables were left as they are.;
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
    proc cas;
        missing = "";
        do name over {"&releaseTable.", "LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET"};
            table.tableExists result = found / caslib = "&caslib." name = name;
            if found.exists == 0 then missing = missing || " " || name;
        end;
        call symputx("_lat_missing", missing);
    quit;
    %if %length(&_lat_missing.) GT 0 %then %do;
        %put ERROR: Load-Accelerator-Tables: not in the CAS library &caslib. at the end of the run: &_lat_missing.. The lines above say why.;
        %let _lat_failed = 1;
    %end;
    %else %put NOTE: Load-Accelerator-Tables: &releaseTable., LLM_FACT_SHEET, EMBEDDING_FACT_SHEET and DECISION_FACT_SHEET are in the CAS library &caslib..;
%mend _lat_look;
%_lat_look;
%sysmacdelete _lat_look;

* Clean up;
%if &_lat_clean_cas_session. %then %do;
    cas _latSess terminate;
%end;
options &_lat_syntaxcheck.;
* A run that wrote an ERROR line ends with the condition code of a failed job, as a SAS error leaves it: a parameter that was refused, and a table that is not in the CAS library at the end. A run with a WARNING for a table that was left as it is ends as before. Nothing follows but the macro variables that are deleted;
%if &_lat_ready. NE 1 or &_lat_failed. EQ 1 %then %do;
    %let SYSCC = 1012;
%end;
%symdel _lat_vars _lat_ready _lat_failed _lat_missing _lat_clean_cas_session _lat_syntaxcheck / nowarn;
%symdel _lfs_path _lfs_caslib / nowarn;
