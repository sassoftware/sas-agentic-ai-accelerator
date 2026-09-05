# Changelog

This changelog documents all the different updates that occur for this framework.

## [2.0.4] - Unreleased

The credential domain names a key the way the model asks for it.

### Fixed

- **Azure OpenAI, AWS Bedrock and Voyage keys are found in the credential domain.** The Prompt Builder and the RAG Builder resolve a model's key with an exact lookup of the `KeyName` its `API_KEY` option references (`AzureOpenAI`, `AWSBedrock`, `VoyageAI`), but `create-credential-domain.sh` / `.ps1` and `mdb credentials-apply` stored those three keys under the provider's display name (`Azure OpenAI`, `AWS Bedrock`, `Voyage.ai`), so the models stayed disabled with a *no credential* note although the key was in the domain. The scripts, `mdb`, the RAG Builder's model → entry maps and the *Managing Credentials* guide now all use the `KeyName`, and a test keeps every definition's `key_name` among the entries the tools write. **Migration:** a credential is replaced whole on every run, so rerun the script (or `mdb credentials-apply`) once per identity that holds one of the three keys. Thanks to @bteleuca for the Azure report and fix (#31).

## [2.0.3] - 2026-09-04

A model answers the whole prompt whichever API calls it, and `mdb` signs in to SAS Viya without a password.

### Added

- **`mdb` signs in to SAS Viya with a token or with the SAS Viya CLI's login, not only with a password.** Every Viya command tries three credentials in order and prints the one it used: `SAS_VIYA_TOKEN` (an OAuth access token from any source, whose expiry `mdb` reads and reports), then `SAS_VIYA_USER` + `SAS_VIYA_PASSWORD` (the password grant, as before), then - with neither set - the access token the SAS Viya CLI keeps in `~/.sas/credentials.json` after `sas-viya auth loginCode` (profile `Default`, or `SAS_CLI_PROFILE`; a login for another server is refused, an expired one says to log in again, and the profile's endpoint stands in for an unset `SAS_VIYA_URL`). On an SSO / SCIM / OIDC site, where the only account with a password is typically `sasboot`, the CLI login is all that is needed - and the repository, projects and models are created in the name of the person who logged in. Asked for in #27
- **The model card says what the container needs.** Every generated `Model-Card.md` (the document SAS Model Manager shows next to the registered model) and folder `README.md` gain a *Deployment* section written per template family: which environment variables the SCR container reads, what is required and what is optional, and the symptom when it is wrong - for Azure definitions that is `AZURE_OPENAI_RESOURCE`, the `AZURE_OPENAI_API_VERSION` semantics (unset keeps the baked route, a version selects the legacy route, empty forces the GA route) and the bare 401 a v1-only resource answers on the legacy route; Bedrock names the region and the credential chain, self-hosted servers their base-URL and token variables, local-weights models where the weights live. The card's *Intended Use* now states the embedding contract (`document`, `project`, `options` → `embedding`, `run_time`, `tokens`) for embedding definitions instead of the LLM one. All 48 definitions regenerated. Asked for in #26

### Fixed

- **Models invoked through the MAS REST API scored the first character of the prompt.** Every generated scorer — and the six hand-maintained ones — read its inputs with `[0]`, the CAS / DATA-step / SCR convention where each input arrives as a one-element list. The MAS REST API (`POST /microanalyticScore/modules/<module>/steps/<step>`) hands over plain strings, and a `str` answers `[0]` too: nothing failed, the model was asked "W" instead of "What is SAS Viya?" and returned a plausible answer to it, and the `options` string collapsed to `{` so every key in it — the API key included — was silently dropped and surfaced later as an unrelated `KeyError`. Inputs are now normalised once at the top of `scoreModel()` through a shared `_scalar()` (plain string, one-element list, tuple or pandas Series all yield the value), the options parser goes through the same helper, and no template indexes an input any more. `mdb test --mas` exercises the plain-string convention, and the tests call the rendered scorers both ways and require the identical request. Every definition is regenerated; a deployed model picks the fix up with `mdb register --update` and `mdb publish`. Reported in #26 — which also noted that an Azure definition created with `AZURE_OPENAI_API_VERSION` in `.env` bakes that version and sends a v1-only resource down the legacy route, answered with a bare 401. 2.0.1 had quietly made that worse: it treated an empty `AZURE_OPENAI_API_VERSION` in the container as unset, which removed the last way back to the GA route short of regenerating. An explicitly empty variable now selects the GA route again — unset keeps the baked version — and the score code, the deploy template and `.env.example` say so
- **The documented install command failed in zsh and other shells that expand brackets.** `pip install -e Model-Definition-Builder/cli[viya]` is now quoted everywhere it appears, including the hint `mdb` prints when sasctl is missing. The same page no longer claims a missing password is prompted for - it never was; the three sign-in routes above replace that. Reported in #27

## [2.0.2] - 2026-09-03

`mdb options-restore` writes again.

### Fixed

- **`mdb options-restore` failed on every run with a 400 from the Reports service.** Report content is BIRD XML on both legs, but the write declared it as `application/vnd.sas.report.content+json`, so the service refused the body as "invalid, possibly in the wrong format" — the documented save/restore workflow around a report import never restored anything in 2.0.0 or 2.0.1. The write now declares `+xml`, and the read asks for `+xml` explicitly rather than trusting the server's default representation (the same endpoint serves JSON when asked, which would have left nothing for the option rewrite to find). A failed read or write now surfaces the service's own `errorCode` and message instead of the bare status line, and the report client and the option rewrite are covered by tests for the first time. Reported and fixed in #29

## [2.0.1] - 2026-09-03

Azure definitions read their connection from the container rather than from the caller, Azure embedding deployments generate correctly, and one published image can serve any Azure deployment.

### Added

