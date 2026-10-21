/*************************************************************************************************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    This script creates a new SAS Intelligent Decisioning node that can be used for Drag & Drop LLM integration.

    _dnt is short for decision node type

    The node is created under one fixed id, f9efcbb7-f57b-4a62-b56d-1a89595e2039. A decision references a custom node by
    its id and not by its name, and the transfer package of the content folder carries the node under this id: every
    site that has the node under it can import the package and decisions built elsewhere.

    The script creates nothing in two cases and says why in the log:
      - The node is already there under this id. Run Update-Custom-SAS-Intelligent-Decisioning-Node.sas to bring its
        content to the one of this release.
      - A node named Call LLM is already there under another id. Node names are unique, so a second one can not be
        created. Run the migration mdb decision-node-migrate (first as a dry run): it renames that node, creates the node
        under this id and moves every decision that uses it, locked versions included. See the section The Call LLM node
        and its id of the page Deployment of Decisions.
    The script never sends the create request for an id that is already there: SAS Viya answers such a request with an
    error, but renames the node of that id to the name of the request before it does.

    You can find the documentation for all of these API endpoints in the DecisionNodeTypes section:
    https://developers.sas.com/rest-apis/decisions

    There is also information available in the SAS Documentation:
    https://go.documentation.sas.com/doc/en/edmcdc/default/edmcustnodes/titlepage.htm

    If you want to learn more about the different styling options check out:
    https://go.documentation.sas.com/doc/en/edmcdc/default/edmcustnodes/p0otn4brmksbk8n1emshmurplrp4.htm

    How an existing site moves its node to the fixed id:
    https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Deployment-of-Decisions#call-llm-node-uuid
*************************************************************************************************************************************************************/

* Set the node definition;
%let _dntNodeName = Call LLM;
* The id of the node. Decisions reference the node by this id and the transfer package carries it, so keep it as it is;
%let _dntNodeID = f9efcbb7-f57b-4a62-b56d-1a89595e2039;
%let _dntDescription = Enables you to call an LLM that is wrapped in the SAS Agentic AI Accelerator template;
%let _dntHasProperties = true;
%let _dntHasInputs = true;
%let _dntHasOutputs = true;
%let _dntInputDatagridMappable = false;
%let _dntOutputDatagridMappable = false;
%let _dntInputDecisionTermMappable = true;
%let _dntOutputDecisionTermMappable = true;
%let _dntIndependentMappings = false;
%let _dntThemeId = DNT_THEME1;
%let _dntType = static;

%let _dntviyaHost=%sysfunc(getoption(SERVICESBASEURL));

filename _dntRsp temp;

* Get a list of all decision node types;
* https://developer.sas.com/rest-apis/decisions/getDecisionNodeTypes;
proc http url = "&_dntviyaHost./decisions/decisionNodeTypes?limit=100"
    method = 'Get'
    out = _dntRsp
    oauth_bearer = sas_services;
    headers 'Accept' = 'application/json';
run;

libname _dntRsp json;

proc print data=_dntRsp.items(drop=ordinal_root ordinal_items) noobs;
quit;

libname _dntRsp clear;
filename _dntRsp clear;

* What the checks below find: the status of the request for the id, the name of the node under the id, the ids of a node of the name;
%let _dntIdStatus = ;
%let _dntIdName = ;
%let _dntNameIDs = ;
%let _dntNameFilter = ;

