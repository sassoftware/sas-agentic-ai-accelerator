# LLM Prompt Builder (Standalone)

A no-code prompt-engineering UI for SAS Viya. Prompt engineers select LLMs, tune
their options, run prompt experiments against a SAS Container Runtime (SCR) LLM
endpoint, compare responses, and save the best prompt back into SAS Model Manager
as a scoreable model.

This is a **standalone** version of the Prompt Builder object from the
[SAS Portal Framework for SAS Viya](https://github.com/sassoftware/sas-portal-framework-for-sas-viya).
It has **no dependency on that repository** and can be extended independently.

It ships as a **single self-contained HTML file** designed to be embedded in a
**SAS Visual Analytics** report (via SAS Job Execution). There is no login flow —
the app inherits the ambient SAS Viya session, so the `@sassoftware/sas-auth-browser`
SDK is not used.

One page builds up to three kinds of thing, chosen on its Setup card under
**"What are you building?"**: a **prompt template** for LLMs, a **decision
template** for decision models, and a **RAG setup** for a document collection
(the RAG Builder, with a step row of its own). Which kinds an object offers is
decided by its switches — see [Configuration](#configuration). There is one
bundle, one Job Definition and one report object for all three.

## Features

- Create / select SAS Model Manager projects and prompt tests
- Select multiple LLMs and tune per-model options (temperature, top_p, top_k, max
  length / tokens, …)
- Run experiments across models in parallel and compare responses side by side
- Automatic flags for fastest response and fewest output tokens
- **Judge which response is best with an LLM-as-a-Judge** — pick a judge model
  and it ranks a run's responses in a single comparative call (reasoning first,
  candidates anonymised and shuffled), suggests the winner and shows its
  rationale. Optional auto-judge on run completion; the judge's own response is
  excluded by default to avoid self-preference bias (override available). The
  judge only *suggests* — you keep the final "best" choice
- Mark a "best" response and persist the experiment tracker to Model Manager
- "Manifest" the best prompt as a Python-scored model (input/output variables +
  score code) ready for use in SAS Intelligent Decisioning
- **Decision templates** for decision models (Jev, Von): a state and typed
  questions (a choice, a yes/no, a score) answered with calibrated
  probabilities, checked against expected answers, evaluated on labelled cases
  and manifested with typed outputs a decision flow can branch on - from the
  model ticked *Best Decision Model* in the most recent run that has one (a
  run has one Best Decision Model)
- Optional **prompt optimization** with DSPy in a SAS Job Execution job (an
  Optimize step of its own)
- **RAG setups**: a folder of documents wired to a vector store — the RAG
  Builder saves the setup to SAS Model Manager, generates the ingestion job, the
  SAS Studio flow and the retrieval model, launches the ingestion and tests the
  retrieval
- Multi-language UI (English and German included; easily extended)

## Requirements

- Node.js `^20.19 || >=22.12` (what Vite 8 requires)
- A SAS Viya environment with SAS Model Manager and an SCR-deployed LLM endpoint
- For RAG setups: the RAG runtime in SAS Content (the `deploy-rag-content`
  scripts), registered embedding models, a vector store and its entries in the
  credential domain — see the "RAG Ingestion and Retrieval" administration guide

## Getting started

```bash
npm install
npm run dev       # local dev server (see "Local development" below)
npm run build     # produces the single-file dist/index.html
```

Scripts:

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server with an API proxy to your Viya host |
| `npm run build` | Type-check, then build `dist/index.html` (single file) |
| `npm run preview` | Serve the built `dist/` locally |
| `npm run typecheck` | `tsc --noEmit` only |

> **Prebuilt file.** A ready-to-embed `dist/index.html` is committed to the
> repository so administrators can deploy it without a local build. If you change
> anything under `src/` or the build config, run `npm run build` and commit the
> regenerated `dist/index.html` — CI rebuilds and fails the PR if the committed
> file is out of date (see
> [`.github/workflows/verify-prompt-builder.yml`](../.github/workflows/verify-prompt-builder.yml)).

## Configuration

There are two configuration inputs, deliberately split so **secrets never travel
in the URL or report definition**:

| Setting | Delivered by | How |
|---|---|---|
| Everything in the tables below | VA properties panel | Mirrored by VA into the iframe URL as query parameters of the same name. |
| Provider API keys, vector-store credentials and location | SAS Viya credential domain | Resolved at run time; never part of the URL or the report. |

**Shared by every kind** (always shown in the panel):

| Option / URL parameter | Label in the panel | Meaning |
|---|---|---|
| `viyaHost` | SAS Viya host | SAS Viya base URL. Defaults to the embedding origin. |
| `modelRepositoryID` | Model Manager repository ID | Repository new prompt, decision and RAG projects are created in. Required by every kind. |
| `llmProjectID` | LLM project ID | Model Manager project holding the available LLM definitions (each with an `options.json`). Required for prompt and decision templates. |
| `SCREndpoint` | SCR endpoint | Base URL of the SCR endpoint hosting the model containers. Required for prompt and decision templates; RAG reads a blank value as `<SAS Viya host>/llm` (the `viyaHost` option, or the address the page is opened at), which then has to be reachable from the compute session of the ingestion and from the publishing destination. One resolved value goes to everything a manifest generates: the ingestion job gets it as `scrEndpoint`, the Embed step of the Studio flow as `_rgem_scrEndpoint`, the manifested retrieval model as `EMBED_ENDPOINT`. |
| `deploymentType` | Deployment type | `k8s` (default) or `aca` (Azure Container Apps). |
| `judgeModel` | Default judge model | Optional default LLM (by name, from `llmProjectID`) used by the LLM-as-a-Judge. Not a secret; the in-app judge selector always overrides it. |
| `modelCardReportURI` | Model card report URI | Optional report (`/reports/reports/<uuid>`) embedded on the model card of a manifested prompt. |
| `credentialDomain` | Credential domain | SAS Viya credential domain (default `agentic-ai-keys`). `none` disables the lookup for prompt and decision templates; RAG cannot be used with `none`. See below. |
| `sslVerify` | Verify TLS certificates in manifested models | Checkbox, ticked by default. See "TLS verification in manifested models" below. |

**The three switches** (always shown; checkboxes, stored and mirrored into the
URL as `true` / `false`):

| Option / URL parameter | Label in the panel | Default | Off means |
|---|---|---|---|
| `enableDecision` | Enable decision templates | Ticked | The decision kind is not offered, no decision model is requested, the Evaluate step is not built. Existing decision projects and templates stay untouched in SAS Model Manager and are not listed. |
| `enableOptimization` | Enable prompt optimization (DSPy) | Not ticked | No Optimize step. |
| `enableRag` | Enable RAG setups | Not ticked | The RAG kind is not offered and nothing of the RAG Builder is built or requested. |

A switch is read strictly: `true`, `1`, `yes`, `on` switch it on, `false`,
`0`, `no`, `off` switch it off (any case), and anything else — absent, blank,
a typo — is its default. A report saved before a switch existed therefore keeps
behaving as it did.

The switches are `Boolean` options and not dropdowns with the keys `true` and
`false`, which is what `enableOptimization` was up to 2.2: VA does not restore
such a dropdown. After every reload of the object the panel showed its first
entry (*Disabled*) whatever the report stored, while the URL carried the stored
value. A checkbox shows the stored state, and a `true` that the old dropdown
stored shows as ticked. No dropdown of the options group may use the words
`true` / `false` as keys; `verify-options.js` checks that.

**TLS verification in manifested models** (the checkbox `sslVerify`). A
manifested model that calls a model container over HTTPS itself verifies the
certificate of that call: a prompt template manifested with the LLM call
included, a decision template, and the retrieval model of a RAG setup. It
trusts the CA bundle SAS Viya mounts into its pods
(`/security/trustedcerts.pem`), or the certificates Python trusts when that
file is not there. A publishing destination that runs Python where the bundle
is not mounted cannot verify a certificate of the SAS Viya CA; the call then
fails with `CERTIFICATE_VERIFY_FAILED`.

The checkbox **Verify TLS certificates in manifested models** (`sslVerify`,
ticked by default) is what an administrator unticks for such a destination. It
is a default written at manifest time:

- It applies to models manifested from then on. A model that is already
  manifested keeps what it was manifested with.
- A model manifested with the option unticked carries the tag
  `TLS-Verification-Off` in SAS Model Manager, and the step that manifests
  shows a warning above its button. The tag follows the score code the model
  holds: a manifest whose score code SAS Model Manager refused is reported as
  failed and leaves the tag as it was, and a tag that could not be
  written - or could not be taken away again - is named beside the Manifest
  button, to be put right in SAS Model Manager. The tags are the first thing
  written once the score code is stored.
- A request that fails outright while a prompt or decision template is
  manifested (no answer, or the error page of an ingress instead of an answer
  from SAS Model Manager), and a write SAS Model Manager refuses with a
  status, leave the button usable again, and where they stop the manifest, a
  line beside the button says so and no toast says "manifested". A write
  that gets no usable answer is read back first: what SAS Model Manager holds
  afterwards decides whether it was carried out, and one that was goes on as
  if it had been answered. Where the manifest stops:
  - in the save of the experiments, which a click on the button does first:
    the line says that the save was not carried out (or, where nothing could
    be read back, that this is not known), and *Not manifested* - the
    manifest is not started, also when the save is refused with an answer;
  - before the score code: *Not manifested*, the model keeps the score code
    and the tags it had;
  - in the upload of the score code itself, when it fails outright and the
    model does not hold the score code afterwards: *Not manifested*, the
    model keeps the score code and the tags it had. Where the score code
    cannot be read back either: *The manifest was not completed, and it is
    not known whether the score code of this run is stored*; until the next
    manifest that completes, the tag may not match the score code the model
    holds. `retrieve_context.py` of a RAG setup is reported the same way
    (*It is not known whether SAS Model Manager stored retrieve_context.py*);
  - in the step for `requirements.json` (written, or removed again for the
    *Call LLM* node variant), after the score code and the tags, and in the
    last step, which copies the attributes of the LLM or of the decision model
    onto the manifested model: *The manifest was not completed*.

  One request after the score code does not stop the manifest, because the
  model holds its score code by then: a tag update that fails - refused or
  outright - gets the line named above while the manifest goes on, and the
  toast "manifested" is shown when it reaches its end. In every other case
  manifest again once the service answers.
- The environment of the destination still decides when it says something:
  `LLMCONTAINERSSLVERIFY` set to `false`, `no` or `0` switches verification
  off, set to `true`, `yes` or `1` switches it on, whatever was manifested.
  The retrieval model reads `RAGEMBED_SSLVERIFY` (the embedding container) and
  `RAGSTORE_SSLVERIFY` (the credentials service) the same way. Unset, or set
  to another word, the manifested default applies.
- In the score code of a prompt or decision template the default is the line
  `sslVerifyDefault = True` (or `False`); in `retrieve_context.py` it is
  `SSL_VERIFY` in the MANIFEST block. With the option ticked the score code
  behaves as it did before the option existed.
- It changes nothing the page does in the browser, nothing in the RAG
  ingestion job, and nothing in a prompt template manifested for the Call LLM
  node (there the DS2 node makes the call).
- The SAS Studio step *RAG - Register Setup* does not read the option. It has
  a checkbox of its own, **Verify TLS certificates in the registered
  retrieval model**, and `rag_core/manifest.py` writes `SSL_VERIFY` under the
  rule of `src/objects/rag-manifest.ts`: the literal false writes `False`,
  and only into a template that has the line.
  `SAS-Viya-Integrations/RAG/tests/test_manifest_parity.py` compares the two,
  so a change to the rule here has to be made there as well.
- A content root that was deployed before the option existed holds a
  `retrieve_context.py` without `SSL_VERIFY`. Such a template is manifested as
  it is and keeps verifying; the outcome line of the manifest says that the
  template has to be deployed again for the option to take effect.

The option is read like a switch, with "on" as the fallback: only `false`,
`0`, `no` or `off` unticks it. A report saved before the option existed, a
blank value and a typo all leave verification on.

The options of a switch join the panel only while it is on, directly below it,
their labels prefixed with its name:

| Option / URL parameter | Label in the panel | Meaning |
|---|---|---|
| `computeContext` | Optimization: compute context | Compute context of the optimization job (its Python needs `dspy`). Not used by RAG. |
| `optimizeJobProgram` | Optimization: job path | SAS Content path of the deployed optimize Job Definition. |
| `minOptimizeSamples` | Optimization: minimum samples | Minimum dataset rows before a run is allowed (default 30). |
| `embeddingProjectID` | RAG: embedding model project ID | Model Manager project holding the registered embedding models. Without it no setup can be saved. |
| `contentRoot` | RAG: content root | SAS Content folder the `deploy-rag-content` scripts populated (default `/SAS Agentic AI Accelerator/RAG`). |
| `casServer` | RAG: CAS server | CAS server of the ledger and the pipeline tables (default `cas-shared-default`). |
| `ragComputeContext` | RAG: ingestion compute context | Compute context of the ingestion job. It must run as the requesting user. Separate from `computeContext`, which RAG never reads. |
| `storeSslmode` | RAG: vector store TLS | `prefer` (default), `require`, `verify-ca`, `verify-full`, `allow`, `disable`. |
| `deletedPolicy` | RAG: when a document disappears from the source | `retire` (default, keep its chunks as history) or `purge`. |
| `retainDays` | RAG: keep retired chunks for (days) | `0` (default) keeps them forever. |
| `recordHistory` | RAG: record run history | Checkbox, on by default. |
| `embedReplicas` | RAG: embedding container replicas | What the ingestion sizes its parallel calls from (default `1`). |
| `persistElements`, `persistChunks` | RAG: save the element table to disk, RAG: save the chunk table to disk | `1` (default) or `0`. |
| `enable_pgvector`, `enable_singlestore` | RAG: offer pgvector (PostgreSQL), RAG: offer SingleStore | Checkboxes, on by default: which vector databases a new setup may choose. A stored setup on a database that is no longer offered still opens and keeps it. |

### Everything-but-the-key: the VA properties panel

The object publishes a **DDC options group** to VA (see
[`src/va/ddc.ts`](src/va/ddc.ts)) and VA renders it as the object's **Properties
panel**, so the author edits the settings there instead of hand-editing a query
string. This follows SAS's own reference implementation, the
[ArcGIS GeoWebMap provider](https://github.com/sassoftware/sas-visualanalytics-geowebmap):
the options are posted **in response to** VA's first message (which carries the
`resultName` the reply must include), wrapped as `{ resultName, optionsConfig }`.
Because the group sets `urlOption: true`, VA mirrors the author's values into the
iframe URL — keyed by each field's `name` — and [`src/config.ts`](src/config.ts)
reads them back as query parameters on the next (re)load.

The page loads as soon as one side is configured: prompt and decision templates
need `modelRepositoryID`, `llmProjectID` and `SCREndpoint`; RAG setups need
`enableRag` switched on, `modelRepositoryID` and a credential domain other than
`none`. Until then the object shows a **"Configuration required"** message that
names what each side is missing, and does **not** call SAS Viya (so it never
fails against placeholder IDs). The build-time defaults in `src/config.ts` are
intentionally blank. An object with only the RAG side configured shows the RAG
Builder alone.

Every parameter of the tables above can also be appended to the object's URL by
hand (e.g. `.../SASJobExecution/?_program=...&_action=form&llmProjectID=<uuid>&SCREndpoint=<url>&enableRag=true`),
which is a reliable fallback if your VA version does not render the options panel.

### The API keys: the credential domain

Provider API keys resolve at load time from a **SAS Viya credential domain**
(default `agentic-ai-keys`) under the **signed-in user's identity** — a user
credential overrides a group credential, and the credential's secrets map holds
one entry per provider name (matching the `API_KEY.default` value an LLM's
`options.json` references, e.g. `Anthropic`, `OpenAI`). Models whose entry is
missing for the current user render **disabled with a note** (as does a model
whose `options.json` holds no options the page can read), so who can run
paid model calls is an identity decision administered centrally — see the
"Managing Credentials" administration guide and
[`src/api/credentials-api.ts`](src/api/credentials-api.ts).

> Keys are never persisted in the report definition, the URL, or a data source —
> they live encrypted in the Credentials service and are fetched per session.

RAG uses the same domain for the vector store: the ingestion job and the
retrieval model take the store's user and password (`<BACKEND>_RAG_USER` /
`<BACKEND>_RAG_PW`) and its location (`<BACKEND>_HOST` / `_PORT` / `_DB`) from
it on the server; the page only checks which entries the signed-in user holds.
That is why RAG has no `none` mode: with `credentialDomain=none` and RAG
switched on, the RAG card is shown but cannot be chosen, and says why.