- **Where an Azure container sends its requests is no longer a scoring option.** Every Azure definition — chat, embedding and environment-configured — resolves its resource, API style and optional gateway endpoint as *`AZURE_OPENAI_RESOURCE` / `AZURE_OPENAI_API_VERSION` / `AZURE_OPENAI_ENDPOINT` container environment variable > the definition's default*, and `options.json` no longer carries `azure_openai_resource`, `azure_api_version` or `endpoint_url`. Those were deployment artifacts wearing an option's clothes: the Prompt Builder offered them as free-text fields, any caller could redirect a model at any host, and a wrong value surfaced as a 404 from Azure. Now the connection is a property of the deployment — `mdb deploy` renders the Azure Deployment YAML (with the env block) for every Azure definition, `mdb import` drops the legacy options from folders that still carry them (previously they leaked into the manifest and broke regeneration), and the resolved endpoint is logged on every call. Regenerated definitions lose those three options; a container that relied on the Prompt Builder supplying its resource must set `AZURE_OPENAI_RESOURCE` instead
- **An Azure model can be configured by the container instead of by the definition.** A new `azure-foundry-env` provider (score template `azure_openai_env`) builds a definition whose API key and deployment come from the container's environment (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, each falling back to the definition's default) on top of the connection every Azure definition reads there — so **one registered model and one published image serve any Azure deployment**, and re-pointing a container at a different subscription, project or model is an edit to its environment plus a rollout restart rather than a regenerate, rebuild and re-publish. Previously the deployment name was baked into the score code at generation time and the key was a required scoring input, which meant a second Azure model, or the same model in a second subscription, was a second image. Such a definition declares **no `API_KEY` option at all** (`auth.mode: none`), so the Prompt Builder neither gates it on a credential-domain entry nor sends a key — a caller that passes one anyway still wins, which keeps credential-domain callers working unchanged. A value that resolves to nothing fails naming the variable that would have supplied it (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_RESOURCE`, `AZURE_OPENAI_DEPLOYMENT`), rather than as an opaque 401 or 404 from Azure, and the resolved endpoint is logged on every call — the one thing about a shared image that cannot be read off the definition. `mdb deploy` renders the matching `SCR-LLM-Deployment-YAML/deploy-modelName-env-template.yaml`, which carries the environment block and reads the key from a Kubernetes secret; rendering the plain template for such a model would produce a pod that starts and then fails every call
- **`azure_openai_env` — the first definition built on it**, defaulting to the `gpt-5.6-luna` deployment. The reasoning scale is mapped to what current Azure GPT-5.x deployments actually accept (`none` | `low` | `medium` | `high` | `xhigh`; they reject `minimal`), so the normalized *minimal*…*maximum* levels reach the provider intact — verified live, as were both endpoint styles. Because the request body is still built from the definition's `options`, one definition serves one option shape: a reasoning deployment (`reasoning_effort`, `max_completion_tokens`) cannot be re-pointed at a chat-only deployment that rejects those parameters

### Fixed

- **`mdb add azure-foundry --kind embedding` generated a scorer with no host and the wrong auth header.** `AzureFoundryAdapter` inherited the generic OpenAI embedding template and a base-URL-derived endpoint from its OpenAI-compatible parent, so the score code rendered `modelEndpoint = '/embeddings'` and sent `Authorization: Bearer` where Azure wants `api-key` — a definition that generated, validated and registered cleanly and failed on its first call. Azure embedding deployments now have their own template (`emb_azure_openai_v1`: `/openai/v1/embeddings` or the legacy deployment-scoped route, `api-key` header, `dimensions` mapped) built on the same environment-resolved endpoint as the chat templates, the adapter smoke-tests embeddings, and `mdb import` recognises every Azure host flavor (`openai.azure.com`, `cognitiveservices.azure.com`, `services.ai.azure.com`). Behind it, the generator now refuses to render *any* template that bakes `provider.endpoint` without an absolute URL — the class of failure, not just this instance. Reported and first fixed in #28
- **`.mdb-lock.json` no longer re-stamps itself whenever a contributor on the other OS regenerates.** The lockfile's `manifest_sha256` hashed `definition.yaml` as read from disk, so a Windows checkout (`core.autocrlf`) and a Linux one disagreed on every definition - a PR touching one Azure template arrived with 42 lock files changed, and the next Windows `mdb generate` would have flipped them all back. The hash is now computed over LF bytes, the same normalisation the per-file hashes already had, and every committed lock carries that canonical value

## [2.0.0] - 2026-09-03

Provider secrets move to **SAS Viya credential domains** — centrally administered, per-user or per-group, encrypted at rest, and audited. One domain (default `agentic-ai-keys`) holds every key the accelerator needs in a named secrets map: `OpenAI`, `Anthropic`, … for the LLM providers, and backend-prefixed `<BACKEND>_RAG_USER`/`<BACKEND>_RAG_PW` entries for RAG vector stores — the prefix lets one domain serve several vector databases side by side. A **user credential overrides a group credential**, so who can call which provider becomes an identity decision instead of an application setting — the Prompt Builder shows each user exactly the models they hold a key for and disables the rest with a note naming the missing entry.

### ⚠️ Breaking change — migrate before upgrading

**The key-table pattern is removed.** The Prompt Builder no longer reads API keys from the report's assigned data table, and the optimize job no longer accepts `keyLibrary`/`keyTable` — deployments upgrading to this release must create the credential domain first (one script run) or every keyed model will show as unavailable:

1. Sign in with the SAS Viya CLI (`sas-viya auth login`).
2. Put your keys in the accelerator's git-ignored `.env` file (most deployments already have them there — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, … plus `<BACKEND>_RAG_USER`/`<BACKEND>_RAG_PW` for vector stores).
3. Run `SAS-Viya-Integrations/Other/create-credential-domain.ps1` (or `.sh`) once per group/user to equip — it maps the `.env` entries onto the domain names automatically (`-EnvFile` selects between multiple `.env` files; `-KeysFile` keeps the raw NAME=VALUE mode) — see the new **Managing Credentials** administration guide.

The `create-api-key-table.sas` helper and the VA data-role assignment are gone; the report needs no assigned data at all.

### Added

- **The RAG Builder stops asking for what it can resolve.** Where a vector store lives (`<BACKEND>_HOST`/`_PORT`/`_DB`/`_SSLMODE`, with the unprefixed `RAGSTORE_*` names as the shared fallback) now travels in the same credential domain that already holds the store's user and password, carried there by the `create-credential-domain` scripts. The Builder resolves and displays the connection instead of asking a person building a corpus to retype a hostname per setup; an explicitly passed value still wins, so the custom steps are unaffected
- **Embedding models are listed, not typed.** A new *Embedding model project ID* option points the Builder at the SAS Model Manager project holding the registered embedding models, and the embedding dimension follows the chosen model's fact sheet. A typed model name has nothing behind it until a container is published — the failure surfaces as an HTTP 404 at the first embed call, after the crawl and the chunking have already run — and a vector column is created at the model's width and cannot be widened afterwards
- **Save documentation** in the RAG Builder, as in the Prompt Builder: the model-card fields save on their own, without needing the setup to be complete enough to validate. Requiring someone to finish choosing a vector database before they could record a purpose is how governance fields end up empty. The save merges into the stored `rag-setup.json` rather than writing only the attributes — writing attributes alone would make the edit reappear as lost the next time the setup was opened — and, for a setup that has never been fully saved, the fields are read back from the model's attributes
- **SAS Content is offered as a document source.** `rag_core.sources` has always read `sascontent:` as well as `sasserver:`, but the Builder never offered the choice and — more seriously — the *generated ingestion job* passed its path as a bare string, which `run_list` treats as a filesystem path. The job now builds its source with `make_source` and hands it to the extract step, so a SAS Content corpus works through the job as it already did through a Studio Flow
- **Chunk overlap is judged as focus leaves the field**, not at save: impossible values (negative, or at/above the token window) block, and anything above 20% of the window warns that the repeated text is embedded and stored twice at every boundary. 10–20% is the commonly published starting band
- **Every validation problem is reported at once**, in an alert beside the Save button rather than at the top of a long editor, and a successful save shows a deep link into SAS Model Manager
- **The vector store's host and port are no longer displayed.** Only the absence of a configured store is worth saying, since that is a blocker to take to an administrator
- **The documentation section drops its Description field** — the description is authored in the create dialog and owned by the model, so the Builder no longer duplicates it or overwrites it on every save

- **Hosted-API embedding models work through the pipeline.** An API-backed embedding container reads its provider key from the SCR `options` argument, but the embedding client only ever sent `Embedding_Mode` — so an OpenAI or Gemini embedding model deployed correctly and then failed at the first embed call with HTTP 422. `rag_core.providers` maps a model to the credential-domain entry holding its provider key (`text_embedding_3_small` → `OpenAI`), the Embed and Retrieve steps and the ingestion job resolve it from the same domain the store credentials come from, and a locally-served model is passed no key at all. A model that needs a key the caller does not hold now fails immediately naming the missing entry, rather than after the crawl and the chunking have run

### RAG Builder: generating what a setup implies

- **One button makes a setup real.** *Manifest setup* saves it, generates the ingestion job and the Studio flow, registers the retrieval model, and records a new **minor model version** — replacing four buttons that had to be pressed in an order the interface never stated. A flow generated against a setup whose score code was never registered is a half-built thing, and nothing said so. It stops at the first failure rather than pressing on, since continuing produces artifacts that disagree with each other, and the version bump is what gives the setup a history of what it used to be. *Save setup* remains on its own for the still-editing case
- **Test retrieval asks the live collection a question** and shows the chunks that come back — score, source, heading, page and the chunk text — with what the probe cost. This is the question someone tuning chunk size or embedding model asks many times a day, and until now the only way to answer it was to deploy something. **It leaves nothing behind**: the job definition it runs is created with *no parent folder*, so it never appears in SAS Content at all, it is deleted when the run ends whatever the outcome, and the hits travel in the log rather than in a CAS table — a table would itself be an artifact of a test that is supposed to have none. It tests the *collection*, not the registered score code, and says so: it queries through `rag_core` exactly as the ingestion does, so a broken `retrieve_context.py` would still pass
- **The saved setup now carries executable scoring code.** Manifesting writes the deployed `retrieve_context.py` with this setup's backend, collection, embedding model, store and credential domain, and writes it onto the RAG Setup model as score-role content. Until this existed a setup saved cleanly and still could not retrieve anything — the model held its configuration but nothing runnable. It refuses rather than guesses: a missing required value, or a template that is not the accelerator's, fails with the reason named
- **The Studio flow is generated from the setup**, the visual twin of the generated ingestion job: the same five steps with the same values, wired in order, registered into the SAS Content folder you choose. Step ids are resolved fresh on every generate — a redeploy of the custom steps mints new ones, and a flow holding a stale id fails at code generation, far from the cause — and the flow is registered as a **dataFlows service resource**, because a `.flw` uploaded as a plain file lands in SAS Content looking correct and then answers HTTP 500 at code generation
- **You choose where generated artifacts go.** A *Generated artifacts* section names the SAS Content folder receiving the ingestion job and the flow, and missing folders below the first level are created. The default was a fixed folder under the content root — convenient for the author, invisible to everyone who would schedule the pipeline later
- **A running ingestion looks like one.** The run panel carries a state badge, a one-second elapsed clock, the latest milestone beside the state, and — when no milestone has arrived yet — the reason, since embedding runs one call per chunk and a large corpus takes minutes before the first line. On success the ledger refreshes on its own, because that is the moment it becomes readable. Previously a thirteen-minute run and a finished one were indistinguishable, and the reasonable next move (open the ledger) reported an empty corpus that was merely not written yet
- **The ledger browser says why it is empty.** While a run is in flight all three outcomes — missing, empty, populated — say so and note that the ledger is written by the run's final step. "No loaded ledger table was found" sent the reader looking for a broken pipeline when the pipeline was still working
- **CAS table names are upper-cased where they are typed.** CAS stores them that way, so a prefix entered as `liti` produced `LITI_LEDGER` on the server and `liti_LEDGER` in every label, link and job parameter here
- **Deleting a setup says what depends on it first.** The Prompt Builder has asked the relationships service before a delete since 1.3.0; the RAG Builder never did, although manifesting writes `retrieve_context.py` onto the setup and so makes it referenceable by a decision in exactly the same way. Both delete paths now ask and put the answer in the confirmation dialog, each dependent decision named and linked; deleting a *project* asks about every setup it holds, because a project delete takes them all and one referenced setup among twelve is precisely the case worth stopping for. "Could not be checked" is deliberately worded differently from "nothing uses this" — a relationships service that did not answer must not read as a clean bill of health — and neither blocks the delete, since the decision may well be the next thing to go
- **The model card lives on the model, and the flow travels with it.** A generated `documentation.md` written beside the setup was the wrong shape: the Prompt Builder records this in model metadata, and two conventions for one thing is one too many. The description now goes where SAS Model Manager already displays it, `documentation.md` files left by earlier versions are removed on save, and the generated `.flw` is attached to the model as an asset so the flow ships with the setup instead of being regenerated from memory

### RAG ingestion: what a corpus should and should not swallow

- **Source files are a named class, skipped by default.** `.py`, `.sas`, `.r`, `.js`, `.ts`, `.sql` and around thirty more are not ingested unless a setup opts in, because a documents folder that happens to sit inside a project would otherwise fill the collection with build scripts that answer no business question. Turning the option on indexes each file as plain text through a new `code-text` extractor, keeping the file name so a hit can be traced back
- **Skipped is no longer called failed.** A document with no extractor, or one whose extractor found no text, is a decision the pipeline made and not a fault: nothing is broken and there is nothing to fix. Calling it `failed` trained the reader to ignore the word, which hid the rows that had genuinely broken. Both statuses now exist separately in the ledger and the run history (`docs_skipped`, added to existing tables by an additive migration), the log reads `N extracted, N skipped, N failed`, and **every skipped document is named in the log**, grouped by reason — a count alone says a corpus is incomplete without saying which part
- **The run log states what the embedding cost**, in dollars, while the run is still on screen — `762 embedding calls, $0.025082 for this run` — rather than requiring a report to answer whether the last ten minutes cost a cent or a hundred dollars. Prices are a regenerable copy of `embedding_fact_sheet.csv`; `RAG_RUN_COST` remains authoritative
- **The chunk token budget says where it came from.** `budget 435 tokens` read like a limit the pipeline imposed; it is the setup's chunk window minus the estimator's safety margin, and unrelated to how many tokens the embedding model accepts. The log now spells out the derivation

### Enrichment: making a chunk say where it came from

- **A new stage between chunking and embedding calls a prompt once per chunk.** `schema.Chunk` has reserved `context_header` since the first release and the Embed step has always prepended it before embedding — the consumer existed, nothing produced it. `rag_core.enrich` is the producer, and it lands as a general slot rather than one hard-coded technique: the headline use is a contextual chunk header (*"this passage is from the FY2026 travel policy, section on per-diem limits"*, the published remedy for a chunk that reads perfectly in place and means nothing alone), and the same call classifies a chunk, extracts an effective date, or flags personal data. Which of those it does is decided by the prompt
- **The prompt is a Prompt Builder artifact, not a setting.** Build and evaluate it in the Prompt Builder, manifest it, point the RAG setup at that model — the prompt then carries its own documentation, versions and permissions, and improving it does not mean editing a RAG setup. The ingestion reads the manifested score code's own two declarations, the `scoreModel` parameter list and its `"Output: …"` docstring, because those are what actually runs; a model whose registered variables disagree with its function would otherwise fail one chunk at a time, deep inside a run. A prompt manifested for the Call LLM node of SAS Intelligent Decisioning returns the request instead of its answer, and is refused by name in both the Builder and the ingestion
- **The Builder's new *Enrichment* section maps it.** Each prompt input is filled from a closed vocabulary of things the pipeline already holds — the chunk, the whole document (capped at 20,000 characters), the neighbouring chunks, the heading path, the file name, the source location, the position — then you choose which output becomes the context header and which are stored as columns. Recognisable input names are filled in for you; an unfamiliar one stays **blank and blocks the save**, because a silently wrong mapping produces a whole corpus of confident nonsense
- **What the LLM extracts becomes real columns on the chunk table**, one per stored output, named after the output (in lower case, since both engines fold an unquoted identifier) and typed from the prompt's registered output variables — a `decimal` lands as a double, so a confidence score can be averaged instead of being parsed back out of a JSON blob. Three rules, all reported in the run log rather than applied quietly: a **new column is not backfilled** (it is null for every chunk written before it existed, which is indistinguishable from an LLM that had nothing to say — only a re-ingestion fills it); a column the setup **stops producing is not dropped**, since removing data must never be a side effect of editing a prompt; and a name the chunk table already uses — `score`, `content`, `page`, `rank` are all plausible things to ask an LLM for — is **refused by name** in both the Builder and the ingestion rather than silently prefixed
- **Enrichment is deliberately NOT part of the configuration fingerprint** (owner decision 2026-08-03). Changing the prompt, the mapping or the stored outputs applies from the next run onward and never demands a re-ingest — the trade being that a collection may hold headers written by two prompt versions. That is made visible rather than left merely true: every enriched chunk records `enrich_version`, the prompt and version that wrote it. The drift guard still covers the chunker, the token window and the embedding model, which change the vectors themselves
- **The prompt can be pinned to a version, or follow the latest.** *Latest* re-reads the prompt on every run, so improving it changes what the next ingestion writes without re-processing anything already stored; pinning freezes exactly the prompt a version carried, which is what a production corpus wants. Three things about the Model Manager API made this worth building carefully, all established against a live server: a version's content lives under `/models/{id}/history/{versionId}/contents` — `/models/{versionId}/contents` answers **200 with an empty collection**, which reads like a version carrying nothing; version labels (`1.0`, `1.1`) are **not unique**, so the id is the identity and the Builder shows the date beside the label; and a version whose snapshot lost its file body answers **200 with zero bytes**, which is refused by name rather than loaded as an empty prompt
- **Failure stays per chunk.** The manifested score code reports a failed call through its `response` output rather than raising, and a response that was not the JSON the prompt asked for leaves every parsed variable holding the prompt author's *default* — both would otherwise be stored as though a model had generated them, the first putting an error message into a vector. Both are detected: the chunk is embedded without a header, and the count is named in the run log and in the run summary
- **The cost is stated while the run is happening** and recorded per run. Enrichment is one LLM call per chunk, repeated on every re-chunk — normally an order of magnitude above the embedding — so `rag_runs` gains `enrich_model`, `enrich_calls`, `enrich_input_tokens`, `enrich_output_tokens`, `enrich_seconds` and `enrich_failed` (added to existing tables by additive migration), and `RAG_RUN_COST` now joins `LLM_FACT_SHEET` as well and reports embedding, enrichment and a total. Input and output tokens are priced separately, because a contextual header sends a whole document in and gets two sentences back. A prompt manifested without the `prompt_length`/`output_length` outputs returns no token counts, and its cost then reads as **unknown rather than free**, saying which outputs would fix it
- **Test retrieval shows the stored header** beside each chunk, in a column that appears only for a collection that has them. A hallucinated header is permanent in the index and invisible at query time; reading a few before building a corpus on a new prompt is the cheapest check there is
- **The document-truncation warning no longer cries wolf.** The per-document context is built on every run — `neighbours` and `position` need the same work — so a corpus of long documents reported truncation whether or not anything was mapped to `document`, which is how it appeared in a real run where it meant nothing. It is now reported only when the mapping actually feeds the prompt from `document` and the cap therefore changed what the LLM was asked about. A run log earns its readers by having nothing ignorable in it: a warning that is routinely meaningless teaches people to skip the ones that are not
- **The enrichment stage is a list of prompts, run one after the other.** Situating a chunk and extracting fields from it are different jobs, and one prompt asked to do both is worse at each — so *Add another prompt* runs a second, a third, over the same chunks in the order shown, each seeing the chunk as the chunker produced it. Two rules hold across the chain and are refused by name rather than resolved silently, in the Builder and again in the ingestion: **only one prompt may write the context header** (a chunk has one, so a second would overwrite the first after you had already paid for it, one LLM call per chunk), and **two prompts may not store the same column**, with no way to tell afterwards which wrote what. `enrich_version` accumulates **every** prompt that contributed to a chunk, so a chunk can say its header came from one prompt and its `clause_type` from another. Each prompt is its own call per chunk, so the cost multiplies with the list — which the card says out loud, and the run log prices per prompt
- **Only prompts manifested with output parsing are offered**, and a prompt project holding none of them is not listed at all. Those are the prompts whose answer is a set of named variables, which is exactly what this stage stores; the rest would fill the picker with prompts that cannot produce a column. The filter runs server-side on the `Output-Parsing` tag the Prompt Builder writes, and the same single request yields the projects — so a project that would open empty never appears, instead of appearing and then disappointing. A prompt the setup already names but which no longer carries the tag is kept in the picker and marked *not found*, because silently dropping it would quietly turn enrichment off
- **`RAG - Enrich Chunks`** is the Studio-flow twin, wired between *Chunk Documents* and *Embed Chunks*. The Builder adds **one step per prompt** to the generated flow, in order, and none at all for a setup that does not enrich — a step sitting in a flow doing nothing invites someone to fill it in without the setup knowing. Chaining the step by hand works the same way: each run adds to what the last wrote, because the usage tally the Load step reads its columns from now merges instead of overwriting
- A prompt whose LLM is a hosted API resolves that provider's key from the same credential domain as the store credentials; `rag_core.providers` gains the LLM-side map (generated from `llm_fact_sheet.csv`), and a prompt on a locally served container takes no key at all

### Credentials for a deployment, not for one person at a time

- **`mdb credentials-init` writes a starter manifest** listing the entries your `.env` actually carries — names only, never a value — so you can see what is available to hand out before deciding who gets it. All the commands default to `credentials.yaml` in the working directory, and `--file`/`--manifest` put it anywhere you keep deployment records; paths inside a manifest resolve against the manifest's own directory, so it and the `.env` files it names travel together. The written source path is relative when the two are near each other and absolute when they are not, because a `../../../../../..` chain is correct, unreadable, and breaks the moment either end moves
- **`mdb credentials-apply` equips a whole deployment from a manifest**, with `--dry-run` first and `mdb credentials-report` after. The shipped scripts author one identity per run — right for a demo, wrong for a rollout, where equipping a department meant running a script once per person with no record of who was equipped. The manifest names **who** gets keys and **where those keys come from**; it never contains a key, so unlike the `.env` it is meant to be committed and reviewed in a diff, because who may call which provider is a decision worth having a history of. `only` narrows an identity to some entries, named as the domain spells them, and a name the source does not carry is refused rather than quietly dropped. Group and user identities are equally first-class, which is what makes "the department shares a key, except the one person with their own quota" expressible
- **The two shipped scripts silently disagreed about case.** PowerShell's `-match` and its hashtables are case-insensitive, Python's `re` and `dict` are not — so a `.env` carrying `singlestore_rag_user` was stored by `create-credential-domain.ps1` and **silently dropped** by `create-credential-domain.sh`. An identity equipped on Windows and read on Linux is the same identity, so both now match on the uppercased name, as does mdb
- Three API facts shaped the design, each established live rather than assumed: a credential read returns **no secrets at all, not even the entry names**, so a dry run reports create-or-replace and never pretends to diff — a privacy property rather than a gap; there is **no collection endpoint** under a domain (`/users` and `/groups` both 404), so who-holds-what can only be answered for identities you name, which is why the report takes the same manifest you applied; and a write **replaces** the whole credential, which is why a manifest entry names a source file rather than a patch
- **Group credentials are confirmed to work, and the catch is documented.** Everything shipped supported them, but no group had ever actually held one, so "the department shares a key" was a design claim rather than a demonstrated fact. It was proven the only way that proves anything — a real user credential removed on a live server and the group asked to take over. Both halves hold: with both present the **user credential wins**, and with the user's gone the group answers with every entry, byte-identical. The catch is worth knowing before you rely on it: a group credential is only found by a reader that passes **`?lookupInGroup=true`** to `GET /credentials/domains/{domain}/secrets`. Without the flag the service reports 404 *for the calling user* even though their group holds a perfectly good credential — a failure mode that looks exactly like a provisioning mistake while the provisioning is in fact correct, since the write succeeded and `credentials-report` confirms the group holds it. Every caller in the accelerator passes the flag; anything you write against this domain must too

### Preserving deployment configuration across a report import

- **`mdb options-save` / `mdb options-restore`.** Every option an admin sets on a builder object lives *inside* the Visual Analytics report, so importing a newer report from a transfer package replaces a site's configuration with whatever the package was built against — and does so silently: the report still opens, the app still runs, it simply points at somebody else's environment. `options-save` discovers the repository and projects as `mdb setup` does and overlays what the live reports actually hold, so a tuned deployment is captured as tuned; `options-restore` writes back only the named options, leaving the new report's layout, data items and objects alone. Options are matched by label rather than by the report's positional parameter name, which is not stable across versions; an option the report does not have is reported rather than inserted; the write is conditional on the report being unchanged since it was read; and **API keys are deliberately not saved**, since this file is meant to be kept with deployment paperwork and read in a diff. The file supersedes the `llm-prompt-builder.json` / `rag-builder.json` seeds as the record of how an environment is configured

- **`mdb package-check` makes that permanent, and CI runs it.** Sanitising by hand worked exactly once: the person doing it fixed the one visible occurrence and left six, and they reached a public repository. So the check is now a command (`--fix` rewrites) and a test over the packages this repository ships, wired into the model-definition workflow — which now also watches `SAS-Viya-Integrations/*.json`, since a package-only change matched none of the existing path filters. It asks the question **backwards**: rather than hunting a known hostname, which the machine running CI cannot know, it requires that every host a report names be the documented placeholder or one of a short allowlist of genuinely public addresses. Anything else fails the build, whoever exported it. Adding to that allowlist means looking at what the host is actually doing — which is how the third shipped package turned out to cite `llmpricecheck.com` in a calculated item's comment as the source of its per-token prices: attribution, allowed by name and reason
- **The shipped transfer packages no longer name the environment that built them.** The same fact that makes `options-restore` necessary — a report's options live inside its content — also means the exporting deployment's hostname travels in `viyaHost`, `SCREndpoint` and the Data-Driven Content URL. The export stores report content zlib-compressed (`TRUE###…`), so those values are invisible to any plain or base64 search over the package, and only the one uncompressed copy had ever been caught. Both packages now carry `your-sas-viya-host` in every occurrence, compressed and plain alike: an import starts from a value that is obviously unset and that `options-restore` overwrites, rather than from a working URL pointing at the cluster the package was built on

### RAG cost

- **`Build-RAG-Cost-View.sas`** builds `RAG_RUN_COST` from the published run history joined to `EMBEDDING_FACT_SHEET`, applying `embed_tokens × input_token_price` for hosted APIs and `embed_seconds × second_cost` for SCR containers. `embed_seconds` meters the embedding time of the run itself — what that ingestion consumed — not the container's uptime bill. An unpriced model yields a missing cost rather than a zero, so it reads as unknown rather than free
- **Granite embedding models are priced.** Both carried no `second_cost` at all, so they would have contributed nothing to every cost view, silently
- **Run-history measures publish to CAS as numerics.** `cas_stage` defaults every column to varchar and the two history publishes never passed a numeric set, so counts, tokens and seconds arrived in CAS as text — categories in Visual Analytics, impossible to sum or multiply by a price without casting. `RUN_NUMERIC` / `EVENT_NUMERIC` now name the measures. The cost view reads either shape, so tables published before this still work
- **The embedding model is chosen before the chunk size, and caps it.** Each model publishes a maximum input — 256 tokens for `all_minilm_l6_v2`, 8192 for the Granite models, 32000 for Voyage — and text beyond it is dropped by the model *silently* rather than rejected, so an oversized chunk reaches the vector as its opening only. The token window field is now capped at the chosen model's limit, states the limit under the field, and refuses to save above it. That is why Embedding sits above Chunking: the ceiling has to be known before anyone picks a number under it
- **The setup editor is grouped by pipeline stage** — Documents, Chunking, Embedding, Vector store, Pipeline tables — in the order the data moves. One long row of unrelated controls hid which setting affected which stage, and the settings interact within a stage (overlap with the token window, dimensions with the model) far more than across them
- **Chunk overlap only appears for chunkers that use it.** `run_chunk` passes `overlap_tokens` to the recursive chunker alone; `paragraph_chunks` does not accept it, so a value set alongside the paragraph chunker was silently discarded. The field now hides itself, and the overlap validation no longer applies where the setting has no effect
- **The pipeline table prefix is bounded where it is typed**, at 20 characters, and accepts only a SAS-shaped name — letters, digits and underscores, starting with a letter or an underscore. Previously it accepted anything and rejected it at save
- **The RAG Builder stops making decisions on the user's behalf.** Nothing is preselected any more: the embedding model and the vector database both start empty and must be chosen, because a default there silently fixes the vector width and the physical location of a corpus nobody had chosen them for — and neither can be changed afterwards without rebuilding. With no embedding model project configured the Builder now says so and refuses to save, rather than quietly proceeding on a hardcoded model
- **Embedding dimensions are read-only** whenever the chosen model publishes a width, because the width belongs to the model rather than to the setup. It stays editable only for a model registered outside the shipped set, where no width is published
- **A personal caslib is no longer offered** as the pipeline-table target. Everything the pipeline writes lands there — ledger, chunk tables, run history — so a corpus built into `CASUSER(...)` cannot be rerun by a schedule or reopened by a colleague, and the failure looks like a lost corpus rather than a choice made months earlier
- **Validation that matches the constraints**: the chunk overlap must be below the embedding token window (at or above it, chunking never advances through a document), negative values are rejected, and the overlap input is bounded live as the window changes. Values are recorded as typed rather than silently clamped, so an impossible pair is reported instead of saved as a different one
- **An ⓘ on every field**, explaining what the setting does and which choices are irreversible once a collection is built
- **Create through a dialog, open in SAS Model Manager, and delete** — the Prompt Builder's project/prompt controls, now present for RAG projects and setups. Deleting a project removes its setups one at a time and names the count first; the confirmation is explicit that vector-store collections and CAS tables are NOT touched. The dialog itself is shared (`src/ui/create-modal.ts`)
- **The RAG Builder captures the same five model-card fields as the Prompt Builder** — purpose, intended use, expected benefit, out-of-scope uses and limitations — in the same collapsible block, with the same ⓘ tooltip per field, written onto the setup as SAS Model Manager attributes under the same names. One governance query now reads prompts and RAG setups alike, and the fields also render into `documentation.md`. The control is shared (`src/ui/doc-section.ts`); previously the RAG Builder had three ad-hoc fields of its own. Setups saved earlier load unchanged — the three new fields simply start blank
- **The RAG Builder's project and setup pickers now look and behave like the Prompt Builder's**: the same `pb-section` layout with stacked full-width pickers, the same name/creator filter row above each, and the same type-to-search combobox. Creating moved out of an input box wedged beside the dropdown into a button row below, where it reads as a separate act rather than part of the selection. The filter control itself is now shared (`src/ui/list-filter.ts`) rather than copied, so the two cannot drift apart again
- **Caslib picker in the RAG Builder**, over the CAS Management listing like the Prompt Builder's dataset picker. Only the caslib is chosen: the CAS server is admin-set, so there is no way to name a server the ingestion will not use. Both pickers keep a saved value the live listing no longer carries, marked rather than silently dropped — a select snapping to its first option is how a saved corpus starts writing somewhere else

### Fixed

- **The generated job and the generated Studio flow were not the same pipeline.** *Manifest setup* produces both for one setup and both write to the same ledger, but they had drifted apart in four places, each of which fails silently.

  They fingerprinted a configuration differently — not only under different key names (`tokens` against `input_token_limit`) but with different value types (`"256"` against `256`) and a different set of keys, so aligning the names alone would not have fixed it. Running the flow once therefore locked the scheduled job out of its own corpus, and the message it got — *configuration drift* — named nothing the person had changed. `rag_core.steps.canonical_config` is now the single definition of what the fingerprint covers, and both paths hash through it. **What it covers is the set of things the ledger cannot see for itself**: a different extractor changes the extracted text, so `content_hash` changes and the ledger re-ingests that document unprompted; a corpus moved between the file system and SAS Content changes `doc_id`, so the ledger sees removals and additions. Neither needs a fingerprint. A change to the chunker, the token window, the overlap or the embedding model leaves every document byte-identical and every vector different — that is what the guard is for. `extractor` and `source_kind` still travel in the configuration and still reach the run history; they are simply no longer hashed.

  The job also called `run_load` without the lineage arguments, so every chunk and every tombstone a scheduled run wrote carried a blank `run_id`, `config_id` and `embed_model` — leaving the retrieval datagrid unable to say which run produced a chunk, and making `restore(collection, "")`, the documented rollback after a bad run, match every chunk the job had ever written at once. The flow ignored the *Ingest source-code files* setting entirely, because the List Documents step had no such control: the same setup ingested its `.py` and `.sas` files as a job and listed them all as skipped as a flow. And the flow sent the vector store's TLS setting under the name only the PostgreSQL branch reads, so a SingleStore deployment that had chosen *disable* connected with TLS anyway and failed with a driver error naming nothing about TLS.

  **Migration.** The fingerprint value changes, so a collection ingested by an earlier build of this release reports drift on its next run. Bump the setup's pipeline version — the sanctioned way to re-ingest — or re-manifest the setup. Re-manifesting is needed in any case for a job or flow generated before this, since both are generated artifacts; the custom steps must be redeployed with `deploy-rag-content` first.
- **The deployed retrieval model could not embed its own query.** Every other embedding call site resolves the provider key and passes it; the manifested `retrieve_context.py` sent `{Embedding_Mode:query}` and nothing else. For any API-backed embedding model, ingestion therefore succeeded and the deployed decision then answered from **no context at all**, because the container's refusal is swallowed by the degradation contract and returns an empty datagrid. The key now travels in the retrieval model's own `options` input — exactly as the LLM score code receives its `API_KEY` — with `RAGEMBED_API_KEY` as the SCR/MAS route. It cannot come from the credential domain: a decision executing in SAS Intelligent Decisioning has no SAS Viya session token, so the lookup that serves the store credentials in a compute session resolves nothing there
- **Enrichment could silently store nothing at all.** The attribute-column schema travels between the steps in `enrich_usage`, a column declared `$ 256` — the right size when that column held five small tallies, and never revisited when the Enrich stage started packing the column schema and the model list into it. A setup storing four or five outputs overflowed it, the truncation made the JSON invalid, `json.loads` raised, the exception was swallowed, and `sync_attributes` was never called: no columns created, every extracted value dropped, no error anywhere. The usage columns are now `$ 32767` — the SAS character maximum, which is the real ceiling regardless of a CAS varchar's ten million — and an entry that still cannot be read is **named in the run log** rather than passed over
- **A contextual header left no trace of which prompt wrote it.** `sync_attributes` is the only place that creates `enrich_version`, and `run_load` called it only when a setup stored columns. The headline enrichment stores none, so the stamp had nowhere to land and `SELECT enrich_version` answered "column does not exist" — the documented way to tell a two-prompt collection apart. The call is now made whenever a run enriched anything; a run that enriched nothing still grows no column it would never fill
- **Citations lost the place they came from.** The chunkers do not emit verbatim slices: `_split_recursive` drops empty parts and re-joins the survivors with **one** separator, and `paragraph_chunks` strips each paragraph. Any chunk spanning a run of blank lines, or a paragraph with padding, therefore failed an exact search and got no span — so a hit could be shown but not opened at the passage it came from. `locate` now falls back to a whitespace-flexible match, which also returns the extent in the *document* rather than in the rewritten copy; every non-whitespace character must still match in order, so a chunk that genuinely is not there is still refused rather than guessed at
- **A document the deployment excludes now loses its chunks; one the pipeline could not read keeps them.** These were both `skipped`, and treating them alike is wrong in both directions. Turning *Ingest source-code files* off is how an administrator gets those files out of the index, but their chunks stayed live and kept coming back in answers with nothing in the ledger to explain it. Retiring on the other kind would be worse: an extractor package that is missing today and installed tomorrow would quietly empty a corpus. The deployment's choice is now its own status, **`excluded`**, and only that retires — never purges, whatever `deleted_policy` says, because the document is still at the source and re-including it should not have cost its history. Both still read the same way in the run log, since both mean "not ingested, and here is why"
- **A SAS Content corpus larger than one page was losing documents.** The folder crawl asked for 500 members and read the answer as the whole folder. A listing is not a partial result here: `run_list` decides a document was deleted by not seeing it, so on the second run every document beyond the first page was tombstoned — or, under `deleted_policy="purge"`, deleted outright — while sitting untouched at the source. The crawl now pages to exhaustion, refuses a listing that repeats itself rather than treating it as complete, and lists each folder once per visit instead of twice
- **An ingestion could erase the enrichment columns it promised to keep.** `sync_attributes` reports a column the setup no longer produces as KEPT, "holding whatever the prompt that wrote them said" — but the upsert named every column on the table, so the run supplied NULL for it and `SET column = EXCLUDED.column` wrote that over the stored value. Because a chunk id is stable across a re-ingestion, editing the end of a document was enough to rewrite its earlier chunks and empty the column there, with nothing in the live rows to recover it from. A write now names only the enrichment columns the records actually carry; a null a failed LLM call produced is still written, because that is this run's answer
- **A Model Manager name containing an apostrophe broke registration.** "Bob's policies" is an ordinary name for a setup, and it ended the filter's string literal early: the service either answered 400 — an opaque failure long after the corpus was ingested — or parsed a different expression that matched nothing, which is worse, since `register_model` reads "no such model" as "create one" and every re-registration added another duplicate. The name is now delimited by whichever quote it does not contain, refused by name if it contains both, and the item's name is compared again on the way back, because the filter narrows the page and is not the match
- **Model Manager names were rendered as HTML in every picker.** The shared filter control assigned project, model and setup names to `innerHTML`, so anyone with authoring rights could name an object such that opening the RAG Builder or the Prompt Builder ran their script in the viewer's session, with the viewer's SAS Viya permissions. Option labels have no use for markup; they are now set as text. The prebuilt `dist/index.html` is rebuilt, since that is the copy a deployment actually serves
- **`Build-RAG-Cost-View.sas` could not run.** It stacked the published run tables with an iterative `%do %while` in open code, where the macro processor rejects it: the step failed with "The %DO statement is not valid in open code" and `RAG_RUN_COST` — the view the rest of the pricing story treats as authoritative — was never built. The loop now lives in a macro, as the comparable loops in the other scripts here already do
- **`RAG - Embed Chunks` could not run at all in a flow.** The step passes `api_key_for(model, secrets)` when it builds its embedding client, but nothing in the step ever resolved `secrets` — so every run raised `NameError`, whatever the model. It came in with hosted-API embedding support and was never exercised through a flow afterwards. The step now has the credential-domain control the Load step already had, and reads the domain **only** when the chosen model has a provider entry, so a deployment running purely local containers still needs none
- **A carriage return would have made every progress milestone unreadable.** The log reader split on `\n` alone, leaving `\r` on the end of each line of a CRLF log — and since the prefix match anchors on `$`, which `.` cannot reach past a `\r`, the result is not an untidy message but *no messages at all*. Found while testing the enrichment probe output; both builders' live run displays now split on either ending
- **Long progress messages were being read as their first 132 characters.** A SAS log wraps at the line size, so a milestone longer than that arrives as one prefixed line followed by unprefixed continuations — and the reader took only the first fragment. Short milestones were unaffected, which is why it went unnoticed until a structured one (a retrieval hit, ~700 characters of JSON) arrived truncated and silently unparseable. The fragments are now rejoined, in both builders' live run displays
- **`-Insecure` broke the deploy it was meant to rescue.** `deploy-rag-content.ps1` and `create-credential-domain.ps1` disabled certificate validation through `ServerCertificateValidationCallback`, which Windows PowerShell 5.1 invokes off the PowerShell thread: every request then fails with *"the underlying connection was closed"*, which reads like a network outage rather than a TLS setting. Both scripts now use a certificate policy, and both explicitly enable TLS 1.2 — 5.1 still negotiates 1.0/1.1 by default, which a current SAS Viya ingress refuses with the same misleading message
- **37 RAG Builder interface strings were unreachable to translation.** They sat at the top level of the locale files rather than inside the `ragBuilder` object the Builder reads, so each one silently fell back to the English literal in the source — invisible in English, and untranslated in German. Four of them also shadowed an older copy inside the object
- **`bge_large_en_v15` was defined as a 384-dimension model**; BAAI/bge-large-en-v1.5 emits 1024. Corrected in both `definition.yaml` and the embedding fact sheet. The wrong width previously only mis-documented the model; now that the RAG Builder creates the vector column from it, it would have produced an unusable collection

- **Credential-domain key resolution** across the Prompt Builder (one secrets-map lookup per session under the signed-in user; a *Credential domain* Option defaulting to `agentic-ai-keys`, `none` for key-less-model deployments) and the optimize job (`keyDomain` parameter, same default — keys resolve server-side under the identity of the user who launched the run and never touch WORK files or logs)
- **Per-user model availability**: models whose provider entry is missing for the signed-in user are visibly disabled in the model selector — with a note naming the entry and domain — and excluded from the judge, council, optimize-target and compare-targets selectors
- **`create-credential-domain.ps1`/`.sh` admin scripts**: author the domain and an identity's full secrets map through the sas-viya CLI session, reading the entries **directly from the git-ignored `.env` file** (provider keys map onto their provider entry names; `<BACKEND>_RAG_USER`/`_RAG_PW` pairs pass through) so secrets never need a second file; `-EnvFile` targets any environment's `.env`, `-KeysFile` stores a raw NAME=VALUE file verbatim, and the CLI's own `credentials` plugin remains the list/inspect/delete tool; the credential is fully replaced on each run
- **A new Administration Guide page, "Managing Credentials"**: the naming convention, the CLI workflow, group-vs-user precedence, and where domains apply (browser + compute sessions) versus where deploy-time environment variables take over (SCR/MAS containers, which have no SAS Viya session)
- **RAG ingestion runtime (`rag_core`)**: a dependency-light Python runtime under `SAS-Viya-Integrations/RAG/` — extractor registry (plaintext, markdown, CSV/JSON, HTML, PDF text layer; markitdown formats when installed), token-aware chunkers with deterministic chunk ids, SCR embedding client, parameterized-filter pgvector adapter with governance DDL, and an incremental-ingestion ledger with per-document failure isolation. Distributed through SAS Content (`/SAS Agentic AI Accelerator/RAG/rag_core`, deployed from the repo checkout with the new `deploy-rag-content.ps1`/`.sh` scripts — nothing is ever fetched from the internet at run time), plus the schedulable `Ingest-Documents.sas` job. The deploy scripts **update an already-registered custom step in place**: a step is a dataFlows service resource, and re-registering it with `overwrite=true` mints a new id, which breaks every saved flow that already uses the step (they reference `/dataFlows/steps/<id>`)
- **Five RAG ingestion custom steps** (`RAG - List Documents`, `RAG - Extract Text`, `RAG - Chunk Documents`, `RAG - Embed Chunks`, `RAG - Load Vector Store`): compose the same runtime visually as a SAS Studio Flow. The document inventory chains through the flow ports while the bulk data rides **promoted CAS tables written through SWAT reusing the step's CAS session** — varchar columns with no 32k limit, named `<project>_ELEMENTS/_CHUNKS/_EMBEDDED/_LEDGER` in a caslib the user picks (every name within 32 characters), each promoted to global scope and saved to disk via `proc casutil` so pipeline state survives server restarts. Incremental diffing against the ledger, per-document failure isolation (a corrupt file never fails the flow), embedding through the governed SCR container with an **automatic checkpoint** (a rerun reuses every vector whose deterministic chunk id is unchanged), and upsert-first-then-delete-stale vector-store loading with credentials resolved from the credential domain. Requires the `swat` and `pandas` packages in the compute context's Python.

  Each step also writes its **output table into the same caslib as the pipeline tables** (`<project>_INV_LIST/_INV_EXTRACT/_INV_CHUNK/_INV_EMBED` and `<project>_LOAD_REPORT`, promoted to global scope so they are visible outside the flow's session) unless the output port points somewhere else, **every shipped column carries a label** on both the output tables and the CAS pipeline tables, and every step closes with a **readable run summary in the log** — the settings it used, per-status document counts, rows written per table and elapsed time. The fields follow suit: the text extractor and the vector store are lists of values rather than free text, token window, chunk overlap, container replicas and embedding dimensions are numeric with sensible minimums, the overlap only appears for the chunker that uses it, the `rag_core` location is displayed but not editable (it belongs to the deployment, not the flow), and the config-hash fields are gone — the ledger's configuration fingerprint is derived from the settings the run actually used, or supplied by the scheduled ingestion job
- **SAS Content as a document source**: the List Documents step's folder selector accepts a **SAS Content folder** as well as a compute-server path, and the downstream steps re-read those documents from where they were found — so a corpus governed in SAS Content needs no copy on a file system. Content documents are fingerprinted from the file's version, size and modification time (a full download per run would cost the same answer), while filesystem documents keep their streaming SHA-256; both diff against the same ledger. Verified end to end on a live environment, ingesting a SAS Content folder tree into pgvector
- **RAG retrieval for SAS Intelligent Decisioning** (`SAS-Viya-Integrations/RAG/retrieve_context.py`): a self-contained retrieval model, manifested per RAG setup, that embeds the question through the SCR embedding container and returns the context datagrid plus a knowledge-graph-ready context envelope. It registers as a SAS Model Manager Python model and as an ID Python code file; connection settings resolve per value from per-call options, the credential domain (browser and compute sessions), or `RAGSTORE_*`/`RAGEMBED_*` environment variables (MAS/SCR destinations, which have no SAS Viya session) — and retrieval failures never raise: the status output carries the message and the decision keeps flowing
- **A collection keeps its history instead of overwriting it.** Re-ingesting a changed document no longer deletes the chunks it replaces: they are **tombstoned** with `valid_from`/`valid_to`/`retired_in_run`, so several generations of a chunk coexist and uniqueness applies to the *live* row. That makes three things possible that a delete forecloses — reading a collection **as it stood on an earlier date** (`as_of`), **rolling back one run**'s ingestion (`restore()`, which un-retires what the run tombstoned and tombstones what it wrote), and attributing an answer to the corpus state that produced it: the retrieval datagrid now carries `source_uri`, `page`/`span_start`/`span_end` and `corpus_run_id` alongside the text, so a citation is openable at the right place and traceable to a run. Retrieval only ever sees the live slice. A chunk's deterministic id includes the **pipeline version**, without which re-chunking silently overwrote the previous generation in place rather than retiring it
- **A run cannot collide with another run, and a changed configuration cannot masquerade as the same corpus.** Each step accumulates the settings it used into the inventory; the load step hashes the total into a `config_id` stamped on the ledger and on every chunk row. A **drift guard** then refuses to write a changed configuration into an unchanged pipeline version — bump the version and the same change is a new generation instead of a silent mix. `RAG - List Documents` takes a **run lock** on the ledger (30-minute staleness, released by whichever step fails) so a scheduled ingestion and an interactive flow cannot write the same collection at once. Steps report failure through the inventory rather than aborting: the flow's remaining steps skip and forward, which suits both SAS Studio and a scheduled job, and neither leaves a lock behind
- **`RAG - Register Setup`**, the step that turns a finished ingestion into something governed and consumable. It manifests `retrieve_context.py` for *this* collection (no deployed model carries a placeholder), registers it as a scoreable SAS Model Manager Python model with typed variables and a pointer to the ledger, and writes `pipeline.yaml`, the ingestion manifest (which documents, at which content hash, from which run), the collection manifest and the store DDL a DBA can review. Pointed at a **SAS Studio Flow**, it also generates the scheduled ingestion job **from that flow** — via the flow-code generation endpoint plus a job definition carrying its `DeployedResourceName` — so the visual pipeline and the scheduled one cannot drift apart. Everything is idempotent: re-registering updates the model, the artifacts and the job definition rather than creating second copies. The generated job's compute context is now a **setting rather than a blank**: an empty `_contextName` lands the job in the stock SAS Job Execution context, which cannot run the steps, so every generated job failed on its first step — it defaults to a context that runs as the requesting user
- **A second vector store: SingleStore**, at feature parity with pgvector — history, as-of reads, rollback, the same portable filter grammar and the same governance DDL — reached through an **adapter registry** where a backend is one entry that supplies its label for the step dropdowns and the driver an admin has to install. One environment can address both at once: connection settings are backend-prefixed (`SINGLESTORE_HOST`, …) with the shared `RAGSTORE_*` values as the fallback, matching the credential domain's existing `<BACKEND>_RAG_USER`/`_RAG_PW` convention, and an unset port follows the backend rather than defaulting to Postgres's. Where SingleStore genuinely differs it is adapted rather than papered over: a live row is marked by a sentinel `valid_to` because there is no `UNIQUE NULLS NOT DISTINCT`, and cosine similarity comes from **normalized vectors ranked by dot product** because the store has no cosine metric and its vector index rejects one — which keeps the ANN index usable and keeps `distance` the same number on both backends. Two limits are reported rather than hidden: without partial indexes a vector index would span retired generations too, so retrieval cost grows with retained history; and the **ANN index is opt-in, not the default**, because measured live it loses rows — on a six-row collection the identical query returned 1, 2, 1, 0, 1 and 1 rows for `LIMIT` 1 to 9, where exact search returned the correct 4 every time (neither `SEARCH_OPTIONS` nor `OPTIMIZE TABLE` changed it). An approximate *order* is acceptable; silently returning nothing to a question is not. `capabilities()` reports `ann_index: False` and `live_only_index: False`, and a deployment that needs the index for a large collection turns it on with `schema={"ann": True}` and validates recall on its own data. **pgvector was checked for the same defect and does not have it** — verified live across four collections of 2, 2, 3 and 1578 live rows, with and without the partial live-row index: every `k` from 1 to 50 returned exactly what existed and the indexed top-5 matched the exact top-5, so pgvector keeps its HNSW index by default. The manifested retrieval model speaks both dialects, so a SingleStore collection is readable from Intelligent Decisioning, MAS and SCR as well. `singlestoredb` is already present in the SAS Viya compute Python
- **Three ways for content to actually leave a collection**, kept deliberately apart, because confusing them is how a "cleanup" destroys an audit trail. Retiring stays the default and stays reversible; the new ones are:
  - a **policy for documents that vanish from the source** on `RAG - Load Vector Store` (and the scheduled job's `deletedPolicy`): keep their chunks as unretrievable history, or remove them for good;
  - **`RAG - Purge Documents`**, a step of its own for erasure. Name documents the way you know them — a file name, a full source path or a doc id — and it resolves them against the ledger, removes every chunk row they own **including retired generations**, and drops their ledger entries so an incremental run no longer considers them ingested. It runs in **Preview mode by default**, reporting exactly what would go before anything does, takes the same run lock as an ingestion so it cannot race a flow or the scheduled job, and says plainly that a document still present at its source will return on the next run — erasure has to happen at the source too;
  - **history retention** (`retainDays`): after a load, retired generations older than the cutoff are dropped. Live rows are never touched, so retrieval cannot change — only how far back an as-of read can reach. This is also what keeps a SingleStore collection's retrieval cost flat, since its vector index cannot exclude retired rows.

- **`RAG - Retrieve Context`**, a step that asks a collection questions and writes the answers to a table. One row per matching chunk, carrying rank, distance and score alongside the source file, heading, page and character span, and the corpus run that produced the chunk — the same retrieval a deployed decision performs, landing somewhere it can be read, compared across configurations, or turned into an evaluation set. Questions come from a field, a wired table, or both. The question is embedded through the **same** SCR model the corpus was built with, in query mode, because a different model puts the question in a different vector space and returns plausible nonsense. Only live chunks are searched. A question that fails to embed or search reports on its own row and the rest still run; a question that matches nothing gets a rank-0 row rather than vanishing. The step is read-only and takes no run lock, so it is safe against a live corpus mid-ingestion

  Neither policy enters the configuration fingerprint: they change nothing about how chunks are produced, so they must not demand a pipeline-version bump — and the drift guard must not refuse a run over a housekeeping setting.

- **Email as a document format, and control over what the pipeline persists.** `.eml` is extracted with the standard library alone — no package for an admin to install — and the headers (from, to, date, subject) become a retrievable heading rather than being discarded, because with mail the metadata is usually *why* someone is searching; every body chunk inherits the subject as its heading path. Multipart mail prefers the plain-text part, falls back to stripped HTML, and never treats an attachment as document text. Outlook `.msg` routes to markitdown, which already carries the reader for that format. Separately, `RAG - Extract Text` and `RAG - Chunk Documents` can now keep their tables **in memory only** instead of saving them to disk: the ledger and the embedded chunks must survive a restart (the incremental diff and the embedding checkpoint depend on it), but elements and chunks are rebuilt from the documents on the next run and are the largest thing the pipeline writes on a big corpus

- **A citation can now be opened at the passage it came from.** The chunk `span` was declared in the schema, carried through every table and read by both the retrieval step and the manifested model — but nothing ever wrote it, so every citation reported `page 0, span 0-0` and the source location was decorative. The chunkers now record where each chunk sits in its document, and the page where an extractor knows one. Rather than threading offsets through splitting, re-merging and overlap — where an error would be silent — each chunk is located in the document text afterwards and checked, and a chunk that cannot be located gets **no** span rather than a guess: a wrong citation is worse than an absent one. A numeric column with no value arrives from CAS as `0.0`, which had been producing `page: 0` in stored citations; pages are 1-based, so anything below that is now recorded as unknown

- **A compute context that cannot run the steps now says so.** The steps reuse the CAS session SAS creates, because the staged table has to be visible to the promote that follows — and CAS only lets an identity reconnect to its *own* session. The stock SAS Job Execution context breaks that: it runs SAS and its token as a service account while the CAS session belongs to the user who launched the job, so the first step failed with a bare "Could not connect to sas-cas-server-default-client on port 5570". SWAT's exception never carries the server's actual reason, so the step now probes: if CAS answers a connection *without* the session id, it is reachable and the problem is whose session it is, and the step says that plainly and names the remedy — run the flow in a context that runs as the requesting user. **This is a prerequisite for scheduled flows**, alongside the same requirement the credential domain already has. The stock **SAS Job Execution compute context** does not qualify; the **SAS Studio compute context** does, and a full five-step ingestion was verified running there as a job — list, extract, chunk, embed, load and run history, with no manual step

- **Run history: the append-only record the ledger cannot be.** The ledger holds one row per document, overwritten every run, so it answers "what is in the corpus now" and nothing else — four of the five questions an owner asks after six months (how big was the corpus last month, which documents changed, which configuration produced this chunk, what did the ingestion cost) had no source of truth at all. Three tables now sit **beside the chunks** in the vector store's own database: `rag_runs` (one row per run, with document counts, chunk counts and embedding cost), `rag_doc_events` (append-only, one row per document per run *where something happened* — unchanged documents are excluded, or the change log would grow by the size of the corpus every run) and `rag_configs` (the parameters behind a `config_id`, giving the hash an inverse). They live in the database rather than CAS because a CAS table here is overwrite-in-place by construction, has no transactional append and no constraints, and an empty run deletes the saved file outright. The consequence — history invisible to Visual Analytics — is answered explicitly: the Load step **publishes `rag_runs` and `rag_doc_events` to CAS** after each run, so the transactional copy stays authoritative and the reporting copy is disposable and rebuildable. A run records what it **found** (new/changed/unchanged/deleted) *and* what it **achieved** (ingested/failed), which are different questions. History never fails a load: a store that refuses the write is reported and the ingestion still stands

- **Two documentation pages for RAG**: a user guide that builds a working pipeline with the shipped steps and then asks it questions, and an administration guide covering deployment, the two prerequisites that are not optional (a compute context that runs as the requesting user, and a credential domain holding the backend-prefixed store credentials), the backend comparison including the SingleStore ANN caveat, retention and erasure, run history, and an explicit **security posture** — where secrets live, what crosses which boundary, what the accelerator does *not* encrypt, and the fact that database error text stored in the ledger can contain infrastructure detail

- **The RAG Builder offers the vector databases you actually run, to the people who can actually use them.** Two separate questions, answered separately: a checkbox per backend (*Offer pgvector*, *Offer SingleStore*) names which backends this deployment presents at all — a site running only pgvector should never show SingleStore — and the **credential domain** decides which of those the signed-in user can reach. A backend they hold no credential for stays **visible but disabled**, naming the missing entry and the domain, because a hidden option looks like the feature does not exist; if they hold none at all, the section says so rather than letting them author a setup that cannot ingest. The port default follows the chosen backend, as it does in the steps. This mirrors the model availability the Prompt Builder already applies to LLM provider keys. The custom steps keep listing every backend — they are a static list, and the Builder is the recommended way in

- **Operational policy is an administrator's decision, not a per-setup one.** Seven settings the custom steps expose are now **RAG Builder Options**: vector store TLS, what happens to a document that disappears from the source, how long retired chunks are kept, whether run history is recorded, the embedding container's replica count, and whether the element and chunk tables are saved to disk. Each setup **records** the values it was created with, so `pipeline.yaml` says what that corpus actually does and re-saving an existing setup does not silently re-baseline it onto a policy that changed since. TLS in particular left the user form deliberately: the six PostgreSQL `sslmode` values are a Postgres concept, and offering them for a store that only knows encrypted-or-not shows a setting that does not mean what it says. The generated ingestion job carries the policies as parameters — `Ingest-Documents.sas` gained `replicas` (which now sizes the embedding parallelism instead of a hardcoded 8) and `recordHistory` — because a setting recorded in a governance artifact and ignored at run time is worse than no setting at all

### Removed

- The Prompt Builder's assigned-data API-key table (DDC data role), the `optimizeKeyLibrary`/`optimizeKeyTable` Options, the job's governed key-table parameters and WORK-file key export, and the `create-api-key-table.sas` helper

## [1.9.0] - 2026-07-27

The Prompt Builder learns to **optimize prompts with DSPy** (Phase 3a of the judging roadmap — the thin end-to-end slice). An opt-in **Optimize** section saves the prompt and launches a SAS Job Execution job that runs DSPy inside `proc python` in a configurable compute context: the runs marked as Best Response become the training examples, models are called through the governed SCR endpoints, the chosen optimizer — bootstrap few-shot, MIPROv2 which also rewrites the instruction text, or GEPA which evolves the instruction from natural-language feedback — maximises the chosen metric (exact match, token-overlap F1 or an LLM judge), and the result comes back **on the prompt itself** for the user to review, judge and accept — completing the loop *judge → optimise → judge again*. Nothing is ever applied automatically.

To pick up the change, re-upload the prebuilt `LLM-Prompt-Builder/dist/index.html` to your SAS Job Execution definition — or import the re-exported **transfer package** (`SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json`), which now bundles the rebuilt UI, the **Optimize-Prompt-DSPy job definition**, the VA report and the API-key table script (`SAS-Viya-Integrations/Other/create-api-key-table.sas`), with the environment host replaced by the `https://your-sas-viya-host` placeholder for the documented import-mapping substitution. The feature itself is **off by default** — enabling it additionally requires preparing a compute context and setting the Options, per the new Administration Guide page.

The whole path was **validated end-to-end against a live SAS Viya environment** (dspy 3.2.1): the deployed job definition was launched through the Job Execution REST API with the Builder's exact arguments, optimised a 10-example prompt through the SCR `qwen_25_05b` container with `BootstrapFewShot`, and wrote back the optimised prompt-test (tags + provenance), the optimization-tracker entry (jobId matching the launched job) and the dataset snapshot. Corrections from that live run are included: the `SCRLM` adapter returns its `usage` as a plain dict (dspy calls `dict(response.usage)`); the Builder resolves the optimize job by **folder membership** because a live `/folders/folders/@item` returns 404 for jobDefinition members; and the Builder parses the job log's real shape — a `vnd.sas.compute.log.line` JSON collection — and filters the `%put` source-echo lines so each milestone shows once. (Also fixed: multi-line `*` comments in the job program containing semicolons, which put the SAS session into syntax-check mode.)

The job also runs a **dependency preflight**: it checks that the compute context's Python has `requests` and `dspy`, that a MIPROv2 run has `optuna` (dspy treats it as an optional extra, so its absence otherwise only surfaces mid-run — found live), and that dspy is at least **3.2.1** — the version the `SCRLM` adapter is validated against (older releases read the LM response differently). A missing or too-old package — like every other failure, including missing job parameters (now validated in Python rather than by a SAS-side abort) — fails the run through one standard path: an `Optimization failed: …` milestone the Builder shows live, a **failed optimization-tracker entry** whose error the Builder's panel displays (e.g. *"the compute context lacks the dspy package"* or *"dspy X is too old"*), and a plain `ERROR:` log line for administrators.

Live-testing the failure path (seven instrumented failure runs) surfaced two SAS Viya behaviors worth knowing, both now engineered around:

- **Text passed to `SAS.logMessage()`/`SAS.symput()` is embedded into generated SAS statements**, so an unbalanced quote — a single apostrophe in an error message, or the quotes routinely found in Python exception texts — corrupts the SAS code stream: the compute session ends in a *Segmentation Violation*, Job Execution never receives its completion handshake, the job shows `running` forever, and the dead server keeps counting against the compute context's server limit until its session is deleted (the stuck servers eventually exhausted the context's slots during testing). The job now routes every such string through a `sas_safe()` sanitizer and its own messages avoid quote characters. Any `ABORT` flavor wedges the job the same way, so the job **never aborts**: it always ends normally, the outcome lives in the tracker entry (`status` + `error`), which the Builder reads on completion, and the log carries a plain `ERROR:`/`NOTE:` summary line for administrators.
- **`proc python` executes the submit block incrementally**, so the job's main flow (including its `try/except`) is wrapped in a single `def main()` — guaranteeing the exception handler is compiled together with the code it guards.

As defense in depth the Builder also finishes early from the tracker entry when a job's state hangs at `running` (the entry is written before the state turns terminal), and still handles a genuine `failed` state (e.g. Job Execution refusing the launch when the context's server limit is reached). The final design was validated live in both directions — dependency failure: job `completed`, tracker entry `failed` with the clean message, no crash; success: `completed`/`succeeded` with metric 0.5 → 1.0 — and both paths are covered by the mock/Playwright suite.

An in-Builder click-through against a live environment added three more fixes: the Builder **primes the Job Execution service session with a GET before its first launch POST** — with browser cookie auth, a first-contact POST triggers the SSO handshake (303 to SASLogon, retried as `?sso_retry=POST`, answered with HTTP 449) which cannot replay the POST body; the job **assigns CAS libraries when the configured key library is not pre-assigned** in the compute session (the accelerator's `LLM_API_KEYS` table lives in caslib `CASUSER`), connecting and terminating its own CAS session best-effort; and the key reader accepts **both `KeyName`/`KeyValue`** (the columns `create-api-key-table.sas` creates) **and `name`/`value`**.

### Added

- **An Optimize section in the Prompt Builder** (only when the deployment enables it): pick the target LLM, see how many usable runs the prompt has (runs with a Best Response; a configurable minimum of 30 gates the run, with a warning below 50 and an explicit "assumed correct" notice), choose the metric (exact match; **token overlap** — a per-example F1 over the words shared with the reference, partial credit for close answers so chatty models are not scored 0 for formatting alone, with per-example scores surfaced in the history's evolution table; or an LLM judge — which now sees the TASK (system prompt + inputs) alongside the reference and candidate and applies the Phase-1 judging rubric (accuracy, relevance, completeness, clarity; length/formatting ignored), with a self-preference warning when the judge equals the target), the **optimizer** (bootstrap few-shot, or **MIPROv2**, which additionally proposes and trials rewritten instructions so the optimised system prompt itself can change — the history's baseline-vs-optimised diff becomes the headline) and the max few-shot examples, and launch. The panel shows a rough call estimate up front (scaled to the chosen optimizer) and, while the job runs, **live progress** polled from the job log (the milestones the job emits via `SAS.logMessage`) plus a collapsed **Run log** with the job's runtime and its own cleaned milestone notes — never the raw SAS log; while no milestones have arrived the log line says WHY (waiting for the first milestone, or the exact reason live streaming is unavailable — an HTTP status or an SSO-redirect answer, which the Builder detects and re-primes on) instead of staying silently empty. **ℹ️ tooltips** on the dataset, metric and optimizer labels explain the choices in place, and the section links to a new **user-guide chapter** (when optimization is useful, which metric/optimizer fits which task, the CAS dataset schema). On completion the transient state line clears, a brief before → after summary appears, and the run lands in the **Optimization history** (below)
- **The shipped optimize job** (`SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas` + `requirements.txt`): a Job Execution job definition whose `proc python` reads the prompt's experiment tracker from Model Manager, calls the target/judge models through the **SCR endpoints** with a small `SCRLM` DSPy adapter (the SAS 3-input contract, k8s and aca URL forms, smoke-tested before spending calls), runs the chosen optimizer (`BootstrapFewShot`, or `MIPROv2` with `auto=light`, sequential calls and full — non-minibatched — evaluation sized to the validation split) — **unless the baseline already scores 1.0 on the validation split**, in which case the optimizer has no gradient to climb and the job skips the search after only the baseline calls, recording the run with a `baseline-perfect` note (surfaced in the history) that suggests harder examples or a stricter metric — bakes the compiled program back into a Prompt-Builder-shaped prompt (instructions → system prompt, selected demos → an examples block; user template and variables preserved), and records **everything on the source prompt itself — no additional Model Manager models are created**: the complete run goes into **`Prompt-Optimization-Tracker.json`** next to the experiment tracker (the job is that file's only writer; failures are recorded too, with their error) — baseline vs optimised prompt, the selected few-shot demos as structured data, per-validation-example before/after evaluations, train/validation sizes — plus a **dataset snapshot** (`Prompt-Optimization-Dataset-<n>.json`) for provenance
- **The GEPA optimizer (Phase 3c — reflective instruction evolution).** The optimizer choice gains **GEPA**, which improves the instruction from *natural-language feedback* instead of blind trials: the job's metric returns each score together with an explanation of **why** the answer scored that way — with the **LLM judge** metric that explanation is the judge's own reasoning (the optimizer literally learns from the judge's critique), with exact match or token overlap it is a generated expected-vs-produced note (missing/unexpected words for overlap) — and a **reflection model** reads that feedback and proposes improved instructions. The reflection model is the judge model picked in the panel, called through the same governed SCR adapter (its calls are charged to the judge's usage bucket), so the panel reveals the judge picker — and gates the Run button with a hint — whenever GEPA is selected, for any metric; GEPA selects no few-shot demos, so the max-examples input is disabled for it. **No new package prerequisite**: the `gepa` engine is a hard dependency of dspy ≥ 3.2.1 (unlike optuna), which the job's preflight still verifies defensively. The call estimate scales up accordingly and the user guide's optimizer section explains when GEPA beats MIPROv2
- **An Optimization history in the Optimize section.** Selecting a prompt renders its past optimization runs (newest first): status, timestamp, target model, optimizer · metric, sample count, and the metric **before → after with a colored delta badge**. Each run expands into its **evolution details** — the baseline vs the optimised system prompt, the user-prompt template, **what the run spent** (per-role model-call counts and token totals the job records from the SCR responses, priced into an estimated cost with the same per-token/per-second attributes as the run table), and a per-validation-example before/after table where examples the optimisation *fixed* are highlighted green and any it *broke* red (the per-example evaluation view popularised by MLflow's DSPy optimizer tracking). **Load as experiment** restores a run's optimised prompt and variables into the workbench and pre-selects its target LLM, so running, judging (Phase 1/2) and manifesting the optimised prompt works like any other experiment. Entries written by earlier builds (which created a separate prompt-test) still show their Model Manager link
- **Compare targets (Phase 4a/4b — inspired by Ryosuke Horiuchi’s MMDSPy candidate-endpoint pipeline).** A **Compare targets…** entry point next to the target dropdown answers *which deployed model should this prompt run on*: pick 2+ candidates in a modal and either **screen** (default — the CURRENT prompt is scored on every candidate, no optimizer, ~one call per example per candidate) or **also optimize each candidate** with the panel’s configured optimizer and rank by the *optimised* metric (the truthful, far more expensive comparison; the estimate scales per optimizer). Both run as the same Job Execution job (`mode=compare|sweep`) and record ONE comparison entry in the optimization history, rendered as a **ranked table** — quality first, average per-call model latency (from the SCR contract’s `run_time`) as the tiebreak, plus per-candidate calls and an **estimated cost** priced like the run table (a ranking dimension MMDSPy lacks). An unreachable candidate is flagged in its row instead of failing the whole run. Screening rows offer **Optimize this target** (pre-selects the model in the panel); sweep rows show their own *before → after* and offer to **load that candidate’s optimised prompt** into the workbench. Screening needs only 10 examples (noise warning below 30); a sweep trains per candidate, so the full configured minimum applies. The dialog states screening’s honest caveat: it ranks the *unoptimised* prompt, and a small model can improve dramatically once optimised — screen to shortlist, then sweep the shortlist
- **A second dataset source: a governed CAS table.** The Optimize panel's dataset choice gains *"A governed CAS table"* with cascading **server → caslib → table dropdowns** browsed live through a new `cas-api` module (CAS Management listings; cached per server/caslib, loaded lazily when the source is first chosen, the single server preselected) — only **loaded** tables appear, which is exactly what the job's compute session can read. The table needs one column per prompt variable plus a `response` column holding the reference answer, built with the new shipped **`Create-Optimization-Dataset.sas`** template. The panel validates the chosen table **before** launching — required columns present, row count above the sample minimum, with a clear toast when a listed table has been dropped in the meantime — and the job exports the table server-side (an explicit-caslib `libname` so long caslib names survive), records the browsed CAS server in the tracker entry for provenance, and snapshots the exact rows used, like the tracker source. The tracker-specific gating (Best-Response minimum, assumed-correct notice) applies only to the tracker source
- **New Prompt Builder Options** with progressive disclosure: the `enableOptimization` master toggle is always shown, and only when enabled does the pane reveal `computeContext` (passed to Job Execution as `_contextName`), `optimizeJobProgram` (the job's SAS Content path), `minOptimizeSamples`, and `optimizeKeyLibrary`/`optimizeKeyTable` — the governed SAS library.table the **job** reads provider API keys from. Only the names travel in the job request; keys never appear in the request, the log, the tracker or the produced prompt
- **A new Administration Guide page, "Enabling Prompt Optimization"**: preparing a compute context whose Python has the `requirements.txt` packages (`dspy`, `requests` — called out as the most common failure), importing the job definition, creating the governed key table, setting the Options, and a troubleshooting table. The user guide gains an "Optimize the prompt" section covering the workflow and its advisory nature

### Changed

- The **project, prompt-test, caslib and table pickers are type-to-filter comboboxes**: typing in the picker narrows the OPEN dropdown live (case-insensitive substring), Enter picks the highlighted or only remaining match, and leaving the field without picking keeps the previous selection - so filtering and choosing are one visible control instead of a separate filter box beside a closed dropdown. The underlying selects stay in the DOM as the source of truth (programmatic value changes and repopulation sync back into the combobox), and the project/prompt "created/modified by" filters are unchanged
- The Prompt Builder launches and monitors the job through the **Job Execution REST API** (`/jobExecution/jobs` with the definition resolved from its Content path) rather than the JES web-app form endpoint, reusing the app's CSRF-aware HTTP client; state is polled and the `NOTE: Python-Subprocess - …` log lines are surfaced as the live status
## [1.8.3] - 2026-07-26

The llm-vs-embedding **kind** of a definition is now unmistakable in `mdb add` - and wrong classifications fail validation instead of surfacing at smoke-test time.

### Fixed

- **Live catalogs no longer lose the kind.** OpenAI's live `/v1/models` returns ids only; the static-snapshot enrichment now carries over `kind` and `embedding_length`, so `mdb add openai text-embedding-3-small` builds an embedding definition online exactly as it does offline (it used to silently build an LLM definition end to end - chat template, LLM-Definitions folder, LLM project)
- **Embedding-only providers force their kind.** A manual model reference on `voyage` produced an inconsistent hybrid (embedding score script filed as an LLM definition); adapters whose only template is an embedding template now always set `kind: embedding`

### Added

- **The kind is stated everywhere it matters:** a banner right after the wizard resolves the model ("Adding an EMBEDDING model definition -> Embedding-Definitions/<id>/"), a `definition / kind` row first in the review table, and the kind in the success line
- **`--kind llm|embedding` works for every adapter that supports embeddings** (previously only ollama/vllm; it was warn-ignored elsewhere), and a manual model reference on a both-kinds provider asks for the kind interactively (`--yes` keeps the previous default)
- **V012 (error): kind/template mismatch.** Embedding templates are named `emb_*`; a chat template on `kind: embedding` (or vice versa) now fails `mdb validate` with a pointer to `--kind`
- **`mdb providers` gains a kinds column** (llm / embedding / llm + embedding), and the README documents how the kind is decided and overridden

## [1.8.2] - 2026-07-24

mdb works with every Azure endpoint flavor and can keep your definitions in your own repository.

### Added

- **All Azure host flavors and both API styles for `azure-foundry` models.** Azure serves the same OpenAI-compatible data plane under three host suffixes — `*.openai.azure.com` (classic Azure OpenAI resource), `*.cognitiveservices.azure.com` (AI Services / Foundry resource) and `*.services.ai.azure.com` (Foundry endpoint, sometimes region-qualified) — and mdb now accepts any of them verbatim (a bare resource name still expands to the classic host). By default calls use Microsoft's recommended **GA v1 endpoint** (`/openai/v1/chat/completions`, deployment in the body); the new **`api_version`** wizard answer / `azure_api_version` option / `AZURE_OPENAI_API_VERSION` container environment variable switches to the **legacy deployment-scoped route** (`/openai/deployments/<name>/chat/completions?api-version=…`) that some resources and org policies still require — with the same option → environment variable → baked-default resolution as the resource host. The smoke test honors the chosen style; the admin guide documents the flavors, including the caveat that Responses-API-only models (`/openai/v1/responses`, a different request shape) are outside the chat-completions score contract. The shipped Azure definition is regenerated with the new `azure_api_version` option (default empty = v1, byte-identical behavior)
- **The wizard confirms catalog-derived values instead of accepting them silently.** `mdb add` now shows the option defaults, metadata (description, context length, dates) and token pricing it derived from the catalog and asks you to confirm — or walk through and adjust — them before anything is written: these values steer scoring behavior and cost monitoring, so they should be accepted consciously. The review can also **rename or drop options** (`max_tokens=max_completion_tokens` renames, `-top_p` drops) — option names are part of the provider contract, and newer OpenAI-style models reject `max_tokens` in favor of `max_completion_tokens`; when the catalog's `supported_parameters` say which one a model takes, the wizard now picks the right one automatically in the first place. The new **`--accept-defaults`** flag skips the review for scripted-but-interactive use (`--yes` implies it), and unknown token pricing is always asked about (or, non-interactively, warned about)
- **Definitions in your own repository.** `MDB_DEFINITIONS` (an absolute path, typically set in the `.env` of your own repo — mdb loads the `.env` of the directory you run it from) names a single root under which mdb keeps its familiar layout, creating the folders as needed: `LLM-Definitions/`, `Embedding-Definitions/`, the fact sheets inside them, and the `mdb retire` archive `_archive/`. Definitions can then be committed to your own git repository while the accelerator clone only supplies the definition-core templates (`MDB_REPO`); every command follows the relocation

- **The endorsed pipeline is now `validate → test → register → publish`.** `mdb add`'s next-steps output and the guides include `mdb test` between validation and registration: `mdb validate --live` proves the provider/endpoint/key through the adapter, while `mdb test` runs the *generated* `scoreModel()` locally — options parsing, request body, response extraction — so a template or option problem surfaces on your machine instead of in a published container

### Fixed

- **Free models are not "missing pricing".** V008 now distinguishes *unknown* token prices (both absent — still warned about) from an **explicit 0** — the correct answer for a `:free` catalog model, which previously produced a contradictory *"fill the pricing"* warning right after the wizard had (correctly) prefilled zeros. And when the catalog genuinely carries no prices, the wizard now **asks** for them during `mdb add` (skippable; `--yes` prints a reminder instead) rather than silently writing an unknown that only surfaces later as V008
- **A rate-limited smoke test is not a failed validation.** `mdb validate --live` reports an upstream **HTTP 429** as *inconclusive* (yellow, exit code unaffected) with a message that says what it proves — the endpoint answered and the key was accepted; the model is temporarily rate-limited upstream — instead of a red failure with a raw provider error. Uniform across all provider adapters
- **`mdb add` keeps the fact sheet in rebuild order.** New rows are inserted in `model_id` order (where `rebuild_sheet` would put them) instead of appended, so an add no longer desyncs the committed sheet from a fresh rebuild

## [1.8.1] - 2026-07-23

Robustness follow-ups to the 1.8.0 code review — no functional changes to what the commands do on the happy path.

### Fixed

- **`mdb load-facts` hardening.** CAS URL path segments (server, caslib, table) are now URL-encoded, so a caslib name with a space no longer produces a malformed request. An unload failure that is not "nothing loaded" (e.g. a 403 permission error) now raises immediately with a clear message instead of surfacing later as an opaque `LOADTABLE_EXISTS` conflict on the upload. When neither fact sheet exists the command exits non-zero instead of silently succeeding, and the per-table message no longer claims "created" when only the loaded copy was absent
- **CLI consistency.** `--prune` without `--rebuild` is now an error (both `mdb sync` and `mdb load-facts`) instead of being silently ignored, and `mdb load-facts --rebuild` gained the same `--prune` option `mdb sync --rebuild` has. The duplicated rebuild loop is factored into one shared helper
- **`rebuild_sheet` rejects a mixed llm/embedding manifest list** instead of silently rendering one kind against the other kind's columns (the CLI never did this; the guard protects future callers)

### Changed

- **Transfer-package import docs use the current CLI form and a verified mapping workflow.** The import guides now use `sas-viya transfer packages upload` / `import` (the plugin marks the old `transfer upload` / `import` as deprecated aliases, noted for older versions), and the mapping-file alternative is documented as validated live: `upload --mapping mapping.json` writes the file with the `VisualElement:ve9` substitution to edit, which `import --mapping` then applies (`transfer get-mapping` regenerates it)

## [1.8.0] - 2026-07-23

An admin migration guide for the move from the retired standalone Python scripts to the Model Definition Builder (`mdb`), and a skill to find registered models that predate the 1.6.0 attribute/lifecycle improvements.

### Added

- **`Model-Definition-Builder/MIGRATION.md`** — an administrator migration guide from the legacy Python scripts (`Model-Manager-Setup.py`, `utility/prompt-builder-json.py`, `register-*.py`, `publish-*.py`, removed in 1.3.0) to `mdb`. It maps each old script to its `mdb` command, walks install/configure → `mdb setup` → `mdb register`/`publish`/`ship`, and — the main task for an existing deployment — how to **backfill models registered before 1.6.0** (`mdb register --update`, or `mdb pull --import` first for models that have no local `definition.yaml`)
- **`model-audit` skill** (`Model-Definition-Builder/.claude/skills/model-audit/`) — a read-only Claude Code skill that audits every model registered in SAS Model Manager: it fetches each model's full detail and variables and reports which of the 1.6.0 attributes are missing (family `llmodelType`, `deploymentId`, per-token/second costs, `endPoint`, `modelStatus`/`approvalState`) and whether its input/output variables are duplicated, printing the exact `mdb register --update` (or `mdb pull`) remediation per model. Its companion `audit-models.py` runs standalone too
- **LLM Usage Report (monitoring template).** `SAS-Viya-Integrations/Logging-Monitoring/LLM Usage Report.json` is a transfer package with the recommended SAS Visual Analytics report for monitoring LLM usage and prompt experimentation. It builds on four CAS tables — `LLM_LOGS`, `LLM_FACT_SHEET`, `EMBEDDING_FACT_SHEET`, `PROMPT_EXPERIMENTS` — all in the `Public` caslib by default. The Logging & Monitoring admin guide documents importing it (SAS Environment Manager or the `sas-viya transfer` CLI) and changing the CAS library from `Public` (a pre-upload `library=Public` substitution, or repointing the data sources in SAS Visual Analytics)
- **`mdb load-facts` uploads the fact sheets to CAS.** A CLI equivalent of `Load-Fact-Sheets.sas`: it uploads each sheet to a CAS library (`--caslib`, env `SAS_CAS_LIBRARY`, default `Public`; server via `--server` / `SAS_CAS_SERVER`, default auto-detected `cas-shared-default`), loading it with **global scope** (promoted) and **saving it to disk** as `LLM_FACT_SHEET` / `EMBEDDING_FACT_SHEET` so the SAS Visual Analytics monitoring report can bind to it across restarts. Any existing table is unloaded first and its saved copy replaced. `--rebuild` regenerates the sheets from the definitions before loading. Uses the casManagement REST API over the same session as register/publish — no separate CAS connection
- **`mdb sync --rebuild` regenerates the fact sheets from the definitions.** The `llm_fact_sheet.csv` / `embedding_fact_sheet.csv` files are now treated as a generated artifact: `mdb sync --rebuild` rewrites each sheet in full from every managed definition (sorted by `model_id`, creating the file if absent), so admins migrating a fleet no longer maintain the CSV by hand alongside the definitions. It is idempotent and preserves hand-maintained rows that have no definition folder, with `--prune` to drop them. The committed sheets are regenerated (row order normalized to sorted `model_id`; contents unchanged)
- **Importing the Prompt Builder via the SAS Viya CLI.** The "Deploying the LLM Prompt Builder" admin guide (`Setup-Additional-UIs.md`) documents importing the `SAS-Agentic-AI-Accelerator-Prompt-Builder.json` transfer package with the `sas-viya transfer` plugin (`upload` → `import`) alongside the Environment Manager UI, including how to point the packaged report at your own host — the package ships a `https://your-sas-viya-host` placeholder in its `substitutions` block, which you replace before upload (or remap with a `--mapping` file). The `transfer` plugin is now part of the CLI setup in `Introduction.md` (`sas-viya plugins install -repo SAS transfer`), which that guide references for install/login

### Changed

- **`.env.example`** wording no longer refers to "the Python setup scripts" — the connection block is used by the `mdb` Viya commands (the last copy that predated the mdb consolidation)

## [1.7.0] - 2026-07-22

The Prompt Builder gains governance documentation and a cost dimension. A prompt can now carry the same model-card fields the mdb-registered models do, the manifested best prompt inherits its winning LLM's provider/cost attributes, and — when the LLMs carry per-token or per-second prices — every response and the judging get an estimated cost, with the cheapest response flagged per run.

To pick up the change, re-upload the prebuilt `LLM-Prompt-Builder/dist/index.html` to your SAS Job Execution definition (and re-export the transfer package for new deployments). The `PROMPT_EXPERIMENTS` table gains `cost`, `cheapest_prompt` and `judge_cost`, so the cost view is available in Visual Analytics alongside the existing metrics. (1.6.0 is the parallel Model Definition Builder release.)

### Added

- **Optional prompt documentation.** A collapsible **Documentation** section under the prompt selector captures Model purpose, Intended use, Expected benefit, Out-of-scope use cases and Limitations — the same governance fields mdb writes on model cards, each with an info icon. The values are stored as the selected prompt's SAS Model Manager attributes (`modelPurpose`, `intendedUse`, `expectedBenefit`, `outOfScopeUseCases`, `limitations`), loaded when a prompt is selected and saved with a **Save documentation** button. Entirely optional
- **Estimated cost indicators.** When a model carries per-token (`inputTokenCount`/`outputTokenCount`) or per-second (`hostingCosts`) prices in its Model Manager registration, the Prompt Builder estimates the cost of every response (token counts × price, falling back to run-time × hosting cost — mirroring mdb's convention) and of the judging (summed across every council member and the chairman). Each response shows an **est. cost**, the judge/council banner shows an **est. judging cost**, and the lowest-cost response in a run gets a new **cheapest** coin icon next to the fastest / fewest-tokens icons. The per-response `cost`, the `cheapest_prompt` flag and the run-level `judge_cost` are persisted with the run and restored on load. Costs are shown as plain unitless numbers (the unit is whatever the registered prices use). Models without prices simply show no cost
- **`PROMPT_EXPERIMENTS` gains cost columns.** `Get-All-Prompts.sas` surfaces `cost` and `cheapest_prompt` per model result and the run-level `judge_cost`, carried onto each model row like the existing judge columns and read with the same backward-compatible approach (older trackers leave them blank). `Track-Prompt-Experiments.sas` and the SAS-code result-table scripts carry the same columns so a server-side append round-trips them

### Changed

- **The manifested best prompt inherits its winning LLM's attributes.** Manifesting the best prompt now copies the attributes of the LLM that produced the winning response — `llmodelType`, `provider`, `deploymentId`, `inputTokenCount`, `outputTokenCount`, `hostingCosts`, `endPoint` — onto the prompt model, so a prompt in Model Manager carries the same provider/cost metadata the registered LLMs do (populated from the mdb-enriched attributes; see the 1.6.0 release)
- **Optional model-card report on the manifested prompt.** A new **Model card report URI** setting in the Options pane (URL/DDC parameter `modelCardReportURI`) takes a SAS Visual Analytics report path (`/reports/reports/<uuid>`). When set, manifesting the best prompt embeds that report on the model card as its custom chart — setting `modelCardCustomChartReport` (`<sas-report url="{viyaHost}" reportUri="{uri}">`) and `modelCardCustomChartEnabled: true`, using the configured SAS Viya host — exactly as mdb populates those attributes. Left blank, the attributes are omitted
- **Prompt models use `function = "prompt template"`.** New prompts are created with the `prompt template` function value instead of the legacy `Prompting`. Existing prompts are still recognised (the prompt list matches either value) and are migrated to the new value in place the first time they are selected, so the change is transparent
- The persisted `Prompt-Experiment-Tracker.json` gains `cost`/`cheapest_prompt` per model row and `judge_cost` on the run header. All fields are optional and read back with guards, so pre-1.7.0 trackers load, render and re-save unchanged

## [1.6.0] - 2026-07-22

Model Definition Builder (`mdb`) improvements: models registered in SAS Model Manager now carry the full set of attributes the platform expects (family, version, per-token/second costs, endpoint) and move through a real lifecycle (register → publish → retire), there is a command to see every registered model and its status at a glance, a command to recreate a local definition from a registered model, and updating a model no longer duplicates its variables. Adds the OpenAI **GPT-5.6 Sol** definition.

### Added

- **`mdb list`** — lists the models registered in SAS Model Manager across the LLM and Embedding projects with their provider, family, version (deploymentId), `modelStatus`, `approvalState` and SCR endpoint. `--kind llm|embedding` narrows it; `--json` for machine-readable output. Fills the gap where there was no way to see what's registered and in what state without opening Model Manager. It fetches each model's full detail rather than trusting the model-list summary, which omits custom attributes such as `llmodelType`
- **`mdb pull <model_id>`** — recreates a local definition folder from a model registered in SAS Model Manager (the reverse of `register`). It downloads the model's content and, for models registered before mdb (which carry no stored `definition.yaml`), rebuilds `modelConfiguration.json` from the model's attributes so the result matches a legacy hand-written folder. `--import` additionally reverse-engineers a `definition.yaml` (equivalent to `mdb import`), and `--force` overwrites an existing folder. Makes a registered model recoverable when its local definition has been lost or was never checked in
- **OpenAI GPT-5.6 Sol (`gpt_56_sol`).** A new LLM definition for OpenAI's `gpt-5.6-sol` reasoning model (1,050,000-token context, 128K max output, `reasoning_effort` + `max_completion_tokens` options, $5/$30 per 1M input/output tokens, knowledge cutoff 2026-02-16), smoke-tested live against the OpenAI API
- **`-h` as a shorthand for `--help`** on `mdb` and its subcommands
- **Richer model attributes on register.** In addition to what was already set (`function`, `targetVariable`, `provider`, `endPoint`), a registered model now carries: `llmodelType` (the model family — Claude/GPT/Gemini/Phi/Qwen/…, derived from the model id instead of the previous hardcoded "GPT"), `deploymentId` (the provider model/version string), `inputTokenCount` / `outputTokenCount` / `hostingCosts` (precise per-token and per-second costs from the definition's pricing, alongside the existing averaged `costPerCall`), and `eventProbVar`. A custom SAS Visual Analytics report can be embedded on the model card (`modelCardCustomChartReport` + `modelCardCustomChartEnabled`, host from `SAS_VIYA_URL`) via a **per-kind** report URI — `SAS_LLM_MODEL_CARD_REPORT_URI` for LLM models and `SAS_EMBEDDING_MODEL_CARD_REPORT_URI` for embedding models — so the two kinds can carry different dashboards (the legacy `SAS_MODEL_CARD_REPORT_URI` is still honored as a shared fallback). All of this applies equally to embedding models; only the enrichment values differ by kind (e.g. `eventProbVar` mirrors the embedding target variable)
- **A model lifecycle across the commands.** `mdb register` sets `modelStatus` = "ready for validation" and `approvalState` = "awaiting approval" on a new model (and no longer resets them on `--update`, though it now backfills them when they are absent — notably on the embedding models, which never carried a lifecycle); `mdb publish` advances a published model to `modelStatus` = "deployed" / `approvalState` = "approved"; `mdb retire` now also sets the registered model to "retired"/"retired" in SAS Model Manager (best-effort — it stays a local tagging operation when Viya is not configured), in addition to tagging the definition deprecated

### Fixed

- **`mdb register --update` duplicated a model's input/output variables.** Re-uploading `inputVar.json`/`outputVar.json` makes Model Manager re-import the variables; without clearing the existing ones first they accumulated on every update. The update path now deletes the model's variables before the re-import, mirroring the Prompt Builder's manifest flow
- **The model family was never persisted.** The attribute was written as camelCase `llmModelType`, but SAS Model Manager's field is spelled `llmodelType` (lowercase m), so the value was silently dropped on the model PUT and every registered model showed no family. It is now written with the correct spelling (confirmed against a live environment)

### Changed

- **`mdb retire --archive` moves the definition out of the active set.** By default retire is unchanged — it tags the definition deprecated in place and keeps it tracked. With the new `--archive` flag it additionally moves the definition folder to a git-ignored `_archive/<kind>/<model_id>/` and drops its fact-sheet row, so a retired model stops cluttering the definitions directory and no longer ships in the transfer package. A local copy is kept for reference and the model stays recoverable with `mdb pull` or from git history. `mdb retire <id> --archive` also archives a definition that was already tagged deprecated
- **The responsible party is set at the project level, where it takes effect.** `mdb setup`/`register` set the project's `modelResponsibleParty` on creation and now also refresh it on an already-existing project (best-effort). The per-model `modeler` field is kept as informational metadata

## [1.5.0] - 2026-07-21

The Prompt Builder's judging grows from a single judge into an **LLM Council**. The judge picker is now a panel: tick one model for the single-judge experience (as before), or several to convene a council whose members each rank the responses independently and whose ballots are aggregated into one verdict. When the judges genuinely split, the tool says so — it shows every judge's ballot and an explicit "judges disagreed" rather than forcing a winner. Judging stays advisory: you still choose the Best Response.

To pick up the change, re-upload the prebuilt `LLM-Prompt-Builder/dist/index.html` to your SAS Job Execution definition (and re-export the transfer package for new deployments). The `PROMPT_EXPERIMENTS` table gains `judge_mode`, `judge_panel` and `judge_agreement`, so a council verdict is visible in Visual Analytics alongside the existing judge columns.

### Added

- **LLM Council (panel judging) in the Prompt Builder.** Judging keeps a single **judge-model** dropdown; once a judge is chosen a **"Convene a council of judges"** question appears, and enabling it reveals a vertically-stacked list of models to form a **panel**. One judge runs the single-judge path unchanged; a panel of two or more convenes a council. Each panel member judges the run in parallel (reusing the existing single-judge call, with per-judge self-exclusion), and the ballots are aggregated with a **Borda count** into an overall ranking and winner. The verdict banner shows the winner, an **agreement** signal ("2 of 3 judges ranked it first"), the full ranking, and **every judge's ballot with its own reasoning**; a genuine split renders an explicit **"judges disagreed"** state with no winner icon and **competition ranking** (tied-top models share rank 1 and are flagged "(tied)", so the numbers reflect the tie instead of implying an order). A **"Use the experiment's models"** button fills the panel from the LLMs currently selected for the experiment. Inline hints nudge toward an odd, provider-diverse panel of about three and warn past five, and an info icon next to the council question explains what a council is, its added cost, when to use it and why an odd number helps. Confidence is the agreement tier (unanimous → high, majority → medium, split → low). The council verdict, its ballots (including each judge's reasoning) and the panel are persisted with the run and restored on load — a reloaded council re-derives its aggregate deterministically from the stored ballots. See `LLM-Prompt-Builder/docs/prompt-council-design.md` for the design
- **Chairman tiebreaker for the council.** An optional "Break ties with a chairman model" toggle (with its own model picker and an info icon) in the council section: when — and only when — the panel ties, a designated chairman model picks among the tied responses (given the task and the panel's notes), one extra model call. The banner then shows the winner with a "Tie broken by chairman …" note and the chairman's reasoning, while the tied models stay flagged "(tied)". Off by default, so ties otherwise surface as "judges disagreed" for the user to decide. The chairman decision is persisted (and surfaced as `judge_chairman_model` in `PROMPT_EXPERIMENTS`) and restored on load
- **`PROMPT_EXPERIMENTS` gains council columns.** `Get-All-Prompts.sas` now surfaces `judge_mode` (`single`/`council`), `judge_panel` (the panel members), `judge_agreement` (e.g. `2/3`) and `judge_chairman_model`, carried onto each model result row like the existing judge columns and read with the same `exist()`-guarded, backward-compatible approach. `Track-Prompt-Experiments.sas` and the SAS-code result-table scripts carry the same columns so a server-side append round-trips them

### Changed

- The persisted `Prompt-Experiment-Tracker.json` gains council fields on the run header row (`judge_mode`, `judge_panel`, `judge_agreement`, and a nested `judge_ballots` array holding each judge's ranking, confidence and reasoning). A single judge stays exactly as in 1.4.0 (`judge_mode` absent/`single`). All fields are optional and read back with guards, so 1.4.0 trackers load, render and re-save unchanged

### Fixed

- **Save vs. manifest link placement.** A plain **Save Experiments** now shows the "open in Model Manager" link next to the Save button, instead of at the bottom of the manifest box; **Manifest Best Prompt** still shows its link in the manifest box. Previously both put the link in the manifest box, which was confusing after a plain save
- **The per-run Judge button sat slightly below the load/delete buttons.** Its hover-hint wrapper is now a centered flex item, so the three run-header buttons align
- **Loading an experiment tracker failed when any run used a non-numeric model option.** The loader reconstructed each run's options by regex-quoting keys and running `JSON.parse`, which only handled numeric values (and `API_KEY` as a special case). A string-valued option such as `reasoning_effort:medium` (or `input_type` / `normalize`) produced invalid JSON (`Unexpected token 'm' … "g_effort":medium`), so the whole tracker failed to load and the prompt showed no runs. The options string is now parsed with a proper key/value parser that coerces numbers and booleans and keeps everything else as a string. Present since judging shipped in 1.4.0

## [1.4.0] - 2026-07-21

The Prompt Builder can now **judge which response is best** with an LLM-as-a-Judge. Pick a judge model, run your prompt across several LLMs as before, and the judge ranks the responses in a single comparative call — reasoning first, candidates shown in randomised order under anonymous labels. The verdict is **advisory**: it sets a judge rank and highlights the strongest response, but you still choose the Best Response yourself. A judge is just another SCR call, so no new infrastructure is needed.

To pick up the change, re-upload the prebuilt `LLM-Prompt-Builder/dist/index.html` to your SAS Job Execution definition (and re-export the `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json` transfer package for new deployments). The prompt-monitoring `PROMPT_EXPERIMENTS` table gains four judge columns, so the judge's verdict is available in Visual Analytics alongside the existing per-run metrics.

### Added

- **LLM-as-a-Judge in the Prompt Builder.** A dedicated "Judge the responses" section (between the workbench and the tracker) holds the judge-model selector, an "Include the judge's own response" toggle (with an info icon explaining self-preference bias), and an "Auto-judge when the experiment finishes" toggle — a clear home that later judging strategies fold into. "Judge this run" (or auto-judge) sends the run's responses to the chosen judge model as a single N-way comparative ranking — the judge reasons step by step, then returns a JSON verdict (winner, ranking, confidence) parsed defensively with one retry. The verdict renders as a banner on the run (winner, ranking, confidence, the judge's reasoning, and — when applicable — a self-preference note), and the judged-best response gets a fourth run icon next to the existing best/fastest/fewest-tokens icons. The judge is **advisory only** — it never changes your Best Response selection; the per-model judge rank is the signal. By default the judge's own response is excluded from the ranking to avoid self-preference bias; the include toggle overrides that with a bias warning. A deployment can set a default judge model through the object's Options pane (`judgeModel`); the in-app selector always wins. See `LLM-Prompt-Builder/docs/prompt-judge-design.md` for the design
- **`PROMPT_EXPERIMENTS` gains judge columns.** `Get-All-Prompts.sas` now surfaces `judge_rank` and `judge_best` per model result plus the run-level `judge_model` and `judge_confidence`, read from the tracker with the same `exist()`-guarded, backward-compatible approach used for the 1.3.0 metadata — trackers written before judging simply report the columns as missing. `Track-Prompt-Experiments.sas` and the SAS-code result-table scripts carry the judge columns so a server-side append round-trips them
- **A Prompt Builder user guide** (`website/docs/User-Guide/Prompt-Builder.md`) walks prompt engineers through the whole workflow — projects and prompt-tests, model selection and options, variables, running experiments, judging, marking the best response, saving/versioning and manifesting — and cross-links to the deployment guide

### Changed

- Disabled buttons now explain themselves on hover. A disabled `<button>` fires no hover events, so the existing `title` hints on the **Manifest Best Prompt** button (no best response selected yet) and the per-run **Judge** button never actually appeared; both are now wrapped so the hint shows. The Judge button is also disabled with a clear hint when a run has fewer than two responses to compare (e.g. only one model was included), instead of failing on click
- The Prompt Builder's persisted `Prompt-Experiment-Tracker.json` gains optional per-model `judge_rank` / `judge_best` fields and, on the run header row, `judge_model` / `judge_confidence` / `judge_reasoning` plus the judge config (`judge_include_self`, `judge_auto`). The full judge context — verdict, reasoning and the judge settings used — is therefore restored when a run is loaded (via Load or the auto-load of the most recent best run), so nothing about a judged run is lost on reload. All fields are optional and read back with guards, so trackers saved by an earlier Prompt Builder load and re-save unchanged

## [1.3.0] - 2026-07-20

This release consolidates the framework onto the `mdb` CLI as the single way to register and publish. The standalone `Model-Manager-Setup.py`, `utility/prompt-builder-json.py` and the four `register-*.py` / `publish-*.py` scripts are removed in favour of `mdb setup` / `register` / `publish` / `ship`, which cover both LLM and Embedding definitions from one place and do more besides (`--all` across the fleet, in-place `--update`, build-completion `--wait`). Install the CLI once with `pip install -e Model-Definition-Builder/cli[viya]`; the Administration and User guides are updated throughout.

**Action needed for the provider fix:** every self-hosted definition's `provider` tag previously held the model's *license* rather than its provider. Twenty definitions are corrected — **re-register the affected models with `mdb register --update`** to refresh the provider recorded in SAS Model Manager. No re-publish is required, since no score code changed.

The prompt-monitoring `PROMPT_EXPERIMENTS` table also gains columns for the newer typed scoring options and for per-run variable / output-variable / integrated-call metadata, so nothing the Prompt Builder records is dropped from the report anymore.

### Changed

- **`Get-All-Prompts.sas` now surfaces the newer experiment metadata in the `PROMPT_EXPERIMENTS` table.** Its option parser was a fixed allow-list (`temperature`, `top_p`, `top_k`, `max_tokens`) that silently dropped every other option, so the typed options the Prompt Builder has emitted since 1.1.0 — `reasoning_effort`, `thinking_budget`, `max_completion_tokens`, `seed`, the penalties, and the embedding options `input_type` / `dimensions` / `normalize` — never reached the report. Each now has its own column. The table also gains `variables_count` (how many custom input variables a run used), `integrated_llm_call` (whether the run combined the LLM call into the manifested model) and `output_variables_count` (how many output variables it parses), read from the run's `variables` and `manifest` structures in the tracker. Older trackers that predate these fields are handled gracefully — every nested read is guarded, so a run without them simply reports zero and never breaks the script. Verified end-to-end against a live SAS session across new-style, old-style and partial trackers

### Removed

- **`Model-Manager-Setup.py` and `utility/prompt-builder-json.py` are removed**, superseded by `mdb setup` (added in 1.1.0). `mdb setup` creates the `LLM Repository` and both model projects, writes the `sas-viya-cli-commands.txt` authorization rules and the `llm-prompt-builder.json` / `rag-builder.json` builder seeds, and is idempotent — everything the two scripts did. Install the CLI with `pip install -e Model-Definition-Builder/cli[viya]` and run `mdb setup`; connection details come from the same `.env` the scripts used. This also retires the unused `-dt/--deployment_type` flag that `prompt-builder-json.py` accepted but never wrote to its output. The Administration Guide's "Setup SAS Model Manager" chapter and the related references now document `mdb setup`
- **The `register-LLMs.py`, `publish-LLMs.py`, `register-Embedding.py` and `publish-Embedding.py` scripts are removed**, superseded by `mdb register`, `mdb publish` and `mdb ship` — one path for both LLM and Embedding definitions. Every shipped definition is on a manifest, so `mdb register --all` / `mdb publish --all` cover the whole fleet in one go, `mdb register --update` updates a model in place (new minor version + content replacement), and `mdb publish --wait` polls the image build to completion — none of which the scripts did. The generated definition READMEs, both "Register & Publish" guide chapters, the Model Definition Builder pages and `.env.example` now document the `mdb` commands. As a bonus, the generated embedding READMEs previously printed the wrong `register-LLMs.py` command (the README template is shared across kinds); they now correctly show `mdb register <id>`, which works for either kind

### Fixed

- **Every self-hosted definition recorded a license as its provider.** `tags.provider_tag` carried the license and the real provider sat in `tags.extra`, so SAS Model Manager stored `smollm_135m` as provider `Apache-2`, the Qwen models as `Apache-2`, the Phi models as `MIT-License`, and so on — only the hosted API models (OpenAI, Anthropic, Google, Mistral, AWS Bedrock, Voyage.ai) were correct. Twenty definitions are corrected: twelve are straight swaps where the two values trade places (llama → `Meta`, `mistral_nemo` → `Mistral`, phi → `Microsoft`, qwen → `Alibaba-Cloud`, smollm → `HuggingFace`) and keep a byte-identical tag set; seven had no provider tag at all and now gain one (bge → `BAAI`, granite → `IBM`, `all_minilm_l6_v2` → `HuggingFace`, `embedding_gemma_300m` → `Google`), with the license moving to `extra` so nothing is lost; and `voyage_code_3` is normalized from `Voyage` to `Voyage.ai` to match its four siblings. Both fact sheets are synced along with the manifests, because `mdb register` reads `fact_row["provider"]` before falling back to `manifest.tags.provider_tag`. **Re-register with `mdb register --update`** to refresh the attribute on already-registered models — no re-publish is needed, since no score code changed
- `SAS-Viya-Integrations/SAS-Code-LLM-Calls/Test-DS2-Scoring-from-SAS-Studio.sas` sent an option named `max_options`, which is not part of the scoring vocabulary and was silently ignored, so the smoke test never actually capped output. Corrected to `max_tokens`
- `SAS-Viya-Integrations/SAS-Code-Model-Manager-Interaction/MM-Get-Models-Information.sas` shipped with a real-looking example model id hardcoded in the `_mgi_model_id` macro variable; it is now an empty placeholder, matching the sibling `MM-Get-*` scripts, so nobody accidentally queries a stale id

## [1.2.0] - 2026-07-20

Open-weight models can now keep their weights **outside** the container image. A new `runtime.weights_source: mounted` setting stages the weights once into a shared `ReadWriteMany` volume that every model container reads at run time, so a 7B model no longer puts ~14 GB into the image, the registry and every node that pulls it — and one staged copy serves every model, replica and republish instead of the data existing in several places. Publishes get correspondingly faster, because the weight download stops being part of the build. Existing definitions are unaffected until you opt in: the default stays `baked`.

The Administration Guide is also updated for the SAS Viya platform release 2025.10, in which `sas-decisions-runtime` was merged into `sas-model-publish` and the Build Kit paths moved.

### Added

- `runtime.weights_source` in `definition.yaml` (`baked` | `mounted`, default `baked`). With `mounted`, `mdb generate` drops the weight download from `requirements.json` and points the score code at `/pybox/model/mount/<model_id>`; the published image then carries only score code and Python dependencies
- `SCR-LLM-Deployment-YAML/llm-weights-pvc-template.yaml` — the shared `ReadWriteMany` weight store, applied once per namespace. Every self-hosted model Deployment mounts this same claim read-only at `/pybox/model/mount`
- `SCR-LLM-Deployment-YAML/stage-weights-job-template.yaml` — a one-off Job that downloads a model into the shared volume. This is also where a Hugging Face token is supplied for gated repositories, so the token is used by an ordinary Kubernetes Job you control rather than by a container build
- Administration Guide page "Serving Open-Weight Models" covering both approaches and when staging is worth the extra setup

### Fixed

- **`deploy-modelName-PV-template.yaml` could not be deployed.** It mounted a volume named `llm-pv` but never declared it, so the API server rejected every Deployment rendered from it: `spec.template.spec.containers[0].volumeMounts[0].name: Not found: "llm-pv"`. Since `mdb deploy` selects this template automatically for every self-hosted model, that affected all of them. The template now declares the volume and binds it to the `llm-weights` claim. Note that a client-side `kubectl apply --dry-run` accepts the broken manifest — the schema is valid and only the cross-reference is wrong — so use `--dry-run=server` when validating rendered manifests
- `mdb generate` no longer emits `hf login --token $(cat /etc/secret-volume/huggingfacetoken)` for gated repositories. That step could not authenticate, so a gated definition now requires `weights_source: mounted` and `mdb generate` reports a clear error instead of producing a definition whose build dies at the download step. No shipped definition is gated, so the fleet is unaffected

### Changed

- The Administration Guide page "Configuration for Publishing" is updated for the SAS Viya platform release **2025.10**, in which `sas-decisions-runtime` was merged into `sas-model-publish` (reported in [#7](https://github.com/sassoftware/sas-agentic-ai-accelerator/issues/7), thanks @cjguerrap). The page referenced `sas-bases/examples/sas-decisions-runtime/buildkit/README.md`, a path that no longer exists — the assets are now at `sas-bases/examples/sas-model-publish/buildkit/` (configuration) and `sas-bases/overlays/sas-model-publish/buildkit/` (pod templates). Verified against the 2026.03 LTS deployment assets
- Build Kit resource sizing no longer works by editing a pod template. The page now documents the supported path — `configuration.env` (`buildkitCpuRequest`, `buildkitMemoryRequest`, `buildkitMemoryLimit`, `buildkitMaxReplicas`, storage size/class) plus the `buildkit-transformer.yaml` entry — and notes the `ReadWriteMany` PVC requirement, the transformer ordering needed when HA is enabled, and the `buildkit-remove-limits.yaml` overlay. The YAML snippet the page previously showed was kaniko-era (container `buildkitd` with `--dockerfile`/`--context`/`--ignore-path` args) and no longer matches the shipped templates, which run `buildctl` against a separate daemon
- The page explains the new two-component build architecture, because it changes what customization is even possible: image builds now execute on the long-lived `sas-buildkitd` Deployment, while the per-publish job only runs a `buildctl` client that ships the build context over `tcp://sas-buildkitd:1234`
- The Build Kit page no longer documents mounting a Hugging Face token into the publish pod. Open-weight models — gated or not — get their weights from the shared volume instead, so the container build needs no Hugging Face credentials

## [1.1.0] - 2026-07-20

To pick up the Prompt Builder option-control update, re-upload the prebuilt `LLM-Prompt-Builder/dist/index.html` to your SAS Job Execution definition. The transfer package `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json` has been re-exported with the same update for new deployments.

The new **Model Definition Builder** (`Model-Definition-Builder/`) takes the chore out of creating and maintaining LLM and Embedding model definitions. A single `definition.yaml` per model becomes the source of truth and the `mdb` CLI generates every framework asset from it (score script, inputVar/outputVar, modelConfiguration, options, requirements, model card, README and the fact-sheet row). The entire definition fleet has been migrated onto manifests and regenerated (see the Changed section — deployed SCR containers keep running their old score code until re-published), and everything continues to work with the established register/publish scripts.

### Added

- Model Definition Builder CLI (`mdb`, package `sas-mdb`) with the commands `add` (interactive wizard and non-interactive flags), `generate` (incl. `--check` drift gate), `validate` (incl. `--live` provider smoke test), `sync` (fact-sheet upsert), `import` (reverse-engineer an existing definition folder into a manifest) and `test` (local scoreModel invocation)
- `definition-core/`: versioned manifest JSON Schema with a typed option vocabulary, score-script templates per provider family, the shared static assets (inputVar/outputVar, modelConfiguration boilerplate, tag taxonomy) and static provider catalog fallback tables for offline use
- Provider adapters: OpenAI-compatible (OpenAI, OpenRouter, Azure AI Foundry v1, Mistral), Anthropic, AWS Bedrock (Converse API with a Bedrock API key by default or a boto3/SigV4 variant), Google Gemini, Voyage AI and self-hosted Hugging Face (`transformers` and `sentence-transformers`); third-party adapters can be added via Python entry points
- Self-hosted OpenAI-compatible providers **Ollama** and **vLLM** (`mdb add ollama` / `mdb add vllm`), each serving both chat (LLM) and embedding models. Definitions are environment-neutral: the server base URL resolves at scoring time via a per-call option, the `OLLAMA_BASE_URL` / `VLLM_BASE_URL` container environment variable, or a localhost default baked in, and an optional bearer token is read from `OLLAMA_API_KEY` / `VLLM_API_KEY` — so one published image can point at any inference server without a rebuild. The model weights stay on the server, so the container only needs the thin api-wrapper requirements. Options include `top_k` (for vLLM sampling), and a `--license` flag records the served model's license instead of assuming Open-Source
- Embedding definitions are fully supported: generated embedding scorers return all three declared outputs and embed the complete vector (fixing two long-standing bugs in several hand-written definitions), and `embedding_fact_sheet.csv` rows are kept in sync
- Open-source CPU embedding definitions informed by the RTEB retrieval leaderboard: IBM `granite_embedding_small_r2` (47M parameters, 384-dim) and `granite_embedding_r2` (149M, 768-dim) — Apache-2.0 ModernBERT bi-encoders with an 8192-token context — join the existing `all_minilm_l6_v2`, the BGE family and `embedding_gemma_300m`. The generated `sentence-transformers` scorer was verified end-to-end on CPU (384-dim vector, correct per-mode token counts, distinct query/document embeddings)
- Environment-neutral definitions: Azure resources and AWS Bedrock regions resolve at scoring time via per-call options or the `AZURE_OPENAI_RESOURCE` / `AWS_BEDROCK_REGION` container environment variables, so one published image serves multiple subscriptions/projects/regions
- Typed scoring options: the option vocabulary covers `reasoning_effort`, `thinking_budget`, `max_completion_tokens`, `seed`, penalties, and the embedding options `input_type`, `dimensions` and `normalize`; `options.json` carries additive `type`/`values` fields for non-numeric options so UIs can render proper controls (numeric options keep the established shape)
- Generated definitions as working examples: `claude_sonnet_4_5`, `gpt_41_mini`, `gpt_5_mini` (reasoning model, live-verified), `claude_haiku_4_5_bedrock` and `titan_embed_text_v2`
- Documentation: Administration Guide page "Model Definition Builder" and a README in `Model-Definition-Builder/`
- CI safeguard `verify-model-definitions.yml`: re-renders every managed definition on pull requests and fails when committed assets drift from their manifest
- The Prompt Builder now renders a control for **every** option of an LLM instead of a fixed set: typed options from the Model Definition Builder appear as segmented selectors (`enum` with up to five values, e.g. the normalized Reasoning Effort scale), checkboxes (`bool`) or text inputs (`string`), unknown numeric options as number inputs — previously options like `reasoning_effort` or an Azure resource override were silently ignored and the score-code defaults applied
- Custom (non-vocabulary) options are supported with an inline `type` in `definition.yaml`: they pass through to the provider under their own name and `mdb generate`/`mdb validate` warn (V010) that they render with their raw name and get no standardized label or cross-provider translation
- Deprecation radar: `mdb radar [--probe]` checks every managed model against its provider's live surface (a 1-token probe is the ground truth — retired models can stay listed, as both Gemini 2.5 and `text-embedding-ada-002` demonstrated); `mdb retire` tags a definition deprecated and regenerates it
- `mdb deploy` renders ready-to-apply SCR Kubernetes YAML with every placeholder of the `SCR-LLM-Deployment-YAML` templates filled, auto-selecting the persistent-volume variant for self-hosted models; `Model-Definition-Builder/ci-recipes/` ships thin GitHub Actions pipelines (model lifecycle + weekly radar) where each step is one mdb verb
- New template families: `hf_onnx` (onnxruntime-genai self-hosted LLMs, `--runtime onnx`) and `emb_bedrock_cohere` (Cohere Embed via Bedrock, with the normalized `input_type` translated to `search_document`/`search_query`)
- Third-party adapter kit: `mdb provider scaffold <name>` generates an entry-point-wired pip package skeleton and `mdb provider check <id>` runs the conformance suite against any installed adapter
- Fleet curation backed by radar evidence: 13 provider-retired definitions removed (Claude 2.x/3.x, the Gemini 1.5 family, Gemini 2.5 Pro/Flash-Lite, `text-embedding-ada-002`); `gemini-2.5-flash` verified still serving and kept — every removed definition remains in git history and is a quick `mdb add` away if a provider revives it
- Viya lifecycle verbs (`pip install sas-mdb[viya]`): `mdb register [--update]` creates or replaces a registered model in place (new minor version, content replacement via `contents?onConflict=update`, refreshed attributes and tags — no more delete-and-re-register), `mdb publish --wait` polls the SCR image build to completion, `mdb ship` chains validate/register/publish, and `mdb endpoints` emits the SCR endpoint manifest; one implementation covers LLM and Embedding models, and every registered model stores its `definition.yaml` as model content
- `mdb setup` creates the SAS Model Manager repository (`LLM Repository`) and the LLM/Embedding Model Projects if they do not exist yet (idempotent, matching `Model-Manager-Setup.py`), and `mdb register` runs the same check automatically for the kind it registers — so a fresh environment can be bootstrapped entirely from the CLI without a separate setup step. `mdb setup` also writes the authorization-group rules (`sas-viya-cli-commands.txt` — the LLM Consumers / Prompt Engineers groups and folder/repository access rules) and the `llm-prompt-builder.json` / `rag-builder.json` builder seed files, so it fully replaces `Model-Manager-Setup.py`
- `mdb unregister <model_id>` deletes a registered model from SAS Model Manager (confirmation prompt, `--yes` to skip); the local definition folder is left untouched, so it can be re-registered any time

### Changed

- **The entire definition fleet (42 legacy folders) is migrated onto `definition.yaml` manifests.** Every folder regenerates deterministically; after the curation below, the CI drift gate covers all 37 migrated definitions (39 in total, including the two new Granite embedding models above). The migration applies these normalizations uniformly: the canonical options parser (fixing the `isinstance(options, str)` bug that silently ignored user options in several scorers), provider usage-based token counting where the API reports it (Anthropic, OpenAI, Gemini), the embedding return-arity and full-vector fixes, the `requirements.json` standardization from PR #6 (pip upgrade step first, `huggingface-hub>=0.18.0`), `toolVersion: "3.11"` (fixes the publish 400 on current SAS Viya releases), and the central `inputVar.json` typo fix. Legacy score filenames are preserved (`generation.score_code_file`) so registered models keep their scoreCodeFile. Six definitions with hand-written runtimes (`llama_31_405b`, `llama_32_1b/3b`, `mistral_nemo`, `phi_3_mini_4k`, `phi_35_mini` — onnxruntime-genai, mistral-inference, pipeline and hosted-endpoint scorers) keep their scorers hand-maintained via `generation.overrides` while gaining managed metadata and the publish fix
- **Re-publish guidance:** deployed SCR containers of migrated models still run the old score code until re-published — republish with `mdb publish <id>` (or the classic scripts) when you want the fixes in production; `mdb register --update` refreshes registered model contents first
- The Azure OpenAI definition's `API_KEY` default is normalized from the `ProviderName` placeholder to `AzureOpenAI`, and the Voyage adapter uses the fleet's established `VoyageAI` KeyName
- The generator emits `toolVersion: "3.11"` — newer SAS Viya releases reject the legacy `3.11-5` format when publishing; the `_Base_Definition` templates are updated as well
- Self-hosted Hugging Face requirements are much leaner so the SCR image builds stay under the publish timeout: PyTorch installs from the CPU-only index (`--extra-index-url https://download.pytorch.org/whl/cpu`, so PyPI still resolves torch's transitive dependencies) instead of the default wheel that bundles ~2 GB of unused CUDA libraries, and the vestigial `git-lfs` install steps are removed because `hf download` uses the huggingface-hub HTTP API rather than git. Applies to the `transformers`, `sentence-transformers` and `onnx` requirements profiles (the `onnx` profile already used no torch); the six hand-maintained runtime folders keep their own requirements. Re-register (`mdb register --update`) and re-publish affected self-hosted models to pick this up

### Fixed

- **Anthropic scorers no longer send `temperature` and `top_p` in the same request.** Claude 4.5 and newer reject a request carrying both (`invalid_request_error: "temperature and top_p cannot both be specified for this model"`), which made *every* call to the generated `claude_sonnet_4_5` scorer fail with HTTP 400 — the provider smoke test missed it because it sends neither. The option vocabulary can now mark options as mutually exclusive per template family (`exclusive_group`), and the generated scorer picks one at scoring time: whichever the caller set explicitly, preferring `temperature` when both or neither are given. Extended thinking additionally drops `top_p` outright, since it pins `temperature` to 1. Verified live against `claude-sonnet-4-5-20250929` across all five paths (neither/temperature/top_p/both set, and `thinking_budget > 0`). **Re-register and re-publish `claude_sonnet_4_5` to pick this up** — the registered model and any published SCR container still carry the broken score code
- Pre-merge review hardening of the self-hosted and Viya-lifecycle additions: `mdb validate --live` now resolves the self-hosted smoke-test server from the `OLLAMA_BASE_URL` / `VLLM_BASE_URL` environment variable (it was pinned to the baked localhost default); the `--kind` and Hugging Face `--runtime` flags are validated (an unrecognized value used to silently produce an LLM/transformers definition); `sentence-transformers` is pinned `>=5.1` to match the 5.x APIs the generated scorer calls; the embedding token count derives the special-token overhead from the tokenizer instead of assuming two; the Embedding Model Project is created with `targetVariable: embedding` (was the nonexistent `response`) in both `mdb` and `Model-Manager-Setup.py`; `mdb register` / `mdb setup` no longer abort a whole batch on one bad folder or a repository-create race; `mdb register <id>` on a folder with no manifest is reported instead of silently succeeding; misdirected `mdb add` flags are surfaced; a new V011 validation flags a self-hosted definition with no base URL; and `mdb unregister` notes that an already-published SCR container is not removed
- Fact-sheet corrections adopted from PR #6 (thanks @bteleuca): the `gemini_falsh_15_002` model_id typo that broke the fact-sheet join for that model, `Propietary` → `Proprietary` (16 LLM + 8 embedding rows), `GPT-4ois` → `GPT-4o is` (3 rows), one grammar fix, and the missing `gpt_4o_mini_az_2024_07_18` row

## [1.0.0] - 2026-07-15

The standalone LLM Prompt Builder now supports deleting prompt experiment runs, prompts and whole projects. To get the update, rebuild the [`LLM-Prompt-Builder`](./LLM-Prompt-Builder) (or use the prebuilt `dist/index.html`) and re-upload it to your SAS Job Execution definition.

### Added

- Prompt experiment runs can now be deleted from the experiment tracker; the remaining runs are automatically renumbered and the change is persisted with the next "Save Experiments"
- Prompts can now be deleted; before deletion the SAS Relationships service is queried and the confirmation dialog lists every SAS Intelligent Decisioning decision that uses the prompt (with a deep link to each decision), or notes that no decisions were found
- Projects can now be deleted; every prompt in the project goes through the same decision-usage confirmation first and a single cancel aborts the whole operation without deleting anything
- Prompt variables: define variables (name, description, string/decimal data type and a value) above the prompt fields and reference them in the system or user prompt with the `{{variableName}}` syntax — right-clicking inside a prompt field opens a menu to insert them. The values are substituted when experiments run, every run stores a snapshot of its variable setup and values in the experiment tracker, and a manifested best prompt turns the referenced variables into the documented inputs of the generated Python score code
- Experiment runs can be loaded back into the workbench — a load button on every run, and selecting a prompt automatically loads its most recent run with a selected best response. Loading restores the prompts, the variables, the LLM selection and the LLM option values, and a notification lists any LLMs of the run that are no longer available
- "Include the LLM call in the manifested model" option: when checked, the manifested Python model calls the LLM container directly (via the `requests` package) and returns the same outputs as the LLM models themselves. Unchecked keeps the previous behavior of returning `llmBody` and `llmURL` for the Call LLM node in SAS Intelligent Decisioning
- With the LLM call included, the default outputs (`response`, `run_time`, `prompt_length`, `output_length`) can be individually selected, and the LLM response can be parsed into user-defined output variables (name, description, string/decimal data type and an optional default value). This expects the LLM to respond with JSON only — a fenced ```json block is unwrapped automatically — and adds a `parse_status` output that returns 1 when every output variable was extracted and 0 otherwise
- The manifest configuration (LLM call included, selected default outputs and output variable definitions) is stored with the run in the experiment tracker, so loading a run also restores it
- Manifesting with the LLM call included also stores a `requirements.json` with the model (same format and role as the LLM definitions), so publishing destinations that build a Python environment install the required `requests` package; it is removed again when a later manifest no longer includes the call
- The integrated LLM call in the generated score code verifies TLS against the CA bundle SAS Viya mounts into its pods (`/security/trustedcerts.pem`) and supports three new environment variables: `LLMCONTAINERCABUNDLE` (alternative CA bundle path), `LLMCONTAINERSSLVERIFY=false` (disable TLS verification) and `LLMCONTAINERTIMEOUT` (call timeout in seconds, default 600)
- Manifesting tags the model in SAS Model Manager with the LLM of the best prompt plus `LLM-Call-Included` and `Output-Parsing` mode tags; tags from an earlier manifest are replaced while custom tags are kept
- The project and prompt selection lists can be filtered by name and by the user who created or last modified an entry (long lists stay searchable; the active selection always remains visible)
- New reusable confirmation modal (`src/ui/confirm-modal.ts`), toast notifications (`src/ui/toast.ts`) and SAS Relationships API wrapper (`src/api/relationships-api.ts`) in the standalone Prompt Builder

### Changed

- The `variableName:variableValue;...` user-prompt syntax is replaced by the `{{variableName}}` variables described above; prompts saved with the old syntax still load and manifest through the previous parsing
- Reworked page layout: the page is grouped into five visual sections (project & prompt, LLMs, prompt workbench, experiment tracker, manifest) with a proper heading hierarchy, and manifesting is its own section with the configuration above the action button
- Saving and manifesting now confirm success via toast notifications (deletion errors report through toasts as well), "Run Experiments" stays disabled (with a hint) until at least one LLM is selected, "Manifest Best Prompt" stays disabled until a run has a selected best response, and the create-prompt explanation moved from the button label into the dialog
- "Save Experiments" and "Manifest Best Prompt" are real buttons now, so they are keyboard-accessible and properly disabled while busy
- The experiment tracker shows an empty-state hint until a prompt with runs is selected, the destructive delete buttons sit right-aligned away from the other actions, and the LLM option explanations are Bootstrap tooltips (keyboard- and touch-accessible)
- A SAS-blue accent theme (buttons, checkboxes, headings, white section cards on a light background) replaces the stock Bootstrap look, and a loading spinner is shown while the app fetches its metadata
- The LLM definitions and their options are now loaded in parallel instead of one after another, which speeds up the initial load considerably for environments with many LLMs
- The Vite dev-server proxy now also forwards `/relationships` and `/decisions` calls to the configured SAS Viya host

### Fixed

- Loading a prompt whose saved experiment tracker contains non-consecutive run numbers (runs whose experiments all failed leave gaps) no longer fails with a console error and unresponsive buttons; the runs load normally and are renumbered contiguously on the next save
- Selecting a "Best Response" checkbox now also updates the in-memory experiment tracker, so unsaved selections survive a re-render of the tracker
- Loading an existing prompt now rebuilds the saveable experiment rows from the loaded runs (previously they were rebuilt from stale state, so a "Best Response" selected right after loading could not be saved)
- Switching the project or prompt selection now fully resets the in-memory experiment state, so experiments from a previously selected prompt can no longer be saved to a different prompt
- A failed LLM call in a manifested model (network, TLS or a non-200 response) no longer raises an exception — which aborted the whole scoring or SAS Intelligent Decisioning run with a pymas execute error — but reports the failure through the `response` output while the parsed output variables keep their defaults and `parse_status` returns 0
- The integrated LLM call builds its request body with `json.dumps` now, so prompts and input values containing apostrophes, quotes, newlines or backslashes are transmitted correctly (the previous manual escaping replaced apostrophes with double quotes and produced an invalid request body for backslashes)

## [0.1.35] - 2026-07-13

The Prompt Builder is now also available as a **standalone application** in the [`LLM-Prompt-Builder`](./LLM-Prompt-Builder) directory. It has no dependency on the [SAS Portal Framework for SAS Viya](https://github.com/sassoftware/sas-portal-framework-for-sas-viya) and can be extended independently. It ships as a single self-contained HTML file that is embedded in a SAS Visual Analytics report via SAS Job Execution.

### Added

- Standalone LLM Prompt Builder (`LLM-Prompt-Builder/`): a no-code prompt-engineering UI (Vite + TypeScript) that builds to a single-file `index.html` for embedding in SAS Visual Analytics via SAS Job Execution
- Configuration is delivered through the Visual Analytics properties panel (Data-Driven Content options), while API keys are delivered through the object's assigned data — keeping secrets out of the URL and the report definition
- Multi-language UI (English and German included)
- `create-api-key-table.sas` helper to create the API-key table consumed by the object's assigned data

### Changed

- None

### Fixed

- None

## [0.1.34] - 2025-12-18

No changes are required at this time.

### Added

- None

### Changed

- As the SCR endpoint isn't currently used in the register-LLMs.py script it has been moved to optional

### Fixed

- Implement fix for no longer requiring the fact sheet entry for LLMs

## [0.1.33] - 2025-12-16

In order for these changes to take effect you need to update the source code of the [SAS Portalframework for SAS Viya](https://github.com/sassoftware/sas-portal-framework-for-sas-viya). If you have deployed the LLM containers in kubernetes no additional changes are required, if you have deployed them as an Azure Container App or Azure Container Instance please utilize the [prompt-builder-json.py](./utility/prompt-builder-json.py) utility script with the additional option -dt aca in order to enable the prompt builder for this as well.

### Added

- Ability for the prompt builder to also communicate with LLM containers deployed as Azure Container Apps or Azure Container Instances
- Prompt Templates now include a check for an environment variable called LLMCONTAINERPATH that can be set in order to provide an environment independant path to the endpoint where the LLMs are hosted.
- Documentation on how to set environment variable

### Changed

- None

### Fixed

- None

## [0.1.32] - 2025-12-07

No changes are required at this time.

### Added

- Utility script for recreating the Prompt Builder JSON

### Changed

- None

### Fixed

- None

## [0.1.31] - 2025-10-23

No changes are required at this time.

### Added

- Improve documentation

### Changed

- None

### Fixed

- None

## [0.1.30] - 2025-10-14

No changes are required at this time.

### Added

- None

### Changed

- None

### Fixed

- The verify_ssl couldn't previously be set to false for the Python scripts

## [0.1.29] - 2025-10-13

No changes are required at this time.

### Added

- None

### Changed

- The register-LLMs.py script does no longer require an entry in the LLM Fact Sheet.

### Fixed

- None

## [0.1.29] - 2025-10-10

No changes are required at this time.

### Added

- Expanded documentation pages by a lot
- Optional argument for the Model-Manager-Setup.py script to provide the ability to change to deployment: k8s or aca
- Ignore .venv added to the .gitignore

### Changed

- The Model-Manager-Setup.py script now provides bash commands, instead of PowerShell

### Fixed

- None

## [0.1.28] - 2025-09-25

No changes are required at this time.

### Added

- None

### Changed

- None

### Fixed

- Fix argument error in publish-Embedding script and tag value in register-LLMs

## [0.1.27] - 2025-09-10

No changes are required at this time.

### Added

- SECURITY.md and CONTRIBUTING.md
- Source code headers for copyright and license information added

### Changed

- None

### Fixed

- None

## [0.1.26] - 2025-09-08

No changes are required at this time.

### Added

- BGE Small, Base and Large EN v1.5
- Attribution note in the main README
- The *register-LLMs.py* now supports the additional new metadata items provided by SAS Model Manager 2025.08+

### Changed

- The *register-LLMs.py* now requires an additional parameter -e which is the same as for the *publish-LLMs.py* to know the endpoint, as this will enable further metadata integration with the SAS Model Manager

### Fixed

- Typo in the Gemma Embedding model folder path

## [0.1.25] - 2025-09-07

No changes are required at this time.

### Added

- All MiniLLM L6 v2, an open-weight embedding model
- Embedding Gemma 300M, an open-weight embedding model

### Changed

- Change model function from classification to text generation to make use of the new features within SAS Model Manager as off 2025.08

### Fixed

- None

## [0.1.24] - 2025-09-06

No changes are required at this time.

### Added

- None

### Changed

- None

### Fixed

- Fix in the _Base_Definition_ baseScore.py LLM code

## [0.1.23] - 2025-09-02

No changes are required at this time.

### Added

- Two images added as preperation for upcoming documentation enhancements

### Changed

- Update main README.md

### Fixed

- Updated two minor versions in the Changelog as they were stuck on 0.1.20

## [0.1.22] - 2025-09-01

No changes are required at this time.

### Added

- README for Tools

### Changed

- None

### Fixed

- Wrongly formatted API_KEYS in embedding models

## [0.1.21] - 2025-08-29

No changes are required at this time.

### Added

- New Tools folder and the first tool contribution for websearch

### Changed

- None

### Fixed

- None

## [0.1.20] - 2025-08-26

Run the *./SAS-Viya-Tool-Integrations/SAS-Intelligent-Decisioning-Integration/Update-Custom-SAS-Intelligent-Decisioning-Node.sas* in your environment to get the update. The script has been validated to not require any changes to your existing messages - but it has expanded the supported character limits of both the llmBody and the llmGenerated variables to the maximum length of 10,485,760 characters.

### Added

- *Update-Custom-SAS-Intelligent-Decisioning-Node.sas* is available as an update script of existing Call LLM nodes to support the increased character limit.
- In the *Troubleshooting-Guide.md* a new line that explains Duplicate Variable Error.

### Changed

- Renamed *Non-SAS-Viya-Tool-Integrations* and *SAS-Viya-Tool-Integrations* to *Non-SAS-Viya-Integrations* and *SAS-Viya-Integrations* to better reflect that these aren't tools themselves but rather integration points. The documentation has been updated accordingly.

### Fixed

- *Create-Custom-SAS-Intelligent-Decisioning-Node.sas* now provides a character limit for the *llmBody* and *llmGenerated* variables of 10,485,760 characters (the maximum supported by all publishing destinations).

## [0.1.19] - 2025-08-14

No changes are required at this time.

### Added

- *Token-Calculator.html* now has an additional input field were you can change to estimate how often a prompt is run per day

### Changed

- None

### Fixed

- None

## [0.1.18] - 2025-08-13

No changes are required at this time.

### Added

- Gemini Flash Lite 2.5, Flash 2.5 and Pro 2.5 have been added
- The *llm_facht_sheet.csv* was updated alongside the introduced models
- Implemented [Issue 30](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/30) - requires update of the Portal Framework, by switching to the aaia branch
- Tag documentation for LLM Definitions

### Changed

- Gemini Flash 1.5 001 and 002 are deprecated by Google and have received that tag accordingly
- Claude 2.0 and 2.1 used a Legacy tag, this has been changed to make use of the deprecated feature

### Fixed

- None

## [0.1.17] - 2025-08-11

The *Token-Calculator.html* and *LLM-Details-Page.html* need to be uploaded to a webserver (e.g. the one used for the Prompt Builder UI or where the customers stores Data Driven Content object sources).

### Added

- *Token-Calculator.html*, in report utility to calculate the tokens used up by a prompt and then multiplies it with the pricing data from the *llm_fact_sheet.csv*
- *LLM-Details-Page.html*, in report utility to display the information from the *llm_fact_sheet.csv* as a type of super light weight model card

### Changed

- In *Load-Fact-Sheets.sas* the default path has been updated.
- *LLM - Get All Prompts.step* has been updated to remove warning as the macro variable was spelled incorrectly.

### Fixed

- Implemented fix suggested by [Issue 29](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues?show=eyJpaWQiOiIyOSIsImZ1bGxfcGF0aCI6IkRhdmlkLldlaWsvc2FzLWxsbS11Y2YiLCJpZCI6NjE0OTh9)
- Fix typo for phi_35_mini model id in *llm_fact_sheet.csv*

## [0.1.16] - 2025-08-10

No changes are required at this time.

### Added

- Update the LLM Fact Sheet to include all current models
- Add Load-Fact-Sheets.sas programm to load the data to CAS

### Changed

- Removed tiktoken dependency for OpenAI models, as tokens are included in the response. This will improve the total processing time

### Fixed

- None

## [0.1.15] - 2025-08-09

No changes are required at this time.

### Added

- Get-All-Prompts.sas retrieves all prompting projects, models and their experiments and turns it into a table for reporting
- LLM - Get ALL Prompts custom step introduced, that does the same as the script, just wrapped in a custom step
- LLM Fact Sheet entries for all Anthropic and Google models

### Changed

- None

### Fixed

- None

## [0.1.14] - 2025-08-05

No changes are required at this time.

### Added

- register-Embedding.py to register Embedding models to SAS Model Manager
- publish-Embedding.py to publish Embedding models to SCR
- Model-Manger-Setup.py now also creates the Embedding Model Project
- .gitignore now ignores the rag-builer.json which is used for the RAG Builder UI
- README.md was updated to refelect these changes
- Added additional embedding models from Voyage.ai

### Changed

- The LLM base example was called _Base-Definitions for consistency this name was update to \_Base\_Definitions

### Fixed

- None

## [0.1.13] - 2025-08-04

This update requires you to update requires you to switch from the prompt builder provider here to https://github.com/sassoftware/sas-portal-framework-for-sas-viya

### Added

- Add Embedding Definitions

### Changed

- Removed LLM Prompt Builder content from this repository and moved it to https://github.com/sassoftware/sas-portal-framework-for-sas-viya
- Leading and Trailing blanks are now removed from the variables in the manifested prompts

### Fixed

- The name of the manifested prompt was based on the name of the prompt, this has now been fixed to adhere to proper Python package names

## [0.1.12] - 2025-07-14

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- New button that provides a link to the model, if one is selected
- A Troubleshooting-Guide.md was added

### Changed

- None

### Fixed

- None

### Removed

- The README.md chapter **Modifying the SAS Portal Framework for SAS Viya** has been removed as the Prompt Builder is now part of the main repository

## [0.1.11] - 2025-06-06

No updating required as this update is a design phase.

### Added

- Added Claude 2.0 as a first test

### Changed

- None

### Fixed

- None

### Removed

- Evals from the fact sheets - mabye an idea for the future in a different sheet

## [0.1.10] - 2025-06-05

No updating required as this update is a design phase.

### Added

- Base attributes for all default included models

### Changed

- Added two additional attributes to the llm_fact_sheet.csv model_id and deployment_type

### Fixed

- None

### Removed

- None

## [0.1.9] - 2025-06-04

No updating required as this update is a design phase.

### Added

- Start desgining the LLM fact sheet which will be the new base for further reporting

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.8] - 2025-06-03

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- One new line in the language file to explain best prompt
- Icon is displayed next to the model name if for best response + with hover text

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.7] - 2025-06-02

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- Two new lines in the language file to explain fastest and fewest token prompts
- Icons are displayed next to the model name if they had the fastest and/or fewest token prompts
- Icons display an hover text to explain themselves

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.6] - 2025-06-01

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- Base implemention for fastest prompt and fewest token prompt has been added (no UI support yet)

### Changed

- [Change display order of Prompt Experiments](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/7)

### Fixed

- None

### Removed

- None

## [0.1.5] - 2025-05-31

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- None

### Changed

- LLM calls are now done in parallel, instead of in sequence - this should lead to a big performance uplift for prompt engineers
- No more leading and trailing new lines in the manifested model

### Fixed

- Added missing semi-colons
- Fix hardcoded model in the model variable deletion
- [Having to escape special characters e.g. \\n](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/3)

### Removed

- None

## [0.1.4] - 2025-05-30

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- Model Responses are now renderd as Markdown instead of plain text if the model response contains Markdown syntax.

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.3] - 2025-05-29

### Added

- [Explain HF token](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/10)
- Documentation was added on how to add Proprietary models
- A template for gpt_4o_mini_az_2024_07_18 was added showcasing how to deploy GPT models using Azure Cognitive Services
- Add default transfer package to implement [Logging and Monitoring Assets](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/21)

### Changed

- Updated the Base-Definition options.json to be a collection of all options that are currently used across the models
- Improved the perfomance and robustness of the log parser code and custom step by moving to Python for processing
- Moved the createLLMRepository.sas script into the MM specific subfolder along with its documentation
- Moved the LLM - Log Parser.step into the Custom Step repository to be more consistent

### Fixed

- Typos in documentation

### Removed

- None

## [0.1.2] - 2025-05-28

### Added

- Two new utility functions have been added to the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js) - get-model-variables.js and delete-model-variable.js
- New utitlity function has been added to the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js) - validate-ds2-variable-name.js

### Changed

- The userPrompt variable now has a description
- When using the prompt variable functionality in the userPrompt the variable is now checked for DS2 variable name compliance

### Fixed

- [Unneccessary API_KEY in userPrompt](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/2)
- The prompt experiment tracker file was called Prompt-Example-Tracker.json - it has been renamed to Prompt-Experiment-Tracker.json
- Fixed an issue in the create-model-content.js utility function via the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js)
- [Missing comma after top_p](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/1)
- When you manifest a prompt a couple of times it would lead to the creation of duplicate variables, this has been fixed
- Fixed an issue where if only one input variable was provided it was not added
- Fixed an issue where if the user used a semi-colon for the last variable it created an empty input variable
- [Prompt Expierments stay when changing/creating projects/prompts](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/11)
- [Catch and Return API errors](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/6)
- [Ensure valid variable names](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/14)

### Removed

- None

## [0.1.1] - 2025-05-27

### Added

- Added CHANGELOG.md to the repository to communicated updates better in the future
- Added rules to .gitignore to ignore the SAS Viya CLI if present in the repository
- Added rule to .gitignore to ignore the SAS Viya CLI setup commands
- Added documentation on setting up the SAS Viya CLI
- Added documentation on the SAS Viya CLI setup commands
- Added the generation of the SAS VIYA CLI setup commands to the Model-Manager-Setup.py script
- Added additional error messages to all Python scripts
- Added gpt-4o-mini-2025-01-01-preview, as an example for using Azure AI Foundry

### Changed

- None

### Fixed

- None

### Removed

- None