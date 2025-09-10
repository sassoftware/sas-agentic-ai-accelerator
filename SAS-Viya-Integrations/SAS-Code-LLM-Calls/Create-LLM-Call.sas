/*******************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Retrieves options for a LLM call and generates it

    This script requires you to set the macro variables
      _slo_optionsFileURI, which can be retrieved using
      the script Get-List-of-available-LLMs.sas

    The output table is called:
      work._slo_models

    slo is short for:
      SAS Large Language Model Options
*******************************************************************************/
* Provide the LLM name;
%let _slo_LLM_name = ;
* Provide the file URI of a LLM;
%let _slo_optionsFileURI = ;
* Provide the LLM-SCR endpoint;
%let _slo_LLMSCR = %sysfunc(getoption(SERVICESBASEURL))/llm;
* Print the code to the results tab? 1 = Yes, 0 = No;
%let _slo_result = 1;
* Save the code to a filename - the filename will be called _slo_LLM and use the following to run it: %include _slo_LLM;;
%let _slo_fl = 1;
* If you save to a filename, you can also choose to set it to file location on the SAS server, otherwise the filename will be temporary;
* If you specify a path put the value into quotes, i.e. '/viya-share/llmCall.sas';
%let _slo_fl_path = temp;

* Get the Viya Host URL;
%let _slo_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

filename _slo_rsp temp;

* https://developer.sas.com/rest-apis/files-v9?operation=getfileContentForGivenId;
proc http
    method='Get'
    url="&_slo_viyaHost.&_slo_optionsFileURI./content"
    oauth_bearer=sas_services
    out=_slo_rsp;
    headers 'Accept' = 'application/json';
run; quit;

libname _slo_rsp json;

* Order the data set to be able to transpose it;
proc sql;
    create table work._slo_alldata as
        select *
            from _slo_rsp.alldata
                where v=1
                    order by P1, P2;
run; quit;

* Flatten the output table structure;
proc transpose data=work._slo_alldata out=work._slo_transposed(drop=_name_) prefix=col_;
    by P1;
    id P2;
    var Value;
run; quit;

* Give meaningful names;
data work._slo_options;
    set work._slo_transposed;
    rename p1 = option_name
      col_default = default
      col_range = range
      col_description = description;
run;

* Create code that generates the option inputs;
data work._slo_final_macro_vars;
    length _slo_code $32767.;
    set work._slo_options;

    * Create macro variables that the user can set;
    _slo_code = '%let ' || compress(option_name) || '=' || compress(default) || ';';
    keep _slo_code;
run;

* Create code that generates the JSON input;
data work._slo_final_json_input;
    length _slo_code $32767.;
    set work._slo_options end=EoF;

    if _n_ = 1 then do;
        _slo_code = '%let systemPrompt=Your system prompt;';
        output;
        _slo_code = '%let userPrompt=Your user prompt;';
        output;
        _slo_code = 'filename llmIn temp;';
        output;
        _slo_code = 'data _null_;';
        output;
        _slo_code = 'length _slo_code $32767.;';
        output;
        _slo_code = 'file llmIn;';
        output;
        _slo_code = 'put ' || "'{" || '"inputs": [' || "';";
        output;
        _slo_code = 'put ' || "'{" || '"name": "systemPrompt", ' || "';";
        output;
        _slo_code = "_slo_code =  '" || '"value": "' || "' || " || '"&systemPrompt." || ' || "'" || '"},' || "';";
        output;
        _slo_code = 'put _slo_code;';
        output;
        _slo_code = 'put ' || "'{" || '"name": "userPrompt", ' || "';";
        output;
        _slo_code = "_slo_code =  '" || '"value": "' || "' || " || '"&userPrompt." || ' || "'" || '"},' || "';";
        output;
        _slo_code = 'put _slo_code;';
        output;
        _slo_code = "_slo_code='{" || '"name": "options", "value": "{' || "';";
        output;
    end;

    if eof then do;
        _slo_code = '_slo_code = compress(_slo_code) ||"' || compress(option_name) || ':"|| "&' || compress(option_name) || '."' || "||'" || '}"' || "'" || ';';
        output;
        _slo_code = 'put _slo_code;';
        output;
        _slo_code = 'call symputx("options", compress(_slo_code) || "}");';
        output;
        _slo_code = "put '}';";
        output;
        _slo_code = 'put "]}";';
        output;
        _slo_code = 'run;';
        output;
    end;
    else do;
        _slo_code = '_slo_code = compress(_slo_code) ||"' || compress(option_name) || ':"|| "&' || compress(option_name) || '.,";';
        output;
    end;

    keep _slo_code;
run;

* Create code that generates the HTTP call;
data work._slo_final_http;
    length _slo_code $32767.;
    _slo_code = 'filename llmOut temp;';
    output;
    _slo_code = 'proc http';
    output;
    _slo_code = "method='Post'";
    output;
    _slo_code = 'url="' || "&_slo_LLMSCR./" || compress("&_slo_LLM_name./&_slo_LLM_name.") || '"';
    output;
    _slo_code = 'in=llmIn';
    output;
    _slo_code = "ct='application/json'";
    output;
    _slo_code = 'out=llmOut;';
    output;
    _slo_code = 'run; quit;';
    output;
    _slo_code = 'libname llmOut json;';
    output;
run;

* Combine the datasets;
data work._slo_final;
    set work._slo_final_macro_vars work._slo_final_json_input work._slo_final_http;
run;

* Print the result for the user;
%if &_slo_result. EQ 1 %then %do;
    title 'Copy & paste this code into a SAS script';
    proc print data=work._slo_final noObs;
    run; quit;
    title;
%end;

* Save the result as a filename;
%if &_slo_fl. EQ 1 %then %do;
    filename _slo_LLM &_slo_fl_path.;

    data _null_;
        file _slo_LLM;
        set work._slo_final;
        put _slo_code;
    run;

    filename _slo_LLM clear;

    %put NOTE: The script is available at &_slo_fl_path.;
%end;

* Clean up;
libname _slo_rsp clear;
filename _slo_rsp clear;
proc datasets library=work noList;
    delete _slo_alldata _slo_transposed _slo_options _slo_final_macro_vars _slo_final_json_input _slo_final_http _slo_final;
run; quit;
%symdel _slo_LLM_name _slo_optionsFileURI _slo_LLMSCR _slo_result _slo_fl _slo_fl_path _slo_viyaHost;