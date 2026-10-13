# Setup: the first page and the tables of the reports

Two programs for two job definitions of the content folder `/SAS Agentic AI Accelerator`. Each job definition holds a copy of its program.

| Job definition | Program | What it does |
| --- | --- | --- |
| `Start Here` (at the root of the content folder) | *Start-Here.sas* | One HTML page: what the accelerator is, links to its two reports and to the guides, and the installation check |
| `Data/Load-Accelerator-Tables` | *Load-Accelerator-Tables.sas* | Loads the release table and the three fact sheets from the folder `Data` into CAS |

Both run in a compute context that runs under the identity of the person who starts them: each job definition names that context in its parameter `_contextName`, and a request can name another one.

## Start Here

Run from SAS Job Execution, the job writes one page to the browser. The page holds no script and loads no file from another host; its style is inline.

```text
https://<your SAS Viya host>/SASJobExecution/?_program=/SAS Agentic AI Accelerator/Start Here
```

The page shows what its SAS session may read, so it is opened in a compute context that runs as you. The parameter `_contextName` of the job definition names that context; to open the page in another one, name it in the address:

```text
https://<your SAS Viya host>/SASJobExecution/?_program=/SAS Agentic AI Accelerator/Start Here&_contextName=<a compute context that runs as you>
```

Measured on SAS Viya 2026.09 on 2026-10-12, when the job definition did not have the parameter yet: opened without `_contextName` the job ran in the `SAS Job Execution compute context`, which runs its sessions under a service account on that system. SAS Viya answered every request of the session with 401, and the page read `13 ok, 0 missing, 15 not checked` - fifteen lines, twelve of them with the advice to ask an administrator. Opened with `&_contextName=SAS Studio compute context` the same page had 97 lines that were ok.

Where SAS Viya refuses every request of the session with 401 or 403, the page has one line at its top in place of those lines: what it is, and how the page is opened with `&_contextName=`. The line says that the session runs under a service account, and not as you, where the user of the SAS session (`SYSUSERID`) is another one than the user SAS Job Execution started the session for (`SYS_COMPUTE_SESSION_OWNER`); the program compares the two and writes neither to the page. In such a session that is refused by some services only, each line that is refused advises to open the page as yourself, and the note under the summary says whose session built the page.

The installation check gives every line one of three states - `ok`, `missing`, `not checked` - with what was found and one sentence what to do:

| Section | What is looked at |
| --- | --- |
| Release | `release.json` in the content root: release, commit and date. The file is asked for at the Files service only |
| Content | Every entry `release.json` lists: present in this content root or not. A custom step is registered - by the import of the package or by `mdb content-deploy` -, so a `.step` file that was uploaded to the folder is not the step: the step is `missing` while only that file is there, and the line of a step that is registered names a file of its name that lies beside it |
| CAS tables of the reports | `ACCELERATOR_RELEASES` (Prompt Builder) and `LLM_LOGS`, `PROMPT_EXPERIMENTS`, `LLM_FACT_SHEET`, `EMBEDDING_FACT_SHEET` (LLM Usage Report): there, their rows, when they were loaded |
| SAS Model Manager | The repository and the projects of the LLM, the embedding model and the decision model definitions, found by name as *Get-All-Prompts.sas* finds them, a project inside the repository as the Prompt Builder asks for the decision models; the number of models of each |
| Options of the Prompt Builder report | `modelRepositoryID`, `llmProjectID` and, with RAG switched on, `embeddingProjectID`: whether each names the object that was found by its name. The Prompt Builder reads the repository and these two projects by the ids the options hold |
| Credentials | The credential domain of the provider keys, by its name. No entry of it is asked for |
| Python packages | For each part what the Python of the compute context needs: `pandas`, `swat`, `requests` for logging; for the RAG runtime also `psycopg2-binary`, `singlestoredb`, `pypdfium2`, `markitdown`; `dspy`, `requests` and `optuna` in the versions of `Prompt-Optimization/requirements.txt` for prompt optimization |
| Publishing destinations | Whether one is defined, and their names |
| Compute contexts | The contexts the options `computeContext` and `ragComputeContext` of the Prompt Builder name, and the ones the parameter `computeContexts` names |

What holds for every page:

