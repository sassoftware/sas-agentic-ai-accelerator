/*********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Save the prompt experiements to a project

    This script requires you to set the macro variables
      _spe_projectID, which can be retrieved using
      the script Get-List-of-available-LLMs.sas

    This script requires access you to have access to
      proc python and needs to have sasctl installed.

    The input table has to follow a the strucutre specified
      in the LLM-Call-Result-Table.sas script or the 
      LLM-Call-Combined-Result-Tables.sas if you ran
      multiple times.

    spe is short for:
      Save Prompt Experiements
*********************************************************************************/
* Provide the name of the LLM Repository in SAS Model Manager;
%let _spe_llm_repository = LLM Repository;
* Provide the name of the Prompt Project in SAS Model Manager;
%let _spe_llm_project = Prompt Project;
* Provide the name of the Prompt in SAS Model Manager;
%let _spe_llm_prompt = Prompt Experiement 1;
* Indicate if you want to have the Project created, if it doesn't exist;
%let _spe_llm_create_project = 1;
* Indicate if you want to have the Prompt created, if it doesn't exist;
%let _spe_llm_create_prompt = 1;
* Provide the name of the input table that contains all of your additional runs;
%let _spe_llm_runs = work._crc_result_table_combined;

* Get the Viya Host URL;
%let _spe_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

filename _spe_rID temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getRepositories;
proc http
    method='Get'
    url="&_spe_viyaHost./modelRepository/repositories?filter=eq(name,'&_spe_llm_repository.')"
    oauth_bearer=sas_services
    out=_spe_rID;
run; quit;

libname _spe_rID json;

* Retrieve the LLM Repository ID and save it to a macro variable;
proc sql noPrint;
    select id into :_spe_repo_id
        from _spe_rID.items;
run; quit;

* Clean up;
libname _spe_rID clear;
filename _spe_rID clear;

filename _spe_pID temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjects;
proc http
    method='Get'
    url="&_spe_viyaHost./modelRepository/projects?filter=and(eq(name,'&_spe_llm_project.'),eq(repositoryId,'&_spe_repo_id.'))"
    oauth_bearer=sas_services
    out=_spe_pID;
run; quit;

libname _spe_pID json;

* Check if the project already exists;
proc sql noPrint;
    select nvar into :_spe_nvar trimmed from dictionary.tables where
            libname='_SPE_PID' and memname='ITEMS';
run; quit;

%if &_spe_nvar. LE 2 AND &_spe_llm_create_project. EQ 1 %then %do;
    %put NOTE: The project &_spe_llm_project. does not exist, it will be created for you;
    filename _spe_pfi temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getRepositories;
    proc http
        method='Get'
        url="&_spe_viyaHost./modelRepository/repositories?filter=eq(name,'&_spe_llm_repository.')"
        oauth_bearer=sas_services
        out=_spe_pfi;
    run; quit;

    libname _spe_pfi json;

    * Retrieve the Model Repository Folder ID;
    proc sql noPrint;
        select folderId into :_spe_repo_folder_id
            from _spe_pfi.items;
    run; quit;

    * Clean up;
    libname _spe_pfi clear;
    filename _spe_pfi clear;

    filename _spe_cpi temp;

    * Create the project definition;
    data _null_;
        length _spe_cpi_text $32767.;
        file _spe_cpi;
        put '{';
        _spe_cpi_text = '"name": "' || "&_spe_llm_project." || '",';
        put _spe_cpi_text;
        put '"function": "prompt",';
        _spe_cpi_text = '"repositoryId": "' || "&_spe_llm_repository." || '",';
        put _spe_cpi_text;
        _spe_cpi_text = '"folderId": "' || "&_spe_repo_folder_id." || '",';
        put _spe_cpi_text;
        put '"tags": ["LLM", "Prompt-Engineering"]';
        put '}';
    run;

    filename _spe_cpo temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=createProject;
    proc http
        method='Post'
        url="&_spe_viyaHost./modelRepository/projects"
        oauth_bearer=sas_services
        in=_spe_cpi
        out=_spe_cpo;
        headers 'Content-Type'='application/json';
        headers 'Accept'='application/json';
    run; quit;

    libname _spe_cpo json;

    * Retrieve the ID of the newly created project;
    proc sql noPrint;
        select id into :_spe_project_id
            from _spe_cpo.root;
    run; quit;

    * Clean up;
    filename _spe_cpi clear;
    libname _spe_cpo clear;
    filename _spe_cpo clear;

    * Final clean up;
    %symdel _spe_repo_folder_id;
