---
name: verify
description: Verify LLM Prompt Builder changes end-to-end by serving the built dist/index.html from a mock SAS Viya server and driving it with Playwright against the system Edge browser. Covers the whole page - prompt templates, decision templates, the RAG Builder, the Options pane switches, the locale files and the built page's text. Use after changing LLM-Prompt-Builder source to observe the real UI instead of only building.
---

# Verify the LLM Prompt Builder without a live SAS Viya

The app is ONE single-file browser bundle (`dist/index.html`) that calls SAS
Viya REST APIs on its own origin (`viyaHost` defaults to
`window.location.origin`). That makes it fully drivable against a local mock:
serve the bundle from a Node server that also implements the Viya endpoints
the app calls. The same bundle holds the Prompt Builder (prompt and decision
templates) and the RAG Builder; which of them a page shows is a matter of URL
parameters, so every suite below drives the same file.

## Run everything

```
npm run build                      # in LLM-Prompt-Builder/ (tsc --noEmit first)
cd <scratch>                       # once: npm i playwright
node <path-to>/LLM-Prompt-Builder/.claude/skills/verify/run-all.cjs
```

`run-all.cjs` copies the harness into the scratch folder as `.cjs` files,
starts a FRESH mock for each suite, runs it, and prints one line per suite
(`verify: <n> OK, 0 FAIL`) plus every `FAIL` and `NOTE` line; the complete
output is in `out-<suite>.log`, the mock's in `mock-<suite>.log`. Suite names
as arguments run a subset (`run-all.cjs verify-rag verify-options`). The exit
code is 1 when a suite failed or stopped before its summary.

The committed `dist/index.html` must equal a fresh build, or the
`verify-prompt-builder.yml` CI check fails: after the last source change run
`npm run build` and commit the bundle with it. No CI job runs these suites.

