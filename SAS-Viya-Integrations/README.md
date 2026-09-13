# SAS Viya Integrations

This folder holds the integrations that put the deployed LLMs, embedding models and RAG pipelines to work inside SAS Viya - as SAS Studio custom steps, SAS code, SAS Job Execution jobs, SAS Intelligent Decisioning nodes and SAS Visual Analytics content. Most of them work in a low-code or no-code fashion.

The [Administration Guide](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/Administration-Guide/Introduction) explains how to deploy each piece; this page is the map of what is here.

## Transfer packages (import with SAS Environment Manager or `sas-viya transfer`)

| Package | Contents | Guide |
| --- | --- | --- |
| `SAS-Agentic-AI-Accelerator-Prompt-Builder.json` | The **LLM Prompt Builder**: its SAS Content folder, the SAS Job Execution definition that serves the single-file app and the SAS Visual Analytics report that hosts it | [Deploying the Builder UIs](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/Administration-Guide/Setup-Additional-UIs) |
| `SAS-Agentic-AI-Accelerator-RAG-Builder.json` | The **RAG Builder**, packaged the same way | [RAG Ingestion and Retrieval](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/Administration-Guide/RAG-Ingestion-and-Retrieval) |
| `Logging-Monitoring/LLM Usage Report.json` | The **LLM Usage Report** - the recommended SAS Visual Analytics monitoring report | [Logging & Monitoring](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/Administration-Guide/Logging-&-Monitoring) |
| `SAS Agentic AI Accelerator.json` | An older starter package (May 2025) with the *LLM - Log Parser* step, `Log-Parser-Code.sas` and a *Monitoring Baseline* report. Superseded by the packages above; kept for existing imports | - |

Importing a Builder report **replaces the options** an administrator set on the object. Save them first with `mdb options-save` and restore them afterwards with `mdb options-restore` - see [Preserving builder options across a report import](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/Administration-Guide/Setup-Additional-UIs#preserving-builder-options-across-a-report-import).

## Folders

### Custom-Steps

SAS Studio custom steps (a SAS Studio Analyst license is required). Import them into SAS Content, or - for the RAG steps - register them with `Other/deploy-rag-content.ps1` / `.sh`, because a `.step` uploaded as a plain file renders as an empty editor. Steps starting with *MM* are general SAS Model Manager helpers built along the way.

| Step | Purpose |
| --- | --- |
| LLM - List Available LLMs | Table of every LLM registered in the LLM project |
| LLM - Get Options | The options an LLM accepts, with their defaults |
| LLM - Create LLM Call | Generates the SAS code that calls one LLM |
| LLM - Get Prompt Experiments | The experiment runs of one prompt-test |
| LLM - Get All Prompts | One table of all prompting projects, prompts and experiments, for reporting |
| LLM - Log Parser | Parses the collected SCR container logs into the `LLM_LOGS` table |
| RAG - List Documents, Extract Text, Chunk Documents, Enrich Chunks, Embed Chunks, Load Vector Store | The ingestion pipeline, in that order (Enrich is optional) |
| RAG - Retrieve Context | Asks a collection a question - the same retrieval a deployed decision performs |
| RAG - Register Setup | Manifests and registers the retrieval model and writes the governance artifacts |
| RAG - Purge Documents | Erases named documents, including their retired generations |
| MM - Get All Repositories / Get Projects in Repository / Get Models in Project / Get Model Information | SAS Model Manager lookups |

The RAG steps are described in the [RAG user guide](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/User-Guide/RAG).

### RAG and RAG-Ingestion

`RAG/rag_core/` is the Python runtime the RAG steps, the ingestion job and the RAG Builder import at run time; `RAG/retrieve_context.py` is the retrieval model template that *RAG - Register Setup* manifests per setup; `RAG/tests/` is its test suite (`python -m pytest SAS-Viya-Integrations/RAG/tests`). `RAG-Ingestion/` holds the standalone schedulable `Ingest-Documents.sas` job and `Test-Retrieval.sas`. Everything under `RAG/` is deployed to SAS Content with `Other/deploy-rag-content.ps1` / `.sh` - SAS Content is the distribution channel, nothing is fetched from GitHub at run time.

### Other

| File | Purpose |
| --- | --- |
| `create-credential-domain.ps1` / `.sh` | Create the `agentic-ai-keys` credential domain and one identity's credential from your `.env` - provider API keys and vector-store credentials. See [Managing Credentials](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/Administration-Guide/Managing-Credentials) |
| `credentials.example.yaml` | A manifest for `mdb credentials-apply`, which equips many identities at once |
| `deploy-rag-content.ps1` / `.sh` | Upload `rag_core`, register the RAG steps and jobs in SAS Content |

### Logging-Monitoring

Everything for monitoring: `README.md` (the `LLM_LOGS` table and where it is used), `Log-Parser-Code.sas` (the log parser as a code file - the *LLM - Log Parser* step carries the same code), `Get-All-Prompts.sas` (the `PROMPT_EXPERIMENTS` table), `Load-Fact-Sheets.sas` (or `mdb load-facts`), `Build-RAG-Cost-View.sas` and the *LLM Usage Report* package.

A note on prices: the report's *Average Price* / *Total Price* calculated items multiply token counts with the per-token prices from the fact sheets. Providers quote prices per million tokens and distinguish input from output tokens; the report works in individual tokens. Locally served models are priced at $0 by default - the calculated item has a comment at the top that explains how to add a per-second price.

### Prompt-Optimization

The server side of the Prompt Builder's **Optimize** feature: `Optimize-Prompt-DSPy.sas` (a SAS Job Execution job that improves a prompt with DSPy), `Create-Optimization-Dataset.sas` (a template for a governed CAS training table) and `requirements.txt` for the compute context. See [Enabling Prompt Optimization](https://sassoftware.github.io/sas-agentic-ai-accelerator/docs/Administration-Guide/Enabling-Prompt-Optimization).

### SAS-Code-LLM-Calls

Scripts to run the prompt workflow from SAS code - the `README.md` there walks through them in order:

- `Get-List-of-available-LLMs.sas` - the LLMs you have access to
- `Create-LLM-Call.sas` - the SAS code that calls a specific LLM, with all of its options
- `LLM-Call-Result-Table.sas` / `LLM-Call-Combined-Result-Table.sas` - the call output as a table, for one or several LLMs
- `Track-Prompt-Experiments.sas` / `Manual-Prompt-Experiment-Tracker.sas` / `Get-Prompt-Experiments.sas` - record and read prompt experiments in SAS Model Manager
- `Test-DS2-Scoring-from-SAS-Studio.sas` - one query against an LLM from PROC DS2

### SAS-Code-Model-Manager-Interaction

General SAS Model Manager helpers, not specific to this project: `MM-Get-Repositories.sas`, `MM-Get-Projects-in-Repository.sas`, `MM-Get-Models-in-Project.sas`, `MM-Get-Models-Information.sas`, and `createLLMRepository.sas` - the SAS-code way to create the *LLM Repository*. Prefer `mdb setup`, which creates the repository, both model projects and the authorization commands.

### SAS-Intelligent-Decisioning-Integration

`Create-Custom-SAS-Intelligent-Decisioning-Node.sas` adds the *Call LLM* node to the *Objects* pane of SAS Intelligent Decisioning; `Update-Custom-SAS-Intelligent-Decisioning-Node.sas` updates it in place. A SAS Intelligent Decisioning license is required.