%end;

%if &_spe_nvar. LE 2 AND &_spe_llm_create_project. EQ 0 %then %do;
    data _null_;
        putLog "ERROR: The project &_spe_llm_project. does not exist and will not be created.";
        abort 42;
    run;
%end;

%if &_spe_nvar. GT 2 %then %do;
    proc sql noPrint;
        select id into: _spe_project_id
            from _spe_pID.items;
    run; quit;
%end;

* Clean up;
libname _spe_pID clear;
filename _spe_pID clear;
%symdel _spe_nvar;

filename _spe_mID temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModels;
proc http
    method='Get'
    url="&_spe_viyaHost./modelRepository/models?filter=and(eq(projectId,'&_spe_project_id.'),eq(name,'&_spe_llm_prompt.'))"
    oauth_bearer=sas_services
    out=_spe_mID;
run; quit;

libname _spe_mID json;

* Check if the model already exists;
proc sql noPrint;
    select nvar into :_spe_nvar trimmed from dictionary.tables where
            libname='_SPE_MID' and memname='ITEMS';
run; quit;

%if &_spe_nvar. LE 2 AND &_spe_llm_create_prompt. EQ 1 %then %do;
    %put NOTE: The prompt &_spe_llm_prompt. does not exist, it will be created for you;
    filename _spe_mfi temp;

    data _null_;
        length _spe_mfi_text $32767.;
        file _spe_mfi;
        put '{';
        _spe_mfi_text = '"name": "' || "&_spe_llm_prompt." || '",';
        put _spe_mfi_text;
        put '"function": "Prompting",';
        put '"tool": "Prompt-Builder",';
        _spe_mfi_text = '"modelere": "' || "&SYSUSERNAME." || '",';;
        put _spe_mfi_text;
        _spe_mfi_text = '"projectId": "' || "&_spe_project_id." || '",';
        put _spe_mfi_text;
        put '"algorithm": "Prompt-Template",';
        put '"tags": ["LLM", "Prompt-Template"]';
        put '}';
    run;

    filename _spe_mfo temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=createModel;
    proc http
        method='Post'
        url="&_spe_viyaHost./modelRepository/models"
        oauth_bearer=sas_services
        in=_spe_mfi
        out=_spe_mfo;
        headers 'Content-Type'='application/vnd.sas.models.model+json';
        headers 'Accept'='application/json';
    run; quit;

    libname _spe_mfo json;

    proc sql noPrint;
        select id into :_spe_model_id
            from _spe_mfo.items;
    run; quit;

    * Clean up;
    filename _spe_mfi clear;
    libname _spe_mfo clear;
    filename _spe_mfo clear;
%end;

%if &_spe_nvar. LE 2 AND &_spe_llm_create_prompt. EQ 0 %then %do;
    data _null_;
        putLog "ERROR: The prompt &_spe_llm_prompt. does not exist and will not be created.";
        abort 42;
    run;
%end;

%if &_spe_nvar. GT 2 %then %do;
    proc sql noPrint;
        select id into: _spe_model_id
            from _spe_mID.items;
    run; quit;
%end;

* Clean up;
libname _spe_mID clear;
filename _spe_mID clear;
%symdel _spe_nvar;

* Check if a Prompt Experiment Tracker already exists;
filename _spe_mci temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModelContents;
proc http
    method='Get'
    url="&_spe_viyaHost./modelRepository/models/&_spe_model_id./contents?filter=eq(name,'Prompt-Experiment-Tracker.json')"
    oauth_bearer=sas_services
    out=_spe_mci;
run; quit;

libname _spe_mci json;

* Check if the Prompt Experiement Tracker already exists;
proc sql noPrint;
    select nvar into :_spe_nvar trimmed from dictionary.tables where
            libname='_SPE_MCI' and memname='ITEMS';
run; quit;

