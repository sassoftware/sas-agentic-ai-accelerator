---
sidebar_position: 11
---

# Deploying the Builder UIs

The Prompt Builder is not required, but it is the no-code tool that puts an otherwise code-only workflow in front of the people who own the work. It is **one application** that builds up to three kinds of things, chosen on its Setup card:

- a **prompt template for LLMs** lets prompt engineers test new prompts across LLMs, compare the results, version their experiments, and turn the best prompt into a model for further consumption in the platform (for example in SAS Intelligent Decisioning);
- a **decision template for decision models** does the same for the typed questions a decision model answers — see [Decision Definitions](./Decision-Definitions.md);
- a **RAG setup** lets a data or decision engineer configure a document pipeline — sources, chunking, embedding model, vector store — save it as a governed RAG setup, and generate the ingestion job and SAS Studio flow that run it. This part is the **RAG Builder**.

One single-file build, one Job Execution definition and one Data-Driven Content object serve all three. Which of them a deployment offers is decided by three switches among the object's options (see [Configuration](#configuration-properties-panel)): decision templates are on by default, prompt optimization and RAG are off until an administrator switches them on. RAG additionally needs its runtime in SAS Content — see [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md).

:::note Standalone build
The Prompt Builder is shipped as a **standalone application** in the [`LLM-Prompt-Builder`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/LLM-Prompt-Builder) directory of this repository. It has no dependency on the SAS Portal Framework for SAS Viya — it is a single self-contained HTML file that you embed directly in a SAS Visual Analytics report through SAS Job Execution. Up to release 2.2 the RAG Builder was a second application with a build, a job definition and a report of its own; all three are retired, and a site that still has them follows the upgrade steps of the 2.3.0 [changelog](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CHANGELOG.md).
:::

## Shortcut: import the transfer package (optional)

