/***************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get Model Information from a model in a SAS Model Manager

    This script requires you to set the macro variable
      _mgi_model_id which is the ID of a SAS Model Manager
      model. To retrieve the ID you can use the script
      MM-Get-Models-in-Project.sas.

    The output tables are called:
      work._mgi_baseInformation
      work._mgi_files
      work._mgi_inputVariables
      work._mgi_outputVariables
      work._mgi_modelVersions
      work._mgi_publishedModels

    A part the model does not have gives no table, and the log
      says so in a NOTE: a model that was never published has
      no published models. The tables of an earlier run are
      deleted first.

    _mgi_ is short for:
      Model Manager Get Model Information
***************************************************************************/
* Set the SAS Model Manager model ID;
%let _mgi_model_id = ;

* Get the Viya Host URL;
%let _mgi_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

* The six tables are deleted first, so that no table of an earlier run stands beside the tables of this model;
proc datasets library=work noList noWarn;
    delete _mgi_baseInformation _mgi_files _mgi_inputVariables _mgi_outputVariables _mgi_modelVersions _mgi_publishedModels;
run; quit;

* Get the model and write a table of each part SAS Model Manager returned;
%macro _mgi_get_information;
    %local _mgi_vars _mgi_part _mgi_i;
    %if %length(%superq(_mgi_model_id)) EQ 0 %then %do;
        %put ERROR: MM-Get-Models-Information.sas: no table was written, because no model ID was chosen - the program needs the ID of a model in _mgi_model_id.;
        %let SYSCC = %sysfunc(max(&SYSCC., 1012));
        %return;
    %end;

    * The status of the answer is emptied first in the global symbol table, where PROC HTTP writes it: it keeps the status of an earlier request where PROC HTTP gets no answer;
    data _null_;
        call symputx('SYS_PROCHTTP_STATUS_CODE', ' ', 'G');
    run;
    filename _mgi_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getModel;
    proc http method = 'Get'
        url = "&_mgi_viyaHost./modelRepository/models/&_mgi_model_id."
        oauth_bearer = sas_services
        out = _mgi_rp;
        headers 'Accept' = 'application/json';
    quit;

    %if "&SYS_PROCHTTP_STATUS_CODE." NE "200" %then %do;
        filename _mgi_rp clear;
        %put ERROR: MM-Get-Models-Information.sas: no table was written, because SAS Model Manager did not answer the request for the model %superq(_mgi_model_id) with HTTP 200 (status: %superq(SYS_PROCHTTP_STATUS_CODE)).;
        %let SYSCC = %sysfunc(max(&SYSCC., 1012));
        %return;
    %end;

    libname _mgi_rp json;

    * One row per property of the model. A model holds only some of the properties of the list - an LLM of the accelerator has no trainTable, targetEvent, candidateChampion, externalModelId or sasOptions, and a model that was never published has no publishTimeStamp - so the list is cut to the properties the model has, in its order;
    data _null_;
        length _mgi_list _mgi_have $1000. _mgi_name $32.;
        _mgi_list = 'creationTimeStamp createdBy modifiedTimeStamp modifiedBy id name role scoreCodeType algorithm function modeler modelType trainTable eventProbVar targetVariable targetEvent targetLevel tool toolVersion candidateChampion publishTimeStamp externalModelId indirectFolderId repositoryId projectId projectVersionId immutable folderRef location modelVersionName projectName projectVersionName projectVersionNum modelLocks version nondeterministic sasOptions';
        _mgi_dsid = open('_mgi_rp.root');
        if _mgi_dsid GT 0 then do;
            do _mgi_i = 1 to countw(_mgi_list, ' ');
                _mgi_name = scan(_mgi_list, _mgi_i, ' ');
                if varnum(_mgi_dsid, _mgi_name) GT 0 then _mgi_have = catx(' ', _mgi_have, _mgi_name);
            end;
            _mgi_rc = close(_mgi_dsid);
        end;
        call symputx('_mgi_vars', _mgi_have, 'L');
    run;

    %if %length(&_mgi_vars.) GT 0 %then %do;
        proc transpose data=_mgi_rp.root out=work._mgi_baseInformation;
            var &_mgi_vars.;
        run; quit;
    %end;
    %else %put WARNING: MM-Get-Models-Information.sas: the answer of SAS Model Manager for the model %superq(_mgi_model_id) holds none of the properties of a model, so the table work._mgi_baseInformation was not written.;

    * A part the model does not have is no table of the answer - publishedModels of a model that was never published - and its table is not written;
    %do _mgi_i = 1 %to 5;
        %let _mgi_part = %scan(files inputVariables outputVariables modelVersions publishedModels, &_mgi_i.);
        %if %sysfunc(exist(_mgi_rp.&_mgi_part.)) %then %do;
            data work._mgi_&_mgi_part.;
                set _mgi_rp.&_mgi_part.;
            run;
        %end;
        %else %put NOTE: MM-Get-Models-Information.sas: the model %superq(_mgi_model_id) has no &_mgi_part. in SAS Model Manager, so the table work._mgi_&_mgi_part. was not written.;
    %end;

    libname _mgi_rp clear;
    filename _mgi_rp clear;
%mend _mgi_get_information;
%_mgi_get_information;
%sysmacdelete _mgi_get_information;

* Clean up;
%symdel _mgi_model_id _mgi_viyaHost / nowarn;