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
The file has to hold the lines as the containers wrote them: the parser only reads a line that starts with `<timestamp> - <logger> - <level> - `, so a timestamp, a pod name or a JSON wrapper that your process adds to each line has to be removed again. If it is not, the parser reads no request and says so with a warning in the SAS log.

For more information on how to parse the log and load it into SAS please take a look at `SAS-Viya-Integrations/Logging-Monitoring`.

:::note What the containers log
The shipped deployment templates set `SAS_SCR_LOG_LEVEL_SCR_IO=TRACE`, and the parser depends on it: the `Request: POST` / `Request Data:` lines carry the inputs and the options of every call, and the lines of the score code the token counts, the run time and the answer. For an LLM that is the system prompt, the user prompt and the full response; for a decision model the state, the questions and every answer with its confidence; for an embedding model the embedded text. That is what makes the usage report possible, and it also means prompt text and responses land in your log pipeline. Decide who may read the collected log file and the `LLM_LOGS` table accordingly, or lower the level and forgo the report.
:::

:::danger The containers also log secrets
At this level a container writes more than prompts. The options of a request are logged as they arrive, and for every model that is called with a provider key they hold it as `API_KEY:<the key>`. The request headers are logged too, and a call from the browser (the Prompt Builder) carries the user's `Cookie` header. Both must not reach a file that SAS Studio users can open: the collection script below replaces them with `REDACTED` before a line is written, and an established logging process needs the same step. The `redact_llm_log` function of the script expects the lines as the container wrote them, which is how `kubectl logs` prints them without `--timestamps` or `--prefix`: its three header expressions match a header at the start of a line, and only the `API_KEY` expression finds its text anywhere in a line. A process that puts a timestamp or a pod name in front of every line, or wraps the line (the log files of the container runtime, JSON), has to redact before that happens or adapt the three header expressions.

A log file collected **without** this redaction - with the script of release 2.2.0 or earlier, for example - may contain provider keys and session cookies. Delete those files, including copies and backups, and rotate every provider key that was in use while they were collected. The `LLM_LOGS` table does not need to be rebuilt for this: the parser never copied the key or a header into it.
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

- **`Log-Parser-Code.sas`** starts a CAS session itself (`cas mysess;`), assigns the librefs `public` and `casuser` and sets its options in the `%let` lines at its top. `_log_target_table_lib` names a libref: the program looks up the caslib behind it and, when no libref of that name is assigned, hands the value to CAS as the name of the caslib.
- **The custom step** is the same program without those first lines, so it takes the following from the SAS session it runs in.

### What the Custom Step Takes from the Session

- **A CAS session.** The step asks the `SESSFOUND` function whether the session that the SAS option `SESSREF` names is connected - that is the session its CAS actions run in. When it is connected, the step starts no session, runs in that one and does not end it. When it is not, the step starts a session of its own (`cas llmLogMySess;`) and ends it as its last action. A table that is not promoted belongs to the session that created it, so of a run with a session of its own only what *Promote the table to global scope* or *Save the table* kept is left. The option goes on naming a session that was ended: on the test system `_CASNAME_`, `_SESSREF_` and the option `SESSREF` still named `LLMLOGMYSESS` after such a run, while `cas _all_ list;` listed no session, and SAS wrote `NOTE: The default CAS session LLMLOGMYSESS identified by SAS option SESSREF= was terminated. Use the OPTIONS statement to set the SESSREF= option to an active session.` That is why the step does not go by a name, and why statements of your own that follow the step in the same SAS session and need CAS have to start a session or set `SESSREF` to one.
- **The libref of the output table.** SAS Studio hands the step the output table as a libref and a table name. The step looks the libref up in `dictionary.libnames` and writes to the caslib it finds there. When the libref is not assigned in the session, nothing is found and the name of the libref is handed to CAS as the name of the caslib - which is the caslib you chose only where the two have the same name, as with `CASUSER` and `PUBLIC`. Without the libref the lines SAS Studio adds behind the step also report the output table as not existing: in the job that had a CAS session and no libref, the log ended with `_FLW_ACTION_TABLE_|CASUSER|<table>|0`, where the job with the libref had `|1`. The table was there - promoted, saved and read from a new CAS session with its 174 rows. What SAS Studio does with that line in a flow was not looked at; assign the libref in front of the flow to have it report the table.
- **The caslib `CASUSER`** of the user who runs the step. The parsed rows are uploaded there as `_TEMP_LLM_LOGS`, copied or appended to the output table from there, and the staging table is dropped at the end.
- **PROC PYTHON with the pandas and swat packages, and what Python needs to reach CAS:** the SAS options `CASHOST` and `CASPORT`, the certificate file named in the environment variable `SSLCALISTLOC` and the token in `SAS_SERVICES_TOKEN`. The Python code connects to the CAS session of the step with them.

