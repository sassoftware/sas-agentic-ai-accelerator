/********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    This script is intended to upload the included fact sheet that contain
      pricing information on LLMs, Embedding models & Decision models.

    The tables created by this script are used in the provided SAS Visual
      Analytics report to help to provide accurate price information. Please
      update the prices, as no discounts or other special configs are assumed.
      A CAS session is started for you when none is connected, and only
      that session is ended again.

    A table that exists is only replaced by a sheet that was read: a file
      that is not in the folder, a file without rows and a sheet that CAS
      does not take leave the table of that sheet as it is, with a WARNING,
      and the other sheets are loaded all the same.

    The job definition Load-Accelerator-Tables includes this file from the
      content folder and loads the release table beside the three sheets.

    LFS is short for Load Fact Sheets
********************************************************************************/
/* A setting is taken from the lines below only where its macro variable is
   not set yet. A program of your own that sets a value with a LET statement
   and then includes this file keeps that value, also after this file was
   replaced by the one of a newer release. */
%macro _lfs_default(_lfs_name, _lfs_value);
    %if %symexist(&_lfs_name.) EQ 0 %then %do;
        %global &_lfs_name.;
        %let &_lfs_name. = &_lfs_value.;
    %end;
%mend _lfs_default;
* Provide the path in SAS Content where the files are being stored;
%_lfs_default(_lfs_path, /SAS Agentic AI Accelerator/Data);
* Provide the target CAS library for the tables;
%_lfs_default(_lfs_caslib, Public);
%sysmacdelete _lfs_default;

* Check if a CAS session exists;
* The option SESSREF names the session the CAS actions run in and keeps the name of a session that was ended, so SESSFOUND is asked whether that session is connected. A session that is connected is used as it is: starting mySess a second time in one SAS session makes SAS warn that the session exists, and ending it takes the session from the librefs another program bound to it;
%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    %let _lfs_clean_cas_session = 1;
    cas mySess;
%end;
%else %do;
    %let _lfs_clean_cas_session = 0;
%end;

/* Reads one sheet from the folder into WORK and tries it in CAS, beside the
   table: it is loaded as <table>_NEW, a table of this session, its rows are
   counted and it is dropped again. _lfs_ok is 1 when CAS held every row of
   the sheet, so that the table is only dropped for a sheet that can take
   its place. A file that is not there is not handed to PROC IMPORT, which
   ends with an error for it: the sheet is a WARNING, and the sheets behind
   it are read. */
%let _lfs_ok = 0;
%macro _lfs_read(_lfs_file, _lfs_work, _lfs_table);
    %local _lfs_rows _lfs_staged _lfs_dsid _lfs_rc;
    %let _lfs_ok = 0;
    %let _lfs_rows = -1;
    %let _lfs_staged = -1;
    proc datasets lib=work noList noWarn;
        delete &_lfs_work.;
    run; quit;

    filename _lfs_fs filesrvc folderPath="&_lfs_path." filename="&_lfs_file.";
    %if %sysfunc(fexist(_lfs_fs)) NE 1 %then %do;
        %put WARNING: The file &_lfs_file. was not found in the folder &_lfs_path., the table &_lfs_caslib..&_lfs_table. was left as it is.;
        filename _lfs_fs clear;
        %return;
    %end;

    proc import dataFile=_lfs_fs
        out=work.&_lfs_work.
        dbms=csv
        replace;
        getNames=yes;
        guessingRows=max;
        delimiter=',';
    run; quit;

    filename _lfs_fs clear;

    %if %sysfunc(exist(work.&_lfs_work.)) %then %do;
        %let _lfs_dsid = %sysfunc(open(work.&_lfs_work.));
        %if &_lfs_dsid. GT 0 %then %do;
            %let _lfs_rows = %sysfunc(attrn(&_lfs_dsid., nlobs));
            %let _lfs_rc = %sysfunc(close(&_lfs_dsid.));
        %end;
    %end;
    %if &_lfs_rows. LE 0 %then %do;
        %put WARNING: The file &_lfs_file. of the folder &_lfs_path. could not be read or holds no rows, the table &_lfs_caslib..&_lfs_table. was left as it is.;
        %return;
    %end;

    proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";
        load data=work.&_lfs_work. casOut="&_lfs_table._NEW" replace;
    run; quit;

    proc cas;
        staged = -1;
        table.tableExists result = found / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW";
        if found.exists != 0 then do;
            staged = 0;
            table.tableInfo result = listing / caslib = "&_lfs_caslib.";
            if exists(listing, "TableInfo") then do;
                do entry over listing.TableInfo;
                    if upcase(entry.Name) == upcase("&_lfs_table._NEW") then staged = entry.Rows;
                end;
            end;
        end;
        table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;
        /* "L": the macro variable is local to this macro, and without a scope CASL writes to the global symbol table */
        call symputx("_lfs_staged", staged, "L");
    run; quit;

    %if &_lfs_staged. EQ &_lfs_rows. %then %do;
        %let _lfs_ok = 1;
    %end;
    %else %do;
        %put WARNING: The &_lfs_rows. rows of the file &_lfs_file. could not be loaded into the CAS library &_lfs_caslib. - CAS held &_lfs_staged. of them, -1 stands for a table that was not found. The table &_lfs_caslib..&_lfs_table. was left as it is.;
    %end;
