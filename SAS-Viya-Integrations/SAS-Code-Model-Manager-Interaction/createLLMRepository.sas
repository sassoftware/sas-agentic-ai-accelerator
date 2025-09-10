/**********************************************************************************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    This code is used to create the repository which will store all information about LLMs and Use Cases
    inside of SAS Model Manager.

    To learn more about SAS Model Manager repositories, take a look at the SAS Documentation:
    https://go.documentation.sas.com/doc/en/mdlmgrcdc/default/mdlmgrug/n0ic5o7hdfphxun1vtgvj5fx0k58.htm

    You can also create the repository using the SAS Model Manager UI, as described in the SAS Documentation.

    This code is designed to be run using SAS Studio or the SAS extension for Visual Studio Code.
    Note you have to be a SAS Administrator in order to run this code or be part of a Model Repository
    administrative group, as described in the SAS Documention (even then you also need the ability to
    change the rules on that repository):
    https://go.documentation.sas.com/doc/en/sasadmincdc/default/calmodels/n1hl4mghwofk6qn1mdih1aezgbtl.htm#p1gv4evkxxg0b6n1g01btzibwqz6

    This script adds two rules one for access to the SAS Model Manager repository and second to the folder in which it is stored.

    This script uses SAS Viya API endpoints to perform its taks:
    - createRepository - https://developers.sas.com/rest-apis/modelRepository-v8?operation=createRepository
    - createRule - https://developers.sas.com/rest-apis/authorization-v8?operation=createRule
**********************************************************************************************************************************************/

* Specify a name for the model repository;
%let repositoryName = LLM Repository;
* Specify a description for the model repository - Optional;
%let repositoryDescription = This repository is used to register LLM deployment instructions to, build, monitor and deploy use cases that take advantage of LLMs;
* Specify if this should be the default repository - Valid values: True|False;
%let defaultRepository = false;
* Specify the principal name - note this is listed as the ID in SAS Environment Manager;
%let principal = SASAdministrators;
* Specify the principal type - Valid values: user, group, authenticatedUsers everyone guest;
%let principalType = group;
* Specify a rule description to help with rule understanding;
%let ruleDescription = Enables access to the LLM Repository in SAS Model Manager;

* Get the Viya Host URL;
%let viyaHost=%sysfunc(getoption(SERVICESBASEURL));

* Create the input data for the request;
filename rpstryIn temp;

data _null_;
    file rpstryIn;
    put '{';
    repositoryName = '"name": "' || "&repositoryName." || '",';
    put repositoryName;
    repositoryDescription = '"description": "' || "&repositoryDescription." || '",';
    put repositoryDescription;
    defaultRepository = '"defaultRepository": ' || "&defaultRepository.";
    put defaultRepository;
    put '}';
run;

* Create a temporary output file;
filename rpstryOt temp;

* Call the /modelRepository/repositories endpoint;
proc http
    method='Post'
    url="&viyaHost./modelRepository/repositories"
    oauth_bearer = sas_services
    in=rpstryIn
    out=rpstryOt;
    headers 'Accept' = 'application/json';
    headers 'Content-Type' = 'application/vnd.sas.models.repository+json';
run;

libname rpstryOt json;

title 'Information about the Created Repository';
proc print data=rpstryOt.root(keep=id name description) noObs;
run; quit;
title;

* Save the model repository ID to a macro variable;
* This value is needed for the configuration of the SAS Prompt Engineering object;
proc sql noPrint;
    select id into :repositoryID
        from rpstryOt.root;

    select folderId into :folderID
        from rpstryOt.root;
run; quit;

%put &=repositoryID &=folderID;

* Clean up;
filename rpstryIn clear;
libname rpstryOt clear;
filename rpstryOt clear;
%symdel repositoryName repositoryDescription defaultRepository;

* Create the rule that enables access to the repository;
* Create the input data for the request;
filename rlIn temp;

data _null_;
    file rlIn;
    put '{';
    put '"type": "grant",';
    put '"permissions": ["read", "update", "delete"],';
    principal = '"principal": "' || "&principal." || '",';
    put principal;
    principalType = '"principalType": "' || "&principalType." || '",';
    put principalType;
    objectURI = '"objectUri": "/modelRepository/repositories/' ||  "&repositoryID." || '",';
    put objectURI;
    ruleDescription = '"description": "' || "&ruleDescription." || '"';
    put ruleDescription;
    put '}';
run;

* Create a temporary output file;
filename rlOut temp;

* Call the /authorization/rules endpoint;
proc http
    method='Post'
    url="&viyaHost./authorization/rules"
    oauth_bearer = sas_services
    in=rlIn
    out=rlOut;
    headers 'Accept' = 'application/json';
    headers 'Content-Type' = 'application/json';
run;

libname rlOut json;

title 'Information about the Created Repository Rule';
proc print data=rlOut.root(drop=version ordinal_root) noObs;
run; quit;
title;

* Clean up;
filename rlIn clear;
libname rlOut clear;
filename rlOut clear;

* Create the rule that enables access to the repository folder;
* Create the input data for the request;
filename rlIn temp;

data _null_;
    file rlIn;
    put '{';
    put '"type": "grant",';
    put '"permissions": ["read", "update", "delete", "add", "remove"],';
    principal = '"principal": "' || "&principal." || '",';
    put principal;
    principalType = '"principalType": "' || "&principalType." || '",';
    put principalType;
    objectURI = '"objectUri": "/folders/folders/' ||  "&folderID." || '",';
    put objectURI;
    containerURI = '"containerUri": "/folders/folders/' || "&folderID." || '",';
    put containerURI;
    ruleDescription = '"description": "' || "&ruleDescription." || '"';
    put ruleDescription;
    put '}';
run;

* Create a temporary output file;
filename rlOut temp;

* Call the /authorization/rules endpoint;
proc http
    method='Post'
    url="&viyaHost./authorization/rules"
    oauth_bearer = sas_services
    in=rlIn
    out=rlOut;
    headers 'Accept' = 'application/json';
    headers 'Content-Type' = 'application/json';
run;

libname rlOut json;

title 'Information about the Created Folder Rule';
proc print data=rlOut.root(drop=version ordinal_root) noObs;
run; quit;
title;

* Clean up;
filename rlIn clear;
libname rlOut clear;
filename rlOut clear;
%symdel principal principalType;

* Final clean up;
%symdel viyaHost repositoryID folderID;