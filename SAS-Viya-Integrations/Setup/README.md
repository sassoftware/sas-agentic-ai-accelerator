# Setup: the first page and the tables of the reports

Two programs for two job definitions of the content folder `/SAS Agentic AI Accelerator`. Each job definition holds a copy of its program.

| Job definition | Program | What it does |
| --- | --- | --- |
| `Start Here` (at the root of the content folder) | *Start-Here.sas* | One HTML page: what the accelerator is, links to its two reports and to the guides, and the installation check |
| `Data/Load-Accelerator-Tables` | *Load-Accelerator-Tables.sas* | Loads the release table and the three fact sheets from the folder `Data` into CAS |

## Start Here

Run from SAS Job Execution, the job writes one page to the browser. The page holds no script and loads no file from another host; its style is inline.

The installation check gives every line one of three states - `ok`, `missing`, `not checked` - with what was found and one sentence what to do:

| Section | What is looked at |
| --- | --- |
| Release | `release.json` in the content root: release, commit and date |
| Content | Every entry `release.json` lists: present in this content root or not. A step that was uploaded as a file is missing: it has to be registered |
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

Parameters of the job definition:

| Parameter | Value | |
| --- | --- | --- |
| `_action` | `execute` | |
| `_output_type` | `html` | The job writes the page itself, to the fileref `_webout` |
| `contentRoot` | `/SAS Agentic AI Accelerator` | The content folder that is checked |
| `caslib`, `casServer` | `Public`, `cas-shared-default` | Where the tables of the reports are |
| `credentialDomain` | `agentic-ai-keys` | Used where the option of the Prompt Builder report is empty or says `none` |
| `repositoryName`, `llmProject`, `embeddingProject`, `decisionProject` | `LLM Repository`, `LLM Model Project`, `Embedding Model Project`, `Decision Model Project` | The names `mdb setup` gives them |
| `computeContexts` | `SAS Studio compute context\|SAS Job Execution compute context` | Compute contexts to look for, separated by `\|` |
| `guideUrl` | `https://sassoftware.github.io/sas-agentic-ai-accelerator` | Where the guides are |

A session without `_webout` - the program run in SAS Studio - gets a NOTE that names the file of the page in its WORK folder.

## Load-Accelerator-Tables

The job gives the two reports their tables after an import of the package, without the command line.

- **The fact sheets** are loaded by *Load-Fact-Sheets.sas* of the folder `Logging and Monitoring` of the content root, which the job includes: one program loads the sheets, run on its own or from the job. The job hands it its folder (`<contentRoot>/Data`) and its CAS library through the two settings `_lfs_path` and `_lfs_caslib`. Where the program is not in the folder, the job says so with a WARNING and loads the release table.
- **The release table** is loaded by the job itself, from `Data/ACCELERATOR_RELEASES.csv`, the file `mdb load-releases --csv` writes. CAS reads the file with the eleven columns of `mdb` in their order, the three numbers (`is_current`, `release_rank`, `item_rank`) as DOUBLE and the eight text columns as VARCHAR - the types the table has that `mdb load-releases` loads. No value passes a SAS variable, so none is cut to 32,767 bytes.
- **A table that exists is replaced only by one that loaded completely.** The file is uploaded beside the table as `<table>_NEW`, a table of the CAS session, and its rows are counted against the lines of the file. Only then is the table dropped, the upload promoted under its name and saved. A file that is not in the folder, a first line with other columns and an upload that does not hold every row leave the table as it is, with a WARNING. *Load-Fact-Sheets.sas* does the same for each sheet.
- **The state of the job.** The job writes an ERROR line and sets `SYSCC` to 1012 when one of the four tables is not in the CAS library at its end, and when a parameter is refused. A table that was left as it is and is there is a WARNING.

| Parameter | Value | |
| --- | --- | --- |
| `_action` | `execute` | |
| `contentRoot` | `/SAS Agentic AI Accelerator` | The content folder whose `Data` folder holds the four files |
| `caslib` | `Public` | The CAS library of the four tables |
| `releaseTable` | `ACCELERATOR_RELEASES` | The name of the release table: a SAS name of 28 characters at most |

`mdb load-releases` and `mdb load-facts` load the same tables from a checkout and stay the way to load them from a workstation.

## Tests

```bash
python -m pytest SAS-Viya-Integrations/Setup/tests
```

`tests/test_start_here.py` runs the Python of *Start-Here.sas* against a SAS Viya system that is made up (`tests/start_here_harness.py`): a complete installation, then one thing taken out at a time, a service that answers 401, 403, 500 or 503, a request without an answer, a `release.json` that is empty and an entry of it that is not in the folder. It reads from the program that it asks with GET and nothing else, that the page holds no token and no user id, and that every text is escaped. `tests/test_load_accelerator_tables.py` holds the columns and types of the release table against the columns of `mdb` and against the file `mdb` writes from `CHANGELOG.md`, and reads the order of the statements that replace a table. The SAS code of both programs does not run in these tests: they hold its text. `Logging-Monitoring/tests/test_handed_to_sas.py` holds *Start-Here.sas* to the escaping function of the logging programs.
