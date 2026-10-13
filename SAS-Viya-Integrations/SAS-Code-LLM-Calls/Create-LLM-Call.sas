/*******************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Retrieves options for a LLM call and generates it

    This script requires you to set the macro variables
      _slo_LLM_name and _slo_optionsFileURI, which can be
      retrieved using the script Get-List-of-available-LLMs.sas.
      Without both it asks SAS Viya for nothing and ends with
      one ERROR line.

    The code is printed to the Results tab and written to the
      fileref _slo_LLM, as the settings below say. No table is left.

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
* Save the code to a file? 1 = Yes, 0 = No. The fileref _slo_LLM then names the file, and an INCLUDE statement with that fileref runs the code;
%let _slo_fl = 1;
* If you save to a filename, you can also choose to set it to file location on the SAS server, otherwise the filename will be temporary;
* If you specify a path put the value into quotes, i.e. '/viya-share/llmCall.sas';
%let _slo_fl_path = temp;
* 1 where there is a model name and an options file to ask for;
%let _slo_chosen = %eval(%length(%superq(_slo_LLM_name)) GT 0 and %length(%superq(_slo_optionsFileURI)) GT 0);

* Get the Viya Host URL;
%let _slo_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

* The code is written only where the default of every option of the options file was read. _slo_ok says so, _slo_why says why not;
%let _slo_ok = 0;
%let SYS_PROCHTTP_STATUS_CODE = ;
%if %sysfunc(libref(_slo_rsp)) EQ 0 %then %do;
    libname _slo_rsp clear;
%end;
proc datasets library=work noList noWarn;
    delete _slo_options;
run; quit;
filename _slo_rsp temp;

* https://developer.sas.com/rest-apis/files-v9?operation=getfileContentForGivenId;
%if &_slo_chosen. EQ 1 %then %do;
proc http
    method='Get'
    url="&_slo_viyaHost.&_slo_optionsFileURI./content"
    oauth_bearer=sas_services
    out=_slo_rsp;
    headers 'Accept' = 'application/json';
run; quit;
%end;

%let _slo_why = SAS Viya did not answer the request for the options file %superq(_slo_optionsFileURI) with HTTP 200 (status: %superq(SYS_PROCHTTP_STATUS_CODE));
%if "&SYS_PROCHTTP_STATUS_CODE." EQ "200" %then %do;
    %let _slo_why = the options file %superq(_slo_optionsFileURI) is no JSON that SAS can read;
    libname _slo_rsp json;
%end;
* A run without a model has asked for nothing;
%if &_slo_chosen. NE 1 %then %do;
    %let _slo_why = no model with an options file was chosen - the script needs a model name in _slo_LLM_name and the URI of its options file in _slo_optionsFileURI;
%end;

* One row per option. An option is a member of the object of the file, and default, range and description are members of an option with one value each;
* A member that holds a list - values, the values an option can take - has a line for each of its entries and is not read. PROC TRANSPOSE stopped at the second of them;
%if %sysfunc(libref(_slo_rsp)) EQ 0 %then %do;
    %let _slo_why = the options file %superq(_slo_optionsFileURI) holds no option that SAS could read;
    proc sql noPrint;
        create table work._slo_options as
            select P1 as option_name,
                   max(case when P EQ 2 and V EQ 1 and P2 EQ 'default' then Value end) as default,
                   max(case when P EQ 2 and V EQ 1 and P2 EQ 'range' then Value end) as range,
                   max(case when P EQ 2 and V EQ 1 and P2 EQ 'description' then Value end) as description
                from _slo_rsp.alldata
                    where P GE 1
                        group by P1
                            order by P1;
    quit;
%end;

* Every option needs its default, the code sets each option to it;
%if %sysfunc(exist(work._slo_options)) %then %do;
    data _null_;
        set work._slo_options end=_slo_last;
        length _slo_unread $32767.;
        retain _slo_unread;
        if missing(default) then _slo_unread = catx(' ', _slo_unread, option_name);
        if _slo_last then do;
            if missing(_slo_unread) then call symputx('_slo_ok', 1);
            else call symputx('_slo_why', catx(' ', 'no default could be read for the option(s)', _slo_unread, 'of the options file', symget('_slo_optionsFileURI')));
        end;
    run;
%end;

* The code is generated only where every option was read;
%if &_slo_ok. EQ 1 %then %do;

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
%end;

* Print the result for the user;
%if &_slo_ok. EQ 1 and &_slo_result. EQ 1 %then %do;
    title 'Copy & paste this code into a SAS script';
    proc print data=work._slo_final noObs;
    run; quit;
    title;
%end;

* Save the result as a filename;
%if &_slo_ok. EQ 1 and &_slo_fl. EQ 1 %then %do;
    filename _slo_LLM &_slo_fl_path.;

    data _null_;
        file _slo_LLM;
        set work._slo_final;
        put _slo_code;
    run;

    %put NOTE: The script is available at &_slo_fl_path., and the fileref _slo_LLM names it until the SAS session ends;
%end;

* Clean up;
%if %sysfunc(libref(_slo_rsp)) EQ 0 %then %do;
    libname _slo_rsp clear;
%end;
filename _slo_rsp clear;
proc datasets library=work noList noWarn;
    delete _slo_options _slo_final_macro_vars _slo_final_json_input _slo_final_http _slo_final;
run; quit;
* A run that could not read the default of every option wrote no code. It says why, and ends with the condition code of a failed job;
%if &_slo_ok. NE 1 %then %do;
    %put ERROR: Create-LLM-Call.sas: no code was written, because %superq(_slo_why).;
    %let SYSCC = %sysfunc(max(&SYSCC., 1012));
%end;
%symdel _slo_LLM_name _slo_optionsFileURI _slo_LLMSCR _slo_result _slo_fl _slo_fl_path _slo_viyaHost;
%symdel _slo_chosen _slo_ok _slo_why / nowarn;