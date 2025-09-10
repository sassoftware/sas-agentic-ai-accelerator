/*******************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get a list of SAS Model Manager Repositories

    The output table is called:
      work._mgr_repositories

    mgr is short for:
      Model Manager Get repositories
*******************************************************************************/

* Get the Viya Host URL;
%let _mgr_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

* Macro to recursivly get all repositories;
%macro _mgr_repository_pagination(_mgr_start=0, _mgr_limit=25);
    %local _mgr_start
        _mgr_limit
        _mgr_ds_nobs
        _mgr_nvar
        _mgr_new_start;

    filename _mgr_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getRepositories;
    proc http method = 'Get'
        url = "&_mgr_viyaHost./modelRepository/repositories?start=&_mgr_start%nrstr(&)limit=&_mgr_limit."
        oauth_bearer = sas_services
        out = _mgr_rp;
        headers 'Accept' = 'application/json';
    quit;

    libname _mgr_rp json;

    * Get the number of observations where one obs = one repository;
    proc sql noprint;
        select count(*) into :_mgr_ds_nobs trimmed from _mgr_rp.items;
    quit;

    * Get the number of columns;
    proc sql noprint;
        select nvar into :_mgr_nvar trimmed from dictionary.tables where
            libname='_MGR_RP' and memname='ITEMS';
    quit;

    * Handle the first iteration and subsequent requests;
    %if &_mgr_start. eq 0 %then %do;
        data work._mgr_repositories;
            length name createdBy modifiedBy $256. description $1000. defaultRepository 8.;
            set _mgr_rp.items(drop=ordinal_root ordinal_items version);
        run;
    %end;
    %else %do;
        %if &_mgr_nvar. gt 2 %then %do;
            proc append force nowarn base=work._mgr_repositories
                data=_mgr_rp.items(drop=ordinal_root ordinal_items version);
            quit;
        %end;
    %end;

    libname _mgr_rp clear;
    filename _mgr_rp clear;

    * Check if another iteration is needed;
    %if &_mgr_ds_nobs eq &_mgr_limit and &_mgr_nvar. gt 2 %then %do;
        %put NOTE: Getting more repositories;
        %let _mgr_new_start=%eval(&_mgr_start. + &_mgr_limit.);
        %_mgr_repository_pagination(_mgr_start=&_mgr_new_start., _mgr_limit=&_mgr_limit.);
    %end;
%mend _mgr_repository_pagination;

* Get a table of all repositories;
%_mgr_repository_pagination();

* Clean up;
%symdel _mgr_viyaHost;
%sysmacdelete _mgr_repository_pagination;