- **It changes nothing.** Every request is a GET, and the one file the program writes is the page, in the WORK folder of its session.
- **It does not fail.** A service that does not answer, a right the session lacks and a folder that is not there are lines of the page: `not checked` with the HTTP status, or `missing`. Where PROC PYTHON does not run in the compute context, the page says that and nothing else. The program switches the option `SYNTAXCHECK` off while it runs and sets `SYSCC` back to 0 at its end, because SAS Job Execution shows the page of a job that completed and its own error page for one that failed.
- **It shows what its session may read.** In a compute context that runs under a service account that is the service account. No token and no user id is written to the page, and of an answer that was refused only the status.
- **Every text is escaped.** A name from the system or from a parameter is text on the page, and a guide address that is no `http` address is no link.
- **No advice is an upload.** What the page says to do about a custom step that is missing names the import of the package and `mdb content-deploy`, which register it. Measured on 2026-10-12: a `.step` file uploaded in the place of a step is a file of the folder and no step, and `mdb content-deploy` then registers the step and leaves that file beside it.

Parameters of the job definition:

| Parameter | Value | |
| --- | --- | --- |
| `_action` | `execute` | |
| `_output_type` | `html` | The job writes the page itself, to the fileref `_webout` |
| `_contextName` | `SAS Studio compute context` | The compute context the job runs in: one that runs as the person who opens the page |
| `contentRoot` | `/SAS Agentic AI Accelerator` | The content folder that is checked |
| `caslib`, `casServer` | `Public`, `cas-shared-default` | Where the tables of the reports are |
| `credentialDomain` | `agentic-ai-keys` | Used where the option of the Prompt Builder report is empty or says `none` |
| `repositoryName`, `llmProject`, `embeddingProject`, `decisionProject` | `LLM Repository`, `LLM Model Project`, `Embedding Model Project`, `Decision Model Project` | The names `mdb setup` gives them |
| `computeContexts` | `SAS Studio compute context\|SAS Job Execution compute context` | Compute contexts to look for, separated by `\|` |
| `guideUrl` | `https://sassoftware.github.io/sas-agentic-ai-accelerator` | Where the guides are |

`_contextName` and `contentRoot` are settings of a site: the compute context that runs as its users, and the folder the package was imported into.

A session without `_webout` - the program run in SAS Studio - gets a NOTE that names the file of the page in its WORK folder.

## Load-Accelerator-Tables

The job gives the two reports their tables after an import of the package, without the command line. Started from SAS Job Execution:

```text
https://<your SAS Viya host>/SASJobExecution/?_program=/SAS Agentic AI Accelerator/Data/Load-Accelerator-Tables&_contextName=<a compute context that runs as you>
```

- **The fact sheets** are loaded by *Load-Fact-Sheets.sas* of the folder `Logging and Monitoring` of the content root, which the job includes: one program loads the sheets, run on its own or from the job. The job hands it its folder (`<contentRoot>/Data`) and its CAS library through the two settings `_lfs_path` and `_lfs_caslib`. Where the program is not in the folder, the job says so with a WARNING and loads the release table.
- **The release table** is loaded by the job itself, from `Data/ACCELERATOR_RELEASES.csv`, the file `mdb load-releases --csv` writes. CAS reads the file with the eleven columns of `mdb` in their order, the three numbers (`is_current`, `release_rank`, `item_rank`) as DOUBLE and the eight text columns as VARCHAR - the types the table has that `mdb load-releases` loads. No value passes a SAS variable, so none is cut to 32,767 bytes.
- **Against the table of `mdb`**, measured on SAS Viya 2026.09 on 2026-10-12 with both loaded from the changelog of one commit: 707 rows, the same eleven columns in the same order, the same types and the same lengths. One difference: the formatted length - the width CAS keeps for display beside the length of a column - is 16 where the table of `mdb` has 6, 10, 8 and 8, for the four text columns whose longest value is shorter than 16 bytes (`version`, `release_date`, `change_type`, `builder_part`). It changes nothing: a width of 16 shows every value of these columns whole, and the page of the Prompt Builder reads nothing from the table it is assigned. The job names the name and the type of a column and no length, so CAS decides both for it.
- **A table that exists is replaced only by one that loaded completely.** The file is uploaded beside the table as `<table>_NEW`, a table of the CAS session, and its rows are counted against the lines of the file. Only then is the table dropped, the upload promoted under its name and saved. A file that is not in the folder, a first line with other columns and an upload that does not hold every row leave the table as it is, with a WARNING. *Load-Fact-Sheets.sas* tries each sheet beside its table in the same way, but drops that trial again: it then drops the table and loads the sheet a second time. A fact sheet table is so replaced only for a sheet CAS held completely, but not by the load whose rows were counted, and a failure of CAS between the drop and the second load leaves the site without that table.
- **The session.** In a compute context that runs under a service account the SAS session is that account. The log then holds one NOTE that the session does not run as the user who started the job - the program compares `SYSUSERID` with `SYS_COMPUTE_SESSION_OWNER` and writes neither -, and that the parameter `_contextName` names a compute context that runs as you.
- **The state of the job.** The job writes an ERROR line and sets `SYSCC` to 1012 when one of the four tables is not in the CAS library at its end, and when a parameter is refused. A table that was left as it is and is there is a WARNING.

