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

    LFS is short for Load Fact Sheets
********************************************************************************/
* Provide the path in SAS Content where the files are being stored;
%let _lfs_path = /SAS Agentic AI Accelerator/Data;
* Provide the target CAS library for the tables;
%let _lfs_caslib = Public;

* Load the LLM Fact Sheet;
filename _lfs_fs filesrvc folderPath="&_lfs_path." filename='llm_fact_sheet.csv';

proc import dataFile=_lfs_fs
    out=work._lfs_llm_fact_sheet
    dbms=csv
    replace;
    getNames=yes;
    guessingRows=max;
    delimiter=',';
run; quit;

filename _lfs_fs clear;

* Load the Embedding Fact Sheet;
filename _lfs_fs filesrvc folderPath="&_lfs_path." filename='embedding_fact_sheet.csv';

proc import dataFile=_lfs_fs
    out=work._lfs_embedding_fact_sheet
    dbms=csv
    replace;
    getNames=yes;
    guessingRows=max;
    delimiter=',';
run; quit;

filename _lfs_fs clear;

* Load the Decision Fact Sheet;
filename _lfs_fs filesrvc folderPath="&_lfs_path." filename='decision_fact_sheet.csv';

proc import dataFile=_lfs_fs
    out=work._lfs_decision_fact_sheet
    dbms=csv
    replace;
    getNames=yes;
    guessingRows=max;
    delimiter=',';
run; quit;

filename _lfs_fs clear;

* Load/Promote/Save to CAS;
* Check if a CAS session exists;
* The option SESSREF names the session the CAS actions run in and keeps the name of a session that was ended, so SESSFOUND is asked whether that session is connected. A session that is connected is used as it is: starting mySess a second time in one SAS session makes SAS warn that the session exists, and ending it takes the session from the librefs another program bound to it;
%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    %let _lfs_clean_cas_session = 1;
    cas mySess;
%end;
%else %do;
    %let _lfs_clean_cas_session = 0;
%end;

proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";
    dropTable casData='LLM_FACT_SHEET' quiet;
    load data=work._lfs_llm_fact_sheet casOut='LLM_FACT_SHEET';
    promote casData='LLM_FACT_SHEET' casOut='LLM_FACT_SHEET';
    save casData='LLM_FACT_SHEET' casOut='LLM_FACT_SHEET' replace;
    dropTable casData='EMBEDDING_FACT_SHEET' quiet;
    load data=work._lfs_embedding_fact_sheet casOut='EMBEDDING_FACT_SHEET';
    promote casData='EMBEDDING_FACT_SHEET' casOut='EMBEDDING_FACT_SHEET';
    save casData='EMBEDDING_FACT_SHEET' casOut='EMBEDDING_FACT_SHEET' replace;
    dropTable casData='DECISION_FACT_SHEET' quiet;
    load data=work._lfs_decision_fact_sheet casOut='DECISION_FACT_SHEET';
    promote casData='DECISION_FACT_SHEET' casOut='DECISION_FACT_SHEET';
    save casData='DECISION_FACT_SHEET' casOut='DECISION_FACT_SHEET' replace;
run; quit;

* Clean up;
proc datasets lib=work noList;
    delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet _lfs_decision_fact_sheet;
run; quit;
%if &_lfs_clean_cas_session. %then %do;
    cas mySess terminate;
%end;
%symdel _lfs_path _lfs_caslib _lfs_clean_cas_session;