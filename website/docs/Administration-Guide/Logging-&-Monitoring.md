---
sidebar_position: 10
title: Logging & Monitoring
---

# Logging

The logs from the LLM containers is standardized across all of the different models.
This has been done in order to be able to parse all of the relevant information from the logs and create monitoring on top of them.

In order to make use of these logs, which are written to the standard out of each container, you must collect the logs.
If you do not have an established logging and monitoring process, you can use the simplified logging script provided below.
Otherwise, if you do have an established way of collecting logs, ensure that you can export them to a folder that can be accessed from SAS Studio as a `.log` file (as that it is the input for the parsing utilities).
The file has to hold the lines as the containers wrote them: the parser only reads a line that starts with `<timestamp> - <logger> - <level> - `, so a timestamp, a pod name or a JSON wrapper that your process adds to each line has to be removed again. If it is not, the parser reads no request and says so with a warning in the SAS log. The lines of each container also have to stay together in the file, as `kubectl logs` writes them: an export that orders the lines of all containers by their time is read with gaps, see [Requests that run at the same time](#requests-that-run-at-the-same-time).

For more information on how to parse the log and load it into SAS please take a look at `SAS-Viya-Integrations/Logging-Monitoring`.

Three programs of that folder work on the collected log, and they run in this order:

1. **Collect** the log - the script below, or your own process.
2. **Parse** it: `Log-Parser-Code.sas` or the *LLM - Log Parser* custom step write one row per request to the table `LLM_LOGS` - see [Running the Parser](#running-the-parser) and [The Columns of LLM_LOGS](#the-columns-of-llm_logs).
3. **Pull** what the Prompt Builder keeps in SAS Model Manager: `Get-All-Prompts.sas` or the *LLM - Get All Prompts* custom step write the experiments, the RAG setups and a catalog of models - see [The Experiments of Every Kind](#what-it-reads).
4. **Link** the two: `Link-Logs-To-Models.sas` fills, for every row of `LLM_LOGS`, the SAS Model Manager id of the model that answered and of the prompt, decision template or RAG setup that sent the request - see [Linking the Log to the Models](#the-rules-in-their-order).

Steps 2 to 4 of release 2.3.0 ran as one chain on the test system on 2026-10-11 - the parse with its 24 further columns, the pull with its four further tables, the link, and the joins of the tables: see [The Chain on SAS Viya](#the-chain-on-sas-viya). That run found defects, and the three programs were changed after it. What they do since is **not run in SAS**, and each section says which part that is: the Python of the three programs runs in the tests of the repository and was run outside of SAS on the log of that day. [What Was Measured](#what-was-measured) is about the parser as it was before the further columns.

:::note What the containers log
The shipped deployment templates set `SAS_SCR_LOG_LEVEL_SCR_IO=TRACE`, and the parser depends on it: the `Request: POST` / `Request Data:` lines carry the inputs and the options of every call, and the lines of the score code the token counts, the run time and the answer. For an LLM that is the system prompt, the user prompt and the full response; for a decision model the state, the questions and every answer with its confidence; for an embedding model the embedded text. That is what makes the usage report possible, and it also means prompt text and responses land in your log pipeline. Of an embedding call the table keeps the hash of the text in place of the text, and it also keeps the options of every call without their keys and the first error of a request that failed. Decide who may read the collected log file and the `LLM_LOGS` table accordingly, or lower the level and forgo the report.
:::

:::danger The containers also log secrets
At this level a container writes more than prompts. The options of a request are logged as they arrive, and for every model that is called with a provider key they hold it as `API_KEY:<the key>`. The request headers are logged too, and a call from the browser (the Prompt Builder) carries the user's `Cookie` header. Both must not reach a file that SAS Studio users can open: the collection script below replaces them with `REDACTED` before a line is written, and an established logging process needs the same step. The `redact_llm_log` function of the script expects the lines as the container wrote them, which is how `kubectl logs` prints them without `--timestamps` or `--prefix`: its three header expressions match a header at the start of a line, and only the `API_KEY` expression finds its text anywhere in a line. A process that puts a timestamp or a pod name in front of every line, or wraps the line (the log files of the container runtime, JSON), has to redact before that happens or adapt the three header expressions.

A log file collected **without** this redaction - with the script of release 2.2.0 or earlier, for example - may contain provider keys and session cookies. Delete those files, including copies and backups, and rotate every provider key that was in use while they were collected. The `LLM_LOGS` table does not need to be rebuilt for this: the parser never copied the key or a header into it. The parser of this release keeps the options of a request and replaces the keys in them itself, and of the headers it keeps the `X-Request-Id` and the kind of client.
:::

## Simplified Logging via a Script

Only use this script if you do not have a more robust process in place.

Create a script—for example `collect_llm_logs.sh`—and ensure that it is executable.
Use the code below and change both the namespace and the log path.
The namespace should be the namespace into which you have deployed the models, and the path needs to be reachable from inside of SAS Studio. The path below is an example and site specific - see [Getting the File to the SAS Compute Server](#getting-the-file-to-the-sas-compute-server) for what it assumes and for a way without it.
You can also change the logging interval; note that any changes you make here will have to be reflected in the entry for the `crontab`.
The default here also replaces the log file as the assumption is that you pick up the new entries using the parsing script and append (though a full replacement is also supported).

```bash
# Set the script variables for your environment
llm_namespace="llm"
llm_log_path="/viya-share/pvs/sasdata/data/llm/llms.log"
llm_logging_interval="1h"
# Selects the model pods: the deployment templates label each of them app=<model name>
llm_pod_selector="app"

# Replaces the secrets in the container log with REDACTED: the provider key in the
# options of a request and the values of the Cookie, Authorization and X-CSRF-TOKEN
# request headers (in any spelling of upper and lower case, which needs GNU sed)
redact_llm_log() {
  sed -E \
    -e "s/(API_KEY:)[^,}'\"]*/\1REDACTED/g" \
    -e 's/^((.* Request Headers: )?Cookie:).*/\1 REDACTED/I' \
    -e 's/^((.* Request Headers: )?Authorization:).*/\1 REDACTED/I' \
    -e 's/^((.* Request Headers: )?X-CSRF-TOKEN:).*/\1 REDACTED/I'
}

# Remove the next line to append to the log file instead
rm -f "$llm_log_path"
for pod in $(kubectl get pods -n "$llm_namespace" -l "$llm_pod_selector" -o name); do
  kubectl logs -n "$llm_namespace" "$pod" --all-containers --since="$llm_logging_interval" | redact_llm_log >> "$llm_log_path"
done
```

The script reads the model pods only. `-l app` selects every pod that carries a label named `app`, which the shipped deployment templates set to the name of the model. The pod of a `stage-weights` job does not carry it and stays out of the file, and so does every other workload that is labelled differently. If something else in your namespace is labelled `app` as well, give the model deployments a label of their own and set `llm_pod_selector` to it (for example `llm_pod_selector="accelerator=model"`).

The redaction happens in the pipe, so an unredacted line is never written to disk, and the parser reads a redacted log exactly like an unredacted one. The four `sed` expressions cover what the containers are known to log; if your requests carry a secret in another header, add a line for it.

An hour without a single request leaves a file without requests. The parser reports that with a note and leaves the `LLM_LOGS` table as it is.

Next add the following entry to your crontab (`crontab -e`), adjusting your path to the script:

```bash
0 * * * * /viya-share/pvs/sasdata/data/llm/collect_llm_logs.sh
```

This entry will run the log collection script at the top of every hour.

## Getting the File to the SAS Compute Server

The paths of this guide are examples, and both are site specific. The script writes to `/viya-share/pvs/sasdata/data/llm/llms.log` and the parser's default is `%let _log_path = sasserver:/export/pvs/sasdata/data/llm/llms.log;`. The two name the same file only on a system where the host that runs the script and the SAS compute server mount the same storage under these two paths. Set `llm_log_path` to the file as the collecting host sees it and `_log_path` to the same file as a compute session sees it, with `sasserver:` in front: the parser reads the file from the file system of the compute server, stops with an error for any other location and does not read from SAS Content.

Whether a compute session sees a directory can be asked in that session, `exists=1` means it does:

```SAS
data _null_;
    exists = fileexist('/export/pvs/sasdata/data/llm');
    put exists=;
run;
```

### Without a Shared Mount

On the test system (SAS Viya 2026.09, measured on 2026-10-11) there is no such mount: in a compute session neither `/export` nor `/viya-share` existed, and the workstation that collected the logs shares no directory with the compute server. The file went through SAS Content there:

1. The script of this guide wrote the redacted file to a local directory of the collecting host (`llm_log_path`).
2. The file was uploaded to a folder in SAS Content with the Files service: `POST /files/files` with the folder as `parentFolderUri`, the file as the body and its name in the `Content-Disposition` header.
3. The job that runs the parser first copied the file from SAS Content into the compute session, with a `FILESRVC` file reference and `fcopy`, and the parser then read the copy:

```SAS
/* Copy the collected file from SAS Content to the SAS compute server */
filename llmsrc filesrvc folderpath='/Users/<user>/My Folder/<folder>' filename='llms.log' recfm=n lrecl=32767;
filename llmdst '/tmp/llms.log' recfm=n lrecl=32767;
data _null_;
    length msg $300;
    rc = fcopy('llmsrc', 'llmdst');
    msg = sysmsg();
    put 'Copy of llms.log: ' rc= msg=;
run;
filename llmsrc clear;
filename llmdst clear;

/* Log-Parser-Code.sas follows, with its own "%let _log_path" line changed to this one */
%let _log_path = sasserver:/tmp/llms.log;
```

The last line is not one more line in front of the program. It is the `%let _log_path` line that `Log-Parser-Code.sas` has among its first lines, changed to name the copy: the program sets `_log_path` itself, so a `%let` that only stands in front of it has no effect. Both were measured on 2026-10-11. With the block in front and the program's own line changed to `sasserver:/tmp/llms.log`, the parser wrote the 174 rows of the file. With the block in front of the unchanged program, the copy went through and the program then read its default path: `FileNotFoundError: [Errno 2] No such file or directory: '/export/pvs/sasdata/data/llm/llms.log'`, then `ERROR: The log could not be parsed, ...`, and the job ended as failed. The custom step has no such line: there the copy is the file the step is given as its log.

These are the statements that ran. On 2026-10-11 they ran as they are printed here, with only the folder filled in - one below the *My Folder* of the user who ran the job: the log said `Copy of llms.log: rc=0 msg=`, and the copy had the size and the MD5 digest of the uploaded file (558,176 bytes). In the first runs, earlier that day, each collection had a file name of its own; `fcopy` returned 0 there as well, and a file of 0 bytes arrived as a file of 0 bytes. `/tmp` is a directory that existed in the compute session of that system - the `fileexist` step above gave `exists=1` for it and `exists=0` for `/export/pvs/sasdata/data/llm`; take one your compute server can write to. The three steps were started one after the other, not from a crontab entry.

The file in SAS Content holds the prompts and the responses like the log itself, so whoever may read that folder reads them (see the note at the top of this page).

## Running the Parser

`Log-Parser-Code.sas` and the *LLM - Log Parser* custom step hold the same program. The README of `SAS-Viya-Integrations/Logging-Monitoring` describes the table it writes, what a row holds for each kind of model and what happens to a table that already exists. The two differ in what they bring along:

- **`Log-Parser-Code.sas`** starts a CAS session itself (`cas mysess;`) when the SAS session has none that is connected, assigns the librefs `public` and `casuser` and sets its options in the `%let` lines at its top. The session and the two librefs stay when the program ends. `_log_target_table_lib` names a libref: the program looks up the caslib behind it and, when no libref of that name is assigned, hands the value to CAS as the name of the caslib.
- **The custom step** is the same program without those first lines, so it takes the following from the SAS session it runs in.

### What the Custom Step Takes from the Session

- **A CAS session.** The step asks the `SESSFOUND` function whether the session that the SAS option `SESSREF` names is connected - that is the session its CAS actions run in. When it is connected, the step starts no session, runs in that one and does not end it. When it is not, the step starts a session of its own (`cas llmLogMySess;`) and ends it as its last action. A table that is not promoted belongs to the session that created it, so of a run with a session of its own only what *Promote the table to global scope* or *Save the table* kept is left. The option goes on naming a session that was ended: on the test system `_CASNAME_`, `_SESSREF_` and the option `SESSREF` still named `LLMLOGMYSESS` after such a run, while `cas _all_ list;` listed no session, and SAS wrote `NOTE: The default CAS session LLMLOGMYSESS identified by SAS option SESSREF= was terminated. Use the OPTIONS statement to set the SESSREF= option to an active session.` That is why the step does not go by a name, and why statements of your own that follow the step in the same SAS session and need CAS have to start a session or set `SESSREF` to one.
- **The libref of the output table.** SAS Studio hands the step the output table as a libref and a table name. The step looks the libref up in `dictionary.libnames` and writes to the caslib it finds there. When the libref is not assigned in the session, nothing is found and the name of the libref is handed to CAS as the name of the caslib - which is the caslib you chose only where the two have the same name, as with `CASUSER` and `PUBLIC`. Without the libref the lines SAS Studio adds behind the step also report the output table as not existing: in the job that had a CAS session and no libref, the log held the line `_FLW_ACTION_TABLE_|CASUSER|<table>|0`, where the job with the libref had `|1`. The table was there - promoted, saved and read from a new CAS session with its 174 rows. What SAS Studio does with that line in a flow was not looked at; assign the libref in front of the flow to have it report the table.
- **The caslib `CASUSER`** of the user who runs the step. The parsed rows are uploaded there as `_TEMP_LLM_LOGS`, and the staging table is dropped at the end. In replace mode they are copied from there to `<table>_NEW` in the caslib of the output table; in append mode a DATA step reads them from there.
- **Two librefs and a work table of its own, in append mode.** To append to a table that exists, the program assigns `_llmtgt` to the caslib of the output table and `_llmsrc` to `CASUSER`, asks CAS for the columns of the existing table (kept in `work._log_target_columns` while it is read, then deleted), writes the existing rows and the new ones to `_TEMP_LLM_LOGS_ALL` in the caslib of the output table with a DATA step, and clears both librefs. The DATA step reads the output table through `_llmtgt`, so the name of the table has to be one a libref can address (32 characters at most). `Log-Parser-Code.sas` does the same. The append ran on the test system on 2026-10-11, see [Appending](#appending).
- **PROC PYTHON with the pandas and swat packages, and what Python needs to reach CAS:** the SAS options `CASHOST` and `CASPORT`, the certificate file named in the environment variable `SSLCALISTLOC` and the token in `SAS_SERVICES_TOKEN`. The Python code connects to the CAS session of the step with them.

The code SAS Studio generates for a flow provides neither a CAS session nor the libref: the code generated on the test system for a flow of the step and its output table held no `cas` and no `libname` statement. The measurements ran that code unchanged as SAS Job Execution jobs, with the output table in `CASUSER`, in three kinds of session. The first had these two lines in front (the name of the session is free):

```SAS
cas mysess;
libname casuser cas caslib='casuser';
```

The second had the `cas` statement alone in front, and the third neither of the two, so that the step started its own session. [What Was Measured](#what-was-measured) has what each of them did. These runs were made with the step as it was earlier on 2026-10-11, which started its own session where the macro variable `_CASNAME_` did not exist. The step now reaches the same two branches through `SESSFOUND`, and with that check it was run again later that day: see [The Session Check with SESSFOUND](#the-session-check-with-sessfound).

### Reading the Job Log

The parser says in the SAS log what it did with the output table:

- `NOTE: The log contains no requests, the table <caslib>.<table> was left as it is.` - an empty log, or one that holds no scoring request.
- `WARNING: Python-Subprocess - No request was read: 'Request: POST' was found in <n> line(s) that do not start as a log line ...`, followed by the same note - the lines of the log carry something in front, see the top of this page.
- `ERROR: The log could not be parsed, the table <caslib>.<table> was left as it is.` - the Python code did not finish, for example because the file is missing. Measured for a file that is missing: the line stood below `ERROR: Unhandled Python exception.` and a traceback that ends in `FileNotFoundError: [Errno 2] No such file or directory: '<path>'`, `SYSCC` was 1012 and the job ended as `failed`.
- An error that says `The saved table <caslib>.<table> could not be loaded, nothing was appended and the saved file was left as it is.` (append) or `The new table could not be created, the table <caslib>.<table> was left as it is.` (replace and append). In append mode that line goes on with what the DATA step that appends returned: `The DATA step that appends ended with the return code <n> and wrote <r> rows, the table held <e> and the log <u> (-1 stands for a table that was not found).`
- `NOTE: <n> request(s) were running at the same time as another request - of their container, or of another container whose lines are mixed with theirs in the file. ... <m> request(s) can have missing values, and failed is missing for <k> request(s).` - see [Requests that run at the same time](#requests-that-run-at-the-same-time). Not seen in a collected log so far.
- `NOTE: <n> request(s) hold a value that could not be read, for example a 'Request Data:' line that was cut off or options that are nested deeper than Python decodes. The rows were kept with what was read.`
- `NOTE: <n> request(s) hold a character that has no UTF-8 bytes in a text column - half of a surrogate pair in a request body. It was replaced by a question mark, the way the hash of a text counts it.` Such a character stopped the upload of the whole file before.
- In append mode the log holds the notes of a DATA step that writes `_TEMP_LLM_LOGS_ALL`. Seen on the test system on 2026-10-11, with the program as it was that day: the step ran in Cloud Analytic Services, in several threads, and for an existing table whose `temperature` was text it wrote `ERROR: Variable temperature has been defined as both character and numeric.`; SAS went on in syntax check mode (`OBS=0`), the program wrote `ERROR: The new table could not be created, the table <caslib>.<table> was left as it is.`, the table was unchanged and the job ended as failed. Since then the program also asks for the return code of that step and compares the rows it wrote with the rows of the two tables it read, before the new table takes the place of the existing one; that check and the longer ERROR line are not run in SAS.

When you search a job log for `ERROR`, start at the first line of the program: lines before the first source line are not the parser's. On the test system every job log of the *SAS Studio compute context* began with two such messages - `ERROR: Failed to connect to host ...` and `ERROR: Unable to connect to Cloud Analytic Services ...` - before source line 1, also in a job that held no parser code. They stand on three log lines, all three of the type error, because the second message is longer than the line and goes on with `parameters and retry.`: a check that counts the lines of the type error finds three, one that counts the lines that start with `ERROR` finds two. The jobs in which nothing else failed ended as `completed`, `SYSCC` was 0 after the parser had run, and from source line 1 on their logs held no ERROR and no WARNING line.

## The Columns of LLM_LOGS

One row per scoring request, whatever kind of model answered it. The first twelve columns are the ones the table had before release 2.3.0 - names, order, types and meanings unchanged, the *LLM Usage Report* reads them - and 24 follow behind them.

| Column | What it holds |
|---|---|
| `timestamp`, `model` | When the request arrived, and the model it was sent to: the name of its container |
| `system_prompt`, `user_prompt`, `response` | For an LLM the two prompts and the response; for a decision model the questions as the JSON that was sent, the state and the answers as JSON; missing for an embedding model |
| `temperature`, `top_p`, `top_k`, `max_tokens` | From the options of the request. `max_tokens` is read from `max_tokens`, then `max_completion_tokens`, then `max_length` |
| `prompt_length`, `output_length`, `runtime` | What the score code logged: input tokens, output tokens, seconds. For an embedding model `prompt_length` holds its tokens |
| `kind` | `llm`, `embedding` or `decision`, by the names of the inputs of the request (`document`; `questions` / `state`; `systemPrompt` / `userPrompt`). Missing when the `Request Data:` line can not be read |
| `status_code` | The status of the `Response:` line. Missing when the log holds no such line for the request |
| `failed` | 1 when the log holds an ERROR line for the request, no `Response:` line, or a status of 400 and above; otherwise 0. A request that was still running where its container's part of the log ends has no `Response:` line either and is a 1 without `error_text`. Missing for a request that ran at the same time as another one where no line of the log says which of them a response belongs to - never 1 for that reason |
| `error_text` | The first ERROR line of the request, cut after 500 characters; for the line the container writes for an exception, with the last line of the traceback added. What has the shape of a key is replaced by `REDACTED` |
| `transaction_id` | The id the container gave the request (the `transaction_<id>` logger, the `transaction_id` of `Response Data:`) |
| `request_id` | The `X-Request-Id` header |
| `user_agent` | The kind of client by the `User-Agent` header: `python-requests` (the score code of another model, a job, a script), `browser`, `SAS` or `other`. No version, no address and no Referer is kept, and no other header is read |
| `embedding_mode` | The `Embedding_Mode` option of a call to an embedding model, as sent. Missing when the call sent none; the model then used its default, which the log does not say |
| `project` | The `project` input of a call to an embedding model (the RAG runtime sends `rag`) |
| `input_hash` | The SHA-256 of the text an embedding model was given. The text itself is not kept |
| `answer`, `answer_confidence` | The headline of a decision: the answer to the first question and its confidence. `response` holds all answers |
| `options` | The options as they were sent, with the value of `API_KEY` and of every other option that is a secret by its name (key, secret, password, passwd, pwd, credential, authorization, bearer, cookie, a token that is no number) or by its shape (`sk-...`, `Bearer ...`, a JWT, 20 and more letters and digits in a row that hold both) replaced by `REDACTED`. A name also says so with `pass` or `pw` as one of its words (`my_pass`, `userPass`, `pw` - not `bypass`) and with `passphrase`. Inside a value the user and the password of an address (`scheme://user:password@host`), a header that carries a secret with everything behind its name, and what follows `bearer` or `basic` are replaced as well. The parser does this itself, whether or not the collection redacted the log |
| `rag_collection`, `rag_query_hash` | For an LLM whose user prompt holds the results of a retrieval: the collection and the SHA-256 of the question. The results are the JSON object the RAG runtime writes, with all six of its keys (`query`, `hits`, `graph_context`, `retrieval_mode`, `collection`, `ingestion_run_id`); of several such objects in one prompt the last one counts. The search tries 500 opening braces of a prompt and decodes 2,000,000 characters at most, so a prompt with more braces than that in front of its retrieval gets no collection |
| `model_id`, `caller_model_id`, `caller_project_id`, `caller_kind`, `caller_name`, `experiment_run_id`, `rag_model_id`, `match_method`, `match_candidates` | Written empty by the parser. No line of a log names the model in SAS Model Manager or the prompt, decision template or RAG setup that sent a request; [`Link-Logs-To-Models.sas`](#the-rules-in-their-order) fills them |

A hash is the SHA-256, in lower-case hex, of the UTF-8 bytes of the text with every run of white space made one blank. The three programs hash a text the same way. That is how the question of a retrieval is found again: its `rag_query_hash` is the `input_hash` of the call that embedded it in query mode. `timestamp` is a SAS datetime; `temperature`, `top_p`, `top_k`, `max_tokens`, `prompt_length`, `output_length`, `runtime`, `status_code`, `failed`, `answer_confidence` and `match_candidates` are numbers; every other column is text (VARCHAR).

### A request that failed

A request whose score code raises writes no `Response:` line: the container logs the traceback and `Error occurred in the model scoring function`. Such a request ends at that line and is a row with `failed` = 1, no `status_code` and its error in `error_text`. Before this release it was a row without numbers and response that nothing told apart from a request still running.

### Appending

The rows of the existing table and the rows of the log are written to one new table, which takes the place of the existing one as in replace mode; a table that was promoted is promoted again, whatever the option of the run says. The 36 columns stand in front in their order. A column the existing table lacks - a table of release 2.2 has the first twelve - is added and is missing in its rows; a column the parser does not know stays behind the others, with its type and length, and is missing in the new rows; what the link wrote into existing rows stays. A text column that the existing table holds with a fixed length (CHAR) is read under another name and its values are handed to the VARCHAR column without their trailing blanks, so nothing of the new rows is cut to the old length. A column that is a number in the table and text in the parser, or the other way round, stops the DATA step: the table is left as it is and the log has `The new table could not be created, the table <caslib>.<table> was left as it is.` The saved copy is replaced by the wider table when saving is on, and left as it was when it is off. Appending rewrites the whole table on every run, and other sessions do not see it between the drop and the promote. To fill `kind` and the other new columns for rows that were parsed earlier, parse their log files again: the parser does not go back over rows that are in the table.

The new table only takes the place of the existing one when the DATA step that wrote it ended with a return code of at most 4 and wrote as many rows as the existing table and the log hold together. Otherwise the program writes one ERROR line with the return code and the three row counts, drops what the DATA step left and leaves the existing table as it is. The new table gets the label of the existing table, and `Extracted LLM Log Data` - the label of a table the parser writes fresh - where that one had none. Both are changes made after the runs below, and are not run in SAS.

Measured on the test system on 2026-10-11, with the program as it was that day (version 2.2 of the step), on a table of twelve columns and 174 rows that the parser had written before it had the further columns (version 2.1 of the step):

- **Into the table of twelve columns.** Appending the log of 1040 requests gave 1214 rows and 36 columns in their order, promoted again and saved again. The 174 earlier rows had missing values in `kind`, `failed` and the link columns, and the first twelve columns of the 1214 rows were those of the 174 earlier rows and of the 1040 parsed ones. The DATA step ran in Cloud Analytic Services, in several threads, read 174 + 1040 rows and wrote 1214. Afterwards no `_TEMP_LLM_LOGS_ALL`, no libref `_llmtgt` or `_llmsrc` and no `work._log_target_columns` was left, and the log held no ERROR and no WARNING. 138 requests are in both collections and are in the table twice.
- **Into a table with CHAR text columns** (`model`, `system_prompt` and `user_prompt` as CHAR 64, 300 and 100): no error, 1214 rows, every text column VARCHAR, and the longest values of the three columns were those of a fresh parse - 1315, 22559 and 2481 characters -, so nothing was cut. The 174 earlier rows were unchanged.
- **Into a table with two columns the parser does not know:** 38 columns, the 36 in front and the two behind them, filled in the 174 earlier rows and missing in the 1040 new ones.
- **Into a table whose `temperature` is text:** the two ERROR lines quoted under [Reading the Job Log](#reading-the-job-log). The table kept its 174 rows, its twelve columns and its modified time, no file was saved and no staging table was left.
- **The label of the table was lost.** The table the parser had written fresh had the label `Extracted LLM Log Data`; after the append it had none. That is what the label of the paragraph above was added for.

Not measured: an append onto a table that is saved and not in memory with the program of this release, and its `could not be loaded` branch.

### Requests that run at the same time

Only the lines of a transaction (the `transaction_<id>` logger) and the `Response Data:` line name the request they belong to. Every other line is given to a request where the lines around it leave one request it can be the line of, and to none otherwise:

- **The first lines of a request** - the headers, the request data, the two `Randomly generated` lines - are the lines of the request whose request line stands in front of them, as long as no other request line stands in between. A first line that no request is waiting for shows that requests wrote their first lines at the same time: every request since the one whose first lines broke off keeps its request line only.
- **The first line of a transaction** belongs to the one request of its model that has all its first lines and no transaction yet. Where two such requests are waiting, the transaction is given to neither: both rows keep their prompts, `options`, `request_id`, `kind` and `user_agent` and have no `transaction_id`, `status_code`, token counts, run time, response or `error_text`.
- **The lines of the score code** (`prompt_length:`, `output_length:`, `run_time:`, `response:`, `answer:`, `answers:`, its ERROR line) **and the `Response:` line** are the lines of the one transaction that is running. A request is taken as still running when a later line of the log names its transaction. While a second one is running, such a line is given to neither.
- **`failed` is missing** for a request whose response could not be given to it. It is never 1 for that reason.
- **A request that waits.** A request whose first lines are complete and that never gets a transaction stays waiting until its container's part of the file ends. The requests of that model that follow it in the part are then left open, and the NOTE counts them.

The SAS log then has `NOTE: <n> request(s) were running at the same time as another request - of their container, or of another container whose lines are mixed with theirs in the file. ...`, with the number of requests that can have missing values and of those whose `failed` is missing.

**The lines of each container have to stay together in the file**, as `kubectl logs` writes them and the collection script appends them. In a file that mixes the lines of several containers - a time-ordered export of a log store - the score lines of a request that ran while a request of another container was running are given to neither, so its token counts, its run time and its response are missing, and the NOTE counts it. A request line for another model does not end a request that is running.

**A response that holds lines of a log.** While a request is the only one that is running, the lines of its response up to the next line of its transaction are kept as text, also where they start like log lines. One case is not told apart: a response that holds a request line with a `Request Headers:` line directly behind it, each at the start of a line as a container writes them, is read as a request - one more row is written, the response ends in front of the quoted lines, and where the quoted lines carry a time before the request, the request loses its `Response:` line (`status_code` missing, `failed` = 1). A prompt is logged on one line and is always kept as text.

In the six collected logs of the test system (1247 requests) no request started before the one in front of it had ended. These rules are therefore read from the line format and not from a case, and they are not run in SAS. Outside of SAS, 32,000 made-up logs whose requests were interleaved at random - with one model, and with a model per request - gave no row with a value of another request, no answered request with `failed` = 1 and no missing value without the NOTE. The parser as it ran on 2026-10-11 had 430 such faults on 2,000 of the logs with one model.

### The parser of this release on SAS Viya

On 2026-10-11 `Log-Parser-Code.sas` as it was that day - the program with the 24 further columns, version 2.2 of the step - ran on the test system as a SAS Job Execution job, on a redacted collection of all model containers with 1040 requests to seven models, and wrote to a table of its own in `CASUSER`:

- No ERROR, no WARNING and no NOTE about requests that ran at the same time. 1040 rows and 36 columns in their order, 24 of them VARCHAR and 12 numbers, promoted and saved. The program took 4.8 seconds, the job 21.
- 437 embedding, 355 decision and 248 LLM rows.
- 787 rows with status 200 and 253 failed rows - 170 of a decision model, 83 of an LLM. They are exactly the requests without a `Response:` line: 251 with `HTTP 401` of the score code and 2 with a `KeyError`.
- 290 embedding rows in query mode and 147 in document mode; 185 decisions with an answer.
- 238 LLM rows with a collection - 193 of one and 45 of another -, each with a question hash that is the `input_hash` of an embedding row in query mode.
- 867 requests of `python-requests` and 173 of a browser; 1040 distinct transaction ids and request ids.
- `options` in every row, 426 of them with `API_KEY:REDACTED`; `max_tokens` in 235 rows.
- The nine link columns empty in every row.
- **The first twelve columns against the parser as it was before the further columns** (version 2.1 of the step), which was run on the same file into a second table: 1035 rows were equal, and 5 differed in `max_tokens` only - missing before, and now 4000 (three times), 64 and 120, read from `max_completion_tokens` and `max_length`.
- **The step in a flow.** The code SAS Studio generated for a flow of the step (version 2.2) held the program of the step character for character and one `%let` line for each of the 36 column controls. Run as a job it wrote the same 1040 rows with the same 36 columns, types and labels, without an ERROR or a WARNING, and its log held `_FLW_ACTION_TABLE_|CASUSER|<table>|1`. The step had been put on the server with `PUT /dataFlows/steps/{id}` and had kept its id.
- The three appends and the refused one are under [Appending](#appending).

The parser was changed after these runs: how a line is given to a request ([Requests that run at the same time](#requests-that-run-at-the-same-time)), which retrieval of a prompt counts, further secrets in `options`, a request whose content can not be read, and what the append checks before it swaps. That parser - version 2.3 of the step - is **not run in SAS**. Outside of SAS, on 2026-10-12, its Python gave the same 36 columns for the 1040 requests as the Python of the program that had run, without a message, in every row but 41: those prompts hold two or three retrievals with different questions, and `rag_query_hash` is now the hash of the last one. All 238 hashes are still the `input_hash` of an embedding row in query mode.

## What Was Measured

The collection script of this guide, `Log-Parser-Code.sas` and the *LLM - Log Parser* step were run on the test system (SAS Viya 2026.09) on 2026-10-11, the parser as SAS Job Execution jobs that wrote to a table in `CASUSER`. All of it was run with the parser as it was before the 24 further columns - version 2.1 of the step, a table of twelve columns, and an append that added rows to the existing table with `table.append`:

- **Collection and redaction.** The script, with only `llm_log_path` and `llm_logging_interval` changed, read the pods its selector picked - all of them model pods - and wrote 5,773 lines with 174 requests. An unredacted copy of the same logs held one provider key and seven `Cookie` headers; the written file held `API_KEY:REDACTED` once and `Cookie: REDACTED` seven times, neither the key nor one of the cookie values, and the same number of lines.
- **Every kind of model that had requests.** The 174 requests gave 174 rows with the twelve columns: 57 of three LLMs with prompts, response, token counts and run time; 50 of a decision model with the questions and the answers as JSON, the state, token counts and run time; 67 of an embedding model with its tokens in `prompt_length` and its run time, and no prompt, response or `output_length`. No cell held `API_KEY` or a `Cookie` header.
- **An empty log and a log without requests.** A file of 0 bytes and a file with start-up lines only each ended with the note, in replace and in append mode. The table kept its 174 rows and its saved file was not written.
- **Append.** Two newer collections with 21 and 7 requests took the table from 174 to 195 to 202 rows, and no earlier row was lost. Before the second one the table was dropped from memory while its `.sashdat` stayed: the parser loaded the saved table first, appended, promoted and saved it again. The three files held different requests - a log that is parsed twice is in the table twice.
- **The warning for unread requests.** A log collected with `kubectl logs --timestamps` (858 lines, 26 of them with `Request: POST`) gave the warning with that count and the note, and the table was left as it is.
- **A request that is still open.** A request that was still running when its container's part of the file ended gave one row with its prompts and without token counts, run time and response. The lines of another container's finished request followed it in the file, and their numbers were not added to it.
- **Failed requests.** Two requests to a decision model that failed in the container - a traceback in the log, no score lines, no `Response: Status Code` line - gave one row each with the questions and the state, without numbers and response.
- **The custom step.** Run as the code SAS Studio generated for a flow, in a session with a CAS session and the libref, the step wrote the same 174 rows as the program in replace mode and the same 195 after appending the second collection.
- **The custom step in a session without a CAS session.** With no `cas` and no `libname` statement in front of the generated code, the macro variable `_CASNAME_` did not exist; in a job of the same context that held no other code, `cas _all_ list;` listed no session and the option `SESSREF` named `CASAUTO`. The step ran `cas llmLogMySess;` - `NOTE: The session LLMLOGMYSESS connected successfully to Cloud Analytic Services ...`, `NOTE: The SAS option SESSREF was updated with the value LLMLOGMYSESS.` -, wrote the table and ended the session: `NOTE: Deletion of the session LLMLOGMYSESS was successful.`, the NOTE about the option `SESSREF=` quoted above, and `NOTE: Request to TERMINATE completed for session LLMLOGMYSESS.` Promoted and saved, the table was read from a new CAS session, global, with the 174 rows. Saved and not promoted, no table was in memory and the `.sashdat` held the 174 rows. Neither promoted nor saved, nothing was left. `SYSCC` was 0 and the jobs ended as `completed`.
- **The custom step without the libref.** Behind `cas mysess;` alone the step ran in that session, started none of its own and wrote the promoted and saved table to `CASUSER`, with the same 174 rows. The log of that job held the line `_FLW_ACTION_TABLE_|CASUSER|<table>|0`; with the libref it was `|1`.
- **The step twice in one SAS session.** The code SAS Studio generated for two flows of the step, one behind the other in one SAS Job Execution job whose session had no CAS session: the first run started `LLMLOGMYSESS` and ended it. Between the runs `_CASNAME_` existed, the option `SESSREF` named `LLMLOGMYSESS` and `cas _all_ list;` listed nothing. The second run started no session: `ERROR: There is no server connection to execute the action 'session.sessionId'.`, `ERROR: Unhandled Python exception.`, `ERROR: The log could not be parsed, the table CASUSER.<table> was left as it is.`, `SYSCC` 1012, no table, and a job that ended as failed. This is what the session check was changed for: the step now asks `SESSFOUND`, and the same job was run again with that check (below).
- **The step put on the server in place.** `PUT /dataFlows/steps/{id}` with the step file plus its id was answered 200 and the id was kept; the stored program and the texts of the *About* tab equalled the file.
- **The copy from SAS Content, as it is printed in this guide** - see [Without a Shared Mount](#without-a-shared-mount): the copy with the size and the digest of the upload, and the 174 rows from `Log-Parser-Code.sas` behind it.
- **A log file that is missing**, in replace mode: the ERROR described under [Reading the Job Log](#reading-the-job-log), no table, no `<table>_NEW` and no saved file. No table of that name existed before the run, so that an existing table is left as it is was not seen there.

The runs from *The custom step in a session without a CAS session* on were made later on 2026-10-11, with the step and with `Log-Parser-Code.sas` as they were at that time of the day: their session check went by the macro variable `_CASNAME_`. Every run of `Log-Parser-Code.sas` in the list above was made before that line was changed as well; the program holds the same check behind its own `cas mysess;`.

### The Session Check with SESSFOUND

After the check was changed, the step was put on the server again - `POST /dataFlows/steps`, then `PUT /dataFlows/steps/{id}`, answered 200 with the id kept, the stored program and `ui` equal to the file - and run the same day in seven more SAS Job Execution jobs of the *SAS Studio compute context*, as the code SAS Studio generated for flows of the step, with the 174 requests of the first collection. It was the step as it was at that point of the release: version 2.1, with the twelve columns.

- **`SESSFOUND` on that system.** No log of the seven jobs held an ERROR or a WARNING at a line that calls the function. It returned 0 in a session without a CAS session, where the option `SESSREF` named `CASAUTO`; 0 for a session that had been ended (`LLMLOGMYSESS`, `MYSESS`); and 1 for a connected `MYSESS`.
- **Twice and three times in one SAS session without a CAS session.** In a job with two flows of the step one behind the other, both runs started and ended `LLMLOGMYSESS`, with two session ids. Between the runs the option `SESSREF` named `LLMLOGMYSESS`, `SESSFOUND` returned 0, `_CASNAME_` existed and `cas _all_ list;` wrote no line. The second run wrote, promoted and saved its table, and none of the lines of the failed second run described above occurred. In a job with three runs the third did the same and replaced the global table of the second.
- **A CAS session of the user, used and left alone.** Behind `cas mysess;`, the libref and a session table of the user, the step ran in `MYSESS`: outside the echoed source no line of the log named `LLMLOGMYSESS`, and no `Deletion of the session` and no `Request to TERMINATE` line was written before the job read the table back. After the step the session had the same id, `SESSFOUND` returned 1 for it, `cas mysess list;` named `MYSESS` as ACTIVE and the session table of the user was still there. The same held after the step had run twice behind that session.
- **A session that was ended before the step.** Behind `cas mysess; cas mysess terminate;` the option `SESSREF` named `MYSESS` and `SESSFOUND` returned 0: the step started `LLMLOGMYSESS`, wrote and saved its table and ended `LLMLOGMYSESS`.
- **A CAS session without the libref.** The step used `MYSESS`, wrote the promoted and saved table and left the session active.
- **`Log-Parser-Code.sas` with the same check**, three of its `%let` lines changed: the program ran in the `mysess` it starts itself, no line outside the source named `LLMLOGMYSESS`, and it wrote the 174 rows.
- **All seven jobs** ended as `completed` with `SYSCC` 0 and without a line of the type error or warning from source line 1 on. Each of the seven tables, read from a new CAS session, was global and had the 174 rows and the twelve columns, the rows equal to those of the earlier runs.
- **`_FLW_ACTION_TABLE_`.** The line ended in `|0` in every run in which the step ran in a session of its own or without the libref - four of the jobs - although the table existed, and in `|1` in the two jobs with the libref.
- **An observation, not caused by the check.** After the step, or `Log-Parser-Code.sas`, had run in a CAS session of the user, the action `session.sessionId` returned that session under the name `py-session-1` where it had returned `MYSESS` before, with the same id. SAS went on listing the session as `MYSESS`, ACTIVE, and `SESSFOUND` returned 1 for `mysess`. The Python part of the program is the one of the runs before the check changed; whether the step of those runs did the same was not looked at.

The step and the program of this release hold that check line unchanged. What this release changes beside it is not part of these runs: for version 2.2 of the step, with the 24 further columns, see [The parser of this release on SAS Viya](#the-parser-of-this-release-on-sas-viya).

### Not Measured

- The redaction of the `Authorization` and `X-CSRF-TOKEN` headers: none occurred in the logs, so only the `API_KEY` and the `Cookie` expression were exercised.
- The shared mount of the example paths and the hourly crontab entry.
- The models that had no request in the collected day. The rows come from three LLMs - one of them, with a single row, is called with a provider key -, one embedding model and one decision model. No embedding model that is called with a provider key had a request, and a second decision model has failed requests only.
- The error branches: an append whose saved `.sashdat` can not be loaded, a replace that can not build the `<table>_NEW` table, and a log file that is missing while the output table exists.
- Append without promoting, a saved table whose name differs from the table name in upper and lower case, and a target caslib other than `CASUSER` - nothing was written to `Public`.
- The step clicked in the SAS Studio UI, and how SAS Studio shows its *About* tab: the step was run as the code SAS Studio generates for a flow, in SAS Job Execution jobs. Whether an interactive SAS Studio session of the test system starts without a CAS session was not looked at either.
- The step's own session in append mode, a CAS session of the user in append mode, and a run without the libref where the name of the libref is not the name of a caslib.
- The condition code SAS Job Execution keeps for a job: the job API returned none for the seven jobs of the session check, their `SYSCC` of 0 was written by the jobs themselves.
- A log with bytes that are not valid UTF-8, and two requests that overlap in one container. Both are covered by the tests of the repository, which run the parser's Python outside of SAS.
- Of the parser with the 36 columns: the output port of the step on the flow canvas of SAS Studio and its *About* tab as SAS Studio shows it - the stored step, its 36 column controls and the generated code were looked at -, `Public` as the target caslib and the default table names (everything ran in `CASUSER`), the NOTE for requests that ran at the same time, and the *LLM Usage Report* opened on a table of this release: the twelve columns it reads of `LLM_LOGS` were compared with the report package and are all there. The package joins `LLM_LOGS` with `LLM_FACT_SHEET`; whether the new column `model_id` of `LLM_LOGS` touches that join was not looked at.
- What the three programs do since the chain ran on 2026-10-11: version 2.3 of the *LLM - Log Parser* step, version 2.1 of the *LLM - Get All Prompts* step and `Link-Logs-To-Models.sas` as they are now. [The Chain on SAS Viya](#the-chain-on-sas-viya) lists what was changed after it.

# The Experiments of Every Kind

`SAS-Viya-Integrations/Logging-Monitoring/Get-All-Prompts.sas` reads what the Prompt Builder keeps in SAS Model Manager and writes it into five CAS tables. Every row of every table carries `modelID` and `projectID`, the SAS Model Manager ids of the model and of its project - the model being the prompt template, the decision template or the RAG setup itself, where its trackers and its manifested score code are stored. The *LLM - Get All Prompts* custom step runs the same program.

The program as it was on 2026-10-11 - version 2.0 of the step - ran on the test system that day: see [The Pull on SAS Viya](#the-pull-on-sas-viya). What was changed after that run is not run in SAS - the refusal of a table name that no libref can address, the runs taken from a ledger, the further columns and roles of the catalog, the setup without a collection and the session check of the code file. Its Python runs in the test suite of the repository against a made-up SAS Model Manager, and the SAS code around it is held by the tests as text.

## What It Reads

- The projects with the tag `Prompt-Engineering` - projects of decision templates carry that tag as well. Of every model: `Prompt-Experiment-Tracker.json`, `Prompt-Optimization-Tracker.json` and the manifested score code (the `.py` file with the role `score`).
- The projects with the tag `RAG-Engineering`. A setup saved by the Prompt Builder is read from its `rag-setup.json`. A model registered by the step *RAG - Register Setup* (named `RAG Retrieval - <setup>`) has no such file: it is read from the `pipeline.yaml` the step stores with it, from its properties `collection`, `pipelineVersion`, `configuration` and `ingestionRunId`, and from its training table, which the step sets to `<CAS server>/<caslib>/<prefix>_LEDGER`. Any other model of such a project is left out with a NOTE.
- The projects that hold the definitions of the models that answer a request. The Prompt Builder finds them by id (its options `llmProjectID` and `embeddingProjectID`) and the decision models by the project name `Decision Model Project`; the program finds all three by name - `LLM Model Project`, `Embedding Model Project`, `Decision Model Project`, the names `mdb setup` gives them - so that it needs no id.

Nothing is written to SAS Model Manager, and every listing is read to its last page.

## Settings

The `%let` lines at the top of the program. In the custom step the output table gives the CAS library and the table of the experiments, and the others are fields of the step.

| Macro variable | Default | Meaning |
|---|---|---|
| `_gap_tag` | `Prompt-Engineering` | Tag of the projects with prompt templates and decision templates |
| `_gap_rag_tag` | `RAG-Engineering` | Tag of the projects with RAG setups; empty reads none (in the step an empty field takes the default) |
| `_gap_llm_project` | `LLM Model Project` | Project of the LLM definitions: a name, a project id or `tag:<tag>`, several separated by `\|`; empty leaves the LLMs out of the catalog |
| `_gap_embedding_project` | `Embedding Model Project` | The same for the embedding models |
| `_gap_decision_project` | `Decision Model Project` | The same for the decision models |
| `_gap_out_caslib` | `Public` | CAS library of all five tables |
| `_gap_out_experiments` | `PROMPT_EXPERIMENTS` | Table names, one per output |
| `_gap_out_optimizations` | `PROMPT_OPTIMIZATIONS` | |
| `_gap_out_rag_setups` | `RAG_SETUPS` | |
| `_gap_out_rag_runs` | `RAG_RUNS` | |
| `_gap_out_catalog` | `MODEL_CATALOG` | |

Two outputs can not have the same name, and an output needs a name: the program says so and reads nothing. The five table names are SAS names of 32 characters at most - letters, digits and underscores, no digit in front. CAS takes a longer name, but no CAS libref can address such a table: the program ends with one ERROR line before it reads SAS Model Manager. The program needs PROC PYTHON with the package `requests`.

`Get-All-Prompts.sas` starts a CAS session (`cas mySess;`) only when the SAS session has none that is connected, and ends only a session it started. The step does the same with a session called `gapMySess`.

## The Tables

Each table is dropped, loaded, promoted and saved, as `PROMPT_EXPERIMENTS` was. A table without rows is written as well, with all its columns: the DATA step that builds it declares the columns and reads nothing. A long text column is as wide as its longest value, at most 32,767 bytes; a longer text is cut between two characters.

| Table | One row per | Key |
|---|---|---|
| `PROMPT_EXPERIMENTS` | experiment run and model that answered, of a prompt template or decision template; one row without a run for a model that has none | `modelID`, `runID`, `model` |
| `PROMPT_OPTIMIZATIONS` | entry of a `Prompt-Optimization-Tracker.json`: an optimization or a comparison of target models | `modelID`, `optimizationId` |
| `RAG_SETUPS` | RAG setup, saved by the Prompt Builder or registered by the step *RAG - Register Setup* | `modelID` |
| `RAG_RUNS` | ingestion run of a setup: the rows of its run table when that is in memory, otherwise the runs its ledger still names | `modelID`, `run_id` |
| `MODEL_CATALOG` | model and pattern | `modelID`, `role`, `runID`, `optimization_id`, `target_model` |

**`PROMPT_EXPERIMENTS`.** The 49 columns the table had, unchanged in name, order and type: `unique_id` (the row number, no key), `name`, `description`, `createdBy`, `modifiedBy`, `function`, `runID`, `model`, `systemPrompt`, `userPrompt` (the templates; for a decision run the questions and the state template), `response`, `run_time`, `prompt_length`, `output_length`, `cost`, `cheapest_prompt`, `best_prompt`, `fastest_prompt`, `fewest_tokens_prompt`, `judge_rank`, `judge_best`, `judge_cost`, `judge_model`, `judge_confidence`, `judge_mode`, `judge_panel`, `judge_agreement`, `judge_chairman_model`, `variables_count`, `integrated_llm_call`, `output_variables_count`, `creationTime`, `modifiedTime`, `temperature`, `top_p`, `top_k`, `max_tokens`, `thinking_budget`, `max_completion_tokens`, `seed`, `frequency_penalty`, `presence_penalty`, `dimensions`, `api_key_required`, `reasoning_effort`, `input_type`, `normalize`, `projectLink`, `modelLink`. Behind them: `modelID`, `projectID`, `projectName`, `kind` (`prompt` or `decision`), `expected` (the expected answers of a decision run, JSON), `options` (as stored; the value of an option that is a secret by its name, by the names the log parser goes by, is replaced by `REDACTED`), `variables` (JSON), `resolved_system_prompt`, `resolved_user_prompt` (the prompts as they were sent), `resolved_system_hash`, `resolved_user_hash`, `judge_reasoning` (at most 2,000 bytes). `runID` is a number.

**`PROMPT_OPTIMIZATIONS`.** `modelID`, `projectID`, `projectName`, `name`, `optimizationId`, `startedAt`, `finishedAt` (SAS datetimes, UTC), `status`, `targetModel`, `optimizer`, `metric`, `judgeModel`, `datasetSource`, `datasetRef`, `sampleCount`, `trainSize`, `validationSize`, `metricBefore`, `metricAfter`, `targetCalls`, `targetPromptTokens`, `targetOutputTokens`, `targetRunTime`, `judgeCalls`, `judgePromptTokens`, `judgeOutputTokens`, `judgeRunTime`, `error`, `type` (`comparison` for a comparison of target models, else empty), `skippedReason`, `jobId`, `computeContext`. The prompts, the few-shot examples and the evaluations of an optimization stay in the tracker.

**`RAG_SETUPS`.** `modelID`, `projectID`, `projectName`, `name`, `description`, `creationTime`, `modifiedTime`, `source` (`builder` or `step`), `collection`, `backend`, `embedding_model`, `embedding_dims`, `chunker`, `input_token_limit`, `overlap_tokens`, `tables_prefix`, `tables_caslib`, `source_path`, `pipeline_version`, `artifacts_folder`, `job_definition_uri` (both only for a setup of the Builder), `ingestion_run_id`, `configuration` (both only for a model of the step), `runs_table` (`<tables_prefix>_RUNS`), `createdBy`, `modifiedBy`. A setup that was saved in the Builder and registered with the step is two models and two rows with the same collection.

**`RAG_RUNS`.** The rows of every setup's run table `<tables_caslib>.<tables_prefix>_RUNS`, stacked (`run_source` = `run table`). Only the step *RAG - Load Vector Store* publishes that table. Only a table that is in memory is read, and no table of a setup is loaded or changed. Columns: `modelID`, `projectID`, then the columns `rag_core` writes - as text `run_id`, `rag_project`, `collection`, `backend`, `started_at`, `finished_at`, `status`, `pipeline_version`, `config_id`, `embed_model`, `chunker`, `enrich_model`, `error_text`; as numbers `embed_dims`, `input_token_limit`, `overlap_tokens`, `docs_new`, `docs_changed`, `docs_unchanged`, `docs_deleted`, `docs_skipped`, `docs_excluded`, `docs_failed`, `docs_ingested`, `chunks_written`, `chunks_retired`, `collection_chunks`, `embed_calls`, `embed_tokens`, `embed_seconds`, `enrich_calls`, `enrich_input_tokens`, `enrich_output_tokens`, `enrich_seconds`, `enrich_failed` - then `source_caslib`, `source_table` and `run_source`: 40 columns. A column a run table of an earlier release lacks is empty, a count that was published as text is a number here, and `started_at` and `finished_at` are the text the run table holds (UTC; on the test system `YYYY-MM-DD HH:MM:SS+00:00`). Only the runs of the setup's own collection are taken. A run table two setups share gives its rows once per setup.

The ingestion job of a setup of the Prompt Builder publishes its ledger only, no run table. For a setup whose run table is not in memory the pull therefore writes the runs that the ledger `<tables_prefix>_LEDGER` still names, if that table is in memory (`run_source` = `ledger`): one row per run id, `started_at` the second the id of the run holds (`run-<seconds since 1970>-<digits>`), `finished_at` the latest `updated_at` of its rows, `collection` and `embed_model` those of the setup, `source_table` the ledger, and every count missing. Every run writes its id into the row of every document of the folder, so a ledger names the newest run only: to link the embeddings of earlier ingestions of such a setup, ingest with the step path and keep `<tables_prefix>_RUNS` in memory. A setup with neither table in memory is left out with a NOTE. The rows taken from a ledger are not run in SAS.

**`MODEL_CATALOG`.** `modelID`, `projectID`, `projectName`, `name`, `role`, `runID` (text), `target_model`, `system_template`, `user_template`, `resolved_system_prompt`, `resolved_user_prompt`, `collection`, `template_source`, `resolved_system_hash`, `resolved_user_hash`, and behind these fifteen the six that were added after the run of 2026-10-11: `run_time`, `prompt_length`, `output_length` (numbers), `window_start`, `window_end` (text, `YYYY-MM-DDTHH:MM:SSZ` in UTC) and `optimization_id` (text). The link program reads every column but `projectName`, `prompt_length` and `output_length`.

| `role` | Rows | What the row holds |
|---|---|---|
| `prompt`, `decision` | one per template (`runID` empty) | The templates with their `{{variables}}`; for a decision template the questions and the state template. `template_source` says where they are from: `score code` (what the manifested score code sends, with the model it calls in `target_model`), `best run` (the newest run marked as the best prompt) or `newest run`. A model without a run and without such score code has no row |
| `prompt`, `decision` | one per experiment run and model (`runID` set) | The templates of the run, the prompts as they were sent, their hashes, the model they were sent to, and what the tracker holds of that call: `run_time` in seconds, `prompt_length` and `output_length` in tokens. The run time is what tells the request of this run from another request with the same prompts |
| `rag` | one per RAG setup that names a collection | `collection`, the embedding model in `target_model`, and in `template_source` who wrote the model: `builder` or `step`. A setup without a collection - its properties or its setup file could not be read - keeps its row of `RAG_SETUPS` and has none here, as no request could be linked to it |
| `judge` | one per run that was judged and model that judged it | `modelID`, `projectID` and `name` are the prompt's, `runID` the run, `target_model` the judge - the judge, each member of a panel, the chairman. `resolved_system_prompt` and `resolved_user_prompt` are the prompts of the run as they were sent, which the Prompt Builder shows a judge in the user prompt of its call. No run time and no window: the tracker holds none for the call of a judge |
| `optimization` | one per entry of the optimization tracker and model it called | `runID` is empty and `optimization_id` the id of the entry; `target_model` is a target model or the judge of the optimization. `window_start` and `window_end` are the start and the end of the job, the start rounded down and the end up to the second, empty where the tracker has none. `resolved_system_prompt` is the system prompt the optimization started from, as it is stored, and the two templates are the optimized prompt where the tracker holds one |
| `llm`, `embedding`, `decision_model` | one per model definition | `name` is the name of the container, which is the model a log row names |

## The Prompts as They Were Sent

The Prompt Builder stores the templates and the variables of a run and sends the prompts with the values filled in. The program fills them in the same way: the variables one after the other in the order they are stored, `{{name}}` with blanks or line ends inside the braces, the value as it is - not trimmed, whatever the type of the variable - and a `{{name}}` of no variable left as typed. A decision run sends its questions as they are stored and gets the variables filled into its state only. The hash is the one of the log table, taken of the whole text before it is cut to the length of its column.

The templates of a manifested model are read from its score code, which is parsed and never run. A manifested model strips every value before it puts it into the prompt, the Prompt Builder does not.

Measured outside of SAS on files saved from the test system: the templates were read from nine manifested score codes, one of them a decision template manifested with 2.2.0 whose score code does not compile and is read statement by statement; the questions and the state template read from the score code of one decision template fit 41 of the 105 requests to its decision model in the collected log of 1040 requests, and the questions of its tracker equalled the questions of those requests character by character. Not compared: a prompt as an experiment run sent it with a logged request.

## Messages and Limits of the Pull

- A model whose file can not be read (not JSON, a failed request), and a project or model that can not be listed, is a WARNING with its name; the model keeps its row without runs and the pull goes on.
- When the projects themselves can not be listed, the program ends with an ERROR and leaves the output tables as they are.
- A definition project that is not found is a WARNING; an empty setting asks for nothing.
- The run ends with one NOTE per output table: `<caslib>.<table> was written with <n> rows and <m> columns.`
- A run table whose name is no SAS name of at most 32 characters, or whose caslib holds a quote, `&`, `%` or `;`, is left out with a WARNING.
- An output table name of more than 32 characters, or one that is no SAS name: `ERROR: The table name <name> of the output <output> has <n> characters. A SAS libref addresses a CAS table by a name of 32 characters at most, nothing was read or written.`, or `... is no SAS name: letters, digits and underscores, and no digit in front. Nothing was read or written.` Nothing is read and no table is touched.
- A RAG setup without a collection: `WARNING: The properties of the model <name> (<id>) could not be read: <reason>. The setup has no collection without them and is left out of the model catalog, where no request could be linked to it. Its row of the RAG setups stays.`, or `WARNING: The RAG setup <name> (<id>) names no collection and is left out of the model catalog, ...`
- A setup whose runs are taken from its ledger: `NOTE: The run table <caslib>.<prefix>_RUNS is not in memory. <n> run(s) were taken from the ledger <caslib>.<prefix>_LEDGER - the runs its rows still name, each from the start its id holds to the latest of its rows.`
- The requests are made one after the other: one per project and one or more per model.

## The Pull on SAS Viya

Measured on the test system on 2026-10-11 with `Get-All-Prompts.sas` as it was that day, in SAS Job Execution jobs that wrote to tables of their own in `CASUSER`. Where a point ends in **Now**, the program was changed after the run, and that change is not run in SAS.

- **What it read.** 41 projects with the tag `Prompt-Engineering` and 92 models in them, 10 projects with the tag `RAG-Engineering` and 17 RAG setups, in 17 to 20 seconds per run - the Python step 16.6 of the 19.5 seconds of the first.
- **The five tables.** `PROMPT_EXPERIMENTS` with 318 rows and 61 columns, `PROMPT_OPTIMIZATIONS` with 27 and 32, `RAG_SETUPS` with 17 and 26, `RAG_RUNS` with 105 and 39 and `MODEL_CATALOG` with 365 and 15, each promoted and saved. The keys were unique: 318 of 318 rows of the experiments and 365 of 365 of the catalog. The JSON libname with its map, the loop over the CAS libraries and the look at each written table through the libref ran as they are written.
- **Three runs with one content.** The program alone, the program behind the parser in one SAS session, and the step (version 2.0) as the code SAS Studio generated for a flow wrote the same five tables - compared by the ids, counts, lengths and hashes of every row, not by the texts.
- **A decision template, a prompt template and a RAG setup of the test system**, looked at row by row. The decision template had 41 rows of 21 runs with two decision models and `kind` = `decision`, and in the catalog its template (`score code`) and 41 run rows with both hashes. The prompt template had 32 rows of 14 runs with four LLMs and one optimization, and in the catalog its template and 32 run rows. The setup had `source` = `builder` with its collection, vector database, embedding model and table prefix, and in the catalog one `rag` row marked `builder`. The catalog held 8 `llm`, 3 `embedding` and 5 `decision_model` rows.
- **The columns the *LLM Usage Report* reads** - 38 of `PROMPT_EXPERIMENTS`, read from the report package - were all in the table under their names. The report was not opened on it.
- **A table name of 34 characters.** For an output with such a name the last line of the run was `ERROR: The table <caslib>.<name> was not written.` - for a table that had been written, promoted and saved with its 27 rows and 32 columns. The closing check looks at the table through a CAS libref, and the `exist` function returned 0 for the name of 34 characters and 1 for one of 32. **Now:** a name of more than 32 characters, or one that is no SAS name, ends the program with one ERROR line before anything is read.
- **A model whose properties the service does not return.** `GET /modelRepository/models/<id>` of one model registered by the step was answered with HTTP 500, by the program and outside of it. The program wrote one WARNING and kept the setup, with an empty collection and embedding model, in `RAG_SETUPS` and in the catalog. **Now:** such a setup keeps its row of `RAG_SETUPS` and is left out of the catalog, and the WARNING says so. Read again outside of SAS on 2026-10-12, with GET requests only and the Python of the program as it is now, the same system gave three such WARNINGs - for that model and for two setups that name no collection - and a catalog of 421 rows: 14 `rag` rows where there had been 17, and 24 `judge` and 35 `optimization` rows.
- **The run table of the Builder's setup does not exist.** The pull wrote `NOTE: The run table Public.<prefix>_RUNS is not in memory, ...`. The caslib held the ledger of the setup (`<prefix>_LEDGER`, 7 rows, and its `.sashdat`) and no run table, in memory or as a file. The 105 rows of `RAG_RUNS` came from four run tables of setups that the step path had ingested; three of those tables are shared by two or three models the step registered and are in `RAG_RUNS` once per model. 15 of the 105 rows hold a `finished_at` about two hours before their `started_at`. **Now:** for a setup without a run table in memory the pull writes the runs its ledger still names, see `RAG_RUNS` above.
- **Behind the parser in one SAS session** the `cas mySess;` of the program wrote `WARNING: A session with the name MYSESS already exists.` - SAS Job Execution keeps such a warning in the record of a job: another job of that day with the same warning ended as completed and carried the error code 5710 with that message -, and the program ended the session the parser had started and its librefs were bound to. **Now:** both code files ask `SESSFOUND` before they start a session, and `Get-All-Prompts.sas` ends only a session it started.
- **Not measured:** an output without rows - every output had rows -, a pull that takes longer than two minutes (51 projects and 109 models took 17 to 20 seconds), `Public` as the output library, and the step run from its page in SAS Studio.

# Linking the Log to the Models

`SAS-Viya-Integrations/Logging-Monitoring/Link-Logs-To-Models.sas` fills the nine link columns of the log table. It reads the log table, `MODEL_CATALOG` and, when it is there, `RAG_RUNS`, links the rows without a `match_method` and puts the table back, promoted and saved.

The program as it was on 2026-10-11 ran on the test system that day, see [The Chain on SAS Viya](#the-chain-on-sas-viya): it linked the 1040 rows of the collected log and left the table as this section says, and its summary broke the SAS log. The program was changed after that run - its messages, the rule for an experiment run, the window of a retrieval query, the calls of optimizations and judges, what it does to a table it links no row of - and as it is described here it is **not run in SAS**: its matching was run outside of SAS, on made-up rows and on the rows of that log.

## Settings of the Link

| Macro variable | Default | Meaning |
| --- | --- | --- |
| `_lnk_log_caslib`, `_lnk_log_table` | `Public`, `LLM_LOGS` | The log table. Its link columns are filled in place |
| `_lnk_catalog_caslib`, `_lnk_catalog_table` | `Public`, `MODEL_CATALOG` | The catalog of Get-All-Prompts.sas |
| `_lnk_runs_caslib`, `_lnk_runs_table` | `Public`, `RAG_RUNS` | The ingestion runs. Without the table no embedding row is linked to an ingestion run; the program says so in a NOTE |
| `_lnk_redo_all` | `0` | `0` links the rows without a `match_method` and leaves every other row as it is. `1` links all rows again |
| `_lnk_min_literal` | `20` | The characters of fixed text (whitespace not counted) a template needs to take part, an experiment run in its prompts, and a call of an optimization or of a judge in the texts the catalog holds of it |
| `_lnk_utc_offset` | `0` | The hours the clock of the containers is ahead of UTC. The ingestion runs and the windows of the optimizations are recorded in UTC |
| `_lnk_query_window` | `300` | The seconds within which the prompt that carries a retrieval follows the embedding of its query. A query embedding without such a prompt is not linked |
| `_lnk_promote_table`, `_lnk_save_table` | `1`, `1` | They decide for a table that is not promoted and for one that has no saved file. A table that is promoted is promoted again and a table that has a saved file is saved again, whatever the two say - as the parser does it when it appends |

A table that was saved but is not in memory (after a restart of the CAS server) is loaded from its `.sashdat` file first; this holds for all three tables. The program needs PROC PYTHON with the pandas and swat packages and the caslib `CASUSER`, as the parser does. A setting that has to be a number and is none gives a WARNING, and its default is taken. The program starts a CAS session (`cas lnkMySess;`) only when the SAS session has none that is connected, and ends only that one.

## The Rules, in Their Order

No request, header or log line names the caller. Every link is therefore made by content, and `match_method` says which rule decided.

1. **The model that answered** (`model_id`): the catalog row with the role `llm`, `embedding` or `decision_model` whose name is the name of the container (`model`). When two models of that role have the name, `model_id` stays empty and a NOTE names the two.
2. **`experiment run`** (LLM and decision rows): the system and the user prompt of the row are the prompts of an experiment run as the Builder sent them (for a decision: the questions and the state), the run was sent to the model of the row, the request was not sent by the score code or a SAS session (`user_agent` is not `python-requests` or `SAS`) - the Builder page sends its runs from a browser -, and the `runtime` of the row is the `run_time` the catalog holds for that run and model, compared with three decimals. The prompts are compared by the hashes the catalog holds of them, so a prompt that is longer than a column of the catalog is still found. A run is given to one row: the earliest request gets it, and a run that a row of an earlier link holds is taken. A request with the prompts of a run whose run time is the one of no run is another request with the same prompts: the rules below say who sent it, and it gets no `experiment_run_id`. Where the catalog holds no `run_time` for the runs with these prompts - a catalog written before the column existed -, the row gets the newest of them, `match_candidates` is their number, and a NOTE counts such rows.
3. **`questions and state`**, **`questions`** (decision rows): the questions of the row are the questions of a decision template. Questions are compared as JSON, so the order of the keys and the whitespace do not matter; a text that is no JSON is compared as text. When the state of the row fits a state template that has fixed text, the method is `questions and state`, otherwise `questions`.
4. **`template`** (LLM rows): the system prompt and the user prompt of the row fit the templates of a prompt. Every `{{variable}}` stands for any text, also for none and for several lines; the fixed text has to be there in its order, from the first character to the last, whitespace squeezed. The templates of earlier experiment runs of a prompt count as its templates.
5. **`optimization`**, then **`judge`** (LLM rows): the request is a call the Prompt Builder made *for* a prompt and not with it - a call of a prompt optimization, or the call a judge gets to rank the answers of a run. A catalog row of that role links a request when three things hold: the request holds every text of the row (`resolved_system_prompt`, `resolved_user_prompt`) in its system or in its user prompt, at least one of them as a part of the prompt and not as the whole of it; it went to the model in `target_model`; and, where the row has `window_start` and `window_end`, it was sent inside that window (whole seconds, `_lnk_utc_offset` applies). The texts need `_lnk_min_literal` characters together. `caller_model_id` is the prompt the call was made for and `caller_kind` what the catalog says that caller is. A judge call gets the `experiment_run_id` of the run that was judged when exactly one run remains; two judged runs with the same prompts leave it empty. An optimization is asked first: a call of a judge that was made while an optimization of the same prompt was running, to a model that optimization called and with the system prompt it started from, is an `optimization` row.
6. **The RAG setup of a prompt** (LLM rows, column `rag_model_id`, does not change `match_method`): the prompt carries the results of a retrieval, and the setup is the one the catalog has for its collection. So a call of a prompt that is fed by a retrieval is linked to its prompt and to the setup. Of several retrievals in one prompt the last one counts, as for the parser.
7. **`retrieval query`** (embedding rows in query mode): the hash of the embedded text is the hash of the query of a retrieval that an LLM row of the table carries, and that LLM row follows the embedding within `_lnk_query_window` seconds. The nearest such row decides: the embedding row gets the setup of the collection it names. The same question is embedded many times and for more than one collection, so a prompt that came before the embedding, or long after it, says nothing about this request. Two prompts of two collections in the same second are `ambiguous`. A setup of which the catalog names another embedding model than the model of the row is not taken.
8. **`ingestion run`** (embedding rows in document mode whose `project` input is `rag`, as the RAG runtime sends it, or empty): the request lies between `started_at` and `finished_at` of exactly one run of `RAG_RUNS` whose `embed_model` is the model of the row. A run without a start or an end, or whose end lies before its start, takes no part, and a NOTE counts such runs.

Between several callers that fit, the one with the most fixed text wins and `match_candidates` is their number. A caller of which the catalog only names other target models is taken when no other caller fits. Two callers with the same amount of fixed text, two collections for one query or two overlapping ingestion runs give `match_method = ambiguous` and empty caller columns. A row that fits nothing gets `none` and `match_candidates = 0`; its `model_id` is filled all the same.

Where two models hold one collection - a setup that was saved in the Prompt Builder and registered with the step *RAG - Register Setup* - the model the Builder saved is taken, for `rag_model_id`, for a retrieval query and for an ingestion run, when the other model is its registration: the catalog marks it `step`, the two name no different embedding models, and it is named `RAG Retrieval - <name of the setup>` or holds an ingestion run of `RAG_RUNS` that the setup holds as well. Another model with the same collection name, two setups of the Builder with one collection, or two models of the step stay open: `rag_model_id` is empty and the embedding rows are `ambiguous`, unless the retrieval names an ingestion run that `RAG_RUNS` holds for one of them only.

A row whose content can not be compared - questions that are nested deeper than Python reads JSON - gets `none`, a catalog row that can not be read takes no part, and a WARNING names both. The rows around them are linked.

**The window of a retrieval query on the collected log.** Of the 290 query embeddings of the log of 1040 requests, 124 were followed by the prompt with their question within one second, 13 more within 33 seconds and one after 178 seconds. 83 were followed only after 7 to 30 minutes - retrievals that were tested before the first prompt of their collection was sent -, 18 only had such prompts before them, and 51 had none. The default of 300 seconds links the 138; with `_lnk_query_window = 1800` it is 221.

## What Cannot Be Linked, and Why

- **No caller id is logged.** A request carries its inputs and nothing about who built them. A direct call of a model (a script, a health check, the test of a deployment) has no caller in SAS Model Manager at all.
- **Two templates with the same text.** A copy of a prompt, or two decision templates with the same questions and no state that tells them apart, fit the same requests: `ambiguous`.
- **A template that is one variable.** `{{input}}` fits everything, so it says nothing. A template with less than 20 characters of fixed text takes no part; the program counts such callers in a NOTE.
- **The Builder's test calls against a template that was changed afterwards.** The catalog holds the saved template and the templates of the runs that were kept. A test call that was not kept as a run and was made with a text that is no longer the template fits nothing.
- **The health check of an optimization, and a call whose instructions the optimizer rewrote.** The calls of a prompt optimization and of a judge are linked through a text of the prompt they hold. The health check an optimization sends first (`You are a health check.`) holds none, and neither does a call of an optimizer that has replaced the instructions by a proposal of its own.
- **A further request with the prompts of an experiment run** - the run sent again from the Builder page without being kept - is linked to the prompt by its template or its questions, and to no run.
- **A retrieval that never reached an LLM prompt** (the test of a retrieval, a flow that only retrieves), or whose prompt does not follow within `_lnk_query_window` seconds: its query embedding has no LLM row to be linked through.
- **Embedding calls outside of an ingestion run**, a run without `finished_at`, a setup with neither a run table nor a ledger in memory when `RAG_RUNS` was written, and the earlier ingestions of a setup of the Prompt Builder: its ingestion job publishes no run table, and its ledger names the newest run only.
- **The LLM calls of an enrichment** during an ingestion: their prompt is in no catalog.
- **A published model that sends the prompts of an experiment run again** is linked to the prompt, not to the run.
- **A template longer than 32,767 bytes** is cut in the catalog and fits no request.
- **Rows of an earlier parser** have no kind, no hashes and no collection: their LLM and decision rows are linked (the kind is taken from the role of the model that answered), their embedding rows and their RAG setup are not.

## How the Tables Join

| From | To | Meaning |
| --- | --- | --- |
| `LLM_LOGS.model_id` | `MODEL_CATALOG.modelID` (roles `llm`, `embedding`, `decision_model`) | The model that answered |
| `LLM_LOGS.caller_model_id` | `PROMPT_EXPERIMENTS.modelID` | The prompt or decision template (`caller_kind` = `prompt` or `decision`) |
| `LLM_LOGS.caller_model_id`, `experiment_run_id`, `model` | `PROMPT_EXPERIMENTS.modelID`, `runID`, `model` | The row of the experiment run (`match_method` = `experiment run`). `experiment_run_id` is text and `runID` a number, and a run id counts inside one model only. A judge call (`match_method` = `judge`) carries the id of the run it judged, and its `model` is the judge |
| `LLM_LOGS.caller_model_id` | `PROMPT_OPTIMIZATIONS.modelID` | The optimizations of that prompt. A call of an optimization (`match_method` = `optimization`) carries no id of the optimization: it is the one with `startedAt <= timestamp <= finishedAt` |
| `LLM_LOGS.caller_model_id` | `RAG_SETUPS.modelID` | The setup of an embedding row (`caller_kind` = `rag`) |
| `LLM_LOGS.rag_model_id` | `RAG_SETUPS.modelID` | The setup whose retrieval an LLM prompt carried |
| `RAG_SETUPS.modelID` | `RAG_RUNS.modelID` | The ingestion runs of a setup |
| `LLM_LOGS.model_id`, `caller_model_id`, `rag_model_id` | `MODEL_CATALOG.modelID` | The name and the project of any of the three |

`MODEL_CATALOG` has one row per model and pattern, so a model is there several times: join to it through a distinct list of `modelID`. `MODEL_CATALOG.target_model`, `PROMPT_EXPERIMENTS.model` and `RAG_SETUPS.embedding_model` are names of containers and equal `LLM_LOGS.model`.

`experiment_run_id` is the place of the run in the tracker of the prompt, not a lasting id: when a run is deleted in the Prompt Builder, the runs behind it are numbered again, so after a new pull a row that was linked earlier can name another run. The link program counts such rows in a WARNING - rows whose run id the catalog no longer has, and rows whose run has another run time than the request - and `%let _lnk_redo_all = 1;` links all rows again.

An example - what each prompt, decision template and RAG setup asked of the models, and the calls of the Builder's experiment runs beside their rows of `PROMPT_EXPERIMENTS`. The three statements ran on the test system on 2026-10-11 on the tables of the chain, with only the table names changed, without an ERROR or a WARNING and in 0.3 seconds; `input(l.experiment_run_id, best32.)` read the VARCHAR column:

```SAS
libname public cas caslib='Public';

proc sql;
    /* Requests, failures, tokens and seconds per caller and answering model */
    create table work.usage_by_caller as
    select l.caller_kind, l.caller_name, l.caller_model_id, l.model,
           count(*) as requests,
           sum(l.failed) as failed_requests,
           sum(l.prompt_length) as input_tokens,
           sum(l.output_length) as output_tokens,
           sum(l.runtime) as seconds
    from public.llm_logs as l
    where l.caller_model_id is not missing
    group by l.caller_kind, l.caller_name, l.caller_model_id, l.model;

    /* The prompts that were fed by a retrieval, per RAG setup */
    create table work.prompts_by_rag_setup as
    select s.name as rag_setup, s.collection, l.caller_name as prompt, count(*) as requests
    from public.llm_logs as l
         inner join public.rag_setups as s on s.modelID = l.rag_model_id
    group by s.name, s.collection, l.caller_name;

    /* The logged request of an experiment run beside what the tracker kept of it */
    create table work.experiment_run_requests as
    select e.name, e.runID, e.model, e.best_prompt, e.run_time as tracked_seconds,
           l.timestamp, l.runtime as logged_seconds, l.status_code
    from public.llm_logs as l
         inner join public.prompt_experiments as e
            on  e.modelID = l.caller_model_id
            and e.runID   = input(l.experiment_run_id, best32.)
            and e.model   = l.model
    where l.match_method = 'experiment run';
quit;
```

## What the Program Does to the Table

The log table has no key, and its prompts and every column a site added must not be rewritten. The program therefore numbers the rows in a copy in the caslib `CASUSER`, reads what it compares into Python (the prompts only of the rows it links), uploads the nine link columns with the row number, merges them back by that number into `<table>_NEW` beside the log table, checks that this table has as many rows as the log table, restores the order of the columns, gives the new table the label of the log table, and replaces the log table the way the parser does: dropped, renamed, promoted, saved. While it runs it needs room for two more copies of the table. A table that grew while it was linked - the parser appended to it - is left as it is, with an ERROR line; run the two one after the other.

A run that links no row - every row has a `match_method` - builds no new table: the log table, its promotion and its saved file stay as they are, and the SAS log has `NOTE: 0 of <n> rows of <caslib>.<table> were linked, as every row has a match_method. The table was left as it is. With _lnk_redo_all = 1 all rows are linked again.`

Every message of the program is made one text before it is handed to SAS, where a line for the log is a statement on its way: in a message a quote shows as a backtick, `%` as `pct`, `&` as `+` and a semicolon as a comma, and a message of more than 900 characters ends in three dots.

A log table without the link columns was written by an earlier parser and is refused with one ERROR line that names the parser. Without the catalog there is one ERROR line that names Get-All-Prompts.sas. In both cases the table is left as it is.

The SAS log ends with one NOTE per kind of row, for the whole table:

```text
NOTE: 248 llm rows: 248 linked to a model, caller by experiment run 32, template 144, optimization 49, judge 13, ambiguous 0, none 10, and 238 linked to a RAG setup.
NOTE: 355 decision rows: 355 linked to a model, caller by experiment run 41, questions and state 310, questions 0, ambiguous 0, none 4.
NOTE: 437 embedding rows: 437 linked to a model, caller by retrieval query 138, ingestion run 3, ambiguous 72, none 224.
```

These are the numbers of the matching of this release, run outside of SAS on 2026-10-12: on the rows its parser makes of the collected log of 1040 requests, the catalog its pull makes of the SAS Model Manager of the test system (read with GET requests only, 421 rows) and the 105 ingestion runs the chain of 2026-10-11 wrote. The matching of the 1040 rows took less than a tenth of a second. [The Chain on SAS Viya](#the-chain-on-sas-viya) has the numbers of the program as it ran there, and what changed between the two.

## The Chain on SAS Viya

On 2026-10-11 the three programs as they were that day ran one after the other on the test system: 23 SAS Job Execution jobs of the *SAS Studio compute context*, all of them on tables of their own in `CASUSER`, on a redacted collection of 1040 requests. The CAS server of that system runs without workers, and its PROC PYTHON is Python 3.12 with pandas 2.3.3, swat 1.18.1 and requests 2.34.2. The job logs hold the build stamp of the system (`V.04.00M0P09082026`), not the name of its release. What the parse did is under [The parser of this release on SAS Viya](#the-parser-of-this-release-on-sas-viya) and [Appending](#appending), what the pull did under [The Pull on SAS Viya](#the-pull-on-sas-viya). The link and the joins:

**What held**

- **The link.** `NOTE: 1040 of 1040 rows of <caslib>.<table> were linked against 365 catalog rows in 0.09 seconds.` Every row had a `model_id`. The table kept its 36 columns in their order with their types and labels - only the lengths of the eight text link columns changed -, its rows in their order and the other 27 columns unchanged in every row. It was promoted and saved, and `_LNK_STAGE`, `_LNK_LINKS` and `<table>_NEW` were gone. The program took 4.9 seconds.
- **Per kind and method**, counted in the table: LLM rows - `experiment run` 32, `template` 144, `none` 72; decision rows - `experiment run` 50, `questions and state` 301, `none` 4; embedding rows - `retrieval query` 239, `ingestion run` 3, `ambiguous` 72, `none` 123.
- **The callers were right.** For all 769 linked rows the caller was compared with the content of the row afterwards, outside of SAS: the questions or the system prompt of the row are those of the caller's template or of a version kept in its runs, the state or the user prompt fits its user template, and no other caller of the catalog fits. 310 decision rows went to one decision template - 168 of them failed requests, linked like the others -, 131 LLM rows to one prompt template, and all 193 LLM rows with the collection of one RAG setup carried that setup in `rag_model_id`, whatever their `match_method`.
- **A second run** linked nothing: `NOTE: 0 of 1040 rows ... were linked against 365 catalog rows in 0.06 seconds, 1040 rows that have a match_method were left as they are.` All nine link columns were unchanged in every row. With `_lnk_redo_all = 1` the program linked the 1040 rows again and wrote the same links.
- **The refusals.** A table of twelve columns as the log table: one ERROR message that names the missing columns and the parser, and the table unchanged. A catalog that does not exist: `ERROR: The catalog table <caslib>.<table> was not found. Run Get-All-Prompts.sas first, ...`, and the log table unchanged. Both jobs ended as completed.
- **More rows than one read.** A table of 3120 rows - the 1040 three times - takes two reads of 2000 rows, with prompts of up to 22559 characters: `3120 of 3120 rows ... in 0.17 seconds`, the rows in the same order before and after, and every row with the links its request had in the table of 1040 rows.
- **Tables that are saved and not in memory.** The log table, the catalog and the runs table were loaded from their `.sashdat` files. The log table was promoted again, the two others stayed tables of the session.
- **The joins.** The three statements of [How the Tables Join](#how-the-tables-join) ran. Counted: 527 rows join to `PROMPT_EXPERIMENTS` by `caller_model_id`, which are the rows with `caller_kind` `prompt` or `decision`; 242 to `RAG_SETUPS` by `caller_model_id`, the rows with `caller_kind` `rag`; 238 by `rag_model_id`; all 1040 to the answering rows of the catalog by `model_id`; and 82 to the row of their experiment run.
- **In one SAS session** - parse, pull, link, link again - the chain gave the same links as the separate jobs, in 47 seconds.
- **What the programs do in CAS**, on this server: the DATA step of the append ran in CAS in several threads; the rows that were numbered with `single='yes'` and merged back by that number came out in their order, for 1040 and for 3120 rows; and the order of the columns was restored. A CAS server with workers was not tried.

**What did not hold, and what the program does now.** The second half of each point is a change made after the run, and it is not run in SAS.

- **The summary broke the SAS log.** The three summary lines held semicolons. A line is handed to the SAS log as a statement, so the statement ended at the first semicolon and the rest ran as SAS code: four `ERROR 180-322: Statement is not valid or it is used out of proper order.` in each of the seven runs that reached the summary, PROC PYTHON stopped, and the job ended as failed although the table had been written, promoted and saved correctly. The NOTE lines ended behind `linked to a model`; the counts per method stood in the echoed source lines of the log only. **Now:** every message takes one way on which it is made one text, the summary is worded without a semicolon, and a macro variable is only ever given a whole number.
- **An experiment run was given to more than one request.** The 82 rows with an `experiment_run_id` belonged to 70 runs and models: twelve were a second row. Three requests of the first run of a prompt carried the id of its second run, which had sent the same prompts - their logged run times are the ones the tracker holds for the first run -, and nine decision requests that had been sent again carried the id of a run whose tracked run time is not theirs. The third statement of the example therefore showed two requests for that second run and none for the first. **Now:** a row gets the run whose `run_time` is its `runtime`, and a run is given to one row. Outside of SAS on the same log: 73 rows for 73 runs and models, each with the run time of its run.
- **A query embedding was linked through any prompt with its question.** 18 query embeddings were linked to a setup whose last prompt with their question had been sent 20 minutes to two hours before them, and 84 embeddings of another setup were made before the first prompt of their collection was sent, up to 20 minutes before it. The rule had no time window. **Now:** the prompt has to follow within `_lnk_query_window` seconds. Outside of SAS on the same log 138 of the 290 query embeddings are linked, where 239 were.
- **The calls of a prompt optimization and of a judge stayed `none`:** 49 and 13 of the 72 LLM rows without a caller. **Now:** they are linked to their prompt with the methods `optimization` and `judge`. Outside of SAS on the same log, with the catalog of this release: 49 and 13, the judge calls with the ids of the runs 2 to 14 of the prompt, one each. 10 LLM rows stay `none`: the health check of the optimization, three browser requests with a system prompt that no kept run holds, and six test calls.
- **A second run rewrote the table.** With 0 rows linked the program still built `<table>_NEW`, dropped, renamed, promoted and saved: the table had a new modified time and its `.sashdat` was written again, with the same content. **Now:** such a run leaves the table as it is.
- **The label of the table was lost** by the link, as it was by the append. **Now:** the new table gets the label of the log table.
- **A second start of `mysess`.** In the chain of one session the second run of the link wrote `WARNING: A session with the name MYSESS already exists.`, as the pull behind the parser did. **Now:** the program asks `SESSFOUND` and starts `lnkMySess` only when no session is connected.
- **No ingestion link for the setup of the Builder.** Its run table does not exist (see [The Pull on SAS Viya](#the-pull-on-sas-viya)). Of the 147 document embeddings 3 were `ingestion run` - they lie inside the one run of a setup that holds its run table alone -, 72 were `ambiguous`, lying inside runs of run tables that two or three models registered by the step share, and 72 were `none`. An embedding row carries no collection, so the log does not say which setup those 72 belong to; 60 of them lie in four bursts of that afternoon. **Now:** the pull writes the runs a ledger still names, which is the newest run of a setup only. With the ledger of that setup as it was read on 2026-10-12, that is one run of six seconds in which the log holds no document embedding, so no link changes.

**Not measured in the chain:** `Public` and the default table names; a CAS server with workers; `_lnk_utc_offset` other than 0 - the timestamps of the log met the windows of the 37 runs that have one -; `_lnk_min_literal` other than 20; a log table that grows while it is linked; the methods `questions` and `ambiguous` for a prompt or a decision, which no row got; and the *LLM Usage Report* opened on these tables.

## Making the Links Exact

The links are made by content because nothing else is logged. What would make them exact is a caller id that the manifested models send themselves: a key in `options` - the SAS Model Manager id of the prompt or decision template - that the generated score code of the answering model ignores and the container logs with the request, and, for a retrieval, the id of the setup or its collection in the `project` input of the embedding call, which the RAG runtime sends as the constant `rag` today. The parser would read both from the `Request Data:` line and no rule above would be needed for such a request. It is not built: it needs a change of the Prompt Builder and of the RAG runtime, and a new manifest of every model - a prompt, a decision template or a retrieval model that was manifested before sends no id.

# The LLM Usage Report

The repository ships a ready-made SAS Visual Analytics report,
[`SAS-Viya-Integrations/Logging-Monitoring/LLM Usage Report.json`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/SAS-Viya-Integrations/Logging-Monitoring/LLM%20Usage%20Report.json),
as a transfer package. It is the **recommended template** for monitoring LLM
usage and prompt experimentation in your environment — import it and use it as-is,
or as the starting point for your own dashboards.

## Prerequisites — the tables it reads

The report builds on four CAS tables, **all expected in the `Public` caslib by
default**. Load them (promoted and saved) before or after importing so the report
has data to bind to:

| Table | Produced by |
|---|---|
| `LLM_LOGS` | Parsing the collected container logs — `Log-Parser-Code.sas` or the *LLM - Log Parser* SAS Studio custom step |
| `LLM_FACT_SHEET` / `EMBEDDING_FACT_SHEET` | `mdb load-facts` (or `Load-Fact-Sheets.sas`) — see [Model Definition Builder](./Model-Definition-Builder.md) |
| `PROMPT_EXPERIMENTS` | `Get-All-Prompts.sas` or the *LLM - Get All Prompts* SAS Studio custom step - see [The Experiments of Every Kind](#what-it-reads) |

The report reads the twelve columns `LLM_LOGS` had before release 2.3.0 and the columns `PROMPT_EXPERIMENTS` had before it. Both tables keep those columns with their names, order and types; the columns this release adds, and the four further tables of the pull, are for reports of your own.

## Import the report

Import the transfer package exactly as for the Prompt Builder package — through
the **SAS Environment Manager → Content → Import** page, or with the `sas-viya`
CLI `transfer` plugin:

```bash
sas-viya transfer packages upload --file "LLM Usage Report.json"   # prints the package id
sas-viya transfer packages import --id <package-id>
```

(On older `transfer` plugin versions the same commands are `sas-viya transfer
upload` / `sas-viya transfer import` — they still work, marked deprecated.)

The CLI must be installed with the `transfer` plugin and a signed-in profile —
see [Introduction — SAS Viya CLI Setup](./Introduction.md#sas-viya-cli-setup).
After import the report appears under **SAS Content > SAS Agentic AI Accelerator >
Logging and Monitoring > LLM Usage Report**.

## Change the CAS library (default: `Public`)

The report binds each of its four tables to
`server=cas-shared-default;library=Public`. If your tables live in a **different
CAS library**, repoint them one of two ways:

- **SAS Viya CLI** — before uploading, replace the library in the package (every
  binding uses the same `library=Public` token, so a single substitution is safe),
  then upload/import as above:

  ```bash
  # bash / Linux / macOS
  sed -i 's/library=Public/library=MyLib/g' "LLM Usage Report.json"
  ```

  ```powershell
  # PowerShell / Windows
  (Get-Content "LLM Usage Report.json") -replace 'library=Public','library=MyLib' |
    Set-Content "LLM Usage Report.json"
  ```

  (If your CAS server is not `cas-shared-default`, substitute
  `server=cas-shared-default` the same way.)

- **SAS Environment Manager / SAS Visual Analytics** — import the package as-is,
  then open **LLM Usage Report** in SAS Visual Analytics and, in the **Data** pane,
  use **Edit → Change data source** on each of the four tables to point to the same
  table in your library.

:::note
The imported report keeps its identifier `/reports/reports/1f08db8d-d6c6-4ed1-9684-e7d11c4ec50c` —
the same URI shown as the example `SAS_LLM_MODEL_CARD_REPORT_URI` and the Prompt
Builder *Model card report URI* — so once imported it can double as the model-card
custom chart for your registered models and manifested prompts.
:::