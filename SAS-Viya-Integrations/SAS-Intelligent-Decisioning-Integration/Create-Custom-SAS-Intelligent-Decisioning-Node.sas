/*************************************************************************************************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0
    
    This script creates a new SAS Intelligent Decisioning node that can be used for Drag & Drop LLM integration.

    _dnt is short for decision node type

    You can find the documentation for all of these API endpoints in the DecisionNodeTypes section:
    https://developers.sas.com/rest-apis/decisions

    There is also information available in the SAS Documentation:
    https://go.documentation.sas.com/doc/en/edmcdc/default/edmcustnodes/titlepage.htm

    If you want to learn more about the different styling options check out:
    https://go.documentation.sas.com/doc/en/edmcdc/default/edmcustnodes/p0otn4brmksbk8n1emshmurplrp4.htm
*************************************************************************************************************************************************************/

* Set the node definition;
%let _dntNodeName = Call LLM;
%let _dntDescription = Enables you to call an LLM that is wrapped in the SAS LLM Use Case Framework template;
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

filename _dntRsp temp;
filename _dntIn temp;

* Define the new node type;
data _null_;
    file _dntIn;
    put '{';
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

* Create a new node type;
* https://developer.sas.com/rest-apis/decisions/createDecisionNodeType;
proc http url = "&_dntviyaHost./decisions/decisionNodeTypes"
    method = 'Post'
    in = _dntIn
    out = _dntRsp
    oauth_bearer = sas_services;
    headers 'Accept' = 'application/vnd.sas.decision.node.type+json, application/json, application/vnd.sas.error+json';
    headers 'Content-Type' = 'application/vnd.sas.decision.node.type+json';
quit;

libname _dntRsp json;

* Get the URI of the new node type;
data _null_;
    set _dntRsp.allData(keep=P1 Value where=(P1 eq 'id'));

    call symputx('_dntID', Value);
run;

libname _dntRsp clear;
filename _dntRsp clear;
filename _dntIn clear;

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
proc http url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntID./content"
    method = 'Post'
    in = _dntIn
    out = _dntRsp
    oauth_bearer = sas_services;
    headers 'Accept' = 'application/vnd.sas.decision.node.type.content+json, application/json, application/vnd.sas.error+json';
    headers 'Content-Type' = 'application/vnd.sas.decision.node.type.content+json';
quit;

filename _dntRsp clear;
filename _dntIn clear;

/************************************************************************
* [Optional] Delete the new node type;
* https://developer.sas.com/rest-apis/decisions/deleteDecisionNodeType;
proc http url = "&_dntviyaHost./decisions/decisionNodeTypes/&_dntID."
    method = 'Delete'
    oauth_bearer = sas_services;
quit;
************************************************************************/

* Clean up;
%symdel _dntviyaHost _dntID _dntNodeName _dntHasProperties _dntHasInputs _dntHasOutputs _dntInputDatagridMappable _dntOutputDatagridMappable _dntInputDecisionTermMappable _dntOutputDecisionTermMappable _dntIndependentMappings _dntThemeId _dntType;