%if &_spe_nvar. LE 2 %then %do;
    %put NOTE: No Prompt-Experiement-Tracker exists, a new one will be created;

    filename _spe_jti "&_SASWORKINGDIR./Prompt-Example-Tracker.json";

    proc json out=_spe_jti pretty;
        export &_spe_llm_runs. / noSASTags;
    run; quit;

    * Create the empty prompt experiement tracker using sasctl;
    proc python restart;
        submit;
import os
from sasctl import Session
from sasctl.services import model_repository as mr

server = SAS.symget('_spe_viyaHost')
token = os.environ['SAS_SERVICES_TOKEN']
model_id = SAS.symget('_spe_model_id')

file_name = 'Prompt-Example-Tracker.json'
file_path = SAS.workpath + file_name

with Session(server, token=token) as s:
    file = open(file_path, 'rb')
    res =mr.add_model_content(model_id,
                         file, 
                         name = file_name)
    file.close()
        endsubmit;
    run; quit;

    proc python terminate;
    run; quit;

    * Clean up;
    data _null_;
        rc = fDelete("_spe_jti");
    run;
    filename _spe_jti clear;
%end;
%else %do;
    * Retrieve the ID of the Prompt-Experiment-Tracker.json;
    proc sql noPrint;
        select id into :_spe_pet_id
            from _spe_mci.items;
    run; quit;

    filename _spe_mcc temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModelContent;
    proc http
        method='Get'
        url="&_spe_viyaHost./modelRepository/models/&_spe_model_id./contents/&_spe_pet_id./content"
        oauth_bearer=sas_services
        out=_spe_mcc;
    run; quit;

    libname _spe_mcc json;

    data work._spe_pet_base_table;
        length runId 8. systemPrompt userPrompt $32767. model $100. options $1000. response $32767. run_time prompt_length output_length best_prompt 8.;
        set _spe_mcc.root(drop=ordinal_root);
    run;

    proc sql noPrint;
        select max(runId) into :_spe_mri
            from work._spe_pet_base_table;
    run; quit;

    data work._spe_pet_append_table;
        set &_spe_llm_runs.;

        runId = runId + &_spe_mri.;
    run;

    proc append base=work._spe_pet_base_table data=work._spe_pet_append_table;
    run; quit;

    proc http
        method='Delete'
        url="&_spe_viyaHost./modelRepository/models/&_spe_model_id./contents/&_spe_pet_id."
        oauth_bearer=sas_services;
    run; quit;

    filename _spe_jti "&_SASWORKINGDIR./Prompt-Example-Tracker.json";

    proc json out=_spe_jti pretty;
        export work._spe_pet_base_table / noSASTags;
    run; quit;

    * Create the empty prompt experiement tracker using sasctl;
    proc python restart;
        submit;
import os
from sasctl import Session
from sasctl.services import model_repository as mr

server = SAS.symget('_spe_viyaHost')
token = os.environ['SAS_SERVICES_TOKEN']
model_id = SAS.symget('_spe_model_id')

file_name = 'Prompt-Example-Tracker.json'
file_path = SAS.workpath + file_name

with Session(server, token=token) as s:
    file = open(file_path, 'rb')
    res =mr.add_model_content(model_id,
                         file, 
                         name = file_name)
    file.close()
        endsubmit;
    run; quit;

    proc python terminate;
    run; quit;

    * Clean up;
    %symdel _spe_pet_id _spe_mri;
    libname _spe_mcc clear;
    filename _spe_mcc clear;
    proc datasets lib=work noList;
        delete _spe_pet_base_table _spe_pet_append_table;
    run; quit;
    data _null_;
        rc = fDelete("_spe_jti");
    run;
    filename _spe_jti clear;
%end;

* Clean up;
libname _spe_mci clear;
filename _spe_mci clear;
%symdel _spe_nvar;

data _null_;
    putLog "NOTE: Find the mode using this link &_spe_viyaHost./SASModelManager/models/&_spe_model_id./files";
    putlog "NOTE: Or navigate to SAS Model Manager > Projects > &_spe_llm_project. > &_spe_llm_prompt.";
run;

* Final clean up;
%symdel _spe_llm_repository _spe_llm_project _spe_llm_prompt _spe_llm_create_project _spe_llm_create_prompt _spe_viyaHost _spe_repo_id _spe_project_id _spe_model_id;