| Parameter | Value | |
| --- | --- | --- |
| `_action` | `execute` | |
| `_contextName` | `SAS Studio compute context` | The compute context the job runs in: one that runs as the person who starts it |
| `contentRoot` | `/SAS Agentic AI Accelerator` | The content folder whose `Data` folder holds the four files |
| `caslib` | `Public` | The CAS library of the four tables |
| `releaseTable` | `ACCELERATOR_RELEASES` | The name of the release table: a SAS name of 28 characters at most |

`mdb load-releases` and `mdb load-facts` load the same tables from a checkout and stay the way to load them from a workstation.

## Tests

```bash
python -m pytest SAS-Viya-Integrations/Setup/tests
```

`tests/test_start_here.py` runs the Python of *Start-Here.sas* against a SAS Viya system that is made up (`tests/start_here_harness.py`): a complete installation, then one thing taken out at a time, a service that answers 401, 403, 500 or 503, a request without an answer, a `release.json` that is empty and an entry of it that is not in the folder. It reads from the program that it asks with GET and nothing else, that the page holds no token and no user id, and that every text is escaped. For a session that is refused every request it holds the one line at the top - with the counts of the page that was measured, 13 ok and 15 not checked -, for a session of the user and for one of another user; that one request that is answered, a service that is down and a request without an answer keep every line; that a `release.json` whose member names another service is not asked for; and that the advice for a step names the import and `mdb content-deploy`, for a step that is missing, a file that was uploaded in its place and a file that lies beside the registered step. `tests/test_load_accelerator_tables.py` holds the columns and types of the release table against the columns of `mdb` and against the file `mdb` writes from `CHANGELOG.md`, reads the order of the statements that replace a table, holds what the header of the program says of *Load-Fact-Sheets.sas* against the statements of that program, and checks that the four columns with the other formatted length are the ones whose longest value is shorter than 16 bytes. The SAS code of both programs does not run in these tests: they hold its text. `Logging-Monitoring/tests/test_handed_to_sas.py` holds *Start-Here.sas* to the escaping function of the logging programs.

`tests/test_readme.py` holds this page: the two addresses with `_contextName`, the parameters of both tables against the programs and against the compute context the manifest gives the Prompt Builder job definition, and what the page says of a custom step, of *Load-Fact-Sheets.sas* and of the four widths.

`tests/test_custom_steps.py` reads the custom step *LLM - Create LLM Call* of the folder `Custom Steps`, which has no code file of its own. For the box *Save the code to a filename?* ticked and no path - the values the step comes with - the program of version 1.0 held an ERROR line and an ABORT statement; the program of version 1.1 writes the code to a temporary file there, as the label of the path says, and keeps the fileref `_slo_LLM`. For a path its statements are the ones of version 1.0. For the options it follows the reading of version 1.2 - and of *LLM - Get Options* 1.1 and `SAS-Code-LLM-Calls/Create-LLM-Call.sas` - on the table the JSON engine makes of the `options.json` of every LLM definition of the repository: every option is read with its default, also one that lists the values it can take (`values`), where versions 1.0 and 1.1 transposed the table and PROC TRANSPOSE stopped at such an option; and a run that can not read the default of every option writes no code and no file and ends with one ERROR line and `SYSCC` 1012.
