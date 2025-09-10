/********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get a list of Projects in a SAS Model Manager Repository

    This script requires you to set the macro variable
      _mgp_repository_id which is the ID of a SAS Model Manager
      repository. To retrieve the ID you can use the script
      MM-Get-Repositories.sas.

    The output table is called:
      work._mgp_projects

    mgp is short for:
      Model Manager Get Projects
********************************************************************************/
* Set the SAS Model Manager project ID;
%let _mgp_repository_id = ;

* Get the Viya Host URL;
%let _mgp_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

* Macro to recursivly get all projects;
%macro _mgp_project_pagination(_mgp_start=0, _mgp_limit=25);
    %local _mgp_start
        _mgp_limit
        _mgp_ds_nobs
        _mgp_nvar
        _mgp_new_start;

    filename _mgp_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjects;
    proc http method = 'Get'
        url = "&_mgp_viyaHost./modelRepository/projects?start=&_mgp_start%nrstr(&)limit=&_mgp_limit.%nrstr(&)filter=eq(repositoryId,'&_mgp_repository_id.')"
        oauth_bearer = sas_services
        out = _mgp_rp;
        headers 'Accept' = 'application/json';
    quit;

    libname _mgp_rp json;

    * Get the number of observations where one obs = one project;
    proc sql noprint;
        select count(*) into :_mgp_ds_nobs trimmed from _mgp_rp.items;
    quit;

    * Get the number of columns;
    proc sql noprint;
        select nvar into :_mgp_nvar trimmed from dictionary.tables where
            libname='_MGP_RP' and memname='ITEMS';
    quit;

    * Handle the first iteration and subsequent requests;
    %if &_mgp_start. eq 0 %then %do;
        data work._mgp_projects;
            length name createdBy modifiedBy function status $256.;
            set _mgp_rp.items(keep=id repositoryId name function status folderId createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
        run;
    %end;
    %else %do;
        %if &_mgp_nvar. gt 2 %then %do;
            proc append force nowarn base=work._mgp_projects
                data=_mgp_rp.items(keep=id repositoryId name function status folderId createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
            quit;
        %end;
    %end;

    libname _mgp_rp clear;
    filename _mgp_rp clear;

    * Check if another iteration is needed;
    %if &_mgp_ds_nobs eq &_mgp_limit and &_mgp_nvar. gt 2 %then %do;
        %put NOTE: Getting more projects;
        %let _mgp_new_start=%eval(&_mgp_start. + &_mgp_limit.);
        %_mgp_project_pagination(_mgp_start=&_mgp_new_start., _mgp_limit=&_mgp_limit.);
    %end;
%mend _mgp_project_pagination;

* Get a table of all projects;
%_mgp_project_pagination();

* Clean up;
%symdel _mgp_repository_id _mgp_viyaHost;
%sysmacdelete _mgp_project_pagination;