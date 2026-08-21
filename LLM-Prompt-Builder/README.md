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
- Multi-language UI (English and German included; easily extended)

## Requirements

- Node.js `^18 || ^20 || >=22`
- A SAS Viya environment with SAS Model Manager and an SCR-deployed LLM endpoint

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
| `viyaHost` | VA properties panel | SAS Viya base URL. Defaults to the embedding origin. |
| `modelRepositoryID` | VA properties panel | Model Manager repository new prompt projects are created in. |
| `llmProjectID` | VA properties panel | Model Manager project holding the available LLM definitions (each with an `options.json`). |
| `SCREndpoint` | VA properties panel | Base URL of the SCR endpoint hosting the LLM containers. |
| `deploymentType` | VA properties panel | `k8s` (default) or `aca` (Azure Container Apps). |
| `judgeModel` | VA properties panel | Optional default LLM (by name, from `llmProjectID`) used by the LLM-as-a-Judge. Not a secret; the in-app judge selector always overrides it. |
| `credentialDomain` | VA properties panel | SAS Viya credential domain provider API keys resolve from (default `agentic-ai-keys`; `none` disables). See below. |

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

Until those values are supplied the object shows a **"Configuration required"**
message and does **not** call SAS Viya (so it never fails against placeholder
IDs). The build-time defaults in `src/config.ts` are intentionally blank.

The same parameters — `viyaHost`, `modelRepositoryID`, `llmProjectID`,
`SCREndpoint`, `deploymentType` — can also be appended to the object's URL by
hand (e.g. `.../SASJobExecution/?_program=...&_action=form&llmProjectID=<uuid>&SCREndpoint=<url>`),
which is a reliable fallback if your VA version does not render the options panel.

### The API keys: the credential domain

Provider API keys resolve at load time from a **SAS Viya credential domain**
(default `agentic-ai-keys`) under the **signed-in user's identity** — a user
credential overrides a group credential, and the credential's secrets map holds
one entry per provider name (matching the `API_KEY.default` value an LLM's
`options.json` references, e.g. `Anthropic`, `OpenAI`). Models whose entry is
missing for the current user render **disabled with a note**, so who can run
paid model calls is an identity decision administered centrally — see the
"Managing Credentials" administration guide and
[`src/api/credentials-api.ts`](src/api/credentials-api.ts).

> Keys are never persisted in the report definition, the URL, or a data source —
> they live encrypted in the Credentials service and are fetched per session.

## Deploying to SAS Visual Analytics

The single-file build is embedded in Visual Analytics through **SAS Job Execution**:

1. Run `npm run build` → `dist/index.html`.
2. In **SAS Studio** / **SAS Environment Manager**, create a **Job Definition**
   whose HTML body is the contents of `dist/index.html`.
3. In a Visual Analytics report, add a **Data-Driven Content** object pointing at
   that job's execution URL.
4. Configure the object from its **Properties panel** (Viya host, Model Manager
   repository, LLM project, SCR endpoint, deployment type, credential domain) —
   the panel is rendered from the options group the app publishes on load. No
   data assignment is needed: provider keys come from the credential domain.

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
> Attribute Value*.

## Local development

`npm run dev` proxies SAS Viya API paths (`/modelRepository`, `/modelManagement`,
`/identities`, `/files`) to the host set in `DEV_VIYA_HOST` in
[`vite.config.ts`](vite.config.ts). Update that value to your Viya host. Note:

- The SCR endpoint is an **absolute URL** and is not proxied; the browser calls it
  directly, so it must allow CORS from your dev origin (or be same-origin).
- Model Manager write operations require a valid, authenticated Viya session.

## Adding a language

1. Copy `src/i18n/locales/en.json` to `src/i18n/locales/<lang>.json` and translate
   the values.
2. Register it in the `LOCALES` map in [`src/i18n/i18n.ts`](src/i18n/i18n.ts).

The active language is chosen from `navigator.language`, falling back to English.

## Project structure

```
src/
  main.ts                 App bootstrap (no-auth; mounts the Prompt Builder)
  config.ts               Build-time config + URL-param overrides
  styles.css              Prompt Builder specific styles (Bootstrap is bundled)
  state/app-state.ts      Minimal global state (viyaHost, CSRF token, user)
  api/
    http-client.ts        viyaFetch + CSRF retry (credentials: include)
    models-api.ts         SAS Model Manager calls
    files-api.ts          File content retrieval
    identity-api.ts       Current-user lookup
    scr-api.ts            SCR LLM invocation
  ui/
    accordion.ts          Bootstrap accordion helper
    dom-helpers.ts        HTML escaping
    markdown.ts           Markdown rendering (marked + DOMPurify)
  util/validation.ts      DS2 / Python name validation
  i18n/                   Bundled locale files + loader
  va/ddc.ts               VA DDC integration: options-group Properties panel
  objects/prompt-builder.ts  The Prompt Builder UI
  types/                  Shared TypeScript types + vendor module decls
```

## License

Apache-2.0. See the repository-root [`LICENSE`](../LICENSE).