The code SAS Studio generates for a flow provides neither a CAS session nor the libref: the code generated on the test system for a flow of the step and its output table held no `cas` and no `libname` statement. The measurements ran that code unchanged as SAS Job Execution jobs, with the output table in `CASUSER`, in three kinds of session. The first had these two lines in front (the name of the session is free):

```SAS
cas mysess;
libname casuser cas caslib='casuser';
```

The second had the `cas` statement alone in front, and the third nothing, so that the step started its own session. [What Was Measured](#what-was-measured) has what each of them did. These runs were made with the step as it was on 2026-10-11, which started its own session where the macro variable `_CASNAME_` did not exist. The step of this release reaches the same two branches through `SESSFOUND`; that check is **not run in SAS**.

### Reading the Job Log

The parser says in the SAS log what it did with the output table:

- `NOTE: The log contains no requests, the table <caslib>.<table> was left as it is.` - an empty log, or one that holds no scoring request.
- `WARNING: Python-Subprocess - No request was read: 'Request: POST' was found in <n> line(s) that do not start as a log line ...`, followed by the same note - the lines of the log carry something in front, see the top of this page.
- `ERROR: The log could not be parsed, the table <caslib>.<table> was left as it is.` - the Python code did not finish, for example because the file is missing. Measured for a file that is missing: the line stood below `ERROR: Unhandled Python exception.` and a traceback that ends in `FileNotFoundError: [Errno 2] No such file or directory: '<path>'`, `SYSCC` was 1012 and the job ended as `failed`.
- An error that says `The saved table <caslib>.<table> could not be loaded, nothing was appended and the saved file was left as it is.` (append) or `The new table could not be created, the table <caslib>.<table> was left as it is.` (replace).

When you search a job log for `ERROR`, start at the first line of the program: lines before the first source line are not the parser's. On the test system every job log of the *SAS Studio compute context* began with two such messages - `ERROR: Failed to connect to host ...` and `ERROR: Unable to connect to Cloud Analytic Services ...` - before source line 1, also in a job that held no parser code. They stand on three log lines, all three of the type error, because the second message is longer than the line and goes on with `parameters and retry.`: a check that counts the lines of the type error finds three, one that counts the lines that start with `ERROR` finds two. The jobs in which nothing else failed ended as `completed`, `SYSCC` was 0 after the parser had run, and from source line 1 on their logs held no ERROR and no WARNING line.

## What Was Measured

The collection script of this guide, `Log-Parser-Code.sas` and the *LLM - Log Parser* step were run on the test system (SAS Viya 2026.09) on 2026-10-11, the parser as SAS Job Execution jobs that wrote to a table in `CASUSER`:

- **Collection and redaction.** The script, with only `llm_log_path` and `llm_logging_interval` changed, read the pods its selector picked - all of them model pods - and wrote 5,773 lines with 174 requests. An unredacted copy of the same logs held one provider key and seven `Cookie` headers; the written file held `API_KEY:REDACTED` once and `Cookie: REDACTED` seven times, neither the key nor one of the cookie values, and the same number of lines.
- **Every kind of model that had requests.** The 174 requests gave 174 rows with the twelve columns: 57 of three LLMs with prompts, response, token counts and run time; 50 of a decision model with the questions and the answers as JSON, the state, token counts and run time; 67 of an embedding model with its tokens in `prompt_length` and its run time, and no prompt, response or `output_length`. No cell held `API_KEY` or a `Cookie` header.
- **An empty log and a log without requests.** A file of 0 bytes and a file with start-up lines only each ended with the note, in replace and in append mode. The table kept its 174 rows and its saved file was not written.
- **Append.** Two newer collections with 21 and 7 requests took the table from 174 to 195 to 202 rows, and no earlier row was lost. Before the second one the table was dropped from memory while its `.sashdat` stayed: the parser loaded the saved table first, appended, promoted and saved it again. The three files held different requests - a log that is parsed twice is in the table twice.
- **The custom step.** Run as the code SAS Studio generated for a flow, in a session with a CAS session and the libref, the step wrote the same 174 rows as the program in replace mode and the same 195 after appending the second collection.
- **The custom step in a session without a CAS session.** With nothing in front of the generated code, the macro variable `_CASNAME_` did not exist; in a job of the same context that held no other code, `cas _all_ list;` listed no session and the option `SESSREF` named `CASAUTO`. The step ran `cas llmLogMySess;` - `NOTE: The session LLMLOGMYSESS connected successfully to Cloud Analytic Services ...`, `NOTE: The SAS option SESSREF was updated with the value LLMLOGMYSESS.` -, wrote the table and ended the session: `NOTE: Deletion of the session LLMLOGMYSESS was successful.`, the NOTE about the option `SESSREF=` quoted above, and `NOTE: Request to TERMINATE completed for session LLMLOGMYSESS.` Promoted and saved, the table was read from a new CAS session, global, with the 174 rows. Saved and not promoted, no table was in memory and the `.sashdat` held the 174 rows. Neither promoted nor saved, nothing was left. `SYSCC` was 0 and the jobs ended as `completed`.
- **The custom step without the libref.** Behind `cas mysess;` alone the step ran in that session, started none of its own and wrote the promoted and saved table to `CASUSER`, with the same 174 rows. The log of that job ended with `_FLW_ACTION_TABLE_|CASUSER|<table>|0`; with the libref it was `|1`.
- **The step twice in one SAS session.** The code SAS Studio generated for two flows of the step, one behind the other in one SAS Job Execution job whose session had no CAS session: the first run started `LLMLOGMYSESS` and ended it. Between the runs `_CASNAME_` existed, the option `SESSREF` named `LLMLOGMYSESS` and `cas _all_ list;` listed nothing. The second run started no session: `ERROR: There is no server connection to execute the action 'session.sessionId'.`, `ERROR: Unhandled Python exception.`, `ERROR: The log could not be parsed, the table CASUSER.<table> was left as it is.`, `SYSCC` 1012, no table, and a job that ended as failed. This is what the session check was changed for: the step now asks `SESSFOUND`.
- **The step put on the server in place.** `PUT /dataFlows/steps/{id}` with the step file plus its id was answered 200 and the id was kept; the stored program and the texts of the *About* tab equalled the file.
- **The copy from SAS Content, as it is printed in this guide** - see [Without a Shared Mount](#without-a-shared-mount): the copy with the size and the digest of the upload, and the 174 rows from `Log-Parser-Code.sas` behind it.
- **A log file that is missing**, in replace mode: the ERROR described under [Reading the Job Log](#reading-the-job-log), no table, no `<table>_NEW` and no saved file. No table of that name existed before the run, so that an existing table is left as it is was not seen there.

The runs of the step in the last bullets, from the session without a CAS session on, were made later on 2026-10-11 with the step as it was that day: its session check went by the macro variable `_CASNAME_`.
- **The warning for unread requests.** A log collected with `kubectl logs --timestamps` (858 lines, 26 of them with `Request: POST`) gave the warning with that count and the note, and the table was left as it is.
- **A request that is still open.** A request that was still running when its container's part of the file ended gave one row with its prompts and without token counts, run time and response. The lines of another container's finished request followed it in the file, and their numbers were not added to it.
- **Failed requests.** Two requests to a decision model that failed in the container - a traceback in the log, no score lines, no `Response: Status Code` line - gave one row each with the questions and the state, without numbers and response.

### Not Measured

- The redaction of the `Authorization` and `X-CSRF-TOKEN` headers: none occurred in the logs, so only the `API_KEY` and the `Cookie` expression were exercised.
- The shared mount of the example paths and the hourly crontab entry.
- The models that had no request in the collected day. The rows come from three LLMs - one of them, with a single row, is called with a provider key -, one embedding model and one decision model. No embedding model that is called with a provider key had a request, and a second decision model has failed requests only.
- The error branches: an append whose saved `.sashdat` can not be loaded, a replace that can not build the `<table>_NEW` table, and a log file that is missing while the output table exists.
- Append without promoting, a saved table whose name differs from the table name in upper and lower case, and a target caslib other than `CASUSER` - nothing was written to `Public`.
- The session check of this release - `SESSFOUND` on the session the option `SESSREF` names: the step twice in one SAS session, the step behind a CAS session of the user that is still there afterwards, and the step behind a CAS session that was ended. It rests on what SAS documents of the function and of the option, and on the tests of the repository, which decide the condition for these three states without running SAS.
- The step clicked in the SAS Studio UI, and how SAS Studio shows its *About* tab: the step was run as the code SAS Studio generates for a flow, in SAS Job Execution jobs. Whether an interactive SAS Studio session of the test system starts without a CAS session was not looked at either.
- The step's own session in append mode, and a run without the libref where the name of the libref is not the name of a caslib.
- A log with bytes that are not valid UTF-8, and two requests that overlap in one container. Both are covered by the tests of the repository, which run the parser's Python outside of SAS.

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
| `PROMPT_EXPERIMENTS` | `Get-All-Prompts.sas` (the LLM Prompt Builder experiment tracker) |

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