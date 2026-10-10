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

For more information on how to parse the log and load it into SAS please take a look at `SAS-Viya-Integrations/Logging-Monitoring`.

:::note What the containers log
The shipped deployment templates set `SAS_SCR_LOG_LEVEL_SCR_IO=TRACE`, and the parser depends on it: the `Request: POST` / `Request Data:` lines carry the inputs and the options of every call, and the lines of the score code the token counts, the run time and the answer. For an LLM that is the system prompt, the user prompt and the full response; for a decision model the state, the questions and every answer with its confidence; for an embedding model the embedded text. That is what makes the usage report possible, and it also means prompt text and responses land in your log pipeline. Decide who may read the collected log file and the `LLM_LOGS` table accordingly, or lower the level and forgo the report.
:::

:::danger The containers also log secrets
At this level a container writes more than prompts. The options of a request are logged as they arrive, and for every model that is called with a provider key they hold it as `API_KEY:<the key>`. The request headers are logged too, and a call from the browser (the Prompt Builder) carries the user's `Cookie` header. Both must not reach a file that SAS Studio users can open: the collection script below replaces them with `REDACTED` before a line is written, and an established logging process needs the same step (the `redact_llm_log` function works on any stream of container log lines).

A log file collected **without** this redaction - with the script of release 2.2.0 or earlier, for example - may contain provider keys and session cookies. Delete those files, including copies and backups, and rotate every provider key that was in use while they were collected. The `LLM_LOGS` table does not need to be rebuilt for this: the parser never copied the key or a header into it.
:::

## Simplified Logging via a Script

Only use this script if you do not have a more robust process in place.

Create a script—for example `collect_llm_logs.sh`—and ensure that it is executable.
Use the code below and change both the namespace and the log path.
The namespace should be the namespace into which you have deployed the models, and the path needs to be reachable from inside of SAS Studio.
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