| Suite | What it drives | Needs |
|---|---|---|
| `verify.js` | The Prompt Builder with LLM prompts: steps, variables and the names they cannot have, runs, failing models, a run in flight while another prompt-test is opened, judging, optimization, manifest (both variants run in Python; saved runs the manifest refuses), the deletion flows with the decision usage note, a first request answered with 449, a prompt-test created, refused and found again; the page with decision templates off and with RAG on; TLS verification in the manifested model with the option ticked and unticked, and a manifest whose score code or tag update SAS Model Manager refuses, or one of whose requests throws (before the score code, after it, and the upload of the score code itself - also one the service stored); the save of the experiments that throws or is refused, at a click on Manifest, at Save Experiments itself and before an optimization (which is then not launched); a double click on Manifest; the Judge button after a run, a judgment that comes back during one (also one nobody answers) and two judgments out at once; the create dialogs, what they say inside, that they send nothing without a name or with a name that shows nothing (the braille pattern blank and control characters among them) while one visible character makes a name, that they create what stood in them at the click, with the fields locked meanwhile, a repository lookup that throws or is refused, an error nobody expected, and a creation started by keyboard (where the focus is, what Escape does, that the dialog cannot be closed while the creation is out); the links that leave the page where no tab can open, where one can, and with a real click - and what the opened tab is told (a Referer on SAS Viya, none on another site); the placeholder entries of the two pickers; an entry of a picker chosen while another is still loading | mock, Python |
| `verify-decision.js` | Decision templates: designer, lint, model cards, runs, evaluation (and the kind change it blocks), manifest (saved runs it refuses; TLS verification with the option ticked and unticked; a score code or a tag update that is refused; a request that throws before or after the score code, or is the upload of the score code; a save that throws or is refused before the manifest; a double click), the kinds' own project lists, a decision project without a name or with one that shows nothing, one whose repository cannot be read, one created by keyboard and refused, and the name it is created under, both delete dialogs; the page with decision templates off | mock, Python |
| `verify-rag.js` | The RAG kind: offered only when switched on, the two flows side by side (keyboard included), a setup created, saved (every variable of the retrieval model with its length), manifested (the lengths written again, onto a setup that held 99 for the two context outputs), ingested, asked a question and deleted; what the delete dialogs say about decisions; what a delete acts on; a withheld vector database; failures; the gates; a RAG-only page; what a manifest says beside its button (a stage that fails, TLS verification off, a retrieval template from before that option, a score code or a tag update that is refused, a score code whose upload gets no usable answer); what the create dialogs say inside, a name that is empty, blanks only or without a visible character, names that have one, a double click on Create, the fields that cannot be edited while a creation is out, a creation started by keyboard (focus, Escape, no closing while it is out), a repository lookup that is cut off, and a setup that was created although the listing or the reading after it failed; the one embedding endpoint of job, Studio flow and retrieval model, with and without the SCR endpoint option | mock, Python |
| `verify-new-item.js` | A new item starts as a freshly loaded page starts one (issue #47), for all three kinds: a new prompt-test, decision template or RAG setup is created after an existing one with a saved configuration, right after another new one that was filled by hand in every later step, in another project, after an item of the other kind and after an optimization - and each time the whole page is compared with a new item on a fresh page. With no item open - another project, another kind, the placeholder entry of a picker (which the two RAG pickers must not send to SAS Model Manager as an id), a deleted prompt-test - the page is what a fresh page shows with a project chosen and nothing else. What is still running when an item is left draws nothing on the item opened next: an evaluation over a CAS table (which asks no further case), an optimization job (shown on the prompt-test it runs for, also on the way back to it; any other one says that an optimization is running), a retrieval test, an ingestion, a setup whose stored configuration answers late, a stored enrichment of two prompts whose first prompt is read back late. An ingestion is shown again on the setup it was launched for when that setup is opened again - still running, with Launch off, or ended meanwhile - and not a second time once it was seen to end; a setup that is deleted takes its run along. The other way round: an existing item opened again shows what it showed the first time, a create dialog that is closed changes nothing, what was typed survives a walk through the steps, and what was typed with no prompt-test open is kept for the prompt-test created for it - also when two prompt-tests are chosen one right after the other. The roads that fill an item by hand have to change every field of the later steps between them; the LLM whose key the default credential domain does not hold is ticked on a page with `credentialDomain=all-keys` | mock |
| `verify-write-lock.js` | While a write to SAS Model Manager is out, what is open cannot be changed. Every write path is started with its requests answered late, and the page is looked at until the write has ended: the kind cards, the two lists with their search fields, Create and Delete cannot be used at any of those moments; a person who reaches for another item or another kind meanwhile does not get there; the requests go to the item that is open and to no other (a tracker with its runs, both files of a setup); the note of the write stands on that item; and afterwards the selection can be used again (after Save Experiments, Save setup and the four deletes the attempt is made again, and opens the other item). The paths: Save Experiments (also with an upload that fails), Save documentation, Manifest for a prompt template and for a decision template, Delete prompt-test, Delete project, the delete of an optimization run, Compare targets and Run optimization (which save first); in the RAG Builder Save setup (also one SAS Model Manager refuses with a status, which must read as failed), Save documentation, Manifest setup, Delete setup and Delete project | mock |
| `verify-outcomes.js` | What a write answered is what the page says, and nothing of one item, one write or one dialog stays for the next. Writes SAS Model Manager refuses with a status: each write of the manifest of a prompt template (the delete of a variable, `inputVar.json`, `outputVar.json`, `requirements.json` and the delete of a stale one, the last update of the attributes) and `outputVar.json` of a decision template, the model version and the delete of the stored tracker of Save Experiments, the delete of an optimization run, and in the RAG Builder the job reference, the copy of the Studio flow and the model version of Manifest setup. A delete whose request gets no answer; Delete prompt-test and Delete project while another item is chosen during the usage query; a change of a list sent by a script while Save Experiments or Save setup is out; a write that never gets an answer (the clock of the page moved on by two minutes); the menu of a search field once it is closed; the Manifest button of the RAG Builder while the model version is asked for; the line that names the generated flow and the banner of a failed run on the setup opened next; a create dialog after a creation; a running ingestion on a page loaded after its launch; an ingestion whose state cannot be read | mock |
| `verify-options.js` | The Options contract: a stand-in for Visual Analytics embeds the app in an iframe, checks the one options group (the checkbox `sslVerify` included: posted, mirrored, read strictly, without effect on the page) and loads every combination of the three switches, asserting what is built and what is requested | mock |
| `verify-locales.js` | `en.json` against `de.json` and both against the sources (keys, placeholders, unused keys, formal address in German) | nothing |
| `verify-tokens.js` | The built `dist/index.html` (or `VERIFY_DIST`): every inline script is stored base64-encoded, and no text outside a comment has the shape `$name$`, which SAS Job Execution substitutes as a token before it serves the page | nothing |

The browser suites stop at their first failed check, so a run without the
`===== SUMMARY =====` block did not run the checks after it. They repeat every
line in that summary: count `OK` lines above it, or read the last line
(`N checks passed, M failed`). `verify-options.js` goes on after a failure and
writes a `NOTE` line when it skips the rest of a scenario. `verify-write-lock.js`
and `verify-outcomes.js` go on after a check that failed and after a road that stopped (a `FAIL`
line that ends in *the road stopped*). `verify-new-item.js`
goes on after a comparison that failed and prints, indented under its `FAIL`
line, every field that differs with both values (`field <step> #<id>: "got"
(expected "...")`, `list ...` for the entries of a select, `text of step ...`,
`hidden in step ...` for text in what is not shown, `state ...`, `marks ...`):
one run lists what each road leaves behind. It reads a builder as a whole -
every input, text area and select of every step, shown or not, the entries of
every list, the text a person can read in each later step, the text a step
holds in what is hidden, what is hidden, disabled or folded open (also inside
a part that is hidden itself), what every element of a later step carries
beside its text (its classes, the styles set on it apart from its display,
its other attributes - not the ones that name another element or the item),
and the row of steps - so a field that is added to a later step is compared
without a line of its own in the suite. A comparison only sees a field that a
road has changed, which is why one check lists the fields that none of the
roads filling an item by hand changes: a new field has to be filled in
`fillPromptTemplate`, `fillDecisionTemplate` or `fillRagSetup`, or that check
names it. (The LLM `contrarian_judge` is the exception the check knows: its
tick box is disabled in the default credential domain, and the road on the
page with `credentialDomain=all-keys` is the one that changes it.)
`verify-tokens.js` writes a `NOTE` line for a `$name$` inside a comment. The built page holds no
`$` at all: the one such comment, the marker `/*$vite$:1*/` the bundler leaves
at the end of the stylesheet, is removed by `vite.config.ts`. A `NOTE` line
therefore means that something new has brought a token into the page.

The six browser suites that drive the page end with a look at
`GET /__direct` of the mock: no request of the browser to a SAS Viya service
may have gone around the app's HTTP client (`viyaFetch`), which is what
handles HTTP 449 and the CSRF token. The calls to the model containers
(`/scr/...`) are direct on purpose: they go to another endpoint, possibly on
another host, that knows neither.

`verify-options.js` proves the app's side of the Options contract only. Its
stand-in behaves the way Visual Analytics was measured to behave on 2026-10-10
(a `Boolean` option is a checkbox mirrored into the iframe URL as `true` /
`false`; a `String` dropdown with the keys `true` / `false` is not restored in
the pane; the message carrying the `resultName` comes once, on Visual
Analytics' own load of the object) - the header of the file has the details.
What no stand-in shows is the Options pane itself: a change to the options
group still needs a look in a real report.

## By hand

1. Copy `mock-server.js`, the suites and `python-check.cjs` into a scratch
   folder, the `.js` files renamed to `.cjs`: the harness is CommonJS, and
   `LLM-Prompt-Builder/package.json` declares `"type": "module"`, so running it
   in place fails. (`verify-locales.js` and `verify-tokens.js` are the
   exception - they run where they lie:
   `node .claude/skills/verify/verify-locales.js`.)
2. Start the mock: `node mock-server.cjs <abs-path-to>/dist/index.html`. It
   serves http://localhost:4173, records every request (`GET /__log`) and
   forgets them on `POST /__reset`.
3. Run a suite with Playwright and the system Edge (no browser download):
   `node verify.cjs`, with `playwright` installed in that folder.
4. Or open the page yourself, with the configuration in the URL:
   `http://localhost:4173/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=http://localhost:4173/scr`

Environment variables:

| Variable | Read by | Meaning |
|---|---|---|
| `MOCK_PORT` | mock, `run-all.cjs` | Port of the mock (default 4173), so several instances can serve several suites at the same time |
| `MOCK_REPO_ROOT` | mock | Repository root. The mock serves the RAG runtime (`Ingest-Documents.sas`, `Test-Retrieval.sas`, `retrieve_context.py`, the `RAG - *.step` files) from the repository and finds it from its own folder, the served bundle or the working directory; set this when the mock is a copy elsewhere serving a bundle elsewhere, or those routes answer 404 |
| `VERIFY_BASE` | suites | Where the mock is (default `http://localhost:4173`); the app URL and its `SCREndpoint` are built from it |
| `VERIFY_PYTHON` | `python-check.cjs` | The interpreter, optionally with arguments (`py -3.12`); then the only candidate |
| `VERIFY_RAG_SECTIONS` | `verify-rag.js` | Comma list of `offered, shell, flow, usage, selection, withheld, failure, gates, ragonly, manifest`; the summary names the sections that ran |
| `VERIFY_VERBOSE` | `verify-options.js` | `1` also prints the browser console errors |
| `VERIFY_DIST` | `run-all.cjs`, `verify-tokens.js` | The bundle to serve and to scan instead of this project's `dist/index.html` |

Example for a second instance: `MOCK_PORT=4174 node mock-server.cjs <dist>` and
`VERIFY_BASE=http://localhost:4174 node verify.cjs`.

## URL parameters that shape the page

- Prompt side (needs all three): `modelRepositoryID=repo-1`,
  `llmProjectID=llm-proj`, `SCREndpoint=<base>/scr`.
- The switches, read strictly (`true/1/yes/on`, `false/0/no/off`, anything
  else is the default): `enableDecision` (default on), `enableOptimization`
  (default off), `enableRag` (default off).
- Optimization: `enableOptimization=true&computeContext=ctx`
  `&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1`.
- RAG: `enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx`.
  `ragComputeContext` is the ingestion context; `computeContext` belongs to the
  optimization job and RAG never reads it. Without `llmProjectID` and
  `SCREndpoint` the page is RAG alone.
- `credentialDomain=all-keys` makes every LLM selectable and holds the entries
  of both vector stores; `credentialDomain=none` switches the lookup off for
  the prompt side and leaves the RAG card disabled with its reason.
- `enable_pgvector=0` / `enable_singlestore=0` withhold a vector database.
- `sslVerify=false` unticks "Verify TLS certificates in manifested models":
  the page behaves as before, and what it manifests from then on (a prompt
  template with the LLM call included, a decision template, a retrieval
  model) does not verify TLS by default, carries the tag
  `TLS-Verification-Off` and shows a warning above its Manifest button. Read
  like a switch, with ON as the fallback: only `false/0/no/off` unticks it.
- With neither side configured the page is one info alert ("Configuration
  required") and makes no SAS Viya request.

## Control routes of the mock

| Route | Effect |
|---|---|
| `GET /__log`, `POST /__reset` | The recorded requests (`method`, `url`, `body`, `ifMatch`, `xhr` - whether it carried `X-Requested-With: XMLHttpRequest` -, `csrf` - the token it carried -, and the `status` it was answered with); clear them |
| `POST /__decision` | Adds the decision fixtures: project `proj-2` with the template `Ticket Routing` (`model-dec`) |
| `POST /__manifested` `{ on }` | Swaps the tracker of `Used Prompt` (`model-used`) for the one of a prompt-test that was manifested: two runs, the second one the best, with two variables used in both prompts, `demo_llm` at temperature 0.9 and `second_llm` at 0.2, a council of those two with `contrarian_judge` as chairman, self-judging and auto-judge on, and a manifest configuration (LLM call included, the outputs `response` and `run_time`, the output variable `sentiment`). Opening it fills every part of Build & Test and Finalize a saved run can fill. `on: false` serves the plain tracker again |
| `POST /__nosession` `{ service, stuck, csrf }` | Takes the browser's session with one service (the first path segment, `relationships` for example) away: its XHR-style requests answer 449 until a plain `GET /<service>/` was seen. `stuck: true` never recovers; `csrf: true` makes the first write afterwards answer the CSRF 403 with the token to repeat it with; an empty body gives every session back |
| `POST /__usage` `{ model, flows, status }` | What the relationships query answers for one model: `flows` (ids of decision flows; `flow-A` "Loan Approval Decision" and `flow-B` "Fraud Check Decision" have a name) makes these decisions use it, an empty list is a model nothing uses, `status` makes the query fail with that status; without both the model answers as its fixture does, without a model all of them do |
| `POST /__failjob` | The next optimization job fails |
| `POST /__failmodel` `{ name, status }` | Every later `POST /scr/<name>/<name>` answers that status (404 and 503 are what an ingress answers for a registered model without a container); `"drop"` closes the connection (a network failure in the browser); no `status` clears that model, an empty body clears all |
| `POST /__norag`, `POST /__rag` | Hide the fixture project `rag-proj`; restore it and clear the RAG failures |
| `POST /__ragfail` `{ projects, run, documents, retrieval, jobDefinition }` | `projects: <status>` makes the RAG project listing answer that status; `run: '<reason>'` makes the next ingestion log `Ingestion failed: <reason>`; `documents: <n>` makes the next ingestion do its work and count that many failed documents in its summary and its ledger; `retrieval: '<reason>'` makes the next probe log `FAILED <reason>`; `jobDefinition: <status>` makes the next request that writes a job definition (POST or PUT) answer that status, once - a manifest that stops half way; an empty body clears all |
| `GET /__ragstate` | What the RAG side created: `projects, models, contents, folders, jobDefinitions, flows, jobs, ledgers` |
| `POST /__ragenrich` `{ on }` | Stores `rag-setup-1` as a setup that enriches its chunks with a chain of two prompts (both call `model-parse`; the second pins version `ver-1`, runs two calls at a time and stores the column `topic`). The Builder reads the prompts of a stored chain back one after the other, so the second step is what there is to draw after the first one was read. `on: false` stores the plain fixture again; either replaces what a suite saved on `rag-setup-1` meanwhile |
| `POST /__ragtemplate` `{ tls }` | Which `retrieve_context.py` the content root holds. By default the current one, whose MANIFEST block has `SSL_VERIFY = True` (the line is added when the file of the repository lacks it); `tls: false` serves the template of a content root deployed before the option for TLS verification existed, without that line; an empty body serves the current one again |
| `GET /__direct` | The requests a browser made to a SAS Viya service without `X-Requested-With: XMLHttpRequest`, i.e. around the app's HTTP client. Not listed: the page itself, `/scr/...`, and the plain `GET /<service>/` that establishes a session. `POST /__reset` does not clear it |

The mock must not define `/__vahost` or `/__vatop`: `verify-options.js` serves
those two pages itself, through Playwright routes on the mock's origin.

## Generated Python is checked with a real interpreter

Every score code a manifest uploads is compiled, and the hostile fixtures are
also run, by `python-check.cjs`:

- `resolvePython()` picks the interpreter: `VERIFY_PYTHON` when set, else the
  first of `py -3.11`, `python3.11`, `python3`, `python` that answers a version
  probe - which is what rejects the Windows Store stub. No interpreter is a
  FAIL, never a skip.
- `compilePython(code, name)` compiles from a temporary file with
  `-I -W error`, so an invalid escape sequence fails like a syntax error.
- `callScoreModel(code, args, response, name, env, files)` runs
  `scoreModel(*args)` with `requests` replaced by a stand-in that records the
  request and answers 200 with `response`; it returns
  `{ ok, error, returned, request }`, the request as `{ url, body, verify }`.
  Use it to assert that a manifested model sends the text the experiment sent
  and returns one value per output variable.
- `evaluateInModule(code, expressions, name, env, files)` loads a module and
  evaluates expressions in it - `_ssl_verify("RAGEMBED")` of a manifested
  `retrieve_context.py`, for example.
- `env` are the environment variables of the call. The variables a manifested
  model reads on its destination (`LLMCONTAINER*`, `RAGEMBED_*`, `RAGSTORE_*`)
  are taken out of the inherited environment first, so the shell that starts
  a suite cannot change a result. In a value `{folder}` is the temporary
  folder of the call; `files` puts further files there (a CA bundle), and the
  same word comes back in the result.
- `withoutTlsDefault`, `expectedTlsVerify` and `tlsVerifyOf` are the checks of
  TLS verification in a manifested score code: the six lines the option
  "Verify TLS certificates in manifested models" added, and the `verify`
  argument `requests.post` gets for `LLMCONTAINERSSLVERIFY` unset, `false`,
  `true` and a word that is neither, with and without a CA bundle.

A new manifest check needs a hostile fixture, not only a friendly one: text
with a backslash, a tab, braces, three double quotes in a row and a double
quote as the last character. `verify-rag.js` compiles the manifested
`retrieve_context.py` the same way.

With the option ticked a score code has to be what it was before the option
existed, plus those six lines. `verify.js` and `verify-decision.js` check that
byte for byte: they take the six lines out and compare the SHA-256 of the
rest (the origin of the mock replaced by a fixed word) with a hash recorded
from the bundle of commit ccae261, the last one before the option. A
deliberate change of a score code changes that hash: the failing line prints
the new one - read the diff of the code before putting it into the suite.

A manifest is generated from the SAVED run, so what an earlier release stored
is an input of its own: `verify.js` and `verify-decision.js` serve a tracker
of their own for that (a Playwright route on `/files/files/trk-1/content` or
`/files/files/dtrk-1/content`, then the prompt-test is selected again) - a
variable or a question named like a Python keyword, an option value with a
backslash, a model name with a double quote. Use that instead of a new
fixture in the mock: the fixtures of a project are counted by other checks.

## What the mock answers

Prompt side:

- `GET /identities/users/@currentUser` (user `tester`)
- `GET /modelRepository/projects` (project dropdown, filter `Prompt-Engineering`;
  `proj-2` answers the `Decision-Engineering` filter after `/__decision`)
- `GET /modelRepository/projects/{llmProjectID}/models` (plus the same URL with
  `filter=eq(tags,'deprecated')` returning empty) and per-LLM
  `GET /modelRepository/models/{id}/contents` → an `options.json` entry whose
  `fileUri` + `/content` returns e.g. `{ "temperature": { "default": 0.7 } }` —
  without this the whole builder fails to load
- `GET /modelRepository/projects/{id}/models` for the selected project: ONE
  listing per project whose items carry `algorithm` and `tags`. A prompt-test
  is a decision template when its `algorithm` is `Decision-Template` (or its
  tags say so) - `model-dec` carries both
- `GET /modelRepository/models/{id}/contents` → `Prompt-Experiment-Tracker.json`
  entry; its `{fileUri}/content` returns the PETRow[] fixture
- `POST /modelRepository/models` (creating a prompt-test): the mock records the
  body, keeps the model (`model-new-1`, `model-new-2`, ...) so the listing of its
  project, its own `GET`/`PUT`/`DELETE` and its contents answer for it, and
  answers 201 `{ items: [model] }` with `tags: null` - the real service drops
  the tags sent at creation, they only stick through a later `PUT`, which the
  app sends right after. A name a created model or a fixture RAG setup of the
  same project already has is refused with 409 and an error body without an id
- `POST /scr/<model>/<model>` (the LLM and decision model containers); see
  `/__failmodel`
- `GET /credentials/domains/{domain}/secrets`: `agentic-ai-keys` (the default)
  holds an OpenAI key and the pgvector entries, so `contrarian_judge` renders
  disabled; `all-keys` holds every provider key and both vector stores
- `POST /relationships/relationships`, `GET /decisions/flows/{id}`
- Save flow: `POST .../modelVersions` (200 JSON), `DELETE .../contents/{id}`
  (204), `POST .../contents?...` (201 JSON, multipart body)
- Optimize flow: `GET /folders/folders/@item?path=...` → job-definition URI,
  `POST /jobExecution/jobs` + `GET /jobExecution/jobs/{id}` (the mock job
  completes on the fourth poll), the job log at `/files/files/joblog-1/content`
  with `NOTE: Python-Subprocess - ...` milestone lines, and — after completion —
  a `Prompt-Optimization-Tracker.json` entry on the source model
- Decision models: the `Decision Model Project` lookup → `dec-proj` with
  `dec-1` (`jev_mock`, needs the OpenAI entry) and `dec-2` (`von_mock`, no
  key); a POST to `/scr/<model>/<model>` whose inputs carry `state` answers in
  the System One shape; `Public.DEC_CASES` is a labelled table
  (`expected_team`, `expected_escalate`)

RAG side (stateful; the block sits before the generic routes):

- Projects: the `RAG-Engineering` filter lists `rag-proj` ("RAG Demo Project")
  plus created ones (`rag-proj-new-N`)
- Setups in `rag-proj`: `rag-setup-1` "Policy Handbook" (saved, pgvector,
  prefix `RAGDEMO`, job `ragjob-1`), `rag-setup-2` "Empty Setup" (never saved),
  `rag-setup-ss` "Legacy Manuals" (SingleStore, job `ragjob-2`). A fixture
  setup answers `GET` with an ETag and `PUT` with 412 unless `If-Match` fits;
  created setups are `model-new-N` like prompt-tests
- Embedding models from `emb-proj` (`bge_small_en_v15`,
  `text_embedding_3_small`, `custom_embedder`); the enrichment prompt
  `model-parse` "Summarise Chunk" for the `Output-Parsing` filter
- SAS Content: the content root `/SAS Agentic AI Accelerator/RAG` with `jobs`,
  `models`, `steps`, `rag_core`; the `generated` folder exists only once a
  manifest created it; `POST /folders/folders` creates `rag-folder-new-N`
- Flows and job definitions: `POST/GET/DELETE /dataFlows/dataFlows`,
  `GET /dataFlows/steps/{id}` (the real `.step` JSON),
  `POST/GET/PUT/DELETE /jobDefinitions/definitions` (PUT needs `If-Match`)
- Jobs: a launch of one of those definitions is `ragrun-N` (ingestion) or
  `ragtest-N` (the definition has a `question` parameter); `running` on the
  first poll, `completed` with a log from the second. An ingestion log is
  written as it arrives in a SAS log: one line per step, each with its own
  count (`0 failed` on a clean run), and the summary last, without the
  semicolon the job's code puts before `documents` (the job blanks `; % &` and
  quotes in every message). A retrieval log carries `RAGRETRIEVE ROW {json}`
  lines. `GET /jobExecution/jobs` with a filter on the state lists, newest
  first, every such job the mock has not yet found completed, as `running`
  with its `jobRequest.jobDefinitionUri`; a listing is no poll
- CAS: the ledger `<PREFIX>_LEDGER` answers for `RAGDEMO` and for every setup
  whose ingestion completed

## Gotchas

- Two builders can be in the document at once. `#app` holds `#app-area-prompt`
  and `#app-area-rag`; the one not shown has the `hidden` attribute and keeps
  its state. The RAG Builder is built the first time the RAG card is chosen -
  before that no `#app-obj-RGB...` element exists and no RAG request is made.
  Scope every selector to its builder (`#app-obj-LPB ...`,
  `#app-obj-LPB-stepper ...`, `#app-obj-RGB ...`): a page-wide `.pb-step` or
  `.pb-section` count is only right while one builder is mounted.
- The id prefixes are fixed: `LPB` for the Prompt Builder, `RGB` for the RAG
  Builder, each after the pane id `app` (`#app-obj-LPB-pet`, run accordions
  `#app-obj-LPB-pet-{i}`, newest run first). The Prompt Builder's pickers are
  `#LPB-project-dropdown` / `#LPB-prompt-dropdown`, the RAG Builder's
  `#app-obj-RGB-project` / `#app-obj-RGB-setup`.
- The kind is chosen on the Setup card of either flow: radios
  `#<prefix>-kind-llm`, `-decision`, `-rag` inside `#<prefix>-kind-options`
  (cards `.pb-kind-option`), prefix `LPB` or `RGB`. A kind that is not offered
  has no radio; with one kind left the group is hidden. A disabled RAG card
  carries `#LPB-kind-rag-reason`; a failed RAG load shows `#LPB-kind-notice`.
  After a change of flow the target shows its Setup step and the checked radio
  has the focus. On a card an arrow key moves the focus to the neighbouring
  card and chooses nothing; Space chooses the focused card. While the RAG
  Builder is built for the first time `#app-area-prompt` is `inert` (and
  `aria-busy`), so a click there does nothing until the flow has changed or
  the build has failed.
- While a write to SAS Model Manager is out, the fieldset that holds the
  controls of a Setup card is disabled: `#LPB-selection` (kind cards, both
  filters and lists, the documentation section, Create and Delete) and
  `#app-obj-RGB-selection` (kind cards, both filters and lists, Create and
  Delete; the documentation card of the RAG Builder is not in it). The
  controls keep their own `disabled` property, so ask with
  `element.matches(':disabled')` or `page.isDisabled`, not with
  `element.disabled`. Playwright waits for a control to be enabled:
  `page.selectOption` and `page.click` on one of them go through once the
  write has ended, so a check that has to act DURING a write gives its
  action a short timeout and expects it to run out (`pickAsAPerson` in
  `verify-write-lock.js`). The lock starts at the click that starts the
  write - for a delete, the delete of an optimization run and Compare
  targets, when their dialog has closed - and ends with the write, also
  when it failed. A creation does not take it: its dialog lies over the
  page. A script is not stopped by a disabled control: a `change` a script
  sends to a list or a tick box inside the fieldset while the lock is on
  (`pickByScript` in `verify-outcomes.js`, which does what the search field
  of a list does) is undone and reaches no handler. Every request of the
  page's HTTP client is given up after two minutes without an answer, so a
  write that never gets one ends as a failed write and gives the lock back;
  a check of that installs the clock of the page (`page.clock.install()`
  before the page is opened) and moves it on with `page.clock.fastForward`.
- A delete dialog is about what was selected at the click. The RAG Builder
  lists the setups of a project again for Delete project. Both builders open
  no dialog when another item or project was chosen while the usage was
  asked (the Prompt Builder also while the prompt-tests of a project are
  listed for Delete project) - a check that changes the selection right after
  the click has to expect that.
- A run in flight keeps Run Experiments disabled until its calls have ended,
  also after another prompt-test was opened; the answers are then dropped.
- A link that leaves the page calls `window.open(url, '_blank')` WITHOUT a
  features string and clears the opener of the window it gets. A stand-in for
  `window.open` has to answer the way a browser does - `null` when the
  features hold `noopener`, a window otherwise - or it cannot tell a tab that
  opened from one that was blocked (`assertLinkOpensTab`). A check that lets
  the real tab open answers its request from the browser context
  (`context.route('**/SASModelManager/**', ...)`): the mock does not serve
  that page and would list the request under `GET /__direct`.
- The page carries `<meta name="referrer" content="same-origin">`, because
  `window.open` cannot be asked for `noreferrer` (it implies `noopener`). The
  handler of the same `context.route` reads the `Referer` the opened tab was
  sent with: present for SAS Model Manager (`assertLinkOpensRealTab`), absent
  for the user guide link of the Optimize step, the one link to another site
  (`assertLinkTellsAnotherSiteNothing`, which answers
  `https://sassoftware.github.io/**` from the context so that nothing leaves
  the machine). A middle click opens the plain anchor, and Playwright does not
  route the first request of that tab - it would load the real page.
- The text a link shows when it is left alone is known to the test (its
  `href` attribute for the links to a saved prompt-test, the label for
  "Open in SAS Model Manager"), never read from the link at the moment of a
  check: a "link copied" note left by the check before would be taken for it.
- A create dialog (`<prefix>Modal`, `<prefix>Name`, `<prefix>Description`)
  says why nothing was created in `<prefix>Error` inside its body, keeps the
  text of its fields between two openings - unless a creation went through
  in between: then it opens empty -, and has its Create button, its
  two fields, Cancel and the X disabled while a creation is out. A name that
  shows nothing - empty, or without a visible character: white space, the
  characters of `Default_Ignorable_Code_Point` (a zero-width space, the
  Hangul fillers, tag characters, variation selectors), control characters,
  the braille pattern blank U+2800, U+1D159 and U+16FE4 (`NOT_VISIBLE` and
  `isBlankName` in `src/ui/create-modal.ts`) - is refused by the dialog
  itself: no request, "A ... name is required." in `<prefix>Error`, the name
  field marked (`is-invalid`, `aria-invalid`) and focused, the button usable.
  The mock CREATES a project without a name, so a check of this answers the
  creation request with a route (HTTP 400, as the service does) and counts
  what reaches it. Write such a name as an escape (`'\u200B'`) in a suite,
  never as the character: the line would read `''`. The dialog hands the name
  and the description of the click to the flow; a check of that slows the
  request the flow waits for (`**/modelRepository/repositories/**` for Create
  a new Project), empties the fields with `page.evaluate` meanwhile - a
  script can set a disabled field - and reads the name in the request.
  While a creation is out the dialog cannot be closed - Escape, Cancel, the
  X and a click beside it (`page.mouse.click(5, 5)`) do nothing - and the
  focus is on `<prefix>Modal` itself. After a refusal the focus is back on
  the Create button, and Escape closes the dialog, also with the focus on
  `<body>` (`document.activeElement.blur()`). A check of this starts the
  creation with `page.focus` on the button and `page.keyboard.press('Enter')`
  or `'Space'`, and answers the creation with a route that waits 1.5 s, so
  that the dialog is looked at while the creation is out. Enter in one of the
  two fields starts nothing. A request that fails BEFORE the creation
  (`**/modelRepository/repositories/**` for the two project dialogs:
  `route.abort('failed')`, an HTML page, a 403) creates nothing, and
  `<prefix>Error` says so. One that fails AFTER it leaves the thing created
  and the dialog closed: the two RAG dialogs report it on the page
  (`.alert-warning` in `#app-obj-RGB-status`, with
  `#app-obj-RGB-retry-projects`), the two Prompt Builder dialogs list and
  select the new entry and write the failure to the console. Such a check
  lets the POST through and fails the GETs only once the POST was seen. An error the flow did not expect ends in `<prefix>Error` too
  ("The creation did not finish"); `verify.js` gets one by leaving the
  project picker without a selected entry (`selectedIndex = -1`).
  Prefixes: `promptBuilderCreateProject`, `promptBuilderCreatePrompt`,
  `app-obj-RGB-create-project`, `app-obj-RGB-create-setup`.
- What a manifest of a prompt or decision template has to say is in
  `#app-obj-LPB-pet-save-result`: `.pet-manifest-failed` (the score code was
  not stored - no tags written, no toast) and `.pet-manifest-tls-tag-failed`
  (the tags could not be updated, in either direction). A fault is injected
  with a Playwright route on the upload that holds `def scoreModel(` or on the
  first `PUT` of the model after it - the tag update, which is the first
  request after the score code. A request that THROWS in the page (a 502 with
  an HTML body, `route.abort('failed')`) and stops the manifest leaves a
  `.pet-manifest-failed` line of a kind of its own and no toast:
  `.pet-manifest-stopped` before the score code was stored,
  `.pet-manifest-unknown` when it is the upload of the score code itself (the
  service may have stored it: `route.fetch()` and then `route.abort('failed')`
  is that case, and the log of the mock shows the upload with a status below
  300), and `.pet-manifest-incomplete` after it (the tags are written by
  then). A write SAS Model Manager refuses with a status stops the manifest
  in the same way, with `.pet-manifest-stopped` or `.pet-manifest-incomplete`
  (a refused score code: the plain `.pet-manifest-failed` line above). One
  request after the score code does NOT stop the manifest when it throws or
  is refused: the tag update (`.pet-manifest-tls-tag-failed`, and the toast
  is shown). The last write, of the attributes, ends it with
  `.pet-manifest-incomplete`.
  The click saves the experiments first: a save that throws leaves
  `.pet-save-failed` in the box (beside Save Experiments,
  `#app-obj-LPB-pet-save-only-result`, when that button was clicked), and
  after a save that failed in any way the manifest does not start
  (`.pet-manifest-failed.pet-manifest-not-saved`). The Manifest button is
  taken at the click, so a double click runs one manifest; both buttons are
  usable again on every path. The checks wait for that with `settles`, which
  answers instead of throwing, so that a bundle that leaves a button on
  "Saving..." fails a check. The mock answers the contents of the fixture
  models from fixtures: a save that failed there loses nothing for the checks
  that follow. Run optimization and Compare targets save first as well and
  launch no job after a save that failed. Run optimization is checked, as
  the last thing in `verify.js`, because a job the mock has launched stays
  launched (the page needs `minOptimizeSamples=1` to offer the run with the
  fixture runs); Compare targets holds the same lines and has no check.
- Each flow is a row of steps and only the current step's pane is visible, so
  Playwright cannot act on a control of another step: open its step first with
  `#app-obj-<P>-step-{key}` (pane `#app-obj-<P>-pane-{key}`, Back/Continue
  `#app-obj-<P>-stepper-back` / `-continue`). Prompt keys: `setup`, `build`,
  `optimize` (only with `enableOptimization=true`), `evaluate`, `finalize`;
  RAG keys: `setup`, `documents`, `store`, `run`. Only the current step is
  highlighted (`.pb-step.is-current`); there is no done state. Running and
  loading a run stay on `build`; loading an optimized prompt moves from
  `optimize` to `build`.
- The Evaluate step (`#app-obj-LPB-step-evaluate`, pane `-pane-evaluate`) is
  built only while decision templates are switched on; it is then in the row
  of every page and hidden (`li[hidden]`) unless a decision template is open.
  The Prompt Builder therefore has seven `.pb-section` cards with decision
  templates on and six with `enableDecision=false`, where the decision models,
  the designer and the decision manifest options are absent as well.
- Bootstrap modals: the `.show` class disappears at the START of the hide
  transition, but promise resolution / button re-enabling happens on
  `hidden.bs.modal` (~300ms later) — poll instead of asserting immediately.
  Escape only dismisses after the fade-in finished (focus trap active); wait for
  `document.activeElement` to be the modal before pressing it.
- The run-header system prompt element id contains a pre-existing typo:
  `...-run-systenPrompt`.
- Decision mode: each view lists its own projects (a POST to
  `/modelRepository/projects` creates `proj-new`) and its own prompt-tests.
  Designer ids: `#app-obj-LPB-decision-state`,
  `#app-obj-LPB-decision-add-question`, rows `.pb-question-row` with
  `.pb-question-id/-type/-instructions/-criteria/-expected`, each with a
  `.pb-question-lint` (items `.pb-lint-item.is-fix/-warn/-tip`, the escape
  fix `.pb-question-add-escape`); the checklist is
  `#app-obj-LPB-decision-checklist`, the template picker
  `#app-obj-LPB-decision-template` + `-apply`, help toggles `.pb-help-toggle`
  reveal `.pb-help`. Decision models are cards (`.pb-model-card` with
  `-meta`, `-price`, `.pb-model-fit.is-ok/.is-no`) whose checkboxes stay
  `#decision-model{i}`; the mock's `dec-1`/`dec-2` details carry the
  `properties` (contextLength, maxOptions, questionTypes) and tags the cards
  read - `von_mock` takes choice and noul only, so a score question makes it
  unfit and skipped. A run's answers carry `.pb-answer-reading` and a
  `.pb-gate.is-auto/-escalate/-abstain`; a run with two models gets
  `#app-obj-LPB-pet-{i}-run-compare`. The mock answers a choice with its
  escape option when no keyword matches (the fourth `DEC_CASES` row), and
  the evaluation shows `.pb-reliability` with ECE and Brier.
- RAG Builder ids (all `#app-obj-RGB-...`): buttons `save`, `manifest`,
  `launch`, `ledger`, `test`, `test-run`, `delete-setup`, `delete-project`,
  `save-documentation`, `open-in-mm`, `retry-projects`; panels `save-result`,
  `ingestion-run`, `run-state`, `run-milestones`, `ledger-panel`,
  `test-panel`, `test-status`, `test-results`; fields `embed-model`,
  `backend`, `collection`, `tables-prefix`, `tables-caslib`. A vector database
  that a stored setup uses but the object no longer offers stays in the
  `backend` list as an option with `data-unlisted="true"`.
- A failed RAG project listing does not fail the build: the RAG flow opens
  with `#app-obj-RGB-status .alert-danger` and the retry button. The "RAG
  Builder could not be built" path of the shell is reached only by an
  exception, which is why `verify-options.js` injects one (an element id set
  to `app-obj-RGB-stepper` throws while `window.__verifyFailRagBuild` is true).
- Run each suite against a FRESHLY started mock instance: the suites create
  models, projects, folders and jobs, the optimize flow mutates job state, and
  `verify-options.js` reads the mock's one request log - a mock that already
  served another suite, or serves two at once, gives wrong results.
- `verify.js` writes its screenshots (`shot-*.png`) into the working directory.
- An item that is left takes with it what is still running for it. A check of
  that needs the answer to arrive AFTER the next item is on the page, so it
  holds the answer back with a Playwright route (a `sleep` before
  `route.continue()`) and reads the page only once the request was answered:
  the calls of an evaluation (`**/scr/**`; `evaluate-run` is disabled until
  they are in), the polls of an ingestion (`**/jobExecution/jobs/ragrun-*`,
  the second one finds the job completed), the reading of a setup
  (`**/modelRepository/models/<id>/contents**`), the versions of an
  enrichment prompt (`**/modelRepository/models/model-parse/modelVersions**`,
  after `POST /__ragenrich`). A retrieval test takes
  three seconds by itself (the page waits that long between two polls) and
  has ended when its job definition was deleted. A held request is not in
  the log of the mock yet, so `atRest` does not wait for it.
- An ingestion that this page launched is shown again when its setup is
  opened again (the page keeps the id of the job; nothing is stored with the
  setup). A setup with a job definition whose run the page does not know of
  - on a page loaded after the launch - asks Job Execution for the jobs that
  are pending or running (`GET /jobExecution/jobs?filter=...state...`) and
  follows the newest one of its job definition. The mock lists there every
  RAG job it has not yet found completed, so an ingestion that a road leaves
  running is shown by the next road that opens its setup, on any page. The
  mock finds a job completed on its second poll, so a check that needs it
  running across a change of setup answers the polls itself (`keepRunning`
  in `verify-new-item.js`: a route on `**/jobExecution/jobs/ragrun-*` that
  fulfils with the mock's own "running" answer), and one that needs it
  ended while another setup is open asks the mock for the job twice from
  Node. A run that is picked up after it has ended shows no "took ...".
  After three polls in a row that fail, a run is no longer followed: the
  card says so and Launch can be used again.
- The first optimization job of a mock runs for four polls (twenty seconds).
  Its entry in the optimization tracker stands from then on, and every later
  job is found finished on its first poll, five seconds after its launch: a
  check that needs a job in flight uses the first one, or holds the polls
  back (`**/jobExecution/jobs/job-1`). Only the prompt-test a job runs for
  shows its state, its log and its result (`optimize-status`, `optimize-log`,
  `optimize-result`, the button reading *Optimising...*); any other one has
  the plain, disabled button and *An optimization is already running.* in
  `optimize-banner`.
