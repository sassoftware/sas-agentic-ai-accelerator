---
sidebar_position: 10
---

# Deploying the LLM Prompt Builder

This step is not required, but the LLM Prompt Builder is a no-code tool that lets prompt engineers test new prompts across LLMs, compare the results, version their experiments, and turn the best prompt into a model for further consumption in the platform (for example in SAS Intelligent Decisioning).

:::info Importing the Prompt Builder
The Prompt Builder can also be imported via the [SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json) transfer package, that you can import using the SAS Environment Manager Import page, if you do you can skip ahead to step 3.
:::

:::note Standalone build
The Prompt Builder is shipped as a **standalone application** in the [`LLM-Prompt-Builder`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/LLM-Prompt-Builder) directory of this repository. It has **no longer has any dependency on the SAS Portal Framework for SAS Viya** — it is a single self-contained HTML file that you embed directly in a SAS Visual Analytics report through SAS Job Execution.
:::

## 1. Get the single-file app

You have two options:

**Option A — use the prebuilt file (recommended).** Download the ready-to-use [`dist/index.html`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/dist/index.html) from the repository. No Node.js or build step is required — skip straight to step 2. Or copy the code from there to your clipboard and move to step 2.

**Option B — build it yourself.** With Node.js (`^18 || ^20 || >=22`) installed:

```bash
git clone https://github.com/sassoftware/sas-agentic-ai-accelerator.git
cd sas-agentic-ai-accelerator/LLM-Prompt-Builder
npm install
npm run build
```

This produces the same single-file `dist/index.html`. See the [project README](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/README.md) for local development and customization details.

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

## 3. Create the API Key data source

The API keys used for the Prompt Builder are stored in a CAS table, this enables you to create one report with applied row-level-security (please add your own columns, the script below only provides the bare bones) and also enables easy key rotation by updating the table. Replace the key values with your API keys and also if you do not want store it in your CASUSER change the CAS Library.

```sas
cas mySess;

data work.LLM_API_KEYS;
    length KeyName $ 64 KeyValue $ 512;
    label KeyName  = "Key Name"
          KeyValue = "API Key";
    infile datalines dlm='|' truncover;
    input KeyName $ KeyValue $;
    datalines;
Anthropic|REPLACE_WITH_YOUR_ANTHROPIC_API_KEY
OpenAI|REPLACE_WITH_YOUR_OPENAI_API_KEY
Google|REPLACE_WITH_YOUR_GOOGLE_API_KEY
;
run;

proc casUtil inCASLib='casuser' outCASLib='casuser';
    dropTable casData='LLM_API_KEYS' quiet;
    load data=work.LLM_API_KEYS casOut='LLM_API_KEYS';
    promote casData='LLM_API_KEYS' casOut='LLM_API_KEYS';
    save casData='LLM_API_KEYS' casOut='LLM_API_KEYS' replace;
quit;

cas mySess terminate;
```

### API keys (assigned data)

The API keys are the entries under `API_KEYS` in that same `llm-prompt-builder.json`. They are supplied through the object's **assigned data** — never the URL or Properties panel — so they never appear in the report definition or a shareable link. Assign a data source with **two columns**, one provider per row:

| Column | Meaning |
|---|---|
| 1st | Key **name** — the `API_KEYS` entry name, which must match the `API_KEY.default` value referenced by an LLM's `options.json` (e.g. `Anthropic`, `OpenAI`, `Google`). |
| 2nd | Key **value** — the actual API key. |

:::warning Keep keys governed
Keeping API keys in a governed CAS/data source (rather than the URL) means they are never persisted in the report definition or a shareable link. Restricting read access to that data source restricts who can run paid model calls.
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
That sandbox does not include `allow-popups`, which is why the "Open in SAS Model Manager" link copies the URL instead of forcing a new tab on a normal click (use the browser's right-click → *Open link in new tab*). Add `allow-popups` to the *IFrame Sandbox Attribute Value* to let that link open a tab directly.
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
2. **Assign the API-key data source** (see below) to the object's data role. If you moved the CAS table from CASUSER to another place you will have to replace the data source.
3. Open the object's **Properties** panel and set the configuration values (see below).

### Configuration (Properties panel)

The environment-specific values below are exactly those captured in your `llm-prompt-builder.json` — the file produced in the [Setup SAS Model Manager](./Setup-SAS-Model-Manager.md) chapter (regenerate it with the [`prompt-builder-json.py`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/utility#prompt-builder-json) utility if you have lost it). Copy each value from that file into the object's **Properties** panel:

| Properties-panel field | `llm-prompt-builder.json` key | Meaning |
|---|---|---|
| SAS Viya host | *(not in the file)* | SAS Viya base URL. Leave blank to default to the embedding origin. |
| Model Manager repository ID | `modelRepositoryID` | Model Manager repository in which new prompt projects are created. |
| LLM project ID | `llmProjectID` | Model Manager project holding the available LLM definitions (each with an `options.json`). |
| SCR endpoint | `SCREndpoint` | Base URL of the SCR endpoint hosting the LLM containers. |
| Deployment type | `deploymentType` | `k8s` (default) or `aca` (Azure Container Apps / Instances). See [Container Deployment](./Container-Deployment.md). |

:::info
Until these values are supplied the object shows a **"Configuration required"** message and does not call SAS Viya, so it never fails against placeholder IDs. The same parameters can also be appended to the object's URL by hand, which is a reliable fallback if your Visual Analytics version does not render the options panel.
:::