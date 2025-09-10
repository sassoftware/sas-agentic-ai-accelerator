/*************************************************************************************************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0
    
    This script is only for people that created the Call LLM node before version 0.20.0.
    If you are reading this after that release and haven't created the node yet, you can ignore this.
*************************************************************************************************************************************************************/

* Set the node name;
%let _dntNodeName = Call LLM;

%let _dntviyaHost=%sysfunc(getoption(SERVICESBASEURL));

filename _dntRsp temp;

* Get a list of all decision node types;
* https://developer.sas.com/rest-apis/decisions/getDecisionNodeTypes;
proc http url = "&_dntviyaHost./decisions/decisionNodeTypes?filter=eq(name,'&_dntNodeName.')"
    method = 'Get'
    out = _dntRsp
    oauth_bearer = sas_services;
    headers 'Accept' = 'application/json';
run;

libname _dntRsp json;

* Retrieve the ID of the Node;
proc sql noPrint;
    select id into :_dntID
        from _dntRsp.items;
run; quit;

libname _dntRsp clear;
filename _dntRsp clear;

filename _dntIn temp;
filename _dntRsp temp;

* Getting the etag of the node content is required to update it;
%macro _retrieve_etag(_resourceURI);
    %global _resource_etag;

    * Create a temporary header output file;
    filename hdrOut temp;

    * Call the resource to get the header;
    proc http
        method = 'Get'
        url = "&_dntviyaHost.&_resourceURI."
        oauth_bearer = sas_services
        headerOut = hdrOut;
        headers 'Accept' = 'application/json';
    run; quit;

    * Check that the resource was found and only then extract the ETag;
    %if &SYS_PROCHTTP_STATUS_CODE. EQ 200 %then %do;
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
    %end;
    %else %do;
        %put ERROR: The resource could not be found. The API responded with &SYS_PROCHTTP_STATUS_CODE.: SYS_PROCHTTP_STATUS_PHRASE.;
    %end;

    filename hdrOut clear;
%mend _retrieve_etag;

%_retrieve_etag(/decisions/decisionNodeTypes/&_dntID./content);

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

* Clean up;
%sysmacdelete _retrieve_etag;
%symdel _dntviyaHost _dntID _resource_etag;