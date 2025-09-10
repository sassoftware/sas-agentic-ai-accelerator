/********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Retrieves a list of available LLMs

    This script requires you to set the following macro
      variables, the defaults match the guided setup defaults:
      - _sll_llm_repository
      - _sll_llm_project

    The output table is called:
      work._sll_models

    sll is short for:
      SAS Large Language Model Listing
********************************************************************************/
* Provide the name of the LLM Repository in SAS Model Manager;
%let _sll_llm_repository = LLM Repository;
* Provide the name of the LLM Model Project in SAS Model Manager;
%let _sll_llm_project = LLM Model Project;

* Helper macro;
* Macro to recursivly get all models in a project;
%macro _sll_model_pagination(_sll_start=0, _sll_limit=25);
    %local _sll_start
        _sll_limit
        _sll_ds_nobs
        _sll_nvar
        _sll_new_start;

    filename _sll_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjectModels;
    proc http method = 'Get'
        url = "&_sll_viyaHost./modelRepository/projects/&_sll_projectID./models?start=&_sll_start%nrstr(&)limit=&_sll_limit."
        oauth_bearer = sas_services
        out = _sll_rp;
        headers 'Accept' = 'application/json';
    quit;

    libname _sll_rp json;

    * Get the number of observations where one obs = one model;
    proc sql noprint;
        select count(*) into :_sll_ds_nobs trimmed from _sll_rp.items;
    quit;

    * Get the number of columns;
    proc sql noprint;
        select nvar into :_sll_nvar trimmed from dictionary.tables where
            libname='_SLL_RP' and memname='ITEMS';
    quit;

    * Handle the first iteration and subsequent requests;
    %if &_sll_start. eq 0 %then %do;
        data work._sll_models;
            length name $256. id $36. optionsFileURI $49.;
            set _sll_rp.items(keep=id name);
        run;
    %end;
    %else %do;
        %if &_sll_nvar. gt 2 %then %do;
            proc append force nowarn base=work._sll_models
                data=_sll_rp.items(keep=id name);
            quit;
        %end;
    %end;

    libname _sll_rp clear;
    filename _sll_rp clear;

    * Check if another iteration is needed;
    %if &_sll_ds_nobs eq &_sll_limit and &_sll_nvar. gt 2 %then %do;
        %put NOTE: Getting more models;
        %let _sll_new_start=%eval(&_sll_start. + &_sll_limit.);
        %_sll_model_pagination(_sll_start=&_sll_new_start., _sll_limit=&_sll_limit.);
    %end;
%mend _sll_model_pagination;

* Retrieve the file URI of the model;
%macro _sll_model_options_file(_sll_modelID);
    %local _sll_modelID;

    filename _sll_mof temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModel;
    proc http
        method='Get'
        url="&_sll_viyaHost./modelRepository/models/&_sll_modelID."
        oauth_bearer=sas_services
        out=_sll_mof;
    run; quit;

    libname _sll_mof json;

    * Update the options file URI;
    proc sql;
        update work._sll_models
            set optionsFileURI=(select fileUri from _sll_mof.files where name EQ 'options.json')
                where id EQ "&_sll_modelID.";
    run; quit;

    * Clean up;
    libname _sll_mof clear;
    filename _sll_mof clear;
%mend _sll_model_options_file;

* Get the Viya Host URL;
%let _sll_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

filename _sll_rep temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getRepositories;
proc http
    method='Get'
    url="&_sll_viyaHost./modelRepository/repositories?filter=eq(name,'&_sll_llm_repository')"
    oauth_bearer=sas_services
    out=_sll_rep;
run; quit;

libname _sll_rep json;

* Retrieve the repository ID;
proc sql noPrint;
    select id into :_sll_repositoryID
        from _sll_rep.items;
run; quit;

* Clean up;
libname _sll_rep clear;
filename _sll_rep clear;

filename _sll_pro temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjects;
proc http
    method='Get'
    url="&_sll_viyaHost./modelRepository/projects?filter=and(eq(name,'&_sll_llm_project.'),eq(repositoryId,'&_sll_repositoryID.'))"
    oauth_bearer=sas_services
    out=_sll_pro;
run; quit;

libname _sll_pro json;

* Retrieve the project ID;
proc sql noPrint;
    select id into :_sll_projectID
        from _sll_pro.items;
run; quit;

* Clean up;
libname _sll_pro clear;
filename _sll_pro clear;

* Get a table of all models;
%_sll_model_pagination();

* Get a table of all the options files for each mode;
%_sll_model_options_file();

* Update the models table with the optionsFileURI;
data _null_;
    set work._sll_models;
    call execute(catx(' ', '%nrstr(%_sll_model_options_file)', '(', id, ')'));
run;

* Final clean up;
%symdel _sll_llm_repository _sll_llm_project _sll_viyaHost _sll_repositoryID _sll_projectID;
%sysmacdelete _sll_model_pagination;
%sysmacdelete _sll_model_options_file;