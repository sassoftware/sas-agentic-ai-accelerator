# Parsing the Log

The goal is to create a table that extracts all of the relevant information from the container logs and collect everything as a CAS table.

For this two options are available:
1. The *Log-Parser-Code.sas* is a script that you can point at the logging file and it will parse all of the information into a CAS table.
2. The *LLM - Log Parser* custom step, which is just a nice little UI on top of the same SAS script - this step is located in the Custom-Steps folder.

By default the parser writes the CAS table **`LLM_LOGS`** to the **`Public`** caslib and both promotes it (global scope) and saves it to disk. Change the `_log_target_table_lib` / `_log_target_table_name` macro variables at the top of *Log-Parser-Code.sas* (or the custom step's options) to target a different library or table name.

The parser reads the log from the file system of the SAS compute server: `_log_path` has to start with `sasserver:`, and a file in SAS Content is not read. The default, `sasserver:/export/pvs/sasdata/data/llm/llms.log`, is an example path. The [Logging & Monitoring administration guide](../../website/docs/Administration-Guide/Logging-&-Monitoring.md) says what that path assumes and how the file reaches the compute server where the host that collects the logs shares no mount with it.

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

`timestamp` is a SAS datetime, the three prompt and response columns and `model` are text, everything else is numeric.

## What a row holds for each kind of model

Every scoring request becomes one row, whatever kind of model served it. The twelve columns are the same for all three kinds:

| Column | LLM | Decision model | Embedding model |
| --- | --- | --- | --- |
| system_prompt | the system prompt | the questions, as the JSON that was sent | missing |
| user_prompt | the user prompt | the state | missing |
| response | the response, its lines joined with a blank | the answers as JSON - every answer with its confidence and probabilities | missing |
| prompt_length | input tokens | input tokens | the tokens of the embedded text |
| output_length | output tokens | output tokens | missing |
| runtime | seconds | seconds | seconds |
| temperature, top_p, top_k, max_tokens | from the options of the request | from the options of the request | from the options of the request |

The text an embedding model receives is **not** copied into the table: an ingestion run embeds every chunk of every document, and the table would end up holding the whole corpus.

The table has no column that names the kind of model or the RAG project of an embedding call. The parser adds no columns on purpose: in append mode it writes into a table that already exists with these twelve columns, and widening such a table is not handled. Until then, join the model name to the fact sheets (`LLM_FACT_SHEET`, `EMBEDDING_FACT_SHEET`, `DECISION_FACT_SHEET`) to tell the kinds apart.

## What happens to the existing table

- **A log without requests** (an hour without traffic, or a file that only holds the start-up lines of the containers) changes nothing: the parser writes a note to the SAS log and leaves the output table as it is.
- **A log that can not be parsed** (the file is missing, Python or one of its packages is not available) leaves the output table as it is as well and ends with an error.
- **Replacing** (the default) builds the new table under the name `<table>_NEW` beside the existing one. Only when that table exists is the old one dropped and the new one renamed, promoted and saved. If the new table can not be built the old one stays loaded.
- **Appending** adds the parsed rows to the existing table, or creates the table if it does not exist yet. A table that was saved but is not in memory - after a restart of the CAS server, or when the table is not promoted - is loaded from its `.sashdat` file first, so the save that follows keeps the rows of the runs before; if that file can not be loaded, nothing is appended or saved and the run ends with an error. Rows are appended as they are parsed, so a log that is parsed twice is in the table twice.

## What the parser expects from the log

The parser reads the lines the containers write with `SAS_SCR_LOG_LEVEL_SCR_IO=TRACE`. A request starts at its `Request: POST` line and ends at its `Response: Status Code` line; the inputs come from the `Request Data:` line and the numbers and the answer from the lines of the score code (`prompt_length:`, `tokens:`, `output_length:`, `run_time:`, `response:`, `answers:`). A line only counts when it starts as a log line (`<timestamp> - <logger> - <level> - `), so a prompt that quotes such a line is kept as text. A log whose lines carry something in front of that - the timestamp of `kubectl logs --timestamps`, the pod name of `--prefix`, a JSON wrapper of a log pipeline - yields no rows; when such a log holds `Request: POST` lines the parser says so with a warning instead of reporting a log without requests. A byte that is not valid UTF-8 is replaced, so one damaged line does not cost the rest of the file.

The collected file holds one container after the other. A request that is still running when a container's part of the file ends has no `Response: Status Code` line; it ends at the first line that is older than itself, which is where the next container's part begins, so the leftover lines there are not added to it.

Two limits follow from what the containers log:

- The lines of the score code carry no request id. If a container logs a second request before it has answered the first, the numbers and the answer of the two can not be told apart. Each request still gets its row with its own inputs.
- Only the options `temperature`, `top_p`, `top_k` and `max_tokens` have a column. Other options a model takes (`reasoning_effort`, `max_completion_tokens`, ...) are not kept.

The collection script in the [Logging & Monitoring administration guide](../../website/docs/Administration-Guide/Logging-&-Monitoring.md) replaces provider keys and cookies in the log with `REDACTED` before the file is written. The parser reads a redacted log like an unredacted one, and it never copies a key or a request header into the table.

## What the custom step takes from the SAS session

*Log-Parser-Code.sas* starts a CAS session and assigns the librefs `public` and `casuser` in its first lines. The custom step is the program without them:

- It asks the `SESSFOUND` function whether the session that the SAS option `SESSREF` names is connected - that is the session its CAS actions run in. When it is connected, the step runs in it and does not end it. When it is not, the step starts a session of its own (`llmLogMySess`) and ends it as its last action, so of such a run only a table that was promoted or saved is left. The option goes on naming a session that was ended, the step's own included, so the name is not taken for a session: `%sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1` is the whole test, and *Log-Parser-Code.sas* holds the same line behind its own `cas mysess;`.
- It reads the caslib of the output table from its libref. When the libref is not assigned in the session, the name of the libref is handed to CAS as the name of the caslib.

The code file and the step both need the caslib `CASUSER` of the user who runs them for the staging table `_TEMP_LLM_LOGS`, and PROC PYTHON with the pandas and swat packages. The guide lists what the step takes from the session in full, what was run on a SAS Viya system and what was not.

## Tests

`tests/` runs the parser's Python outside of SAS on made-up logs and checks that *Log-Parser-Code.sas* and the custom step still hold the same program:

```bash
python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
```

The SAS code around that Python - the CAS actions that replace, append, load, promote and save the table - does not run in these tests: they hold its text and the order of its actions, and the text of the step's About tab and of the guide where those describe the program. The condition of the session check is taken from the program and decided for three states of a SAS session - no CAS session, the step's own session ended with its name left in the option, a session of the user - with the values the two functions returned on SAS Viya 2026.09 on 2026-10-11: the option `SESSREF` named `CASAUTO` without a CAS session and the session's name after one was ended, and `SESSFOUND` returned 0 in both states and 1 for a connected session. What the runs on SAS Viya showed - the step twice and three times in one SAS session, a session of the user used and left alone - is in the guide under *What Was Measured*.

The program lives in both files. Change *Log-Parser-Code.sas*, then carry the same code into the `templates.SAS` entry of *LLM - Log Parser.step* (the step is one line of JSON - load it, replace the entry and write it back compactly rather than editing it by hand); the tests fail while the two differ.

## Where this table is used

`Public.LLM_LOGS` is one of the four CAS tables behind the **LLM Usage Report** - the recommended monitoring template shipped in this folder as *LLM Usage Report.json*. The other three are the fact sheets *LLM_FACT_SHEET* / *EMBEDDING_FACT_SHEET* (loaded with `mdb load-facts` or *Load-Fact-Sheets.sas*) and *PROMPT_EXPERIMENTS* (from *Get-All-Prompts.sas*). All four are expected in the *Public* caslib by default.

See the [Logging & Monitoring administration guide](../../website/docs/Administration-Guide/Logging-&-Monitoring.md) for how to import the report (SAS Environment Manager or the `sas-viya transfer` CLI) and how to point it at a different caslib if your tables are not in *Public*.