%mend _lfs_read;

* Load the LLM Fact Sheet - Load/Promote/Save to CAS;
%_lfs_read(llm_fact_sheet.csv, _lfs_llm_fact_sheet, LLM_FACT_SHEET);
%if &_lfs_ok. EQ 1 %then %do;
    proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";
        dropTable casData='LLM_FACT_SHEET' quiet;
        load data=work._lfs_llm_fact_sheet casOut='LLM_FACT_SHEET';
        promote casData='LLM_FACT_SHEET' casOut='LLM_FACT_SHEET';
        save casData='LLM_FACT_SHEET' casOut='LLM_FACT_SHEET' replace;
    run; quit;
%end;

* Load the Embedding Fact Sheet - Load/Promote/Save to CAS;
%_lfs_read(embedding_fact_sheet.csv, _lfs_embedding_fact_sheet, EMBEDDING_FACT_SHEET);
%if &_lfs_ok. EQ 1 %then %do;
    proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";
        dropTable casData='EMBEDDING_FACT_SHEET' quiet;
        load data=work._lfs_embedding_fact_sheet casOut='EMBEDDING_FACT_SHEET';
        promote casData='EMBEDDING_FACT_SHEET' casOut='EMBEDDING_FACT_SHEET';
        save casData='EMBEDDING_FACT_SHEET' casOut='EMBEDDING_FACT_SHEET' replace;
    run; quit;
%end;

* Load the Decision Fact Sheet - Load/Promote/Save to CAS;
%_lfs_read(decision_fact_sheet.csv, _lfs_decision_fact_sheet, DECISION_FACT_SHEET);
%if &_lfs_ok. EQ 1 %then %do;
    proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";
        dropTable casData='DECISION_FACT_SHEET' quiet;
        load data=work._lfs_decision_fact_sheet casOut='DECISION_FACT_SHEET';
        promote casData='DECISION_FACT_SHEET' casOut='DECISION_FACT_SHEET';
        save casData='DECISION_FACT_SHEET' casOut='DECISION_FACT_SHEET' replace;
    run; quit;
%end;

* Clean up;
proc datasets lib=work noList noWarn;
    delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet _lfs_decision_fact_sheet;
run; quit;
%if &_lfs_clean_cas_session. %then %do;
    cas mySess terminate;
%end;
%sysmacdelete _lfs_read;
%symdel _lfs_ok / nowarn;
%symdel _lfs_path _lfs_caslib _lfs_clean_cas_session;
