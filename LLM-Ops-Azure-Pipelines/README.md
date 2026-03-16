# LLMOps with Azure DevOps Pipelines

Learn to automate the complete LLM deployment lifecycle using Azure DevOps pipelines. This exercise transforms the manual process into an automated, repeatable CI/CD workflow.

Key Concepts:

- Automate LLM model wrapper registration and publishing
- Deploy LLM containers to Azure using pipelines
- Automate LLM containers scoring and testing

Table of Contents:

<!-- starttoc -->
* [Pipeline A: LLM Model Deployment](#pipeline-a-llm-model-deployment)
* [Approach A: Deploy to Azure Container Instances/Apps](#approach-a-deploy-to-azure-container-instancesapps)
  * [Architecture Diagram](#architecture-diagram)
  * [Pipeline Steps](#pipeline-steps)
* [Prerequisites](#prerequisites)
  * [Azure OpenAI](#azure-openai)
  * [Azure DevOps](#azure-devops)
    * [Configuring Azure DevOps Library](#configuring-azure-devops-library)
  * [Azure Resources](#azure-resources)
  * [SAS Viya](#sas-viya)
  * [Local Tools (for testing)](#local-tools-for-testing)
* [Pipeline YAML: LLM Deployment (ACI)](#pipeline-yaml-llm-deployment-aci)
  * [Summary of the YAML structure](#summary-of-the-yaml-structure)
    * [Stage 1 — Register](#stage-1-register)
    * [Stage 2 — Publish](#stage-2-publish)
    * [Stage 3 — Deploy](#stage-3-deploy)
    * [Stage 4 — Test](#stage-4-test)
<!-- endtoc -->

---

## Pipeline A: LLM Model Deployment

**Purpose:** Register validated LLM wrappers, publish container images to ACR, deploy one private ACI per configured model, and emit a reusable endpoints artifact.

**Inputs:**

- Workshop repository containing the validated Azure Pipelines YAML
- SAS Viya credentials from the `sas-viya-credentials` variable group
- Trusted certificate from Azure DevOps Secure Files
- Azure subscription service connection and self-hosted agent pool (`pool` pipeline variable)
- Parameterized model list with model names, CPU, memory, prompts, and options

**Outputs:**

- One private Azure Container Instance per configured model
- `llm-endpoints.json` pipeline artifact with model name, container deployed name, private IP, and endpoint URL
- Optional per-model scoring output when `RUN_TESTS=true`

**Stages:**

1. **Register** - Install Python dependencies, download the trusted certificate, clone the accelerator repository, create the LLM Model Project, and register all configured wrappers in SAS Model Manager.
2. **Publish** - Clone the accelerator repository, publish only missing model images to ACR, and wait until all required repositories appear.
3. **Deploy** - Create or reuse the delegated ACI subnet, deploy one container per configured model, wait for a healthy running state plus private IP assignment, and publish the endpoints artifact.
4. **Test** (optional) - Download the endpoints artifact and run scoring calls against every configured model endpoint.

**Validated default model set:**

- `gpt_4o_mini_az_2024_07_18` - Azure OpenAI-backed wrapper, 2 CPU / 4 GiB
- `qwen_25_05b` - local-style wrapper, 4 CPU / 16 GiB
- `phi_3_mini_4k` - local-style wrapper, 4 CPU / 16 GiB

---

## Approach A: Deploy to Azure Container Instances/Apps

**Best for:** Quick MVP, simple deployments, cost-effective testing

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Azure DevOps Pipeline                             │
│  ┌────────────┐    ┌─────────────┐    ┌──────────────┐               │
│  │  Register  │ →  │   Publish   │ →  │   Deploy     │               │
│  │  (Python)  │    │  (SCR+ACR)  │    │   (az CLI)   │               │
│  └────────────┘    └─────────────┘    └──────────────┘               │
└──────────────────────────────────────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│                       Azure Resources                                │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  Azure Container Registry (ACR)                         │         │
│  │  - gpt_4o_mini_az_2024_07_18:latest                     │         │
│  │  - qwen_25_05b:latest                                   │         │
│  │  - phi_3_mini_4k:latest                                 │         │
│  └─────────────────────────────────────────────────────────┘         │
│                          ↓                                           │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  VNET: prefix-vnet                                      │         │
│  │  ┌───────────────────────────────────────────────────┐  │         │
│  │  │ Subnet: prefix-cont-subnet (ACI)                  │  │         │
│  │  │                                                   │  │         │
│  │  │  ACI: prefix-gpt   -> gpt_4o_mini_az_2024_07_18   │  │         │
│  │  │  ACI: prefix-qwen  -> qwen_25_05b                 │  │         │
│  │  │  ACI: prefix-phi   -> phi_3_mini_4k               │  │         │
│  │  │  Private IP per container, port 8080              │  │         │
│  │  └───────────────────────────────────────────────────┘  │         │
│  └─────────────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────────┘
```

### Pipeline Steps

| **Step** | **Action** | **Tool** | **Duration** |
|----------|------------|----------|--------------|
| 1 | Install Python dependencies in Register and Publish jobs | `pip install` | 1-2m |
| 2 | Download `gelenv_trustedcerts.pem` from Secure Files | DownloadSecureFile task | 20s |
| 3 | Clone `sas-agentic-ai-accelerator` and checkout `bteleuca_contributions` | git | 30s |
| 4 | Create the LLM Model Project in SAS Model Manager | `Model-Manager-Setup.py` | 1-2m |
| 5 | Register all configured wrappers from the `models` parameter | `register-LLMs.py` | 1-2m |
| 6 | Verify model wrapper names selected from pipeline parameters | shell + `jq` | 10s |
| 7 | Publish only missing model images to ACR | `publish-LLMs.py` + Azure CLI | 5-15m |
| 8 | Poll ACR until every required image is available (up to 40 checks) | Azure CLI | 1-20m |
| 9 | Create or reuse the delegated ACI subnet | `az network vnet subnet create` | 30s |
| 10 | Deploy one private ACI per configured model with per-model CPU and memory settings; skip healthy existing containers | `az container create --no-wait` + Azure CLI checks | 2-5m |
| 11 | Wait for all containers to be running and to receive private IPs | Azure CLI | 2-15m |
| 12 | Build `llm-endpoints.json` with model name, container name, IP, and endpoint | `jq` + Azure CLI | 30s |
| 13 | Delete any stale `llm-endpoints` artifact and publish a fresh one | REST API + PublishPipelineArtifact | 20s |
| 14 | Test every deployed endpoint using the configured prompts (optional stage) | `curl` + `jq` | 1-3m |
| **Total** | | | **~15-30 minutes** |

**Behavior of the validated ACI pipeline:**

- The pipeline runs on the self-hosted pool provided through `$(pool)`.
- `PREFIX` is sourced from the `sas-viya-credentials` variable group, and `PREFIXNODASH` is derived in YAML with `replace()`.
- Model deployment is parameter-driven, so adding or removing models is done by editing the `models` object.
- Reruns are safer: the publish stage skips repositories already present in ACR, and the deploy stage skips containers that are already healthy and have a private IP.
- The artifact published for downstream use is `llm-endpoints`, which contains `llm-endpoints.json`.

## Prerequisites

### Azure OpenAI

Before starting this exercise, ensure you have deployed an **Azure OpenAI** resource (in the same subscription and region as your SAS Viya environment). Retrieve:

- **Azure OpenAI** endpoint URL
- **Azure OpenAI** API key (maps to the `AZURE_OAI_KEY` variable in the `sas-viya-credentials` variable group)

### Azure DevOps

**MUST HAVE** a variable group **sas-viya-credentials**. Check the variable values match your SAS Viya deployment.

- Azure DevOps organization
- [ ] Azure DevOps Project: e.g. `PSGEL313`
- [ ] Service connection to Azure subscription where SAS Viya is deployed. Set azureSubscription to your Azure DevOps service connection name, replace in the pipeline the placeholder `my_service_connection`.
- [ ] Variable group: `sas-viya-credentials` with:
  - `PREFIX`
  - `SAS_VIYA_URL`
  - `SAS_USER`
  - `SAS_PASS`
  - `AZURE_OAI_KEY`
- [ ] Secure files library with:
  - `gelenv_trustedcerts.pem`
- [ ] Self-hosted agent configured (required, private VNET access). The Jump VM deployed with SAS Viya can be used as the self-hosted agent.
- [ ] Pipeline variable: `pool` set to the self-hosted agent pool name

Certificate must be downloaded from the SAS Viya Kubernetes cluster (using SCP, or a similar method) and then uploaded to Azure DevOps Secure files.

#### Configuring Azure DevOps Library

The pipelines require credentials and certificates stored in Azure DevOps Library for secure access:

1. **Variable Group (`sas-viya-credentials`):**
   - Go to **Pipelines** → **Library** → **+ Variable group**
   - Name: `sas-viya-credentials`
   - Add these variables:
     - `PREFIX` - Workshop prefix used to derive Azure resource names (for example, `${PREFIX}-rg` and `${PREFIXNODASH}acr`)
     - `SAS_VIYA_URL` - Your SAS Viya environment URL (e.g., `https://$RG.gelenable.sas.com`)
     - `SAS_USER` - Your SAS username (e.g., `sasdemo`)
     - `SAS_PASS` - Your SAS password (mark as secret 🔒)
     - `AZURE_OAI_KEY` - Your Azure OpenAI API key (mark as secret 🔒)
   - Click **Save**

2. **Secure Files (`gelenv_trustedcerts.pem`):**
   - Go to **Pipelines** → **Library** → **Secure files** → **+ Secure file**
   - Upload the certificate file you downloaded from SAS Viya e.g. `gelenv_trustedcerts.pem`
     - This certificate enables secure HTTPS connections to SAS Viya

**Note:** Pipelines mentioned in this readme must run **on the self-hosted agent** (Jump VM) because the SAS Viya endpoint is on a private network. Microsoft-hosted agents cannot reach the private URL. The pipeline uses the secure file path directly at runtime and does not require manual certificate installation in the agent home directory.

### Azure Resources

- [x] Resource group: `${PREFIX}-rg`
- [x] Azure Container Registry: `${PREFIXNODASH}acr`
- [x] Virtual Network: `${PREFIX}-vnet`
- [x] Jump VM: `${PREFIX}-jump-vm` (for scoring tests)

### SAS Viya

- [x] SAS Model Manager with permissions
- [x] SAS Intelligent Decisioning with permissions
- [x] Publishing destination configured: `AzureCLI`
- [x] CAS server: `cas-shared-default`
- [x] Caslib: `Public`

### Local Tools (for testing)

- [x] Azure CLI 2.50+
- [x] kubectl 1.27+ (required if you use kubectl-based workflow to retrieve `gelenv_trustedcerts.pem` from the SAS Viya Kubernetes cluster)
- [x] SAS Viya CLI 1.33+
- [x] Python 3.11+
- [x] jq (JSON processor)

---

## Pipeline YAML: LLM Deployment (ACI)

**File:** [llm-deployment-aci.yml](llm-deployment-aci.yml)

This validated pipeline is the current reference implementation for the ACI approach. It uses a parameterized `models` list, publishes only missing images, deploys private ACI containers inside the workshop VNET, and emits the `llm-endpoints` artifact for downstream consumers.

### Summary of the YAML structure

This Azure DevOps pipeline registers, publishes, deploys, and tests multiple LLM wrappers into SAS Viya — all from a parameter list. One run, four stages, multiple models. Let me show you how it works.

Pipeline parameters.

Here is everything the end user touches. A list of models. Each one has:

- Name, which must match the sub-folder name from [LLM-Definitions](../LLM-Definitions) e.g. phi_3_mini_4k.
- CPU, memory for the Azure Container Instance. Currently max for CPU is 4, and memory can be up to 16Gi.
- userPrompt
- systemPrompt
- options

For the last three fields, see each LLM wrapper's sub-folder from [LLM-Definitions](../LLM-Definitions).

You edit this YAML list, you click Run.

The sample pipeline deploys three models today: one proprietary wrapper hitting an external API,  gpt_4o_mini_az_2024_07_18 from Azure OpenAI, and two open-source models — Qwen and Phi — that carry their own weights, downloaded from HuggingFace.

#### Stage 1 — Register

The Register stage sets up Python, authenticates to SAS Viya, creates the LLM model project if it doesn't already exist, and registers each wrapper in SAS Model Manager.

#### Stage 2 — Publish

Publish builds a container image for each model and pushes it to Azure Container Registry. But first, it checks. If an image already exists in ACR for that model version, it skips it.

The pipeline detects the existing image and moves on. No rebuild, no wasted time. For the models that do need publishing — especially open-source ones carrying large weight files — this is the longest stage.

#### Stage 3 — Deploy

Deploy creates or reuses a container subnet in Azure, then spins up one Azure Container Instance per model. Each gets its own private IP. The pipeline waits for every container to report healthy before moving on.

The pipeline also writes an llm-endpoints.json artifact — model name, endpoint URL, ready for downstream usage.

#### Stage 4 — Test

The final stage is optional but I always leave it on. It sends a scoring request to each deployed endpoint using prompts defined in the pipeline parameters.

Four stages. Multiple models. Existing images skipped. Private endpoints deployed and tested. One pipeline run.

---