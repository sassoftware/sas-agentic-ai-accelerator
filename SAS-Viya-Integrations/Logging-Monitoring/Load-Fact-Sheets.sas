/********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    This script is intended to upload the included fact sheet that contain
      pricing information on LLMs & Embedding models.

    The tables created by this script are used in the provided SAS Visual
      Analytics report to help to provide accurate price information. Please
      update the prices, as no discounts or other special configs are assumed.
      A CAS session will be created for you.

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

* Load/Promote/Save to CAS;
cas mySess;

proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";
    dropTable casData='LLM_FACT_SHEET' quiet;
    load data=work._lfs_llm_fact_sheet casOut='LLM_FACT_SHEET';
    promote casData='LLM_FACT_SHEET' casOut='LLM_FACT_SHEET';
    save casData='LLM_FACT_SHEET' casOut='LLM_FACT_SHEET' replace;
    dropTable casData='EMBEDDING_FACT_SHEET' quiet;
    load data=work._lfs_embedding_fact_sheet casOut='EMBEDDING_FACT_SHEET';
    promote casData='EMBEDDING_FACT_SHEET' casOut='EMBEDDING_FACT_SHEET';
    save casData='EMBEDDING_FACT_SHEET' casOut='EMBEDDING_FACT_SHEET' replace;
run; quit;

* Clean up;
proc datasets lib=work noList;
    delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet;
run; quit;
cas mySess terminate;
%symdel _lfs_path _lfs_caslib;