## Deploying to SAS Visual Analytics

The single-file build is embedded in Visual Analytics through **SAS Job Execution**:

1. Run `npm run build` → `dist/index.html`.
2. In **SAS Studio** / **SAS Environment Manager**, create a **Job Definition**
   whose HTML body is the contents of `dist/index.html`.
3. In a Visual Analytics report, add a **Data-Driven Content** object pointing at
   that job's execution URL.
4. Configure the object from its **Properties panel** (Viya host, Model Manager
   repository, LLM project, SCR endpoint, deployment type, credential domain,
   then the three switches and what each of them brings) — the panel is
   rendered from the options group the app publishes on load.
   Assign the `ACCELERATOR_RELEASES` table (`mdb load-releases`) as the object's
   data - a DDC object renders only with a data assignment; the app reads nothing
   from it, and provider keys come from the credential domain.

RAG setups need no second deployment of this page: switch **Enable RAG setups**
on in the same object. What they do need is the RAG runtime in SAS Content
(`deploy-rag-content`) — see the "RAG Ingestion and Retrieval" administration
guide.

Because SAS Job Execution serves HTML through a **Go template engine** (which
treats `{{ … }}` as directives), the build base64-encodes every inline `<script>`
and decodes it at runtime (UTF-8 aware, via a `new Function(...)` wrapper). This
keeps the minified bundle — which inevitably contains `{{`/`}}` — from being
corrupted.

