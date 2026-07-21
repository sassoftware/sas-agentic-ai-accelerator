/*********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get all Prompts

    This script filters SAS Model Manager projects for a
      specific tag, then collects the Prompt Experiements
      and combining all into a table. The final output table
      work._gap_all_data is meant to be used in a VA report.
      By default a CAS session called mySess will be started
      and the table will be promoted into Public + saved as
      public.PROMPT_EXPERIMENTS.

    The output tables are called:
      work._gap_all_projects
      work._gap_all_models
      work._gap_all_experiments
      work._gap_all_data

    gap is short for:
      Get All Prompts
*********************************************************************************/
* Set the Tag which is used for filtering for projects;
%let _gap_tag = Prompt-Engineering;

* Get the Viya Host URL;
%let _gap_viyaHost=%sysfunc(getoption(SERVICESBASEURL));

* Macro to recursivly get all projects;
%macro _gap_project_pagination(_gap_start=0, _gap_limit=25);
    %local _gap_start
        _gap_limit
        _gap_ds_nobs
        _gap_nvar
        _gap_new_start;

    filename _gap_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjects;
    proc http method = 'Get'
        url = "&_gap_viyaHost./modelRepository/projects?start=&_gap_start%nrstr(&)limit=&_gap_limit.%nrstr(&)filter=eq(tags,'&_gap_tag.')"
        oauth_bearer = sas_services
        out = _gap_rp;
        headers 'Accept' = 'application/json';
    quit;

    libname _gap_rp json;

    * Get the number of observations where one obs = one project;
    proc sql noprint;
        select count(*) into :_gap_ds_nobs trimmed from _gap_rp.items;
    quit;

    * Get the number of columns;
    proc sql noprint;
        select nvar into :_gap_nvar trimmed from dictionary.tables where
            libname='_GAP_RP' and memname='ITEMS';
    quit;

    * Handle the first iteration and subsequent requests;
    %if &_gap_start. eq 0 %then %do;
        data work._gap_all_projects;
            length name createdBy modifiedBy function status $256.;
            set _gap_rp.items(keep=id repositoryId name function status folderId createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
        run;
    %end;
    %else %do;
        %if &_gap_nvar. gt 2 %then %do;
            proc append force nowarn base=work._gap_all_projects
                data=_gap_rp.items(keep=id repositoryId name function status folderId createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
            quit;
        %end;
    %end;

    libname _gap_rp clear;
    filename _gap_rp clear;

    * Check if another iteration is needed;
    %if &_gap_ds_nobs eq &_gap_limit and &_gap_nvar. gt 2 %then %do;
        %put NOTE: Getting more projects;
        %let _gap_new_start=%eval(&_gap_start. + &_gap_limit.);
        %_gap_project_pagination(_gap_start=&_gap_new_start., _gap_limit=&_gap_limit.);
    %end;
%mend _gap_project_pagination;

* Get a table of all projects;
%_gap_project_pagination();

* Macro to recursivly get all models in a project;
%macro _gap_model_pagination(_gap_project_id, _gap_start=0, _gap_limit=100);
    %local _gap_start
        _gap_limit
        _gap_ds_nobs
        _gap_nvar
        _gap_new_start;

    filename _gap_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8?operation=getProjects;
    proc http method = 'Get'
        url = "&_gap_viyaHost./modelRepository/projects/&_gap_project_id./models?start=&_gap_start%nrstr(&)limit=&_gap_limit."
        oauth_bearer = sas_services
        out = _gap_rp;
        headers 'Accept' = 'application/json';
    quit;

    libname _gap_rp json;

    * Get the number of observations where one obs = one model;
    proc sql noprint;
        select count(*) into :_gap_ds_nobs trimmed from _gap_rp.items;
    quit;

    * Get the number of columns;
    proc sql noprint;
        select nvar into :_gap_nvar trimmed from dictionary.tables
            where libname='_GAP_RP' and memname='ITEMS';
    quit;

    %if &_gap_nvar. gt 2 %then %do;
        * Check for description;
        proc sql noprint;
            select * from dictionary.columns
                where libname='_GAP_RP' and memname='ITEMS' and name='description';
        quit;

        %if &SQLOBS. %then %do;
            proc append force nowarn base=work._gap_all_models
                data=_gap_rp.items(keep=id projectId repositoryId name description function createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
            quit;
        %end;
        %else %do;
            proc append force nowarn base=work._gap_all_models
                data=_gap_rp.items(keep=id projectId repositoryId name function createdBy creationTimeStamp modifiedBy modifiedTimeStamp);
            quit;
        %end;
    %end;

    libname _gap_rp clear;
    filename _gap_rp clear;

    * Check if another iteration is needed;
    %if &_gap_ds_nobs eq &_gap_limit and &_gap_nvar. gt 2 %then %do;
        %put NOTE: Getting more models;
        %let _gap_new_start=%eval(&_gap_start. + &_gap_limit.);
        %_gap_model_pagination(&_gap_project_id., _gap_start=&_gap_new_start., _gap_limit=&_gap_limit.);
    %end;
%mend _gap_model_pagination;

* Get a table of all models in a project;
data work._gap_all_models;
    length name description createdBy modifiedBy function $256. id projectId repositoryId $36. creationTimeStamp modifiedTimeStamp $24.;
run;

data _null_;
    set work._gap_all_projects(keep=id);
    args = '%nrstr(%_gap_model_pagination(' || id || '))';
    call execute(args);
run;

* Macro to retrieve the Prompt-Experiement-Tracker.json and other metadata;
%macro _gap_pet_data(_gap_model_id, _gap_start=0, _gap_limit=100);
    %local _gap_model_id
        _gap_start
        _gap_limit
        _gap_ds_nobs
        _gap_nvar
        _gap_tracker_uri;

    filename _gap_rp temp;

    * https://developer.sas.com/rest-apis/modelRepository-v8/getModelContents;
    proc http method = 'Get'
        url = "&_gap_viyaHost./modelRepository/models/&_gap_model_id./contents?start=&_gap_start%nrstr(&)limit=&_gap_limit."
        oauth_bearer = sas_services
        out = _gap_rp;
        headers 'Accept' = 'application/json';
    quit;

    libname _gap_rp json;

    * Get the number of observations where one obs = one model;
    proc sql noprint;
        select count(*) into :_gap_ds_nobs trimmed from _gap_rp.items;
    quit;

    * Get the number of columns;
    proc sql noprint;
        select nvar into :_gap_nvar trimmed from dictionary.tables
            where libname='_GAP_RP' and memname='ITEMS';
    quit;

    %if &_gap_nvar. gt 2 %then %do;
        * Check for a Prompt Experiment Tracker file;
        proc sql noprint;
            select fileUri into :_gap_tracker_uri trimmed from _gap_rp.items
                where name='Prompt-Experiment-Tracker.json';
        quit;
        
        %if &SQLOBS. %then %do;
            filename _gap_pe temp;
            
            * https://developer.sas.com/rest-apis/files/getfileContentForGivenId;
            proc http method='Get'
                url="&_gap_viyaHost.&_gap_tracker_uri./content"
                oauth_bearer = sas_services
                out = _gap_pe;
                headers 'Accept' = 'application/json';
            quit;

            libname _gap_pe json;

            * A run's input variables and its manifest (the integrated-LLM-call
              flag and the parsed output variables) live on the run's header row
              as nested JSON. The JSON engine explodes those into side tables
              keyed by ordinal_root. Count them per run below. Trackers written by
              an older Prompt Builder have neither, so every read is guarded with
              exist() - a missing side table simply yields a count of zero and the
              script keeps working against old prompts;
            %if %sysfunc(exist(_gap_pe.variables)) %then %do;
                proc sql;
                    create table work._gap_var_counts as
                        select ordinal_root, count(*) as variables_count
                        from _gap_pe.variables group by ordinal_root;
                quit;
            %end;
            %else %do;
                data work._gap_var_counts; length ordinal_root variables_count 8.; stop; run;
            %end;

            %if %sysfunc(exist(_gap_pe.manifest)) %then %do;
                data work._gap_manifest;
                    set _gap_pe.manifest(keep=ordinal_root ordinal_manifest integratedLLMCall);
                run;
            %end;
            %else %do;
                data work._gap_manifest; length ordinal_root ordinal_manifest integratedLLMCall 8.; stop; run;
            %end;

            %if %sysfunc(exist(_gap_pe.manifest_outputvariables)) %then %do;
                proc sql;
                    create table work._gap_out_counts as
                        select m.ordinal_root, count(*) as output_variables_count
                        from _gap_pe.manifest_outputvariables as o
                            inner join work._gap_manifest as m
                                on o.ordinal_manifest = m.ordinal_manifest
                        group by m.ordinal_root;
                quit;
            %end;
            %else %do;
                data work._gap_out_counts; length ordinal_root output_variables_count 8.; stop; run;
            %end;

            * One row per run header (ordinal_root), with the three metrics;
            proc sql;
                create table work._gap_run_meta as
                    select coalesce(v.ordinal_root, f.ordinal_root, o.ordinal_root) as ordinal_root,
                           coalesce(v.variables_count, 0)        as variables_count,
                           coalesce(f.integratedLLMCall, 0)      as integrated_llm_call,
                           coalesce(o.output_variables_count, 0) as output_variables_count
                    from work._gap_var_counts as v
                        full join work._gap_manifest as f on v.ordinal_root = f.ordinal_root
                        full join work._gap_out_counts as o
                            on coalesce(v.ordinal_root, f.ordinal_root) = o.ordinal_root
                    order by ordinal_root;
            quit;

            data work._gap_all_experiments_temp(drop=systemPromptTEMP userPromptTEMP vcTEMP illcTEMP ovcTEMP jmTEMP jcTEMP ordinal_root);
                length modelID $36. runID 8. systemPrompt systemPromptTEMP userPrompt userPromptTEMP $32767. model $64. options $256. response $32767. run_time prompt_length output_length best_prompt fastest_prompt fewest_tokens_prompt judge_rank judge_best variables_count integrated_llm_call output_variables_count 8. judge_model jmTEMP $64. judge_confidence jcTEMP $16.;
                * root is emitted in ordinal_root order, matching _gap_run_meta.
                  judge_rank / judge_best sit on the model rows; judge_model /
                  judge_confidence sit on the header row (like the prompts) and
                  are carried onto each model row below. Trackers written before
                  judging simply leave every judge_* column missing;
                merge _gap_pe.root _gap_run_meta;
                by ordinal_root;

                modelID = "&_gap_model_id.";

                * Set the prompts and carry the run-level metrics from the header
                  row onto each model result row;
                if systemPrompt ne '' then do;
                    systemPromptTEMP = systemPrompt;
                    userPromptTEMP = userPrompt;
                    vcTEMP = coalesce(variables_count, 0);
                    illcTEMP = coalesce(integrated_llm_call, 0);
                    ovcTEMP = coalesce(output_variables_count, 0);
                    jmTEMP = judge_model;
                    jcTEMP = judge_confidence;
                end;

                if model ne '' then do;
                    systemPrompt = systemPromptTEMP;
                    userPrompt = userPromptTEMP;
                    variables_count = vcTEMP;
                    integrated_llm_call = illcTEMP;
                    output_variables_count = ovcTEMP;
                    judge_model = jmTEMP;
                    judge_confidence = jcTEMP;
                    output;
                end;

                retain systemPromptTEMP userPromptTEMP vcTEMP illcTEMP ovcTEMP jmTEMP jcTEMP;
            run;

            proc append base=work._gap_all_experiments data=work._gap_all_experiments_temp force nowarn;
            quit;

            proc datasets lib=work nolist;
                delete _gap_all_experiments_temp;
            quit;

            libname _gap_pe clear;
            filename _gap_pe clear;
        %end;
    %end;

    libname _gap_rp clear;
    filename _gap_rp clear;

    * Check if another iteration is needed;
    %if &_gap_ds_nobs eq &_gap_limit and &_gap_nvar. gt 2 %then %do;
        %put NOTE: Getting more models;
        %let _gap_new_start=%eval(&_gap_start. + &_gap_limit.);
        %_gap_pet_data(&_gap_model_id., _gap_start=&_gap_new_start., _gap_limit=&_gap_limit.);
    %end;
%mend _gap_pet_data;

* Create base table;
data work._gap_all_experiments;
    length modelID $36. runID 8. systemPrompt userPrompt $32767. model $64. options $256. response $32767. run_time prompt_length output_length best_prompt fastest_prompt fewest_tokens_prompt judge_rank judge_best variables_count integrated_llm_call output_variables_count 8. judge_model $64. judge_confidence $16.;
run;

data work._gap_all_models;
    * Drop the empty row;
    set work._gap_all_models(rename=(id=modelID) firstobs=2);
    args = '%nrstr(%_gap_pet_data(' || modelID || '))';
    call execute(args);
    drop args;
run;

* Join the model and experiment into one table;
proc sql;
    create table work._gap_all_data as
        select a.*,
            b.runID,
            b.model,
            b.systemPrompt,
            b.userPrompt,
            b.options,
            b.response,
            b.run_time,
            b.prompt_length,
            b.output_length,
            b.best_prompt,
            b.fastest_prompt,
            b.fewest_tokens_prompt,
            b.judge_rank,
            b.judge_best,
            b.judge_model,
            b.judge_confidence,
            b.variables_count,
            b.integrated_llm_call,
            b.output_variables_count
            from work._gap_all_models as a
                left join work._gap_all_experiments as b
                    on a.modelID = b.modelID;
quit;

* Post-Process data for reporting;
data work._gap_all_data;
    length unique_id 8.;
    set work._gap_all_data(drop=repositoryId);
    length creationTime modifiedTime temperature top_p top_k max_tokens
        thinking_budget max_completion_tokens seed frequency_penalty presence_penalty
        dimensions api_key_required 8. reasoning_effort input_type $32. normalize $8.;
    format creationTime modifiedTime datetime22.8;
    label unique_id = 'Unique ID'
        name = 'Prompt Name'
        description = 'Description'
        createdBy = 'Created By'
        modifiedBy = 'Modified By'
        function = 'Function'
        modelLink = 'Model Link'
        projectLink = 'Project Link'
        creationTime = 'Creation Time Stamp'
        modifiedTime = 'Modified Time Stamp'
        runID = 'Run ID'
        model = 'Model'
        systemPrompt = 'System Prompt'
        userPrompt = 'User Prompt'
        response = 'Response'
        run_time = 'Run Time (s)'
        prompt_length = 'Prompt Length (tokens)'
        output_length = 'Output Length (tokens)'
        best_prompt = 'Best Prompt in Run'
        fastest_prompt = 'Fastest Prompt in Run'
        fewest_tokens_prompt = 'Fewest Tokens Prompt in Run'
        judge_rank = 'Judge Rank in Run'
        judge_best = 'Best Prompt per Judge'
        judge_model = 'Judge Model'
        judge_confidence = 'Judge Confidence'
        temperature = 'Temperature'
        top_p = 'Top P'
        top_k = 'Top K'
        max_tokens = 'Max Tokens'
        thinking_budget = 'Thinking Budget'
        max_completion_tokens = 'Max Completion Tokens'
        seed = 'Seed'
        frequency_penalty = 'Frequency Penalty'
        presence_penalty = 'Presence Penalty'
        reasoning_effort = 'Reasoning Effort'
        input_type = 'Input Type'
        dimensions = 'Dimensions'
        normalize = 'Normalize'
        variables_count = 'Custom Input Variables (count)'
        integrated_llm_call = 'Integrated LLM Call'
        output_variables_count = 'Parsed Output Variables (count)'
        api_key_required = 'API Key Required';
    
    unique_id = _n_;

    * Convert the timestamp into a SAS timestamp;
    creationTime = input(creationTimeStamp, E8601DZ24.);
    modifiedTime = input(modifiedTimeStamp, E8601DZ24.);

    * Create a deep link to SAS Model Manager;
    projectLink = catx('/', "&_gap_viyaHost.", 'SASModelManager/projects', projectID);
    modelLink = catx('/', "&_gap_viyaHost.", 'SASModelManager/models', modelID);

    * Parsing the options;
    if options NE '' then do;
        api_key_required = 0;
        options = strip(substr(options, 2, length(options)-2));
        do i = 1 to countw(options, ',');
            key_value_pair = scan(options, i, ',');
            key = scan(key_value_pair, 1, ':');
            value = scan(key_value_pair, 2, ':');

            if key = 'temperature' then temperature = input(value, 8.);
            else if key = 'top_k' then top_k = input(value, 8.);
            else if key = 'top_p' then top_p = input(value, 8.);
            else if key = 'max_tokens' then max_tokens = input(value, 8.);
            else if key = 'thinking_budget' then thinking_budget = input(value, 8.);
            else if key = 'max_completion_tokens' then max_completion_tokens = input(value, 8.);
            else if key = 'seed' then seed = input(value, 8.);
            else if key = 'frequency_penalty' then frequency_penalty = input(value, 8.);
            else if key = 'presence_penalty' then presence_penalty = input(value, 8.);
            else if key = 'dimensions' then dimensions = input(value, 8.);
            else if key = 'reasoning_effort' then reasoning_effort = value;
            else if key = 'input_type' then input_type = value;
            else if key = 'normalize' then normalize = value;
            else if key = 'API_KEY' then api_key_required = 1;
        end;
    end;

    drop creationTimeStamp modifiedTimeStamp modelID projectID options i key_value_pair key value;
run;

cas mySess;

proc casUtil inCASlib='Public' outCASLib='Public';
    dropTable casData='PROMPT_EXPERIMENTS' quiet;
    load data=work._gap_all_data casOut='PROMPT_EXPERIMENTS';
    promote casData='PROMPT_EXPERIMENTS' casOut='PROMPT_EXPERIMENTS';
    save casData='PROMPT_EXPERIMENTS' casOut='PROMPT_EXPERIMENTS.sashdat' replace;
quit;

* Clean up;
cas mySess terminate;
%symdel _gap_viyaHost _gap_tag;
%sysmacdelete _gap_project_pagination;
%sysmacdelete _gap_model_pagination;
%sysmacdelete _gap_pet_data;