%macro _dntCreateNode;
    filename _dntRsp temp;

    * Look for the node under its id;
    * https://developer.sas.com/rest-apis/decisions/getDecisionNodeType;
    proc http url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntNodeID."
        method = 'Get'
        out = _dntRsp
        oauth_bearer = sas_services;
        headers 'Accept' = 'application/json';
    run;

    %let _dntIdStatus = &SYS_PROCHTTP_STATUS_CODE.;

    * The node is already there: nothing is created;
    %if &_dntIdStatus. EQ 200 %then %do;
        libname _dntRsp json;
        data _null_;
            set _dntRsp.allData(keep=P P1 Value where=(P eq 1 and P1 eq 'name'));
            call symputx('_dntIdName', Value);
        run;
        libname _dntRsp clear;
        filename _dntRsp clear;
        %put NOTE: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: the node &_dntIdName. is already there under the id &_dntNodeID.. Nothing was created. To bring its content to the one of this release, run Update-Custom-SAS-Intelligent-Decisioning-Node.sas.;
        %return;
    %end;

    filename _dntRsp clear;

    * Any other answer than 404 leaves open whether the id is taken: nothing is created;
    %if &_dntIdStatus. NE 404 %then %do;
        %put ERROR: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was created, because SAS Viya did not answer the request for the node type &_dntNodeID. with 200 or 404 (status: &_dntIdStatus.).;
        %return;
    %end;

    * Look for a node of the same name under another id. Node names are unique;
    * https://developer.sas.com/rest-apis/decisions/getDecisionNodeTypes;
    data _null_;
        call symputx('_dntNameFilter', urlencode("eq(name,'&_dntNodeName.')"));
    run;

    filename _dntRsp temp;

    proc http url = "&_dntviyaHost./decisions/decisionNodeTypes?filter=&_dntNameFilter."
        method = 'Get'
        out = _dntRsp
        oauth_bearer = sas_services;
        headers 'Accept' = 'application/json';
    run;

    %if &SYS_PROCHTTP_STATUS_CODE. NE 200 %then %do;
        filename _dntRsp clear;
        %put ERROR: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was created, because SAS Viya did not answer the request for the node types named &_dntNodeName. with 200 (status: &SYS_PROCHTTP_STATUS_CODE.).;
        %return;
    %end;

    libname _dntRsp json;

    data _null_;
        set _dntRsp.allData(keep=P P1 P2 Value where=(P eq 2 and P1 eq 'items' and P2 eq 'id'));
        length ids $ 1000;
        retain ids;
        ids = catx(' ', ids, Value);
        call symputx('_dntNameIDs', ids);
    run;

    libname _dntRsp clear;
    filename _dntRsp clear;

    * The node of this site is under another id: the migration moves it, this script creates nothing;
    %if %length(&_dntNameIDs.) GT 0 %then %do;
        %put ERROR: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was created, because a node named &_dntNodeName. is already there under the id &_dntNameIDs. and not under the id &_dntNodeID. that the accelerator ships. Node names are unique.;
        %put ERROR: Run the migration mdb decision-node-migrate, first as a dry run: it renames that node, creates the node under the id &_dntNodeID. and moves every decision that uses it, locked versions included. Do not delete that node: every version of every decision that uses it would break.;
        %put ERROR: See The Call LLM node and its id: https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Deployment-of-Decisions#call-llm-node-uuid;
        %return;
    %end;

    filename _dntRsp temp;
    filename _dntIn temp;

    * Define the new node type, under its id;
    data _null_;
        file _dntIn;
        put '{';
        id = '"id": "' || "&_dntNodeID." || '",';
        put id;
        name = '"name": "' || "&_dntNodeName." || '",';
        put name;
        description = '"description": "' || "&_dntDescription." || '",';
        put description;
        hasProperties = '"hasProperties": ' || "&_dntHasProperties." || ',';
        put hasProperties;
        hasInputs = '"hasInputs": ' || "&_dntHasInputs." || ',';
        put hasInputs;
        hasOutputs = '"hasOutputs": ' || "&_dntHasOutputs." || ',';
        put hasOutputs;
        inputDatagridMappable = '"inputDatagridMappable": ' || "&_dntInputDatagridMappable." || ',';
        put inputDatagridMappable;
        outputDatagridMappable = '"outputDatagridMappable": ' || "&_dntOutputDatagridMappable." || ',';
        put outputDatagridMappable;
        inputDecisionTermMappable = '"inputDecisionTermMappable": ' || "&_dntInputDecisionTermMappable." || ',';
        put inputDecisionTermMappable;
        outputDecisionTermMappable = '"outputDecisionTermMappable": ' || "&_dntOutputDecisionTermMappable." || ',';
        put outputDecisionTermMappable;
        independentMappings = '"independentMappings": ' || "&_dntIndependentMappings." || ',';
        put independentMappings;
        themeId = '"themeId": "' || "&_dntThemeId." || '",';
        put themeId;
        type = '"type": "' || "&_dntType." || '"';
        put type;
        put '}';
    run;

    * Create a new node type. The id of the request is kept;
    * https://developer.sas.com/rest-apis/decisions/createDecisionNodeType;
    proc http url = "&_dntviyaHost./decisions/decisionNodeTypes"
        method = 'Post'
        in = _dntIn
        out = _dntRsp
        oauth_bearer = sas_services;
        headers 'Accept' = 'application/vnd.sas.decision.node.type+json, application/json, application/vnd.sas.error+json';
        headers 'Content-Type' = 'application/vnd.sas.decision.node.type+json';
    quit;

    filename _dntRsp clear;
    filename _dntIn clear;

    %if &SYS_PROCHTTP_STATUS_CODE. NE 201 %then %do;
        %put ERROR: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: SAS Viya did not create the node &_dntNodeName. under the id &_dntNodeID. (status: &SYS_PROCHTTP_STATUS_CODE.).;
        %return;
    %end;

    filename _dntIn temp;
    filename _dntRsp temp;

    * Create the content for the node type;
    data _null_;
        file _dntIn;
        put '{';
        put '"contentType": "DS2",';
        put '"staticContent": "package \"${PACKAGE_NAME}\" /inline;\n    dcl package http webreq();\n    dcl package json j();\n    dcl varchar(10485760) token;\n    dcl varchar(10485760) character set utf8 response;\n    dcl int rc status tokenType parseFlags;\n   method execute(varchar(512) llmURL,\n                  varchar(10485760) llmBody,\n                  in_out varchar llmGenerated,\n                  in_out double output_length,\n                  in_out double prompt_length);\n        webreq.createPostMethod(\"llmURL\");\n        webreq.setRequestBodyAsString(\"llmBody\");\n        webreq.setRequestContentType(''application/json'');\n        webreq.executeMethod();\n        status = webreq.getStatusCode();\n        if (status eq 200) then do;\n            webreq.getResponseBodyAsString(response, rc);\n             rc = j.createParser(response);\n            do while (rc = 0);\n                j.getNextToken(rc, token, tokenType, parseFlags);\n                if(token eq ''response'') then do;\n                    j.getNextToken(rc, token, tokenType, parseFlags);\n                    \"llmGenerated\" = token;\n                end;\n                else if(token eq ''output_length'') then do;\n                    j.getNextToken(rc, token, tokenType, parseFlags);\n                    \"output_length\" = token;\n                end;\n                else if(token eq ''prompt_length'') then do;\n                    j.getNextToken(rc, token, tokenType, parseFlags);\n                    \"prompt_length\" = token;\n                end;\n            end;\n            rc = j.destroyParser();\n        end;\n        else do;\n            \"llmGenerated\" = ''EMPTY'';\n            \"output_length\" = 0;\n            \"prompt_length\" = 0;\n        end;\n    end;\nendpackage;",';
        put '"nodeTypeSignatureTerms": [';
        put '{"name": "llmBody", "dataType": "string", "length": 10485760, "defaultValue": "", "direction": "input", "generateDataGridColumns": false},';
        put '{"name": "llmURL", "dataType": "string", "length": 512, "defaultValue": "", "direction": "input", "generateDataGridColumns": false},';
        put '{"name": "llmGenerated", "dataType": "string", "length": 10485760, "defaultValue": "", "direction": "output", "generateDataGridColumns": false},';
        put '{"name": "output_length", "dataType": "decimal", "defaultValue": "", "direction": "output", "generateDataGridColumns": false},';
        put '{"name": "prompt_length", "dataType": "decimal", "defaultValue": "", "direction": "output", "generateDataGridColumns": false}';
        put ']';
        put '}';
    run;

    * Add the content to the node type;
    * https://developer.sas.com/rest-apis/decisions/createDecisionNodeTypeContent;
    proc http url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntNodeID./content"
        method = 'Post'
        in = _dntIn
        out = _dntRsp
        oauth_bearer = sas_services;
        headers 'Accept' = 'application/vnd.sas.decision.node.type.content+json, application/json, application/vnd.sas.error+json';
        headers 'Content-Type' = 'application/vnd.sas.decision.node.type.content+json';
    quit;

    filename _dntRsp clear;
    filename _dntIn clear;

    %if &SYS_PROCHTTP_STATUS_CODE. NE 201 %then %do;
        %put ERROR: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: the node &_dntNodeName. was created under the id &_dntNodeID., and SAS Viya did not add its content (status: &SYS_PROCHTTP_STATUS_CODE.). Run Update-Custom-SAS-Intelligent-Decisioning-Node.sas to add it.;
        %return;
    %end;

    %put NOTE: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: the node &_dntNodeName. was created under the id &_dntNodeID., with its content.;
%mend _dntCreateNode;

%_dntCreateNode;

/************************************************************************
* [Optional] Delete the new node type;
* Never delete it while a decision uses it: SAS Viya does not refuse the
* delete, and every version of every decision that uses the node loses
* its reference to it and can not run anymore;
* https://developer.sas.com/rest-apis/decisions/deleteDecisionNodeType;
proc http url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntNodeID."
    method = 'Delete'
    oauth_bearer = sas_services;
quit;
************************************************************************/

* Clean up;
%sysmacdelete _dntCreateNode;
%symdel _dntviyaHost _dntNodeID _dntNodeName _dntDescription _dntHasProperties _dntHasInputs _dntHasOutputs _dntInputDatagridMappable _dntOutputDatagridMappable _dntInputDecisionTermMappable _dntOutputDecisionTermMappable _dntIndependentMappings _dntThemeId _dntType _dntIdStatus _dntIdName _dntNameIDs _dntNameFilter / nowarn;
