---
title: Troubleshooting Guide
---

# Troubleshooting Guide

The Troubleshooting Guide tries to provide solution orientied fixes to reported issues. If your issue is not on this please raise an issue or if you already have a solution please contribute it as a pull request.

## SAS Intelligent Decisioning

This part of the troubleshooting guide describes issues that can occur when working with this in SAS Intelligent Decisioning.

### Duplicate declaration for VARIABLE

This error can be encountered during testing within SAS Intelligent Decisioning. Here is how the issue might show up for a variable called **llmurl**:

![Duplicate Variable Error](../../static/Duplicate-Variable-Error.png)

```SAS
ERROR: Compilation error.
ERROR: Line 167: Duplicate declaration for llmurl.
```

This error occurs when your model (for example the prompt template model) has the same variable twice in its *Variable* tab inside of SAS Model Manager. If that is the case, you have identified the source of the error. In order to fix this, please ensure that you are using the current version of the *Prompt Builder UI* (compare the versions using the [project CHANGELOG](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CHANGELOG.md)). If you created the model not using that tool, please make sure that model variables are correctly updated when creating new versions of the model.

### DS2 "pymas" package encountered a failure in the 'execute' method

This error can be encountered when testing or running a decision that contains a prompt template that was manifested with the *Include the LLM call in the manifested model* option:

```SAS
ERROR: Line 53: DS2 "pymas" package encountered a failure in the 'execute' method.
```

It means that the Python score code of the model raised an unhandled error. The most common cause is the LLM call itself failing — for example because the certificate of the endpoint hosting the LLM containers is not trusted or the endpoint is not reachable from where the model runs. Here is how to address it:

1. Ensure that you are using the current version of the *Prompt Builder UI* (compare the versions using the [project CHANGELOG](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CHANGELOG.md)) and manifest the prompt again. Since version 1.0.0 a failing LLM call no longer raises an error: the failure reason is returned through the *response* output of the model (e.g. *LLM call failed: ...*) and *parse_status* returns 0. Keep the *response* output selected while validating a decision, so you can see why a call failed.
2. If the *response* output reports a TLS/certificate problem: the model verifies the connection against the CA bundle at */security/trustedcerts.pem*, which SAS Viya mounts into the pods of CAS and of the compute servers and which covers LLM containers hosted behind the SAS Viya ingress. If your LLM containers are hosted elsewhere, point the *LLMCONTAINERCABUNDLE* environment variable to a CA bundle that covers them — see [Deployment of Decisions](./Deployment-of-Decisions.md) for all supported environment variables and how to set them per runtime. If the problem only shows once the decision is published to MAS, see [CERTIFICATE_VERIFY_FAILED in the response of a published model](#certificate-verify-failed-published-model) below.
3. If the model fails at publish time instead (*py.publish() failed*), the *requests* package is most likely missing from the Python environment. The manifested model ships a *requirements.json*, so SAS Container Runtime destinations install the package automatically during publishing; for runs in SAS Intelligent Decisioning tests, CAS or MAS, an administrator has to make the *requests* package available in the configured Python environment.

Please note that the *Call LLM node* path (prompt templates manifested without the included LLM call) is not affected by this — for that path see the Micro Analytic Service and SAS Container Runtime sections below.

## Micro Analytic Service

This part of the troubleshooting guide describes issues that can occur when publishing decisions or models that call the LLMs to the SAS Micro Analytic Service (MAS).

### 500 Internal Server Error - EXECUTEMETHOD failed for HTTP POST method with URL

This error occurs because the endpoint under which the LLMs are hosted doesn't have a valid certificate from the standpoint of your SAS Micro Analytic Service. This fix has to be done by an administrator with the ability to patch the SAS Viya deployment - for more information check out the [SAS documentation](https://go.documentation.sas.com/doc/en/mascdc/default/masag/p0kuxnv51taahin1cf23yhr0g23z.htm):

1.   Go to *$BASE_PATH/sasinstall/sas-bases/examples/sas-microanalytic-score/config* and copy the *mas-add-environment-variables.yaml*
2.   Then adjust the yaml in the following way - this assumes that you have deployed the LLMs behind the same ingress as SAS Viya if not you need to point it to your certificate accordingly:

```yaml
---
apiVersion: builtin
kind: PatchTransformer
metadata:
  name: mas-add-environment-variables
patch: |-
  - op: add
    path: /spec/template/spec/containers/0/env/-
    value:
      name: SSLCALISTLOC
      value: /security/trustedcerts.pem
target:
  group: apps
  kind: Deployment
  name: sas-microanalytic-score
```

3.   Add the transformer to your *kustomization.yaml* and apply it.
4.   Check the environment variables of the sas-microanalytic-score pod definition and there under the sas-microanalytic-score container check that the environment variable was added accordingly.
5.   Now rerun your the decision/model call and check that the error has been actually fixed.

### CERTIFICATE_VERIFY_FAILED ... self-signed certificate in certificate chain in the response of a published model {#certificate-verify-failed-published-model}

This error can be encountered in the publishing validation of a decision on a MAS destination, when the decision contains a model that calls the LLM or decision model container itself: a prompt template manifested with the *Include the LLM call in the manifested model* option, a decision template, or the retrieval model of a RAG setup. The validation ends without an error, and every row carries the message in the model's text output (*response*, *answer* or *retrieval_status*) while the other outputs are empty:

```text
Decision model call failed: HTTPSConnectionPool(host=..., port=443): Max retries exceeded with url: /llm/von_1_2/von_1_2 (Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain')))
```

The same decision returns answers in the in-app test of SAS Intelligent Decisioning, and a decision that uses the *Call LLM node* works on the same destination. The cause is that MAS runs the Python code of the model in a separate runner container that does not have the CA bundle (*/security/trustedcerts.pem*) the main container has. This fix has to be done by an administrator with the ability to patch the SAS Viya deployment:

1.   Confirm the cause and mount the bundle file into the Python runner container as described in [Publishing to SAS Micro Analytic Service: the certificate bundle](./Deployment-of-Decisions.md#mas-certificate-bundle) — the one file with a *subPath* mount, not the whole *security* volume, which also holds the private key directory of the pod. That keeps TLS verification on and is the fix for production.
2.   Check that */security/trustedcerts.pem* is listed inside the runner container, and that nothing else is under */security* there, with the commands given there.
3.   Run the publishing validation again and check that the rows carry answers.

On a test system with a closed cluster network, verification can be switched off instead — with the option **Verify TLS certificates in manifested models** of the Prompt Builder, followed by manifesting the model again (for a retrieval model registered with the custom step *RAG - Register Setup*: with the step's checkbox **Verify TLS certificates in the registered retrieval model**, followed by running the step again), or with the environment variable *LLMCONTAINERSSLVERIFY=false* on the destination (*RAGEMBED_SSLVERIFY=false* for a retrieval model). The model then no longer checks which container answers; the same section says when that is acceptable. Where MAS runs Python in the runner container, set the variable on the runner as well — see [the variable and the Python runner](./Deployment-of-Decisions.md#the-variable-and-the-python-runner).

### HTTP status 201, an empty error column and empty outputs in a publishing validation: the execution timed out {#empty-outputs-published-decision}

The publishing validation of a decision on a MAS destination ends without an error, a row has HTTP status 201 and an empty error column — and every output of the decision is empty in that row, also the outputs of nodes that have nothing to do with each other. The log of the validation job has no ERROR and no WARNING line. The same decision returns its outputs in the in-app test of SAS Intelligent Decisioning, and the same validation, run again without any change, can return other rows empty than before.

SAS Micro Analytic Service ended the execution of such a row because it took longer than 30 seconds. That was measured on 2026-10-11 on SAS Viya 2026.09, with a decision of three model nodes that were all manifested in the Prompt Builder: the retrieval model of a RAG setup, a prompt template with the included LLM call that is given the retrieved context, and a decision template. Three places show it:

- **The table of the publishing validation**: HTTP status 201, an empty error column, every output empty. Nothing in it names a timeout.
- **A direct REST call** of the step of the published module answers HTTP status 201 as well, with `"executionState": "timedOut"` and `"outputs": []`.
- **The log of the pod *sas-microanalytic-score***: *Failure encountered during execution in revision … of module context '…'. Return code: 0x8b3ff145.* and *Micro Analytic Service failed to execute method*. With the log level debug, the scheduling thread writes `0x803fc006=tkWait()` exactly 30.0 seconds after its line *Scheduled work on worker thread*, and the two failure lines follow.

What was measured about the 30 seconds:

- **The publishing validation sends the rows of its input table at the same time.** Three rows were scheduled within 12 milliseconds.
- **One call at a time stayed within them.** In single calls the retrieval node took 0.2 seconds, the LLM node 1.3 to 4.2 seconds and the decision model node 1.7 to 4.1 seconds; a whole call took 6.5 to 9 seconds. Six single calls one after another all returned HTTP status 201, `retrieval_status` *ok*, three hits, an envelope of 1969 characters and the answer of the LLM from the retrieved context.
- **Three rows at once did not.** The durations the service logs for each node (*NODE DURATION*) were 0.2 to 2.2 seconds for the retrieval node, 5 to 6 seconds for the LLM node of the first row and more than 29 seconds for the two others, and 24 to 25 seconds for the decision model node while the LLM container was busy. The LLM and the decision model were both self-hosted and ran on the same CPU node; with the retrieved context the prompt of the LLM was about 900 characters, where it was about 50 without.
- **The same validation gave two results.** The same module with the same table, run twice without any change: the first run returned all three rows empty, the second returned one row complete — its three nodes had taken 29.65 seconds in total — and two empty.
- **Three direct calls at once** all answered `timedOut`, after 30.8, 31.7 and 36.5 seconds.

In the measured decision a row was lost by the time its models took when they were called at the same time: the same rows came back complete in single calls, with nothing in the decision or in a model changed. That is one decision on one system, with two self-hosted models on one CPU node; another decision can be slow for another reason. What was ruled out for the measured one: **the lengths of the variables of a model are not the cause**: with the two context outputs of the retrieval model declared with 100 characters, single calls returned the envelope complete, with its 1969 characters ([the lengths of the variables](./RAG-Ingestion-and-Retrieval.md#the-lengths-of-the-variables)). A publishing validation that returned every row after a change to the decision or to a model had finished within the 30 seconds that time; run it more than once before reading a change as the remedy.

What a site can do follows from the durations above; none of it was measured as a remedy:

1.   **Fewer calls at once.** Validate with a table of one row, or call the module row by row. A caller of the published decision that sends its requests one after another is the case that stayed within the time.
2.   **A faster model, or models that do not share a node.** The time was spent in the two self-hosted models on one CPU node: the decision model took 24 to 25 seconds while the LLM container was busy, where it took 1.7 to 4.1 seconds alone. Whether a GPU node, a node for each of the two or a hosted model for one of them brings a validation of several rows under the 30 seconds was not measured.
3.   **A shorter prompt.** With the retrieved context the prompt of the LLM was about 900 characters instead of about 50. A smaller `k` of the retrieval model, or smaller chunks, shorten it.

No setting for the time limit is named here because none was found: the configuration definition *sas.microanalyticservice.system* of that system holds no timeout property (it holds *core.numthreads* 4 and, under *asynchronousexecution*, *corepoolsize* 5 and *maxpoolsize* 10), and no way to change the 30 seconds was tried.

## SAS Job Execution

This part of the troubleshooting guide describes what can be found in the log of a job that runs a program of the accelerator - the RAG ingestion job, a job generated from a RAG flow or the log parser.

### Two ERROR messages about Cloud Analytic Services before the first line of the program {#cas-connect-errors-before-the-program}

On the test system (SAS Viya 2026.09, 2026-10-11) every job log of the *SAS Studio compute context* began with two ERROR messages, before the first source line of the program. They stand on three log lines, because the second is longer than the line:

```text
ERROR: Failed to connect to host '<SAS Viya host>', port 5570.
ERROR: Unable to connect to Cloud Analytic Services <SAS Viya host> on port 5570. Verify connection 
       parameters and retry.
```

They stood in the logs of the RAG ingestion job, of the jobs generated from a RAG flow and of the log parser, and equally in a job that held none of the accelerator's code. The jobs ended as `completed` when nothing else failed, `SYSCC` was 0 after the log parser had run, and the CAS session the ingestion job starts itself connected on the same port. Why that system writes the two messages was not established: this is an observation of one system, and another system may not show it.

What follows from it where a log shows these lines:

1.   A check that counts the ERROR lines of a job log never finds 0 there: it finds three when it counts the lines of the type error, the continuation included, and two when it counts the lines that start with `ERROR`. Start at the first source line of the program, or read the state of the job.
2.   The lines of the accelerator name their program: `ERROR: RAG ingestion failed`, `ERROR: RAG - <step> failed`, `ERROR: The log could not be parsed`. An ERROR line before source line 1 is none of them. Since release 2.3.0 the RAG lines are written from a variable and not as a quoted string; they are still of the type error (measured on 2026-10-11).

### A long message goes on in the next line {#long-log-lines}

The job logs of the test system are 132 characters wide, and a longer message goes on in the next log line. Where the break stands depends on who wrote the line. Measured on 2026-10-11 with the RAG steps and the ingestion job of release 2.3.0:

- **A line a program of the accelerator writes with `PUT`** - the ERROR line of a RAG step and of the ingestion job, a part of a step's summary - is cut after 132 characters, inside a word, and what follows has no prefix:

  ```text
  ERROR: RAG - List Documents failed: another ingestion run holds the lock on this ledger - wait for it to finish, or retry in 30 minu
  tes if it crashed
  ```

- **A long NOTE milestone and the WARNING lines of the steps** were broken at a blank before column 132: the first log line ended with a word, in the logs of that day after 127 to 129 characters, and what followed was not indented.
- **A message SAS writes itself** is broken at a blank as well, and its next line is indented - as the second of the two connect messages above, which goes on with seven blanks and `parameters and retry.`

Every log line of a message has the type of its first one: the three lines of a 311-character ERROR line of *RAG - List Documents* and the four of a 413-character ERROR line of the ingestion job were all of the type error, and the four lines of two WARNING messages of *RAG - List Documents* were all of the type warning. A search for the text of a message therefore has to expect a break in it - anywhere in a line written with `PUT`, at a blank in the others - and a check that counts lines counts a long message more than once.

### A job of the accelerator that failed reports the error code 5713 {#failed-job-5713}

A run of the RAG ingestion job that failed ends as `failed` in SAS Job Execution from release 2.3.0 on. The record of the job does not hold the reason at its top level: measured on 2026-10-11, it held the error code 5713 with the message *A procedure in the SAS program provided for the job completed execution with at least one error.*, and inside it, under `errors`, an error with the code 1012 - the `SYSCC` the program sets - and the text of the program's last ERROR line, for example *RAG ingestion failed: another ingestion run appears active for this ledger (run lock held) - retry later*. A monitor that wants the reason reads that inner message or the log. In the log the ERROR line is the last message of the program and not the last line: the timing lines of its DATA step and the echoed source of the last statement follow it. A job of the log parser that failed carried the same code 5713, with `SYSCC` 1012 in its log.

### The *LLM - Log Parser* step fails in its second run of one SAS session {#log-parser-second-run}

`ERROR: There is no server connection to execute the action 'session.sessionId'.`, followed by `ERROR: Unhandled Python exception.` and `ERROR: The log could not be parsed, the table <caslib>.<table> was left as it is.`, in a run of the step that follows an earlier run in the same SAS session: the earlier run had started a CAS session of its own and ended it, and the step of release 2.2 and earlier takes the macro variable `_CASNAME_`, which the ended session leaves behind, for a session. Nothing is written by such a run. Measured on 2026-10-11 with two runs of the step in one SAS Job Execution job whose session had no CAS session. The step of release 2.3.0 asks the `SESSFOUND` function instead - import it again with a `PUT`, which keeps its id. With that check the step was run on the same system later that day, twice and three times in one job whose session had no CAS session: every run started and ended a session of its own and wrote its table ([Logging & Monitoring](./Logging-&-Monitoring.md#the-session-check-with-sessfound)). With the older step, put a `cas` statement in front of the flow code: a step that finds a CAS session uses it and ends none.

### The log table, the pull and the link: what their messages mean {#log-table-pull-link}

The messages of `Log-Parser-Code.sas`, `Get-All-Prompts.sas` and `Link-Logs-To-Models.sas` of release 2.3.0 that end a run or leave something out. The three ran as a chain on the test system on 2026-10-11 and twice on 2026-10-12 ([Logging & Monitoring](./Logging-&-Monitoring.md#the-chain-on-sas-viya)). Seen in those runs: the refusals of the link program for a table of twelve columns and for a catalog that is missing, the refused append of the parser, the NOTEs of the parser about requests that ran at the same time and about a character without UTF-8 bytes, the two ERROR lines of the pull for a table name of 34 characters, its WARNINGs for a setup without a collection and its two NOTEs about a run table that is not in memory, and of the link program the NOTE of a run that links nothing, the NOTE about runs without a run time, the WARNING about run ids that no longer fit and the NOTE about two models of one name. The other lines are read from the programs. Six lines are of the programs as they are since the second run; they were seen in [the third run](./Logging-&-Monitoring.md#the-third-run) later on 2026-10-12, and the table says for each what was seen. Since the second run a program that wrote an ERROR line and no table also sets `SYSCC` to 1012 - in the second run the refusal of the pull and the refusal of the link ended as completed jobs. In the third run eight such refusals ended their jobs as failed; the first DATA step behind such a program in the same SAS session does not run until `SYSCC` is reset, which in the code of a flow is the end action of the flow itself ([Reading the Job Log](./Logging-&-Monitoring.md#reading-the-job-log)). What was changed after the third run - the counter of an `optimizationId` among it - is **not run in SAS**, and the table says so where it applies. In a message of the three programs a quote shows as a backtick, `%` as `pct`, `&` as `+` and a semicolon as a comma, a comment mark is split by a blank, and parentheses that do not pair up show as brackets.

| What the log says | What it means and what to do |
|---|---|
| `ERROR: The table <caslib>.<table> has no column kind, … : it was written by a Log-Parser-Code.sas or a step LLM - Log Parser that is older than the link columns.` (link) | The log table has the twelve columns of an earlier release. Append a log to it with the parser of this release once - the append adds the columns - and run the link program again. The table was left as it is |
| `ERROR: The catalog table <caslib>.<table> was not found. Run Get-All-Prompts.sas first, …` (link) | `MODEL_CATALOG` is neither in memory nor saved under the name in `_lnk_catalog_caslib` / `_lnk_catalog_table`. Run the pull, or set the two to where it wrote the table |
| `NOTE: The table <caslib>.<table> was not found, so no embedding row is linked to an ingestion run.` (link) | `RAG_RUNS` is missing. Every other rule runs; the document embeddings of an ingestion stay `none` |
| `ERROR: The new table has <n> rows, <m> were linked and <caslib>.<table> has <k>: the table changed while it was linked, …` (link) | The parser appended to the log table while the link program ran. The table was left as it is: run the two one after the other |
| `ERROR: The table <caslib>.<table> holds <n> rows and _log_append_table is not set, so nothing was read and nothing was written - not the table and not its saved file. Set it in front of the program: %let _log_append_table = 1; adds the requests of the log to the table, %let _log_append_table = 0; replaces the table with them.` (parser) | `Log-Parser-Code.sas` ran without a value of `_log_append_table` and the table holds rows, in memory or only as its saved file. The setting has no default since release 2.3.0 - the file of release 2.2 held it as a line of its own, with `0`. The program that includes the parser sets `1` to add the requests of the log to the table or `0` to replace the table with them, in front of every include: the parser deletes its settings at its end. Nothing was read or written, and the job ended as failed ([Logging & Monitoring](./Logging-&-Monitoring.md#appending)) |
| `ERROR: The rows of the table <caslib>.<table> could not be counted and _log_append_table is not set, so nothing was read and nothing was written - not the table and not its saved file. ...` (parser) | `Log-Parser-Code.sas` ran without a value of `_log_append_table`; CAS said the table is in memory, and the listing of its rows named no table. Nothing was read or written, and the job ended as failed. Set `1` or `0` in front of the include ([Logging & Monitoring](./Logging-&-Monitoring.md#appending)) |
| `ERROR: CAS refused an action that the count of the rows of the table <caslib>.<table> needs and _log_append_table is not set, ...` (parser) | `Log-Parser-Code.sas` ran without a value of `_log_append_table`, and CAS refused `table.tableExists` of the table or the listing of its saved files (`table.fileInfo`); the ERROR lines of CAS stand above. Nothing was read or written, and the job ended as failed. Check that the account may list the CAS library ([Logging & Monitoring](./Logging-&-Monitoring.md#appending)) |
| `ERROR: CAS refused an action that the run needs for the table <caslib>.<table>, nothing was appended (replaced) and neither the table nor its saved file was written.` (parser) | CAS refused a question about the table, the listing of its saved files or of its columns, or the listing of `CASUSER`; its ERROR lines stand above. The table and its saved file are as before; a table that the run loaded from its saved file for the append was dropped again |
| `ERROR: CAS refused to say whether a table the program reads is in memory or to list the saved files of its CAS library, ...` (link) | `table.tableExists` or `table.fileInfo` was refused for the log table, the catalog or `RAG_RUNS`, or for their CAS library. Nothing was linked; the tables are as before |
| `WARNING: The table <caslib>.<table> was not in memory. It was loaded from its saved file, and CAS refused to say whether it was promoted then: ...` (link) | The run had nothing to link, and the question after the promotion of the loaded log table was refused. Where the promotion did not take, the table ends with the CAS session; its saved file is as before |
| `ERROR: The new table could not be created, the table <caslib>.<table> was left as it is. The DATA step that appends ended with the return code <n> and wrote <r> rows, the table held <e> and the log <u>, and <x> rows of the table were taken out for <p> rows of the log (-1 stands for a table that was not found). The DATA steps that leave out the requests the table holds already ended with the return code <c>.` (parser, append) | The existing table and the parser disagree about a column - a number on one side, text on the other: the line then stands behind an ERROR of the DATA step, as in `ERROR: Variable temperature has been defined as both character and numeric.` -, or the DATA step did not write as many rows as the table and the log hold together. The existing table is unchanged and what the DATA step left was dropped. A table that the run loaded from its saved file for the append was dropped again, so only its saved file is left. A table whose name no libref addresses - more than 32 characters - ends all three DATA steps this way. Without the second sentence the line is the one of replace mode, or of a parser of before 2026-10-12; without the third it is a parser of before 2026-10-13; without `, and <x> rows of the table were taken out for <p> rows of the log` it is version 2.8 of the step (2026-10-13) |
| `NOTE: <n> request(s) were running at the same time as another request - of their container, or of another container whose lines are mixed with theirs in the file. ... <m> request(s) can have missing values, and failed is missing for <k> request(s). A request that was running or waiting where the lines of its container end is counted in the NOTE of those requests, and not in the last number.` (parser) | Lines that name no request were given to none. In a file of the collection script this is two requests of one container at the same time. In a file that orders the lines of all containers by their time it is every request that ran while another container was answering: export each container's lines as one block ([Logging & Monitoring](./Logging-&-Monitoring.md#requests-that-run-at-the-same-time)) |
| `NOTE: <n> request(s) were running where the lines of their container end - at the end of the file, or at the end of a part of it: where the collection script wrote its line in front of the lines of the next container, or, in a file without such lines, where the time of the lines goes back. The log holds neither a response nor an error for them, and failed is missing for them.` (parser) | The lines of a container end in the middle of these requests - a collection that was made while they were running -, so the file holds their first lines and no answer. Their rows have `failed` missing, not 1. Seen on 2026-10-12 in the third run, on files made by hand, with the words of version 2.4 of the step (`... at the end of a part of it, where the time of the lines goes back. ...`) ([Logging & Monitoring](./Logging-&-Monitoring.md#requests-that-run-at-the-same-time)) |
| `NOTE: <n> request(s) have no response: a line that reads as a request line stands in their response, in front of the next line of their transaction, and nothing behind it in its part says whether it is a line of the response or a request that came in while the response was written and that the end of the part cut off. It was read as a line of the response.` (parser) | The response of these requests quotes a request line, and nothing later in its part tells a quote from a request that the end of the collection cut off: the two files are the same line for line. No row is written for the line, and the response of the request is missing. New in version 2.6 of the step and not seen in a SAS log: the file its code file read on 2026-10-13 gave no NOTE ([Logging & Monitoring](./Logging-&-Monitoring.md#requests-that-run-at-the-same-time)) |
| `NOTE: <n> line(s) that read as a request line stand in the response of another request, in front of the next line of its transaction, and got no line of their own - no transaction, no error, no status and no response - while a request that came later in their part got its transaction. They were read as lines of that response, and no row was written for them. A request whose container wrote nothing but its first lines there reads the same.` (parser) | A response holds a line that reads as a request line, that line got nothing of its own, and a request that came later in its part got its transaction: the line was read as text of the response, and the response is whole. A request whose container wrote nothing but its first lines there is the same file line for line, and it has no row. New in version 2.7 of the step, not run in SAS ([Logging & Monitoring](./Logging-&-Monitoring.md#requests-that-run-at-the-same-time)) |
| `NOTE: <n> request(s) hold a value that could not be read, for example a 'Request Data:' line that was cut off or a request body that is nested deeper than Python reads. The rows were kept with what was read.` (parser) | The rows are in the table with what was read; `kind` is missing where the inputs could not be read. Options that are nested deeper than Python decodes are not counted here: they are read as names and values, and the row has them as they were sent - measured on 2026-10-12 with options of 100000 opening brackets. The NOTE as it is here was seen later that day for a `Request Data:` line nested 100000 deep |
| `NOTE: <n> request(s) hold a character that has no UTF-8 bytes in a text column ...` (parser) | Half of a surrogate pair in a request body. It is a question mark in the table |
| `ERROR: The table <lib>.<name> is not a CAS table. For this step to run the target table needs to be in CAS, no log was read.` (parser) | `_log_target_table_engine` is not `CAS`, or the output table of the step is no CAS table. It is the one line of the run: no log was read and no table was touched, `SYSCC` is 1012 and the job ends as failed - seen on 2026-10-12 in the third run with the engine `V9`. The parser of 2026-10-12 and earlier wrote `... is not a CAS table.`, `ERROR: In order for this step to run your target table needs to be in CAS` and `ERROR: Execution terminated by an ABORT statement`, and then `ERROR: The log could not be parsed, ...` about a log it had not read (measured on 2026-10-12) |
| `NOTE: The run table <caslib>.<table> is not in memory, its runs are not in <RAG_RUNS>.` (pull) | Neither the run table of the RAG setup nor its ledger is in memory; the pull loads none. The step *RAG - Load Vector Store* publishes a `<prefix>_RUNS` table, and so does the ingestion job of a setup once its job definition holds the program of release 2.3.0 - **Manifest setup** writes it there - and it ran with the run history switched on. Load the table of the setup, or let its ingestion run once, and run the pull again if its ingestion runs should be linked |
| `NOTE: The run table <caslib>.<prefix>_RUNS is not in memory. <n> run(s) were taken from the ledger <caslib>.<prefix>_LEDGER - the runs its rows still name, each from the start its id holds to the latest of its rows.` (pull) | The setup has no run table in memory and its ledger is: its ingestion job has not published the table - the job definition holds the program from before release 2.3.0, or the run history of the setup is switched off. A ledger names the newest run only, so `RAG_RUNS` holds that one run of the setup, without counts (`run_source` = `ledger`). For every run the vector store holds, run **Manifest setup** and the ingestion once and then the pull again. Seen both ways on 2026-10-12 in the third run: with the run table the job had published the pull read four runs from it and wrote no such line, and with that table dropped from memory it wrote this line and took one run from the ledger |
| `ERROR: The table name <name> of the output <output> has <n> characters. A SAS libref addresses a CAS table by a name of 32 characters at most, nothing was read or written.` or `... is no SAS name: letters, digits and underscores, and no digit in front. Nothing was read or written.` (pull) | CAS takes such a name and no libref can address the table: on 2026-10-11 a table with a name of 34 characters was written, promoted and saved and then reported as `was not written`. Give the output a SAS name of 32 characters at most |
| `WARNING: The properties of the model <name> (<id>) could not be read: ... The setup has no collection without them and is left out of the model catalog, ...` or `WARNING: The RAG setup <name> (<id>) names no collection and is left out of the model catalog, ...` (pull) | The setup keeps its row of `RAG_SETUPS` and has no row in `MODEL_CATALOG`, where no request could be linked to it. On the test system SAS Model Manager answered the request for one model with HTTP 500, also outside of the program |
| `WARNING: No project <name> was found, the <role> models it would hold are not in the catalog.` (pull) | The project of the model definitions has another name on this system. Set `_gap_llm_project`, `_gap_embedding_project` or `_gap_decision_project` (the field of the step) to its name, its id or `tag:<tag>` - without those models no row of that kind gets a `model_id` -, or to nothing where the site has no such models |
| `ERROR: The experiments could not be read from SAS Model Manager, the output tables in <caslib> were left as they are.` (pull) | The projects could not be listed, two outputs have one name, or the Python package `requests` is missing; the line above says which |
| `ERROR: The selected output library does not point to a CASlib, nothing was read or written.` (step *LLM - Get All Prompts*) | The output table of the step is no CAS table. Nothing was read from SAS Model Manager, and the job ends as failed. Seen on 2026-10-12 in the third run, in the code of a flow with the output table in WORK: the end action of the flow behind the step did not run, so the log has `_FLW_ACTION_START_` and no `_FLW_ACTION_END_`; up to version 2.1 the step ended such a run with an ABORT statement |
| `WARNING: The file Prompt-Optimization-Tracker.json of the model <name> (<id>) holds the optimizationId <ids> on more than one entry. In the model catalog the rows of a later such entry carry the id with a counter, as in 1#2 - ...` (pull) | Two entries of one tracker have one id. No entry is left out: in `MODEL_CATALOG` the later one is `<id>#2`, so that the key of the catalog stays unique, and `PROMPT_OPTIMIZATIONS` holds both with the id of the tracker. A join of the catalog to `PROMPT_OPTIMIZATIONS` by the id does not find the rows with a counter. Seen on 2026-10-12 in the third run for a tracker with the id 1 on two entries: `1` and `1#2` in the catalog. Since then a counter that would make the id of another entry is passed over (1, 1 and `1#2` give 1, `1#3` and `1#2`), and where entries without an `optimizationId` repeat, the line says `... holds <n> entries without an optimizationId.` and their rows carry the counter alone (`#2`); that is not run in SAS |
| `ERROR 180-322: Statement is not valid or it is used out of proper order.` four times behind the summary of the link, and a job that ended as failed | The link program of before 2026-10-12, whose summary lines held semicolons. The table was written, promoted and saved correctly. Replace your copy of `Link-Logs-To-Models.sas` |
| `WARNING: A session with the name MYSESS already exists.` | `Log-Parser-Code.sas` or `Get-All-Prompts.sas` of an earlier release - or the link program of before 2026-10-12 - ran behind another of them in one SAS session and started `mysess` again. The programs of this release ask `SESSFOUND` first: with them the parser, the pull and the link ran in one job on 2026-10-12 without this line. `Load-Fact-Sheets.sas` asks it since then as well - on 2026-10-13 it ran where no session was connected, started one of its own and ended it; behind a connected session it is not run in SAS -; `Build-RAG-Cost-View.sas` does, and ran behind a connected session on 2026-10-12 without starting one |
| `NOTE: 0 of <n> rows of <caslib>.<table> were linked, as every row has a match_method. The table was left as it is. With _lnk_redo_all = 1 all rows are linked again.` (link) | Nothing was new. After a new pull - a template that changed, runs that were added - set `_lnk_redo_all` to 1 |
| `NOTE: The table <caslib>.<table> was not in memory. It was loaded from its saved file and promoted as it was loaded, the saved file was left as it is.`, or `... and not promoted, as _lnk_promote_table is not 1, so it is a table of this CAS session and gone when the session ends. ...`, or `WARNING: ... could not be promoted, ...` (link) | After a restart of the CAS server the log table was only there as its saved file, and the run had no row to link. With the first line the table is in memory again for every session; with the two others it is not, and the next program that needs it has to load it. The two NOTEs were seen on 2026-10-12 in the third run, each with what the next job found: the table in memory, and no table. The WARNING was not provoked |
| `NOTE: <n> row(s) were linked to an experiment run without a run time to compare, as the catalog holds none for the runs with their prompts. ...` (link) | `MODEL_CATALOG` was written by a pull that is older than the column `run_time`. The rows carry the newest run with their prompts. Run the pull of this release and link again with `_lnk_redo_all = 1` |
| `WARNING: <n> row(s) that were linked to an experiment run earlier no longer name that run: for <a> the catalog has no run with their id, for <b> the run with their id has another run time than the request. ...` (link) | The id of a run is its place in the tracker, and the Prompt Builder numbers the runs again when one is deleted. Link all rows again with `_lnk_redo_all = 1` |
| `NOTE: The catalog holds 2 models named <name> with the role <role> - <id> in project <id>, <id> in project <id> - so the name of the container does not say which of them answered, and model_id is empty in <n> row(s).` (link) | Two model definitions have the name of one container. Name the project that holds the deployed definitions by its id in `_gap_llm_project`, `_gap_embedding_project` or `_gap_decision_project` of the pull |
| `NOTE: <n> catalog row(s) of an optimization or a judge name no model, hold less than <n> characters of text or have a window that can not be read. Such a row takes no part.` (link) | The entry of the optimization tracker holds no system prompt the optimization started from, or names no model. The read of the test system of 2026-10-12 held 8 such rows, of six entries of July 2026. The entries are in `PROMPT_OPTIMIZATIONS` all the same |
| `WARNING: The catalog row of <name> (<modelID>) could not be read - <error> - and takes no part in the links.` or `WARNING: <n> row(s) could not be compared with the catalog - <error> - and got match_method none.` (link) | The content of one row stopped the comparison, for example questions nested deeper than Python reads JSON. Every other row is linked |
| `WARNING: The setting <name> is <value>, which is no number. <default> is taken in its place.` (link) | `_lnk_min_literal`, `_lnk_utc_offset` or `_lnk_query_window` holds a text |
| `NOTE: The label of the table <caslib>.<table> could not be given to the linked table, which has none now.` (link) | CAS refused the label. The links are in the table |
| Many rows with `match_method` = `none` | Expected for direct calls of a model, for the health check of a prompt optimization, for queries whose prompt does not follow within `_lnk_query_window` seconds and for embeddings outside of an ingestion run. For the calls of a published prompt or decision: look whether `MODEL_CATALOG` holds its template (`role` = `prompt` or `decision`, `runID` empty) and whether the template has 20 characters of fixed text - the link program counts the templates that have less in a NOTE. For the calls of an optimization or of a judge: look whether the catalog holds rows of the role `optimization` or `judge` for the prompt, with the model the calls went to in `target_model` |
| Rows with `match_method` = `ambiguous` | Two callers fit equally well: two prompts or decision templates with the same text, one question asked of two collections in the same second, two RAG setups with one collection of which neither is the registration of the other, two ingestion runs that overlap, a run table that several setups share. `match_candidates` is their number |

## SAS Container Runtime

This part of the troubleshooting guide describes issues that can occur when publishing decisions or models that call the LLMs to a SAS Container Runtime (SCR) publishing destination (independent of AWS, Azure, GCP or Private Docker).

### SSL Error: Missing CA trust list

This error is mostly likely caused by the fact that you are using a certificate for the LLM containers that isn't valid according other SCR containers - this might be because you are using a self-signed certificate or organization signed. This fix has to be done by the person that deploys the container - for more information check out the [SAS documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/p0r7reghey2nmsn1lubyxbibzwjh.htm):

```yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <CONTAINER>
  namespace: <NS>
spec:
  selector:
    matchLabels:
      app: <APP_NAME>
  template:
    metadata:
      labels:
        app: <APP_NAME>
    spec:
      containers:
      - name: <APP_NAME>
        image: <CONTAINER_REGISTRY>/<CONTAINER>:latest
        resources:
          requests:
            memory: "4000Mi"
            cpu: "1000m"
        ports:
        - containerPort: 8080
          protocol: TCP
        env:
        - name: SAS_SCR_SSL_CERTIFICATE
          value: /ssl/certificate-file.crt
 ... Shortend for brevity
```

Please note that you have to mount the certificate file in a volume to the container!