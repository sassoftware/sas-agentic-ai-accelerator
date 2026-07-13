---
sidebar_position: 10
---

# Deploying the LLM Prompt Builder

This step is not required, but the LLM Prompt Builder is a no-code tool that lets prompt engineers test new prompts across LLMs, compare the results, version their experiments, and turn the best prompt into a model for further consumption in the platform.

:::note Standalone build
The Prompt Builder is shipped as a **standalone application** in the [`LLM-Prompt-Builder`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/LLM-Prompt-Builder) directory of this repository. It has **no dependency on the SAS Portal Framework for SAS Viya** — it is a single self-contained HTML file that you embed directly in a SAS Visual Analytics report through SAS Job Execution.
:::

## 1. Get the single-file app

You have two options:

**Option A — use the prebuilt file (recommended).** Download the ready-to-use [`dist/index.html`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/dist/index.html) from the repository. No Node.js or build step is required — skip straight to step 2.

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

1. In **SAS Studio** or **SAS Environment Manager**, create a new **Job Definition**.
2. Use the full contents of `dist/index.html` as the HTML body of the job.
3. Save the job — its execution URL is what the Visual Analytics object will point to.

:::note Why the build base64-encodes its scripts
SAS Job Execution serves HTML through a Go template engine that treats `{{ … }}` as directives. The build base64-encodes every inline `<script>` and decodes it at runtime so the minified bundle — which inevitably contains `{{`/`}}` — is not corrupted. This is automatic and requires no action.
:::

## 3. Add the object to a Visual Analytics report

1. In a Visual Analytics report, add a **Data-Driven Content** object and point its **Content** at the Job Execution URL from the previous step.
2. **Assign the API-key data source** (see below) to the object's data role.
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

### API keys (assigned data)

The API keys are the entries under `API_KEYS` in that same `llm-prompt-builder.json`. They are supplied through the object's **assigned data** — never the URL or Properties panel — so they never appear in the report definition or a shareable link. Assign a data source with **two columns**, one provider per row:

| Column | Meaning |
|---|---|
| 1st | Key **name** — the `API_KEYS` entry name, which must match the `API_KEY.default` value referenced by an LLM's `options.json` (e.g. `Anthropic`, `OpenAI`, `Google`). |
| 2nd | Key **value** — the actual API key. |

You can create this table with the [`create-api-key-table.sas`](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/LLM-Prompt-Builder/create-api-key-table.sas) helper.

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

You are now set up.
