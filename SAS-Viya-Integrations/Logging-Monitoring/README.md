# Parsing the Log

The goal is to create a table that extracts all of the relevant information from the container logs and collect everything as a CAS table.

For this two options are available:
1. The *Log-Parser-Code.sas* is a script that you can point at the logging file and it will parse all of the information into a CAS table.
2. The *LLM - Log Parser* custom step, which is just a nice little UI on top of the same SAS script - this step is located in the Custom-Steps folder.

By default the parser writes the CAS table **`LLM_LOGS`** to the **`Public`** caslib and both promotes it (global scope) and saves it to disk. Change the `_log_target_table_lib` / `_log_target_table_name` macro variables at the top of *Log-Parser-Code.sas* (or the custom step's options) to target a different library or table name.

The parser reads the log from the file system of the SAS compute server: `_log_path` has to start with `sasserver:`, and a file in SAS Content is not read. The default, `sasserver:/export/pvs/sasdata/data/llm/llms.log`, is an example path. The [Logging & Monitoring administration guide](../../website/docs/Administration-Guide/Logging-&-Monitoring.md) says what that path assumes and how the file reaches the compute server where the host that collects the logs shares no mount with it.

Three programs of this folder work on one set of tables, in this order:

1. collect the log (the script of the guide)
2. *Log-Parser-Code.sas* (or the step *LLM - Log Parser*) - one row per request in `LLM_LOGS`
3. *Get-All-Prompts.sas* (or the step *LLM - Get All Prompts*) - the experiments, the RAG setups and a catalog of models from SAS Model Manager
4. *Link-Logs-To-Models.sas* - fills the columns of `LLM_LOGS` that name the model that answered and the prompt, decision template or RAG setup that sent the request

The programs of steps 2 to 4 as they are in this release - the 24 further columns of the log table, the five tables of the pull, the link - have **not been run in SAS**. Their Python runs in the tests of this folder; the guide says what was measured on a SAS Viya system and with which version.

The resulting table has the following columns, in this order:

| Column Name   | Column Label                                                 |
| ------------- | ------------------------------------------------------------ |
| timestamp     | The datetime of the request                                  |
| model         | The name of the model used                                   |
| system_prompt | The system prompt used by the request. For a decision model the questions |
| user_prompt   | The user prompt used by the request. For a decision model the state |
| temperature   | The setting of temperature. Missing means it was not present in the request |
| top_p         | The setting of top p. Missing means it was not present in the request |
| top_k         | The setting of top k. Missing means it was not present in the request |
| max_tokens    | The setting of max new tokens. Missing means it was not present in the request |
| prompt_length | The amount of input tokens in the request                    |
| output_length | The amount of output tokens in the request                   |
| runtime       | The time for the model to generate the response in seconds   |
| response      | The response of the model to the users request. For a decision model the answers |
| kind          | The kind of the request: llm, embedding or decision |
| status_code   | The HTTP status code of the response. Missing means the log holds no response for the request |
| failed        | 1 if the log holds an error or no response for the request, otherwise 0 |
| error_text    | The first error the log holds for the request, shortened |
| transaction_id | The transaction ID the container gave the request |
| request_id    | The X-Request-Id header of the request |
| user_agent    | The kind of client that sent the request: python-requests, browser, SAS or other |
| embedding_mode | The Embedding_Mode option of a call to an embedding model: query or document |
| project       | The project input of a call to an embedding model |
| input_hash    | The SHA-256 hash of the text an embedding model was given. The text itself is not kept |
| answer        | The answer of a decision model to its first question |
| answer_confidence | The confidence of the answer of a decision model to its first question |
| options       | The options of the request as they were sent, with keys replaced by REDACTED |
| rag_collection | The collection of the retrieval whose results the user prompt holds |
| rag_query_hash | The SHA-256 hash of the question of the retrieval whose results the user prompt holds |
| model_id      | The Model Manager ID of the model that answered. Empty until the logs are linked |
| caller_model_id | The Model Manager ID of the prompt, decision template or RAG setup that sent the request. Empty until the logs are linked |
| caller_project_id | The Model Manager ID of the project of the caller. Empty until the logs are linked |
| caller_kind   | The kind of the caller: prompt, decision or rag. Empty until the logs are linked |
| caller_name   | The name of the caller. Empty until the logs are linked |
| experiment_run_id | The run of the experiment tracker the request belongs to. Empty until the logs are linked |
| rag_model_id  | The Model Manager ID of the RAG setup whose collection the user prompt holds. Empty until the logs are linked |
| match_method  | How the caller was found. Empty until the logs are linked |
| match_candidates | The number of callers the request fits. Missing until the logs are linked |

`timestamp` is a SAS datetime; `temperature`, `top_p`, `top_k`, `max_tokens`, `prompt_length`, `output_length`, `runtime`, `status_code`, `failed`, `answer_confidence` and `match_candidates` are numeric; every other column is text (VARCHAR). The first twelve columns are the ones the table had before release 2.3.0 - same names, order, types and meanings, and the *LLM Usage Report* reads those twelve. The last nine are written empty by the parser: *Link-Logs-To-Models.sas* fills them.

## What a row holds for each kind of model

Every scoring request becomes one row, whatever kind of model served it. The columns are the same for all three kinds:

| Column | LLM | Decision model | Embedding model |
| --- | --- | --- | --- |
| kind | `llm` | `decision` | `embedding` |
| system_prompt | the system prompt | the questions, as the JSON that was sent | missing |
| user_prompt | the user prompt | the state | missing |
| response | the response, its lines joined with a blank | the answers as JSON - every answer with its confidence and probabilities | missing |
| prompt_length | input tokens | input tokens | the tokens of the embedded text |
| output_length | output tokens | output tokens | missing |
| runtime | seconds | seconds | seconds |
| temperature, top_p, top_k, max_tokens | from the options of the request | from the options of the request | from the options of the request |
| status_code, failed, error_text | of the response; a request that failed has no status, `failed` = 1 and its first error | the same | the same |
| transaction_id, request_id, user_agent | of the request | the same | the same |
| options | as sent, keys replaced by `REDACTED` | the same | the same |
| embedding_mode, project, input_hash | missing | missing | the `Embedding_Mode` option as sent, the `project` input, the SHA-256 of the embedded text |
| answer, answer_confidence | missing | the answer to the first question and its confidence | missing |
| rag_collection, rag_query_hash | the collection and the SHA-256 of the question when the user prompt holds the results of a retrieval, otherwise missing | missing | missing |

The text an embedding model receives is **not** copied into the table: an ingestion run embeds every chunk of every document, and the table would end up holding the whole corpus. Its hash is kept instead.

`kind` names the kind of model. It is decided by the names of the inputs of the request (`document`; `questions` / `state`; `systemPrompt` / `userPrompt`) and is missing when the `Request Data:` line can not be read. `max_tokens` is read from the option `max_tokens`, then from `max_completion_tokens`, then from `max_length`.

A hash is the SHA-256, in lower-case hex, of the UTF-8 bytes of the text with every run of white space made one blank; a character that has no UTF-8 bytes counts as a question mark. *Get-All-Prompts.sas* and *Link-Logs-To-Models.sas* hash a text the same way. The question of a retrieval therefore has the hash of the embedding call that embedded it: `rag_query_hash` of the LLM row equals `input_hash` of the embedding row in query mode.

A request that failed in the container writes no `Response:` line: the container logs the traceback and `Error occurred in the model scoring function`. Such a request ends at that line and has `failed` = 1, no `status_code` and its first ERROR line in `error_text` (cut after 500 characters; for the line the container writes for an exception, with the last line of the traceback added). `failed` is also 1 for a status of 400 and above, and for a request that was still running where its container's part of the file ends - that one has no error text.

The request headers are read for `X-Request-Id` and for the kind of client only: no address, no Referer, no cookie and no version of a client reaches the table. In `options` the value of `API_KEY` and of every other option that is a secret by its name (key, secret, password, credential, authorization, bearer, cookie, a token that is no number) or by its shape (`sk-...`, `Bearer ...`, a JWT, 20 and more letters and digits in a row that hold both) is replaced by `REDACTED` by the parser itself, whether or not the log was redacted when it was collected; what has the shape of a key in the error text is replaced as well.

## What happens to the existing table

- **A log without requests** (an hour without traffic, or a file that only holds the start-up lines of the containers) changes nothing: the parser writes a note to the SAS log and leaves the output table as it is.
- **A log that can not be parsed** (the file is missing, Python or one of its packages is not available) leaves the output table as it is as well and ends with an error.
- **Replacing** (the default) builds the new table under the name `<table>_NEW` beside the existing one. Only when that table exists is the old one dropped and the new one renamed, promoted and saved. If the new table can not be built the old one stays loaded.
- **Appending** writes the rows of the existing table and the parsed rows to one new table (`_TEMP_LLM_LOGS_ALL` in the caslib of the output table, by a DATA step) and puts it in the place of the existing one, the way replacing does; where no table exists the parsed rows become the table. A table that was saved but is not in memory - after a restart of the CAS server, or when the table is not promoted - is loaded from its `.sashdat` file first; if that file can not be loaded, nothing is appended or saved and the run ends with an error. The 36 columns of the parser stand in front in their order. A column the existing table lacks is added and is missing in its rows - a table of release 2.2 has the first twelve. A column the parser does not know stays behind the others with its type and length and is missing in the new rows. What the link wrote into existing rows stays. A text column the table holds as CHAR is handed over to the VARCHAR column of the parser without its trailing blanks, so new rows are not cut to its length; a column that is a number on one side and text on the other stops the DATA step, and the table is left as it is with the error `The new table could not be created, ...`. A table that was promoted is promoted again whatever `_log_promote_table` says; the saved copy is replaced only when `_log_save_table` is on. The table is read through a libref, so in append mode its name has to be one a libref can address (32 characters at most), and other sessions do not see the table between the drop and the promote. Rows are appended as they are parsed, so a log that is parsed twice is in the table twice. The DATA step of append mode has not been run in SAS.

## What the parser expects from the log

The parser reads the lines the containers write with `SAS_SCR_LOG_LEVEL_SCR_IO=TRACE`. A request starts at its `Request: POST` line and ends at its `Response: Status Code` line or, when it failed, at the `Error occurred in the model scoring function` line of its transaction; the inputs come from the `Request Data:` line and the numbers and the answer from the lines of the score code (`prompt_length:`, `tokens:`, `output_length:`, `run_time:`, `response:`, `answer:`, `answers:`). A line only counts when it starts as a log line (`<timestamp> - <logger> - <level> - `), so a prompt that quotes such a line is kept as text. A log whose lines carry something in front of that - the timestamp of `kubectl logs --timestamps`, the pod name of `--prefix`, a JSON wrapper of a log pipeline - yields no rows; when such a log holds `Request: POST` lines the parser says so with a warning instead of reporting a log without requests. A byte that is not valid UTF-8 is replaced, so one damaged line does not cost the rest of the file.

The collected file holds one container after the other. A request that is still running when a container's part of the file ends has no `Response: Status Code` line; it ends at the first line that is older than itself, which is where the next container's part begins, so the leftover lines there are not added to it.

Limits that follow from what the containers log:

- The lines of the score code and the `Response:` line name no request. While one request is running they are its lines. A request whose transaction (the `transaction_<id>` logger, the `transaction_id` of `Response Data:`) a later line still names was running when the next one started: a line that names no request and was written while more than one request was running is given to none of them - the values stay missing, `failed` stays missing where the log does not say - and the SAS log has a NOTE with the number of such requests. A `Response:` line is given to a request only when the next line is the `Response Data:` line that names it. Requests whose first lines interleave keep their request line only. A log whose lines name no transaction is read by the order of its lines alone. In the six collected logs of the test system (1247 requests) no request started before the one in front of it had ended, so this rule is read from the line format and not from a case.
- Only `temperature`, `top_p`, `top_k` and `max_tokens` have a column; every other option is in `options`.
- No line of a log names the model in SAS Model Manager or the prompt, decision template or RAG setup that sent a request. The link columns are filled by content, see below.

The collection script in the [Logging & Monitoring administration guide](../../website/docs/Administration-Guide/Logging-&-Monitoring.md) replaces provider keys and cookies in the log with `REDACTED` before the file is written. The parser reads a redacted log like an unredacted one, it never copies a request header other than the `X-Request-Id` into the table, and it replaces keys in the options and in the error text itself.

## What the custom step takes from the SAS session

*Log-Parser-Code.sas* starts a CAS session and assigns the librefs `public` and `casuser` in its first lines. The custom step is the program without them:

- It asks the `SESSFOUND` function whether the session that the SAS option `SESSREF` names is connected - that is the session its CAS actions run in. When it is connected, the step runs in it and does not end it. When it is not, the step starts a session of its own (`llmLogMySess`) and ends it as its last action, so of such a run only a table that was promoted or saved is left. The option goes on naming a session that was ended, the step's own included, so the name is not taken for a session: `%sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1` is the whole test, and *Log-Parser-Code.sas* holds the same line behind its own `cas mysess;`.
- It reads the caslib of the output table from its libref. When the libref is not assigned in the session, the name of the libref is handed to CAS as the name of the caslib.
- In append mode the program assigns the librefs `_llmtgt` (the caslib of the output table) and `_llmsrc` (`CASUSER`) and clears them, and keeps the column list of the existing table in `work._log_target_columns` while it reads it.

The code file and the step both need the caslib `CASUSER` of the user who runs them for the staging table `_TEMP_LLM_LOGS`, and PROC PYTHON with the pandas and swat packages. The guide lists what the step takes from the session in full, what was run on a SAS Viya system and what was not.

## The experiments: Get-All-Prompts.sas

*Get-All-Prompts.sas* reads what the Prompt Builder keeps in SAS Model Manager and writes five CAS tables. Every row carries `modelID` and `projectID`, the SAS Model Manager ids of the model - the prompt template, the decision template or the RAG setup - and of its project. The *LLM - Get All Prompts* custom step runs the same program.

| Table | One row per | Key |
|---|---|---|
| `PROMPT_EXPERIMENTS` | experiment run and model of a prompt template or decision template; one row for a model without a run | `modelID`, `runID`, `model` |
| `PROMPT_OPTIMIZATIONS` | optimization or comparison of a prompt template | `modelID`, `optimizationId` |
| `RAG_SETUPS` | RAG setup, saved by the Prompt Builder or registered by the step *RAG - Register Setup* | `modelID` |
| `RAG_RUNS` | ingestion run of a setup whose `<tables_prefix>_RUNS` table is in memory | `modelID`, `run_id` |
| `MODEL_CATALOG` | model and pattern: the template of a model, each of its experiment runs with the prompts as they were sent, a RAG setup with its collection, a model that answers | `modelID`, `role`, `runID`, `target_model` |

The settings are the `%let` lines at the top of the program:

| Macro variable | Default | |
|---|---|---|
| `_gap_tag` | `Prompt-Engineering` | Tag of the projects with prompt templates and decision templates |
| `_gap_rag_tag` | `RAG-Engineering` | Tag of the projects with RAG setups |
| `_gap_llm_project`, `_gap_embedding_project`, `_gap_decision_project` | `LLM Model Project`, `Embedding Model Project`, `Decision Model Project` | The projects of the model definitions: a name, a project id or `tag:<tag>`, several separated by `\|`; empty leaves the kind out of the catalog |
| `_gap_out_caslib` | `Public` | CAS library of all five tables |
| `_gap_out_experiments`, `_gap_out_optimizations`, `_gap_out_rag_setups`, `_gap_out_rag_runs`, `_gap_out_catalog` | the table names above | One table name per output |

In the custom step the output table names the table of the experiments and the CAS library; the other settings are fields of the step.

How it works: one PROC PYTHON step reads SAS Model Manager (it needs the package `requests`), shapes the rows and hands each table to SAS as a JSON file with a map in the WORK folder, with the LENGTH statement of the table in a macro variable. A DATA step reads each file - or, for a table without rows, only declares the columns, so that an empty output still has all of them - and PROC CASUTIL drops, loads, promotes and saves the table. `RAG_RUNS` is stacked in SAS from the run tables that are in memory; a run table that is not is left out with a NOTE, and no table of a setup is loaded or changed.

`PROMPT_EXPERIMENTS` keeps the 49 columns it had - the *LLM Usage Report* reads 38 of them - and has twelve more behind them: `modelID`, `projectID`, `projectName`, `kind` (`prompt` or `decision`), `expected`, `options`, `variables`, `resolved_system_prompt`, `resolved_user_prompt`, `resolved_system_hash`, `resolved_user_hash`, `judge_reasoning`. The resolved prompts are the templates of a run with the values of its variables filled in the way the Prompt Builder fills them, and the two hashes are taken of those texts before a text is cut to the length of a SAS column. In `options` the value of an option that is a secret by its name is replaced by `REDACTED`, by the names the log parser goes by. `runID` is a number in this table and text in `MODEL_CATALOG`.

`MODEL_CATALOG` has the columns `modelID`, `projectID`, `projectName`, `name`, `role`, `runID`, `target_model`, `system_template`, `user_template`, `resolved_system_prompt`, `resolved_user_prompt`, `collection`, `template_source`, `resolved_system_hash`, `resolved_user_hash`. `role` is `prompt`, `decision` or `rag` for what sends requests and `llm`, `embedding` or `decision_model` for what answers them; the `name` of an answering model is the name of its container, which is the `model` of a log row. `template_source` says where a row is from: `score code`, `best run` or `newest run` for the templates of a model, `builder` or `step` for a RAG setup. The guide lists the columns of all five tables.

Limits: a model whose file can not be read is named in a WARNING and keeps a row without runs; when the projects can not be listed the program ends with an ERROR and leaves the tables as they are; the prompts as sent are what the Prompt Builder sent in an experiment, and the templates of a manifested model are read from its score code without running it. A setup that was saved in the Prompt Builder and registered with the step *RAG - Register Setup* is two models, two rows of `RAG_SETUPS` and two `rag` rows of the catalog with one collection, and its runs are in `RAG_RUNS` once for each.

## Linking the log to the models and to what called them

*Link-Logs-To-Models.sas* fills the link columns of the log table: for every request the SAS Model Manager model that answered, and the manifested prompt, decision template or RAG setup that made the call. It reads the log table, `MODEL_CATALOG` and, when it is there, `RAG_RUNS`.

The settings are macro variables at the top of the program:

| Macro variable | Default | Meaning |
| --- | --- | --- |
| `_lnk_log_caslib`, `_lnk_log_table` | `Public`, `LLM_LOGS` | The log table. Its link columns are filled in place |
| `_lnk_catalog_caslib`, `_lnk_catalog_table` | `Public`, `MODEL_CATALOG` | The catalog that Get-All-Prompts.sas writes |
| `_lnk_runs_caslib`, `_lnk_runs_table` | `Public`, `RAG_RUNS` | The ingestion runs. Without the table no embedding row is linked to an ingestion run |
| `_lnk_redo_all` | `0` | `0` links the rows without a `match_method`, `1` links all rows again |
| `_lnk_min_literal` | `20` | The characters of fixed text a template needs to take part, whitespace not counted |
| `_lnk_utc_offset` | `0` | The hours the clock of the containers is ahead of UTC; the ingestion runs are recorded in UTC |
| `_lnk_promote_table`, `_lnk_save_table` | `1`, `1` | Promote and save the table, as the parser does |

### The link columns

| Column | What the program writes |
| --- | --- |
| model_id | The SAS Model Manager ID of the model that answered: the catalog row of the role `llm`, `embedding` or `decision_model` whose name is the name of the container. Empty when no model or two models have the name |
| caller_model_id, caller_project_id, caller_kind, caller_name | The prompt, decision template or RAG setup that made the request (`caller_kind`: `prompt`, `decision`, `rag`). Empty when `match_method` is `none` or `ambiguous` |
| experiment_run_id | The run, when `match_method` is `experiment run`. Text; it is the `runID` of `PROMPT_EXPERIMENTS`, which is a number there, and a run id counts inside one model only |
| rag_model_id | For an LLM row whose prompt carries a retrieval: the RAG setup of its collection. Filled whatever `match_method` says |
| match_method | The rule that decided: `experiment run`, `questions and state`, `questions`, `template`, `retrieval query`, `ingestion run`, `ambiguous`, `none` |
| match_candidates | The number of candidates the rule found: runs for `experiment run`, callers otherwise, 0 for `none` |

### The rules

No request and no log line names its caller, so every link is made by content. The first rule that fits decides.

| Kind of row | Rule | The row is linked when |
| --- | --- | --- |
| LLM, decision | `experiment run` | its prompts (for a decision its questions and state) are the prompts of an experiment run as they were sent - compared by the hashes the catalog holds of them -, the run went to the model of the row, and the request did not come from the score code or a SAS session (`user_agent` is not `python-requests` or `SAS`). Of several runs with the same prompts the newest is written |
| decision | `questions and state` | its questions are the questions of a decision template - compared as JSON, so key order and whitespace do not matter - and its state fits the state template |
| decision | `questions` | its questions are the questions of a decision template whose state template has no fixed text or does not fit |
| LLM | `template` | its system and user prompt fit the templates of a prompt: every `{{variable}}` stands for any text, also none or several lines, the fixed text is there in its order from the first character to the last, whitespace squeezed. The templates of earlier runs of a prompt count as its templates |
| embedding, query mode | `retrieval query` | the hash of its text is the hash of the query of a retrieval that an LLM row of the table carries, and all such LLM rows name the same collection. There is no time window: the same question is embedded many times. A setup of which the catalog names another embedding model than the model of the row is not taken |
| embedding, document mode | `ingestion run` | its time lies inside exactly one run of `RAG_RUNS` with its embedding model |

When several callers fit, the one with the most fixed text wins. A caller of which the catalog only names other target models is taken when no other caller fits. Callers that tie, two collections for one query and two overlapping ingestion runs are `ambiguous`; a row that fits nothing is `none`. Where two models hold one collection - a setup saved in the Prompt Builder and registered with the step - the model the Builder saved is taken (`template_source` = `builder`); two setups of the Builder, or two models of the step, with one collection are `ambiguous`. Rows of an earlier parser have no `kind`: it is taken from the role of the model that answered.

### What cannot be linked

- A request names no caller, and a direct call of a model (a script, a health check) has none.
- Two prompts or decision templates with the same text fit the same requests: `ambiguous`.
- A template that is one variable, or has less than 20 characters of fixed text, takes no part - it would fit the requests of other callers. The program counts such callers in a NOTE.
- A test call of the Builder that was not kept as a run and was made with a text that is no longer the template.
- The calls of a judge and of a prompt optimization: they send prompts of their own.
- The query of a retrieval that never reached an LLM prompt, an embedding call outside of an ingestion run, and a run without `finished_at`.
- A published model that sends the prompts of an experiment run again is linked to its prompt, not to the run.
- A template that is longer than a SAS column (32,767 bytes) is cut in the catalog and fits no request; an experiment run with such a prompt is still found, by its hash.

### How the tables join

- `LLM_LOGS.model_id` = `MODEL_CATALOG.modelID` - the model that answered
- `LLM_LOGS.caller_model_id` = `PROMPT_EXPERIMENTS.modelID` - the prompt or decision template; with `experiment_run_id` = `runID` (the first is text, the second a number) and `model` = `model` the row of the run
- `LLM_LOGS.caller_model_id` = `PROMPT_OPTIMIZATIONS.modelID` - the optimizations of that prompt
- `LLM_LOGS.caller_model_id` = `RAG_SETUPS.modelID` - the setup of an embedding row (`caller_kind` = `rag`)
- `LLM_LOGS.rag_model_id` = `RAG_SETUPS.modelID` - the setup whose retrieval an LLM prompt carried
- `RAG_RUNS.modelID` = `RAG_SETUPS.modelID` - the ingestion runs of a setup

`MODEL_CATALOG` has one row per model and pattern; join callers to it through a distinct list of `modelID`.

### What happens to the table

- The twelve columns of every release, the columns of the parser and every column the program does not know stay in CAS and are not rewritten. The program numbers the rows in a copy in `CASUSER`, reads what it compares (the prompts only of the rows it links), uploads the link columns with the row number and merges them back by that number.
- The new table is built as `<table>_NEW` beside the log table and replaces it the way the parser replaces a table; promote and save follow the two settings. While the program runs it needs room for two more copies of the table.
- A row that has a `match_method` is left as it is, also when the catalog has changed since; `_lnk_redo_all = 1` links every row again.
- A log table without the link columns, written by an earlier parser, is refused with one ERROR line. Without `MODEL_CATALOG` there is one ERROR line that names *Get-All-Prompts.sas*. The table is left as it is in both cases.
- A table that grew while it was linked is left as it is with an ERROR line: run the parser and the link program one after the other.

The SAS log ends with one NOTE per kind of row: the rows, how many are linked to a model, the callers by method, `ambiguous` and `none`.

## Tests

`tests/` runs the Python of the three programs outside of SAS on made-up logs and files and checks that the code files and the custom steps still hold the same programs:

```bash
python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
```

`tests/test_log_parser.py` runs the parser on made-up logs. The SAS code around that Python - the CAS actions that replace, append, load, promote and save the table - does not run in these tests: they hold its text and the order of its actions, and the text of the step's About tab and of the guide where those describe the program. They also hold the SAS text of append mode and what its DATA step writes for a table of twelve columns, for a table with some of the new columns, with a column the parser does not know, with CHAR text columns and with a column of the other type - by the rules of LENGTH and SET, not by a run in SAS. The condition of the session check is taken from the program and decided for three states of a SAS session - no CAS session, the step's own session ended with its name left in the option, a session of the user - with the values the two functions returned on SAS Viya 2026.09 on 2026-10-11: the option `SESSREF` named `CASAUTO` without a CAS session and the session's name after one was ended, and `SESSFOUND` returned 0 in both states and 1 for a connected session. What the runs on SAS Viya showed - the step twice and three times in one SAS session, a session of the user used and left alone - is in the guide under *What Was Measured*.

`tests/test_get_all_prompts.py` runs the Python of *Get-All-Prompts.sas* against a made-up SAS Model Manager that pages its listings and can fail a request (`tests/get_all_prompts_harness.py`), on trackers, setups and score code written for the tests in the shape of the real ones. It checks the variables filled into a prompt, a run without a system prompt, a decision template, the optimizations, both kinds of RAG setup, the catalog, paging, the failures and an empty output. The DATA steps, the stacking of the run tables and PROC CASUTIL do not run in these tests: they hold their text.

`tests/test_link_logs.py` runs the Python of *Link-Logs-To-Models.sas*. `tests/link_logs_harness.py` builds log rows and catalog rows with the columns of the two other programs and stands in for the SAS object and for CAS: it keeps tables as frames and does what the program asks CAS for. The tests cover every rule and their order, ambiguity, the minimum of fixed text, a prompt with a retrieval, ingestion windows, a second run and a redo, the columns the program does not know and the two refusals. They do not show what CAS does with the calls.

`tests/test_end_to_end.py` runs the three one after the other: log lines of all three kinds become rows, tracker, setup and score code files become the catalog, and the link program is given both. It also reads from the code of the three programs that they hand each other the same columns, kinds, roles, default table names and the same hash.

Each program lives in its code file and in a step (the link program has no step). Change *Log-Parser-Code.sas*, then carry the same code into the `templates.SAS` entry of *LLM - Log Parser.step* (the step is one line of JSON - load it, replace the entry and write it back compactly rather than editing it by hand). For *Get-All-Prompts.sas*, put the lines between the two `gap core` comments of the program between the step's own first and last lines. The tests fail while a step and its program differ.

## Where these tables are used

`Public.LLM_LOGS` is one of the four CAS tables behind the **LLM Usage Report** - the recommended monitoring template shipped in this folder as *LLM Usage Report.json*. The other three are the fact sheets *LLM_FACT_SHEET* / *EMBEDDING_FACT_SHEET* (loaded with `mdb load-facts` or *Load-Fact-Sheets.sas*) and *PROMPT_EXPERIMENTS* (from *Get-All-Prompts.sas*, which writes four further tables the report does not read). All four are expected in the *Public* caslib by default. The report reads the twelve columns of `LLM_LOGS` and the columns of `PROMPT_EXPERIMENTS` that both tables had before release 2.3.0; the columns this release adds are for reports of your own.

See the [Logging & Monitoring administration guide](../../website/docs/Administration-Guide/Logging-&-Monitoring.md) for how to import the report (SAS Environment Manager or the `sas-viya transfer` CLI) and how to point it at a different caslib if your tables are not in *Public*.
