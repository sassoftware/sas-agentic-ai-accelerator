/******************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get a list of Models in a SAS Model Manager Project

    This script requires you to set the macro variable
      _mgm_project_id which is the ID of a SAS Model Manager
      project. To retrieve the ID you can use the script
      MM-Get-Projects-in-Repository.sas.

    The output table is called:
      work._mgm_models

    mgm is short for:
      Model Manager Get Models
******************************************************************************/
* Set the SAS Model Manager project ID;
%let _mgm_project_id = ;

* Get the Viya Host URL;
%let _mgm_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

* Macro to recursivly get all models in a project;
%macro _mgm_model_pagination(_mgm_start=0, _mgm_limit=25);
    %local _mgm_start
        _mgm_limit
        _mgm_ds_nobs
        _mgm_nvar
        _mgm_new_start;

    filename _mgm_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjectModels;
    proc http method = 'Get'
        url = "&_mgm_viyaHost./modelRepository/projects/&_mgm_project_id./models?start=&_mgm_start%nrstr(&)limit=&_mgm_limit."
        oauth_bearer = sas_services
        out = _mgm_rp;
        headers 'Accept' = 'application/json';
    quit;

    libname _mgm_rp json;

    * Get the number of observations where one obs = one model;
    proc sql noprint;
        select count(*) into :_mgm_ds_nobs trimmed from _mgm_rp.items;
    quit;

    * Get the number of columns;
    proc sql noprint;
        select nvar into :_mgm_nvar trimmed from dictionary.tables where
            libname='_MGM_RP' and memname='ITEMS';
    quit;

    * Handle the first iteration and subsequent requests;
    %if &_mgm_start. eq 0 %then %do;
        data work._mgm_models;
            length name createdBy modifiedBy function role $256.;
            set _mgm_rp.items(keep=id projectId repositoryId name function role createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
        run;
    %end;
    %else %do;
        %if &_mgm_nvar. gt 2 %then %do;
            proc append force nowarn base=work._mgm_models
                data=_mgm_rp.items(keep=id projectId repositoryId name function role createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
            quit;
        %end;
    %end;

    libname _mgm_rp clear;
    filename _mgm_rp clear;

    * Check if another iteration is needed;
    %if &_mgm_ds_nobs eq &_mgm_limit and &_mgm_nvar. gt 2 %then %do;
        %put NOTE: Getting more models;
        %let _mgm_new_start=%eval(&_mgm_start. + &_mgm_limit.);
        %_mgm_model_pagination(_mgm_start=&_mgm_new_start., _mgm_limit=&_mgm_limit.);
    %end;
%mend _mgm_model_pagination;

* Get a table of all models;
%_mgm_model_pagination();

* Clean up;
%symdel _mgm_project_id _mgm_viyaHost;
%sysmacdelete _mgm_model_pagination;