Rather than building the app and wiring it up by hand (steps 1–2), you can import the [`SAS-Agentic-AI-Accelerator-Prompt-Builder.json`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json) transfer package. It bundles the whole UI ready to run — the **Prompt Builder** folder, the **SAS Job Execution** definition that serves the single-file app, the optimization job and the **Visual Analytics report** that hosts the object. Import it and you can skip straight to [step 3](#3-provide-the-api-keys-via-the-credential-domain).

The package ships the report's Prompt Builder object pointing at a **placeholder host, `https://your-sas-viya-host`** — point it at your own environment before or after import.

A second package holds the **RAG runtime** — the RAG custom steps, `rag_core`, the ingestion and retrieval-test jobs and the retrieval model template, and no UI. It is only needed where RAG setups are built; [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md#what-gets-deployed) describes it.

### Option A — `mdb builders-import` (recommended)

With `mdb` installed and your `.env` filled in ([Setup SAS Model Manager](./Setup-SAS-Model-Manager.md#envSetup)), one command imports the Prompt Builder package and then the RAG runtime package, loads the release table the report binds to, points the Data-Driven Content URL and the host defaults at your server, and configures the object - from a file saved with `mdb options-save` when you pass one, else with the repository, the LLM and embedding projects, the SCR endpoint and the deployment type it discovers:

```bash
mdb builders-import --dry-run                     # what would happen
mdb builders-import                               # first install
mdb builders-import --options builder-options.json   # upgrade: keep this site's options
```

Name package files to import only one (`mdb builders-import SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json`). Afterwards continue at [step 4](#4-sas-environment-manager-configuration) (the Content Security Policy) and, for a first install, set the optional object options in [step 5](#5-add-the-object-to-a-visual-analytics-report). Without `--options` the three switches keep the package's defaults, so RAG and prompt optimization are off until you enable them there.

### Option B — SAS Environment Manager

Import the JSON from **SAS Environment Manager → Content → Import**. Then correct the host in [step 5](#5-add-the-object-to-a-visual-analytics-report): open the report and update the Data-Driven Content object's base URL to your SAS Viya server.

### Option C — SAS Viya CLI

Use the `sas-viya` command-line interface's `transfer` plugin. This assumes you have already installed the CLI, created a connection profile, logged in and installed the `transfer` plugin as described in [Introduction — SAS Viya CLI Setup](./Introduction.md#sas-viya-cli-setup). In the commands below substitute `https://viya.example.com` with your own SAS Viya URL.

1. **Point the package at your host.** Replace the placeholder in the JSON so the imported report loads the app from your server (the percent-encoded program path after the host is left untouched):

   ```bash
   # bash / Linux / macOS
   sed -i 's#https://your-sas-viya-host#https://viya.example.com#' SAS-Agentic-AI-Accelerator-Prompt-Builder.json
   ```

   ```powershell
   # PowerShell / Windows
   (Get-Content SAS-Agentic-AI-Accelerator-Prompt-Builder.json) `
     -replace 'https://your-sas-viya-host','https://viya.example.com' |
     Set-Content SAS-Agentic-AI-Accelerator-Prompt-Builder.json
   ```

2. **Upload** the package — the command prints the new package's `id`:

   ```bash
   sas-viya transfer packages upload --file SAS-Agentic-AI-Accelerator-Prompt-Builder.json
   ```

3. **Import** it by that id:

   ```bash
   sas-viya transfer packages import --id <package-id>
   ```

   Or do both in one go, capturing the id (requires [`jq`](https://jqlang.github.io/jq/)):

   ```bash
   pkg=$(sas-viya --output json transfer packages upload \
     --file SAS-Agentic-AI-Accelerator-Prompt-Builder.json | jq -r '.id')
   sas-viya --output text transfer packages import --id "$pkg"
   ```

   (On older `transfer` plugin versions the same commands are `sas-viya transfer
   upload` / `sas-viya transfer import` — they still work, marked deprecated.)

:::note The host is a transfer substitution
The placeholder lives in the package's `substitutions` block (the `VisualElement:ve9` value), which is exactly what SAS's import machinery is designed to remap. Editing the string before upload (step 1 above) is the simplest fix. Alternatively, remap it at import time with a **mapping file**: pass `--mapping mapping.json` to the upload and the CLI writes a JSON file whose `substitutions` block contains the `VisualElement:ve9` entry with the placeholder URL — edit that value to your host, then pass the same file to the import:

```bash
sas-viya transfer packages upload --file SAS-Agentic-AI-Accelerator-Prompt-Builder.json --mapping mapping.json
# edit mapping.json: replace https://your-sas-viya-host in the VisualElement:ve9 value
sas-viya transfer packages import --id <package-id> --mapping mapping.json
```

(`sas-viya transfer get-mapping --id <package-id>` regenerates the file for an already-uploaded package.) If you do neither, the report still imports; you then correct the object's base URL in [step 5](#5-add-the-object-to-a-visual-analytics-report).
:::

After import, continue at [step 3](#3-provide-the-api-keys-via-the-credential-domain) — the Job Execution definition and report already exist under **SAS Content > SAS Agentic AI Accelerator > Prompt Builder**.

:::warning A package from before 2.3.0 brings the old RAG UI back
Packages exported before release 2.3.0 hold a report named **RAG Builder** and a job definition **RAG-Builder-UI** — the standalone RAG UI that is retired. Importing such a file through SAS Environment Manager or the CLI puts both back. `mdb builders-import` names such a package before it imports it; use the packages of a current checkout.
:::

## Preserving builder options across a report import

:::danger Importing a report overwrites your configuration
Every option you set on the Prompt Builder object — repository and project IDs, the SCR endpoint, the credential domain, the three switches, and for RAG the content root, the ingestion compute context and which vector stores the deployment offers — is stored **inside the Visual Analytics report**, not alongside it. A transfer package carries those values too, so importing a newer version of the report **replaces your settings with whatever the package was built against**.

This does not fail loudly. The report still opens and the app still runs; it simply shows the package's defaults — RAG switched off, no project IDs. Save your options before importing.
:::

Two `mdb` commands cover this (see [Model Definition Builder](./Model-Definition-Builder.md) for installing the CLI and its `[viya]` extra):

```bash
# BEFORE importing a new report package
mdb options-save --file builder-options.json

# AFTER the import
mdb options-restore --file builder-options.json --dry-run   # check what would change
mdb options-restore --file builder-options.json             # write it back
```

`options-save` discovers the Model Manager repository and the LLM and embedding projects exactly as `mdb setup` does, then overlays whatever your live **Prompt Builder** report already holds — so a deployment that has been tuned is captured as tuned, not as freshly bootstrapped. Keep the resulting file with your deployment records; it supersedes the `llm-prompt-builder.json` seed as the record of how this environment is configured.

`options-restore` writes back **only the options named in the file**. The newly imported report keeps its new layout, data items and objects, and regains your configuration. It also replaces the shipped placeholder host `your-sas-viya-host` - in the object's Data-Driven Content URL and in the `viyaHost` / `SCREndpoint` defaults - with `SAS_VIYA_URL`, so a report imported by hand needs no URL edit afterwards (`mdb builders-import` does both in one run).

Points worth knowing:

- **The report is matched by name** (`Prompt Builder`). If you have renamed the report in your deployment, rename it in the file to match, or the restore skips it.
- **An option the report does not have is reported, not inserted.** That usually means the option was renamed or removed in the newer version of the app — worth reading the message rather than ignoring it. When the missing option is one the current Prompt Builder report has, the report on the site is an older one: everything it can hold is still written, and the command ends with exit code 1 so that the gap is not taken for a finished restore — import the Prompt Builder package of this release and restore again. It is also why the shipped report already holds every option, including the ones that only show while a switch is on.
- **A switch is saved as the word `true` or `false`**, which is what its checkbox in the Options pane stores. A file saved with 2.2.x holds the same two words for `enableOptimization`, so it restores unchanged.
- **`sslVerify` is saved and restored like every other option**, as the same two words. A file saved with 2.2.x does not name it, and nothing supplies a value for it: the imported report keeps its own default, which is ticked. A package is exported with the option ticked: on a report that would ship it switched off, `mdb package-export` ends with exit code 1 and writes nothing.
- **API keys are deliberately not saved.** This file is meant to be kept with deployment paperwork and reviewed in a diff, which is no place for a provider key. The Prompt Builder reads its keys from the credential domain at run time — see [Managing Credentials](./Managing-Credentials.md).
- **The file records which deployment it came from.** Restoring it against a different SAS Viya URL still works — moving configuration between environments is a legitimate thing to do — but the command says so first, because doing it by accident is exactly the failure this guards against.
- **Concurrent edits fail loudly.** The write is conditional on the report being unchanged since it was read, so a colleague editing the report in Visual Analytics at that moment causes an error rather than silently losing their work.

On a brand-new environment with no live report to capture from, run `mdb options-save` anyway: with nothing to overlay it writes the discovered values alone, which is the same content the seed file carries.

### The retired RAG Builder report

Up to release 2.2 the RAG Builder had a report of its own, named **RAG Builder**, with its own options. A site that upgrades still has it, and its options are carried into the Prompt Builder report by a **fold** that all three commands share:

- `mdb options-save` reads the live `RAG Builder` report when one exists and writes **one** `Prompt Builder` section. Run it **before** the import and before deleting the old report — afterwards the new report holds its own defaults, and what the Prompt Builder report holds always wins over the old report. A site that imported first still has a way back while the old report exists: `options-save` names every value of the old report that it did not carry, with what the old report held. Copy the ones you want into `builder-options.json` and run `mdb options-restore`.
- `mdb options-restore` and `mdb builders-import --options` fold a file saved with 2.2.x, which has a `RAG Builder` section, in the same way before they write. Nothing is ever written to the retired report, and nothing is deleted.

What the fold does, and prints as it does it:

| In the old report | In the Prompt Builder section |
| --- | --- |
| The RAG options (`embeddingProjectID`, `contentRoot`, `casServer`, `storeSslmode`, `deletedPolicy`, `retainDays`, `recordHistory`, `embedReplicas`, `persistElements`, `persistChunks`, `enable_pgvector`, `enable_singlestore`) | Carried under the same name with the stored value, where the Prompt Builder section has none |
| `computeContext` | Becomes `ragComputeContext`. It is never written to the Prompt Builder's `computeContext`, which is the context of the optimization job |
| `viyaHost`, `modelRepositoryID`, `SCREndpoint`, `deploymentType`, `credentialDomain` | The Prompt Builder value is kept; a different value in the old report is printed. If the credential domains differ, the RAG part now reads its secrets from the kept one — add the vector-store entries to it. If the kept value is `none`, there is no domain to add them to and RAG cannot be used: the message names the domain of the old report — set `credentialDomain` to it in the file before restoring, or in the Options pane afterwards. Models without an API key are called as before |
| `enabledBackends` | Dropped — it has been unused since the per-store switches exist |
| *(nothing)* | `enableRag` is set to `true` when the site had the report `RAG Builder` — live, or as the `RAG Builder` section of a file saved from one — and only if the Prompt Builder section does not hold an `enableRag` yet. The sign is a content root or a CAS server in the old section, which the report shipped with 2.2.x holds from the start |

The old report does not record whether anyone used it, so the fold switches RAG on at every site that had it. **A site that does not use RAG** sets `"enableRag": "false"` in the `Prompt Builder` section of `builder-options.json` before restoring, or clears **Enable RAG setups** in the Options pane afterwards. A 2.2.x file that was saved while no `RAG Builder` report existed holds only discovered values in its `RAG Builder` section; `enableRag` is then not set and RAG stays off.

While a report named `RAG Builder` is still on the site, `options-restore` and `builders-import` say so on every run: it keeps serving the old standalone UI with its own options. Once the Prompt Builder report shows your RAG setups, delete the report **RAG Builder** and the job definition **RAG-Builder-UI** next to it (SAS Environment Manager → Content, or SAS Drive), and give the people who used it Read access to the **Prompt Builder** folder if they did not have it.

## 1. Get the single-file app

You have two options:

**Option A — use the prebuilt file (recommended).** Download the ready-to-use [`dist/index.html`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/dist/index.html) from the repository. No Node.js or build step is required — skip straight to step 2. Or copy the code from there to your clipboard and move to step 2.

**Option B — build it yourself.** With Node.js (`^20.19 || >=22.12`) installed:

```bash
git clone https://github.com/sassoftware/sas-agentic-ai-accelerator.git
cd sas-agentic-ai-accelerator/LLM-Prompt-Builder
npm install
npm run build
```

This produces the same single-file `dist/index.html`, with all three kinds in it — there is no separate build for the RAG Builder. See the [project README](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/README.md) for local development and customization details.

## 2. Create a SAS Job Execution definition

The single-file app is served to Visual Analytics through **SAS Job Execution**:

1. In **SAS Data and AI Studio**, click on *New > Job definition* to create a new **Job Definition**
2. In the *Job Definition (edit)* tab go to the right hand side bar and click on the *first icon (Associate a Form)* and change it from **Prompt designer to HTML form**.
2. Copy the full contents of `dist/index.html` and paste it into the *Job Definition (edit)* tab
3. Save the job to the place you want it in SAS Content - note that people that should be using it need read access.
4. On the right hand side bar click the *second icon (Properties)*, expand the *Details* section and copy the value under *Job URL* - it should look something like this: `https://your-viya-host/SASJobExecution/SASJobExecution/?_program=path-to-your-job-definition` after the path value add the following: `&_action=form`, as this enables us to view the HTML form without having to provide any SAS code. Save that URL as we will need it in fifth step.

:::note Why the build base64-encodes its scripts
SAS Job Execution serves HTML through a Go template engine that treats `{{ … }}` as directives. The build base64-encodes every inline `<script>` and decodes it at runtime so the minified bundle — which inevitably contains `{{`/`}}` — is not corrupted. This is automatic and requires no action.
:::

## 3. Provide the API keys via the credential domain

The Prompt Builder resolves provider API keys from a **SAS Viya credential domain** (default `agentic-ai-keys`) under the identity of the signed-in user — a user credential overrides a group credential, and models the user holds no key entry for are shown disabled with a note. RAG setups take the vector-store credentials, and where each store lives, from the same domain. Set the domain up once with the CLI-based scripts as described in [Managing Credentials](./Managing-Credentials.md); no data source, table, or report assignment is involved.

:::warning Keys stay governed
Keys live encrypted in the Credentials service — never in the report definition, the URL, or a data table. Who holds a credential in the domain (directly or via group membership) is who can run paid model calls.
:::

## 4. SAS Environment Manager configuration

To let the app run inside SAS Visual Analytics, set the following Content Security Policy directives via **SAS Environment Manager → Configuration → View Definitions**.

**SAS Visual Analytics** — `sas.commons.web.security` → *SAS Visual Analytics* → `content-security-policy`:

```
default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src * blob: data:; frame-src * blob: data: mailto:; connect-src 'self' *.sas.com login.microsoftonline.com graph.microsoft.com *.arcgis.com *.arcgisonline.com; object-src 'none'
```

**SAS Job Execution** — `sas.commons.web.security` → *SAS Job Execution* → `content-security-policy` (replace `<sas-viya-host>` with your environment's URL). The `'unsafe-inline'` and `'unsafe-eval'` in `script-src` are what allow the base64-decoded single-file bundle to run:

```
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://<sas-viya-host>; style-src 'self' 'unsafe-inline'; img-src * blob: data:; child-src 'self' blob: data: ; frame-ancestors 'self'; form-action 'self';
```

**IFrame sandbox** — verify the `sas.visualanalytics` definition's *IFrame Sandbox Attribute Value* contains at minimum `allow-same-origin allow-scripts`.

:::note
That sandbox does not include `allow-popups`, which is why the links that leave the page — "Open in SAS Model Manager", the link to a saved prompt-test, the links to a decision in the delete dialogs — copy their URL instead of forcing a new tab on a normal click (use the browser's right-click → *Open link in new tab*). Add `allow-popups` to the *IFrame Sandbox Attribute Value* to let these links open a tab directly. A link is copied only where no tab opened: with `allow-popups` a click opens the tab and leaves the clipboard alone.

The page carries the referrer policy `same-origin`, a `meta` element in its head: what it asks of another origin than its own carries no `Referer`, what it asks of its own origin keeps it. A link to another site — the user guide link of the *Optimize* step — therefore does not tell that site the address of your SAS Viya host. A tab the page opens on SAS Viya itself, such as SAS Model Manager, is told where the click came from, as before, and the calls to SAS Viya keep their `Referer`; that holds where the page is served from the SAS Viya origin, as in the setup described on this page, and not where the `viyaHost` parameter names another origin. A call to a model container on another host carries no `Referer` either, but that host still learns where the page runs: a browser names the origin of the page in the `Origin` header of every cross-origin call, whatever the referrer policy.
:::

Restart the following kubernetes pods of your SAS Viya environment if saving the changes didn't trigger a restart automatically:
- sas-job-execution
- sas-job-execution-app
- sas-logon-app
- sas-visual-analytics
- sas-visual-analytics-app

Example kubectl command:
```bash
kubectl get pods -n <your-namespace> -o name | grep -E 'pod/sas-job-execution|pod/sas-logon-app|pod/sas-visual-analytics' | xargs kubectl delete -n <your-namespace>
```

## 5. Add the object to a Visual Analytics report

If you imported the SAS-Agentic-AI-Accelerator-Prompt-Builder.json package then open up the SAS Visual Analytics report under SAS Content > SAS Agentic AI Accelerator > Prompt Builder > Prompt Builder and then continue.

1. In a Visual Analytics report, add a **Data-Driven Content** object and in the **Options** pane under Web Content enter the URL from the previous step or if you imported it update the base URL to your SAS Viya server.
2. Assign the **release table** to the object: in the **Data** pane pick `Public.ACCELERATOR_RELEASES` (loaded by `mdb setup` or `mdb load-releases`; the library and name follow `SAS_CAS_LIBRARY` / `SAS_RELEASES_TABLE`) and add any of its columns to the object's role. A Data-Driven Content object does not render until it has a data assignment; the Prompt Builder itself reads nothing from it, and provider keys still come from the credential domain (step 3). The table carries the accelerator's changelog - one row per release and per change, with a `component` column - so the same report can show what changed for the Prompt Builder next to the object.
3. Open the object's **Properties** panel and set the configuration values (see below).

### Configuration (Properties panel)

The object publishes **one list of options**, in the order of the tables below: first the options every kind shares, then the three switches, each directly followed by the options that only matter while it is on. Visual Analytics writes every value into the object's URL under the **URL parameter** name and reloads the app, which is how a changed switch makes its options appear or disappear.

The five environment-specific values are exactly those captured in your `llm-prompt-builder.json` — the file produced in the [Setup SAS Model Manager](./Setup-SAS-Model-Manager.md) chapter (regenerate it by re-running `mdb setup` if you have lost it), where each stands under the name of its URL parameter: `modelRepositoryID`, `llmProjectID`, `embeddingProjectID`, `SCREndpoint` and `deploymentType`. `mdb builders-import` writes the same five into the report by itself, so copying them by hand is only needed for a report you built yourself.

**Shared by every kind** — always shown:

| Properties-panel field | URL parameter | Default | Meaning |
|---|---|---|---|
| SAS Viya host | `viyaHost` | the embedding origin | SAS Viya base URL. Leave blank to default to the embedding origin. |
| Model Manager repository ID | `modelRepositoryID` | *blank* | Model Manager repository in which new prompt projects, decision projects and RAG projects are created. **Required by every kind.** |
| LLM project ID | `llmProjectID` | *blank* | Model Manager project holding the available LLM definitions (each with an `options.json`). Required for prompt and decision templates; a page that only builds RAG setups can leave it blank. |
| SCR endpoint | `SCREndpoint` | *blank* | Base URL of the SCR endpoint hosting the LLM and decision model containers and, for RAG, the embedding containers. Required for prompt and decision templates; RAG reads a blank value as `<SAS Viya host>/llm`, the host being the *SAS Viya host* option or the address the page is opened at. For a RAG setup the one resolved value goes to the ingestion job, *Test retrieval*, the Embed step of the generated SAS Studio flow and the retrieval model, so it has to be reachable from the compute session of the ingestion and from the publishing destination — set the option where the address of the page is not. |
| Deployment type | `deploymentType` | `k8s` | `k8s` or `aca` (Azure Container Apps / Instances) — how the model containers are deployed. See [Container Deployment](./Container-Deployment.md). |
| Default judge model | `judgeModel` | *blank* | Optional. Name of an LLM in the LLM project used by default for the [LLM-as-a-Judge](../User-Guide/Prompt-Builder.md) comparison; prompt engineers can still override it in the app. Leave blank for no default. |
| Model card report URI | `modelCardReportURI` | *blank* | Optional. A SAS Visual Analytics report path (`/reports/reports/<uuid>`). When set, manifesting the best prompt embeds that report on its model card as the custom chart, hosted from the *SAS Viya host* above. Leave blank to omit. |
| Credential domain | `credentialDomain` | `agentic-ai-keys` | Credential domain that provider API keys and, for RAG, the vector-store credentials resolve from (see step 3). Enter `none` when only key-less self-hosted models are used: no credential is then looked up for prompt and decision templates. **RAG needs a domain** — with `none` its card is shown disabled with that reason. |
| Verify TLS certificates in manifested models | `sslVerify` | ticked | A checkbox. Ticked: a model manifested in the Prompt Builder verifies the certificate of the model container it calls. Not ticked: models manifested from then on do not verify it, carry the tag `TLS-Verification-Off` in SAS Model Manager, and the step that manifests them shows a warning. See [below](#verify-tls-certificates-in-manifested-models). |

#### Verify TLS certificates in manifested models {#verify-tls-certificates-in-manifested-models}

Three kinds of manifested model make an HTTPS call themselves when they are scored: a prompt template manifested with *Include the LLM call in the manifested model*, a decision template, and the retrieval model of a RAG setup. Each verifies the certificate of what it calls against the CA bundle of the SAS Viya deployment. `sslVerify` decides what is written into the score code of such a model when it is manifested:

- **It is a default written at manifest time.** Changing the option changes the models manifested from then on. A model that is already manifested keeps what it was manifested with, in SAS Model Manager and on every destination it is published to, until it is manifested again.
- **It changes nothing else.** The calls the Prompt Builder page makes in the browser, the ingestion job of a RAG setup, and a prompt manifested for the *Call LLM* node (where the node makes the call) are as before.
- **The SAS Studio custom step has a checkbox of its own.** A retrieval model registered with *RAG - Register Setup* is not manifested on this page, and the step does not read `sslVerify`. Its checkbox **Verify TLS certificates in the registered retrieval model** (*Runtime* section, ticked by default) writes the same line into the same score code and sets and removes the same tag — see [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md#tls-register-setup-step).
- **A destination can overrule it in both directions** with an environment variable: `LLMCONTAINERSSLVERIFY` for prompt and decision templates, `RAGEMBED_SSLVERIFY` and `RAGSTORE_SSLVERIFY` for the retrieval model. `true` switches verification on for a model manifested with it off, `false` the reverse — see [Deployment of Decisions](./Deployment-of-Decisions.md#tls-default-at-manifest-time).
- **Off is visible.** A model manifested while the option is not ticked carries the tag `TLS-Verification-Off`, on all three kinds, and manifesting it again with the option ticked removes the tag. The step shows a warning before the click that names this option and the variable that turns verification back on: *Finalize* for a decision template, *Finalize* for a prompt only while **Include the LLM call in the manifested model** is ticked, and *Manifest setup* for a RAG setup. [Finding the models that do not verify](./Deployment-of-Decisions.md#finding-the-models-that-do-not-verify) lists such models and names the line to look for in a score code.
- **A report saved before the option existed shows the checkbox ticked**, and so verifies. The state of the checkbox is stored with the report: it is there again after the report is saved and reopened.

Leave it ticked unless a destination cannot verify. The case it exists for is SAS Micro Analytic Service running Python in a container that does not have the CA bundle; [Publishing to SAS Micro Analytic Service: the certificate bundle](./Deployment-of-Decisions.md#mas-certificate-bundle) describes it, the fix that keeps verification on, and when switching it off is acceptable — a test system on a closed cluster network, not production.

In the URL the option reads like a switch: Visual Analytics writes `sslVerify=true` or `sslVerify=false`; in a URL written by hand `0`, `no` and `off` also switch verification off, in any case. A blank, missing or any other value keeps it on.

**The three switches** — always shown, each a checkbox:

| Properties-panel field | URL parameter | Default | Ticked | Not ticked |
|---|---|---|---|---|
| Enable decision templates | `enableDecision` | ticked | The Setup card offers decision templates for decision models, with the question designer, the **Evaluate** step and the typed manifest. | The kind is not offered and nothing is shown for it. The *Decision Model Project* is not looked up and no decision model is called. Existing decision projects and templates are not listed; they stay untouched in SAS Model Manager. |
| Enable prompt optimization (DSPy) | `enableOptimization` | not ticked | Prompt templates gain the **Optimize** step, and the three `Optimization:` options below appear. See [Enabling Prompt Optimization](./Enabling-Prompt-Optimization.md). | No Optimize step, and no request to SAS Job Execution or to the folder of the optimize job. |
| Enable RAG setups | `enableRag` | not ticked | The Setup card offers the RAG setup, and the `RAG:` options below appear. Nothing is requested for RAG until a user chooses that kind — the RAG Builder is loaded on first use. | The kind is not offered; nothing of the RAG Builder is loaded and no RAG project, embedding project, CAS library or content folder is requested. Stored RAG setups stay untouched. |

A switch that is off costs nothing at run time, which is the point of having it: a site that does not publish decision models, or has no vector store, should neither show the kind nor make requests that can only fail. With one kind left, the question *What are you building?* is not shown at all.

The report stores a switch as the word `true` or `false`. Up to release 2.2 **Enable prompt optimization (DSPy)** was a dropdown with *Enabled* and *Disabled* that stored the same two words, so a report saved then keeps its setting: a stored `true` shows as a ticked checkbox.

**Only while prompt optimization is enabled:**

| Properties-panel field | URL parameter | Default | Meaning |
|---|---|---|---|
| Optimization: compute context | `computeContext` | *blank* | SAS Compute context the optimization job runs in. Its Python needs `dspy`. Not used by RAG. |
| Optimization: job path | `optimizeJobProgram` | *blank* | SAS Content path of the optimize job definition, e.g. `/Public/Jobs/Optimize-Prompt-DSPy`. |
| Optimization: minimum samples | `minOptimizeSamples` | `30` | Minimum dataset rows before an optimization run is allowed; the Optimize panel warns below 50. |

**Only while RAG is enabled** — what each one governs is explained in [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md):

| Properties-panel field | URL parameter | Default | Meaning |
|---|---|---|---|
| RAG: embedding model project ID | `embeddingProjectID` | *blank* | Model Manager project holding the registered embedding models. The RAG Builder lists that project instead of asking for a model name; without it no embedding model can be chosen and no setup can be saved. |
| RAG: content root | `contentRoot` | `/SAS Agentic AI Accelerator/RAG` | SAS Content folder the `deploy-rag-content` scripts populated. Generated job definitions go to its `generated` subfolder unless a setup names another. |
| RAG: CAS server | `casServer` | `cas-shared-default` | CAS server of the ingestion ledger and the pipeline tables. |
| RAG: ingestion compute context | `ragComputeContext` | *blank* | SAS Compute context the ingestion job and the retrieval test run in. It **must run as the requesting user** — *SAS Studio compute context* does, the stock *SAS Job Execution compute context* does not. Launching and testing are refused until it is set. |
| RAG: vector store TLS | `storeSslmode` | `prefer` | How the ingestion connects to the vector store: `prefer`, `require`, `verify-ca`, `verify-full`, `allow` or `disable`. Stores other than PostgreSQL read `disable` as no TLS and anything else as TLS on. |
| RAG: when a document disappears from the source | `deletedPolicy` | `retire` | `retire` keeps its chunks as history that no retrieval returns; `purge` removes them for good. |
| RAG: keep retired chunks for (days) | `retainDays` | `0` | Retired chunk generations older than this are dropped after each load. `0` keeps them forever. |
| RAG: record run history | `recordHistory` | on | A checkbox. Writes `rag_runs`, `rag_doc_events` and `rag_configs` beside the collection and publishes the first two to CAS. |
| RAG: embedding container replicas | `embedReplicas` | `1` | How many replicas of the embedding container the deployment runs; the ingestion sizes its parallel calls from it. |
| RAG: save the element table to disk | `persistElements` | `1` | `1` saves `<prefix>_ELEMENTS` to disk, `0` keeps it in memory only. The value is recorded on each setup; the SAS Studio flow that *Manifest setup* generates does not apply it yet and keeps this table in memory either way. |
| RAG: save the chunk table to disk | `persistChunks` | `1` | The same for `<prefix>_CHUNKS`; in the generated flow it reaches the enrichment step only so far. The ledger and the embedded chunks are always saved. |
| RAG: offer pgvector (PostgreSQL) | `enable_pgvector` | on | A checkbox per vector database: whether users may choose it for a new setup. A setup that already uses a withheld database keeps it and says so. |
| RAG: offer SingleStore | `enable_singlestore` | on | As above. |

:::note Two compute contexts, two options
`computeContext` is the context of the **optimization** job and `ragComputeContext` the context of the **RAG ingestion**; neither is ever read for the other. They are kept apart because they usually cannot be the same context: the optimization job is commonly run in the stock *SAS Job Execution compute context*, which runs its servers as a service account — and an ingestion cannot reuse its CAS session there (see [the two prerequisites](./RAG-Ingestion-and-Retrieval.md#two-prerequisites-that-are-not-optional)). Before release 2.3.0 the RAG Builder's own report called its context `computeContext` too; `mdb options-save` renames it when it carries that report's options over.
:::

:::info
Until the object is configured it shows a **"Configuration required"** message and does not call SAS Viya, so it never fails against placeholder IDs. The message names what is missing for each side, and completing either is enough for the page to load: prompt and decision templates need the repository ID, the LLM project ID and the SCR endpoint; RAG setups need the repository ID, **Enable RAG setups** ticked and a credential domain other than `none`. A site with no LLM project therefore gets the RAG Builder alone.

Every field can also be appended to the object's URL by hand using its **URL parameter** name above (for example `&judgeModel=...&modelCardReportURI=/reports/reports/<uuid>`), which is a reliable fallback if your Visual Analytics version does not render the options panel. For a switch, Visual Analytics writes `true` for a ticked checkbox and `false` for one that is not; in a URL written by hand a switch also reads `1`, `yes` or `on` as on and `0`, `no` or `off` as off. Anything else, and a blank or missing value, is its default.
:::