### SAS Environment Manager configuration

To let the app run inside SAS Visual Analytics, set the following Content Security
Policy directives via **SAS Environment Manager → Configuration → View
Definitions** (adapted from the
[SAS MAS Scorer documentation](https://github.com/sassoftware/sas-mas-scorer#sas-environment-manager-configuration)).

**1. SAS Visual Analytics** — `sas.commons.web.security` → *SAS Visual Analytics* →
`content-security-policy`:

```
default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src * blob: data:; frame-src * blob: data: mailto:; connect-src 'self' *.sas.com login.microsoftonline.com graph.microsoft.com *.arcgis.com *.arcgisonline.com; object-src 'none'
```

**2. SAS Job Execution** — `sas.commons.web.security` → *SAS Job Execution* →
`content-security-policy` (replace `<sas-viya-host>` with your environment's URL).
The `'unsafe-inline'` and `'unsafe-eval'` in `script-src` are what allow the
base64-decoded single-file bundle to run:

```
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://<sas-viya-host>; style-src 'self' 'unsafe-inline'; img-src * blob: data:; child-src 'self' blob: data: ; frame-ancestors 'self'; form-action 'self';
```

**3. IFrame sandbox attributes** — verify the `sas.visualanalytics` definition's
*IFrame Sandbox Attribute Value* contains at minimum `allow-same-origin allow-scripts`.

> That sandbox does **not** include `allow-popups`, which is why the "Open in SAS
> Model Manager" link cannot force a new tab on a normal click and instead copies
> the URL (use the browser's right-click → *Open link in new tab*). To let that
> link open a tab on a normal click, add `allow-popups` to the *IFrame Sandbox
> Attribute Value*. The link is copied only where no tab opened: with
> `allow-popups`, and on the job URL opened in a browser tab, a click opens the
> tab and leaves the clipboard alone.

The page carries the referrer policy `same-origin` (a `<meta name="referrer">`
in its head): what it asks of another origin than its own carries no
`Referer`, what it asks of its own origin keeps it.

- A link that leaves for another site - the user guide link of the Optimize
  step - does not tell that site the address of your SAS Viya host.
- A tab the page opens on SAS Viya itself (SAS Model Manager, SAS Intelligent
  Decisioning) is told where the click came from, as before, and the calls to
  SAS Viya keep their `Referer` - where the page is served from the SAS Viya
  origin, which is the deployment described here. With the `viyaHost`
  parameter naming another origin, all of them are cross-origin and carry
  none.
- A call to a model container on another host carries no `Referer` either,
  but that host still learns where the page runs: the browser names the origin
  of the page in the `Origin` header of a cross-origin call, and no referrer
  policy takes that away. The same holds for SAS Viya under such a `viyaHost`.

This is what a browser does under that policy; it was checked with Edge
against the mock of the verify suites, not against a SAS Viya deployment.

## Local development

`npm run dev` proxies the SAS Viya services the page calls (`/modelRepository`,
`/identities`, `/files`, `/folders`, `/credentials`, `/jobExecution`, … — the
`proxyPaths` list) to the host set in `DEV_VIYA_HOST` in
[`vite.config.ts`](vite.config.ts). Update that value to your Viya host. Note:

- The SCR endpoint is an **absolute URL** and is not proxied; the browser calls it
  directly, so it must allow CORS from your dev origin (or be same-origin).
- Model Manager write operations require a valid, authenticated Viya session.

## Adding a language

1. Copy `src/i18n/locales/en.json` to `src/i18n/locales/<lang>.json` and translate
   the values.
2. Register it in the `LOCALES` map in [`src/i18n/i18n.ts`](src/i18n/i18n.ts).

Every text has to exist in every language. For English and German
`node .claude/skills/verify/verify-locales.js` checks that: the same keys, the
same placeholders, and no key that no source file reads.

The active language is chosen from `navigator.language`, falling back to English.

## Project structure

One app is built from this source tree: `index.html` + `src/main.ts`
(`npm run build` → `dist/index.html`). It holds both builders — the Prompt
Builder for prompt and decision templates and the RAG Builder for RAG setups —
inside one shell that shows one of them at a time.

```
src/
  main.ts                   App bootstrap (no-auth; mounts the shell)
  config.ts                 Build-time config + URL-param overrides: the prompt
                            side, the RAG side and the three switches
  styles.css                Shared styles (Bootstrap is bundled)
  state/app-state.ts        Minimal global state (viyaHost, CSRF token, user)
  api/
    http-client.ts          viyaFetch: CSRF retry, and the session of a service
                            the browser has not met yet (HTTP 449)
    models-api.ts           SAS Model Manager calls
    files-api.ts            File content retrieval
    stored-json.ts          The JSON files the page reads from SAS Model
                            Manager, each checked for the fields the page
                            needs from it
    folders-api.ts          SAS Content folders
    identity-api.ts         Current-user lookup
    credentials-api.ts      Provider keys from the credential domain
    scr-api.ts              SCR LLM and decision model invocation
    judge-api.ts            LLM-as-a-Judge calls
    jobdef-api.ts / jobexec-api.ts  SAS Job Execution (optimize, ingest, test retrieval)
    dataflows-api.ts        SAS Studio flows and custom steps
    relationships-api.ts    The decisions that use a prompt-test or a RAG setup
                            (the note in every delete dialog)
    cas-api.ts              CAS servers, caslibs and tables
  ui/                       stepper (the step row of both builders), kind
                            selector (the "What are you building?" cards),
                            selection lock (while a write to SAS Model Manager
                            is out, what is open cannot be changed and the
                            buttons that write to it are off),
                            instruction card, accordion, combobox, modals, doc
                            sections, list filter, markdown (marked +
                            DOMPurify), option controls, toasts, HTML escaping,
                            links to other SAS Viya applications
  util/
    flags.ts                The strict reader of the three switches
    validation.ts           DS2 / Python name validation
  i18n/                     Bundled locale files + loader
  va/ddc.ts                 VA DDC integration: the one options group of the page
  objects/
    builder-shell.ts        The page: which kinds are offered, both builders
                            mounted, one shown, the RAG Builder built on first use
    prompt-builder.ts       The Prompt Builder UI (prompt and decision templates)
    prompt-decision.ts      Decision templates: designer, lint, runs, evaluation,
                            manifest
    rag-builder.ts          The RAG Builder UI
    rag-*.ts                RAG backends, options, enrichment, flow and job
                            generation, manifest, retrieval log
    embedding-models.ts     Vector width of the shipped embedding models
  types/                    Shared TypeScript types + vendor module decls
```

## Verifying a change

`.claude/skills/verify/` holds a mock of the SAS Viya services the page calls
and Playwright suites that drive the built bundle in the system Edge browser:
the Prompt Builder (`verify.js`), decision templates (`verify-decision.js`), the
RAG kind (`verify-rag.js`), a new item of each of the three kinds, which has to
start as on a freshly loaded page whatever was open before or is still running
for it (`verify-new-item.js`), the lock on what is open and on the buttons
that write while a write to SAS Model Manager is out (`verify-write-lock.js`),
what a write answered and what
one item, one write or one dialog leaves for the next (`verify-outcomes.js`),
a read of what is stored that fails, and a stored JSON file that is not the
file of its name - neither is taken for what is stored (`verify-reads.js`),
the options group with its checkboxes
(`verify-options.js`), the locale files (`verify-locales.js`) and the built
page itself (`verify-tokens.js`: no text SAS Job Execution would substitute as
a `$token$` - the build removes the one marker comment of the bundler that
had that shape). After `npm run build`, from a scratch folder in which
`playwright` is installed:

```bash
node <path-to>/LLM-Prompt-Builder/.claude/skills/verify/run-all.cjs
```

[`SKILL.md`](.claude/skills/verify/SKILL.md) in that folder describes the mock,
its fixtures and what each suite needs.

## License

Apache-2.0. See the repository-root [`LICENSE`](../LICENSE).
