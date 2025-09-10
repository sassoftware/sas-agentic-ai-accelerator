/*********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get a table of the Prompt Experiements

    This script requires you to set the following macro
      variables, the defaults match the guided setup defaults:
      - _gpe_llm_repository
      - _gpe_llm_project
      - _gpe_llm_project
      - _gpe_llm_pte

    The output table is called:
      work._gpe_models

    gpe is short for:
      SAS Large Language Model Listing
*********************************************************************************/
* Provide the name of the LLM Repository in SAS Model Manager;
%let _gpe_llm_repository = LLM Repository;
* Provide the name of the Prompt Project in SAS Model Manager;
%let _gpe_llm_project = Prompt Project;
* Provide the name of the Prompt in SAS Model Manager;
%let _gpe_llm_prompt = Prompt Experiement 1;
* Specify an output table for the prompt experiements;
%let _gpe_llm_pte = work._gpe_prompt_experiments;

* Get the Viya Host URL;
%let _gpe_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

filename _gpe_rep temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getRepositories;
proc http
    method='Get'
    url="&_gpe_viyaHost./modelRepository/repositories?filter=eq(name,'&_gpe_llm_repository')"
    oauth_bearer=sas_services
    out=_gpe_rep;
run; quit;

libname _gpe_rep json;

* Retrieve the repository ID;
proc sql noPrint;
    select id into :_gpe_repositoryID
        from _gpe_rep.items;
run; quit;

* Clean up;
libname _gpe_rep clear;
filename _gpe_rep clear;

filename _gpe_pro temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjects;
proc http
    method='Get'
    url="&_gpe_viyaHost./modelRepository/projects?filter=and(eq(name,'&_gpe_llm_project.'),eq(repositoryId,'&_gpe_repositoryID.'))"
    oauth_bearer=sas_services
    out=_gpe_pro;
run; quit;

libname _gpe_pro json;

* Check that the project already exists;
proc sql noPrint;
    select nvar into :_gpe_nvar trimmed from dictionary.tables where
            libname='_GPE_PRO' and memname='ITEMS';
run; quit;

%if &_gpe_nvar. LE 2 %then %do;
    data _null_;
        putLog "ERROR: There is no project with the name &_gpe_llm_project. in the &_gpe_llm_repository. repository.";
        abort 42;
    run;
%end;

* Retrieve the project ID;
proc sql noPrint;
    select id into :_gpe_projectID
        from _gpe_pro.items;
run; quit;

* Clean up;
%symdel _gpe_nvar;
libname _gpe_pro clear;
filename _gpe_pro clear;

filename _gpe_mID temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModels;
proc http
    method='Get'
    url="&_gpe_viyaHost./modelRepository/models?filter=and(eq(projectId,'&_gpe_projectID.'),eq(name,'&_gpe_llm_prompt.'))"
    oauth_bearer=sas_services
    out=_gpe_mID;
run; quit;

libname _gpe_mID json;

* Check if the model already exists;
proc sql noPrint;
    select nvar into :_gpe_nvar trimmed from dictionary.tables where
            libname='_GPE_MID' and memname='ITEMS';
run; quit;

%if &_gpe_nvar. LE 2 %then %do;
    data _null_;
        putLog "ERROR: There is no prompt with the name &_gpe_llm_prompt. in the project &_gpe_llm_project..";
        abort 42;
    run;
%end;

proc sql noPrint;
    select id into: _gpe_model_id
        from _gpe_mID.items;
run; quit;

* Clean up;
%symdel _gpe_nvar;
libname _gpe_mID clear;
filename _gpe_mID clear;

* Check if a Prompt Experiment Tracker already exists;
filename _gpe_mci temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModelContents;
proc http
    method='Get'
    url="&_gpe_viyaHost./modelRepository/models/&_gpe_model_id./contents?filter=eq(name,'Prompt-Experiment-Tracker.json')"
    oauth_bearer=sas_services
    out=_gpe_mci;
run; quit;

libname _gpe_mci json;

* Check if the Prompt Experiement Tracker already exists;
proc sql noPrint;
    select nvar into :_gpe_nvar trimmed from dictionary.tables where
            libname='_GPE_MCI' and memname='ITEMS';
run; quit;

%if &_gpe_nvar. LE 2 %then %do;
    data _null_;
        putLog "ERROR: There are no prompt experiemnts available for &_gpe_llm_prompt..";
        abort 42;
    run;
%end;

* Retrieve the ID of the Prompt-Experiment-Tracker.json;
proc sql noPrint;
    select id into :_gpe_pet_id
        from _gpe_mci.items;
run; quit;

* Clean up;
%symdel _gpe_nvar;
libname _gpe_mci clear;
filename _gpe_mci clear;

filename _gpe_mcc temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModelContent;
proc http
    method='Get'
    url="&_gpe_viyaHost./modelRepository/models/&_gpe_model_id./contents/&_gpe_pet_id./content"
    oauth_bearer=sas_services
    out=_gpe_mcc;
run; quit;

libname _gpe_mcc json;

data &_gpe_llm_pte.;
    length runId 8. systemPrompt userPrompt $32767. model $100. options $1000. response $32767. run_time prompt_length output_length best_prompt 8.;
    set _gpe_mcc.root(drop=ordinal_root);
run;

title 'Prompt Experiment Tracker for';
title2 "&_gpe_llm_prompt. in the project &_gpe_llm_project.";
proc print data=&_gpe_llm_pte. noObs;
run; quit;
title;

* Clean up;
libname _gpe_mcc clear;
filename _gpe_mcc clear;

* Final clean up;
%symdel _gpe_llm_repository _gpe_llm_project _gpe_llm_prompt _gpe_llm_pte _gpe_viyaHost _gpe_repositoryID _gpe_projectID _gpe_model_id _gpe_pet_id;