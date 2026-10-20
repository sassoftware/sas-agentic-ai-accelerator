/*************************************************************************************************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    This script brings the content of the Call LLM node to the one of this release. It was written for people that
    created the Call LLM node before version 0.20.0; it updates the content of any Call LLM node in place.

    The script looks for the node under the id the accelerator ships, f9efcbb7-f57b-4a62-b56d-1a89595e2039, first. Where
    no node is under that id, it looks for the node by its name and updates that one, with a NOTE that the node of the
    site is not under the shipped id yet. The migration mdb decision-node-migrate moves it there, see the section
    The Call LLM node and its id of the page Deployment of Decisions:
    https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Deployment-of-Decisions#call-llm-node-uuid
*************************************************************************************************************************************************************/

* Set the node name;
%let _dntNodeName = Call LLM;
* The id the accelerator ships the node under;
%let _dntNodeID = f9efcbb7-f57b-4a62-b56d-1a89595e2039;

%let _dntviyaHost=%sysfunc(getoption(SERVICESBASEURL));

* What the lookup finds: the id of the node to update, the ids of a node of the name, the ETag of its content;
%let _dntID = ;
%let _dntNameIDs = ;
%let _dntNameFilter = ;
%let _resource_etag = ;

%macro _dntUpdateNode;
    filename _dntRsp temp;

    * Look for the node under the id the accelerator ships;
    * https://developer.sas.com/rest-apis/decisions/getDecisionNodeType;
    proc http url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntNodeID."
        method = 'Get'
        out = _dntRsp
        oauth_bearer = sas_services;
        headers 'Accept' = 'application/json';
    run;

    filename _dntRsp clear;

    %if &SYS_PROCHTTP_STATUS_CODE. EQ 200 %then %do;
        %let _dntID = &_dntNodeID.;
    %end;
    %else %if &SYS_PROCHTTP_STATUS_CODE. EQ 404 %then %do;
        * Not under the shipped id: look for the node by its name;
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
            %put ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was updated, because SAS Viya did not answer the request for the node types named &_dntNodeName. with 200 (status: &SYS_PROCHTTP_STATUS_CODE.).;
            %return;
        %end;

        libname _dntRsp json;

        * Retrieve the ID of the Node;
        data _null_;
            set _dntRsp.allData(keep=P P1 P2 Value where=(P eq 2 and P1 eq 'items' and P2 eq 'id'));
            length ids $ 1000;
            retain ids;
            ids = catx(' ', ids, Value);
            call symputx('_dntNameIDs', ids);
        run;

        libname _dntRsp clear;
        filename _dntRsp clear;

        %if %length(&_dntNameIDs.) EQ 0 %then %do;
            %put ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was updated, because there is no node under the id &_dntNodeID. and none named &_dntNodeName.. Run Create-Custom-SAS-Intelligent-Decisioning-Node.sas to create it.;
            %return;
        %end;

        %let _dntID = &_dntNameIDs.;
        %put NOTE: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: the node &_dntNodeName. of this site is under the id &_dntID., not under the id &_dntNodeID. that the accelerator ships. Its content is updated all the same. The migration mdb decision-node-migrate moves the node and the decisions that use it to the shipped id: https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Deployment-of-Decisions#call-llm-node-uuid;
    %end;
    %else %do;
        %put ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was updated, because SAS Viya did not answer the request for the node type &_dntNodeID. with 200 or 404 (status: &SYS_PROCHTTP_STATUS_CODE.).;
        %return;
    %end;

    * Getting the etag of the node content is required to update it;
    filename hdrOut temp;

    * Call the resource to get the header;
    proc http
        method = 'Get'
        url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntID./content"
        oauth_bearer = sas_services
        headerOut = hdrOut;
        headers 'Accept' = 'application/json';
    run; quit;

    * Check that the resource was found and only then extract the ETag;
    %if &SYS_PROCHTTP_STATUS_CODE. NE 200 %then %do;
        filename hdrOut clear;
        %put ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was updated, because the content of the node &_dntID. could not be found. The API responded with &SYS_PROCHTTP_STATUS_CODE.: &SYS_PROCHTTP_STATUS_PHRASE..;
        %return;
    %end;

    * Extract the ETag, this is needed to update the resource;
    data _null_;
        length headerLine $256.;
        inFile hdrOut delimiter='|' missover;
        input headerLine $;

        if substr(headerLine, 1, 4) EQ 'ETag' then do;
            headerLine = translate(substr(headerLine, 7), '', '"');
            eTag1 = '"' || headerLine || '"';
            eTag2 = "'" || eTag1 || "'";
            call symputx('_resource_etag', compress(eTag2));
        end;
    run;

    filename hdrOut clear;

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
    * https://developer.sas.com/rest-apis/decisions/updateDecisionNodeTypeContent;
    proc http url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntID./content"
        method = 'Put'
        in = _dntIn
        out = _dntRsp
        oauth_bearer = sas_services;
        headers 'Accept' = 'application/json, application/vnd.sas.decision.node.type.content+json, application/vnd.sas.error+json'
            'Content-Type' = 'application/json'
            'If-Match' = &_resource_etag.;
    quit;

    filename _dntRsp clear;
    filename _dntIn clear;

    %if &SYS_PROCHTTP_STATUS_CODE. NE 200 %then %do;
        %put ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: SAS Viya did not update the content of the node &_dntID. (status: &SYS_PROCHTTP_STATUS_CODE.).;
        %return;
    %end;

    %put NOTE: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: the content of the node &_dntNodeName. under the id &_dntID. was updated.;
%mend _dntUpdateNode;

%_dntUpdateNode;

* Clean up;
%sysmacdelete _dntUpdateNode;
%symdel _dntviyaHost _dntNodeName _dntNodeID _dntID _dntNameIDs _dntNameFilter _resource_etag / nowarn;
