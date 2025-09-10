/***************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get Model Information from a model in a SAS Model Manager

    This script requires you to set the macro variable
      _mgi_model_id which is the ID of a SAS Model Manager
      project. To retrieve the ID you can use the script
      MM-Get-Models-in-Project.sas.

    The output tables are called:
      work._mgi_baseInformation
      work._mgi_files
      work._mgi_inputVariables
      work._mgi_outputVariables
      work._mgi_modelVersions
      work._mgi_publishedModels

    _mgi_ is short for:
      Model Manager Get Model Information
***************************************************************************/
* Set the SAS Model Manager project ID;
%let _mgi_model_id = 8ea896fd-1508-4424-a2e7-8096ca319050;

* Get the Viya Host URL;
%let _mgi_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

filename _mgi_rp temp;

* https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModel;
proc http method = 'Get'
    url = "&_mgi_viyaHost./modelRepository/models/&_mgi_model_id."
    oauth_bearer = sas_services
    out = _mgi_rp;
    headers 'Accept' = 'application/json';
quit;

libname _mgi_rp json;

proc transpose data=_mgi_rp.root out=work._mgi_baseInformation;
    var creationTimeStamp createdBy modifiedTimeStamp modifiedBy id 'name'n 'role'n scoreCodeType algorithm 'function'n modeler modelType trainTable eventProbVar targetVariable targetEvent targetLevel tool toolVersion candidateChampion publishTimeStamp externalModelId indirectFolderId repositoryId projectId projectVersionId 'immutable'n folderRef 'location'n modelVersionName projectName projectVersionName projectVersionNum modelLocks version nondeterministic sasOptions;
run; quit;

data work._mgi_files;
    set _mgi_rp.files;
run;

data work._mgi_inputVariables;
    set _mgi_rp.inputVariables;
run;

data work._mgi_outputVariables;
    set _mgi_rp.outputVariables;
run;

data work._mgi_outputVariables;
    set _mgi_rp.outputVariables;
run;

data work._mgi_modelVersions;
    set _mgi_rp.modelVersions;
run;

data work._mgi_publishedModels;
    set _mgi_rp.publishedModels;
run;

* Clean up;
libname _mgi_rp clear;
filename _mgi_rp clear;
%symdel _mgi_model_id _mgi_viyaHost;