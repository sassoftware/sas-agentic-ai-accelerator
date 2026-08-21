---
sidebar_position: 4
title: Setup SAS Model Manager
---

The SAS Agentic AI Accelerator will create an additional model repository in your environment called **LLM Repository**. This repository will be used both to store the project which contains the LLMs, all the different prompting projects, the embedding models and the RAG setups.

The repository and its projects are created by **`mdb setup`**, part of the [Model Definition Builder](Model-Definition-Builder.md). It creates the `LLM Repository`, the `LLM Model Project` and the `Embedding Model Project` if they do not already exist, and it is idempotent — running it again on an environment that is already set up changes nothing. (You do not have to run it explicitly at all: `mdb register` performs the same check automatically for the kind it registers. Running `mdb setup` up front is simply the tidiest way to bootstrap a fresh environment.)

Install the CLI once, then run setup from within the locally cloned repository. The connection details are read from a `.env` file — see [Providing credentials without the command line](#envSetup):

```bash
# Install the CLI with the Viya extra (one time)
pip install -e Model-Definition-Builder/cli[viya]

# Create the repository and both model projects, and write the seed files
mdb setup
```

`mdb setup` reads the SAS Viya connection from `SAS_VIYA_URL` / `SAS_VIYA_USER` / `SAS_VIYA_PASSWORD`, the point of contact from `SAS_RESPONSIBLE_PARTY`, the SCR base URL from `SAS_SCR_ENDPOINT`, and the deployment type from `SAS_DEPLOYMENT_TYPE` (`k8s` by default, or `aca` for Azure Container Apps/Instances). All of these live in your `.env`.

Running it produces two additional json files as outputs, that are required for the steps on the page [Deploying the Builder UIs](Setup-Additional-UIs.md):
- *llm-prompt-builder.json*, this will enable your users to do No-Code Prompt Engineering.
- *rag-builder.json*, this will enable your users to do No-Code RAG pipeline setups.

Use `--out <dir>` to write those files somewhere other than the current directory, or `--no-files` to create only the repository and projects. Run `mdb setup --help` for the full list.

### Providing credentials without the command line {#envSetup}

`mdb` reads any parameter from an **environment variable** or a **`.env` file** instead of the command line. This keeps your credentials out of your shell history and the process list. The order of precedence is: command-line argument, then environment variable, then `.env` file, then the built-in default.

To use a `.env` file, install the optional dependency and copy the template:

```bash
# Install the optional python-dotenv package
pip install python-dotenv
# Copy the template and edit it with your values
cp .env.example .env
```

The available variables (documented in `.env.example`) map to the arguments as follows:

| Environment variable | Purpose |
|---|---|
| `SAS_VIYA_URL` | SAS Viya server URL |
| `SAS_VIYA_USER` | Username to authenticate with |
| `SAS_VIYA_PASSWORD` | Password (prompted for if omitted) |
| `SAS_VIYA_VERIFY_SSL` | Set to `false` only for an unrecognized self-signed certificate |
| `SAS_SCR_ENDPOINT` | Base SCR endpoint URL |
| `SAS_DEPLOYMENT_TYPE` | `k8s` (default) or `aca` |
| `SAS_RESPONSIBLE_PARTY` | Point of contact recorded in Model Manager |
| `SAS_PUBLISH_DESTINATION` | Default SCR publishing destination (`mdb publish`, overridable with `-d`) |

The `.env` file is git-ignored, so your credentials are never committed. If the password is not supplied by any source, you are prompted for it securely instead of failing. With the connection details in `.env`, `mdb setup` needs no arguments at all.

### Authorizing the Repository

By default newly created SAS Model Manager repositories are only authorized for access for the *SAS Administrators* group, please adjust the access rights as you require it for your environment - it is recommended add authorization on a group basis.

![LLM Repository Default Authorization](../../static/LLM-Repository-Default-Authorization.png)

Running `mdb setup` will produce a file called *sas-viya-cli-commands.txt* which contains the following groups and rules as a template to apply authorization to your environment. Of course this is just a basic template, please read through it carefully and adjust it to your needs:

```bash
# Each command comes with a description, please read it and the documentation before running anything

# First a Custom Group is created called LLM Consumers - if you do not want use this group, skip this step and replace the name in subsequent commands
sas-viya identities create-group --id LLMConsumers --name "LLM Consumers" --description "This group enables a general access to the LLM repository. This group is meant for anybody that requires access to it."
# Add members to the LLM Consumers group
sas-viya identities add-member --group-id LLMConsumers --group-member-id GroupYouWantToAdd

# Second a Custom Group is created called Prompt Engineers - if you do not want use this group, skip this step and replace the name in subsequent commands
sas-viya identities create-group --id PromptEngineers --name "Prompt Engineers" --description "This group enables its members to create, update and delete Prompt Engineering projects in the LLM repository"
# Add members to the Prompt Engineers group
sas-viya identities add-member --group-id PromptEngineers --group-member-id GroupYouWantToAdd

# Create two rules that open up access to the LLM Repository for the LLM Consumers
sas-viya authorization create-rule -o /folders/folders/folder-uuid -g LLMConsumers -p Read,Add,Remove -d "Enables the LLM Consumers to interact with the LLM repository" --reason "You are not part of the LLM Consumers group"
sas-viya authorization create-rule --container-uri /folders/folders/folder-uuid -g LLMConsumers -p Read,Add,Update,Remove,Delete -d "Enables the LLM Consumers to interact with the LLM repository" --reason "You are not part of the LLM Consumers group"

# Create a rule to enable the Prompt Engineers to create new projects in the LLM repository
sas-viya authorization create-rule -o /modelRepository/repositories/repo-uuid -g PromptEngineers -p Read,Add,Create,Update,Remove,Delete -d "Enables the group to create prompt engineering projects in the LLM repository" --reason "You are not part of the prompt engineering group"
```
