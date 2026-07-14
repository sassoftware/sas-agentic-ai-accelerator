---
sidebar_position: 4
title: Setup SAS Model Manager
---

The SAS Agentic AI Accelerator will create an additional model repository in your environment called **LLM Repository**. This repository will be used both to store the project which contains the LLMs, all the different prompting projects, the embedding models and the RAG setups. The project is created using the script `Model-Manager-Setup.py` which is located in the root folder of the repository.

This script creates the new SAS Model Manager repository and the SAS Model Manager projects for you that serve as the home for all LLM and Embedding related models. You need to run the script from within the locally cloned version of this repository. Make sure that the Python environment that was created during the [initial setup](Introduction.md) is still active - check out [Providing credentials without the command line](#envSetup) for how to move things into a `.env` instead of as CLI arguments:

```bash
# Run the setup script with the help (-h) flag to get more information on each parameter
# Run the setup script - make sure to update the parameter values that are passed into the script
# If you are planning on deploying your LLM containers not in kubernetes but rather as Azure Container Apps/Instances use -dt aca
python ./Model-Manager-Setup.py -vs sas-viya-url -u username -p password -rp responsible_party -e endpoint_from_scr_deployment
```

Running this script will produce two additional json files as outputs, that are required for the steps on the page [Setup Additional UIs](Setup-Additional-UIs.md):
- *llm-prompt-builder.json*, this will enable your users to do No-Code Prompt Engineering.
- *rag-builder.json*, this will enable your users to do No-Code RAG pipeline setups.

Explanation of the different available options:
- -vs, short for --viya-server, is the URL for your SAS Viya server. The argument is required.
- -u, short for --username, is the username to authenticate with SAS Viya. The argument is required.
- -p, short for --password, is the password to authenticate with SAS Viya. The argument is required.
- -rp, short for --responsible_party, is the person or group that will be listed in SAS Model Manager as a point of contact. The argument is required.
- -e, short for --scr_endpoint, is the base URL where the LLM containers will be published. The argument is required.
- -dt, short for --deployment_type, can be set to k8s (default) if you are deploying the LLM containers to kubernetes or aca if you are deploying the LLM containers to Azure Container Apps/Instances. This argument is optional as it defaults to k8s. For k8s please paste the full link e.g. https://base-url/llm and for Azure Container Apps please only provide the following *randomString.region.azurecontainerapps.io* from the https://model.randomString.region.azurecontainerapps.io/model URL.
- -k, short for --verify_ssl, should only be changed to false if you have a self-signed certificat on SAS Viya that your machine doesn't recognize. This argument is optional as it defaults to true.

### Providing credentials without the command line {#envSetup}

Every Python setup script (`Model-Manager-Setup.py`, `register-LLMs.py`, `publish-LLMs.py`, `register-Embedding.py`, `publish-Embedding.py` and `utility/prompt-builder-json.py`) can read any parameter from an **environment variable** or a **`.env` file** instead of the command line. This keeps your credentials out of your shell history and the process list. The order of precedence is: command-line argument, then environment variable, then `.env` file, then the built-in default.

To use a `.env` file, install the optional dependency and copy the template:

```bash
# Install the optional python-dotenv package
pip install python-dotenv
# Copy the template and edit it with your values
cp .env.example .env
```

The available variables (documented in `.env.example`) map to the arguments as follows:

| Environment variable | Replaces argument |
|---|---|
| `SAS_VIYA_URL` | `-vs` / `--viya_server` |
| `SAS_VIYA_USER` | `-u` / `--username` |
| `SAS_VIYA_PASSWORD` | `-p` / `--password` |
| `SAS_VIYA_VERIFY_SSL` | `-k` / `--verify_ssl` |
| `SAS_SCR_ENDPOINT` | `-e` / `--scr_endpoint` |
| `SAS_DEPLOYMENT_TYPE` | `-dt` / `--deployment_type` |
| `SAS_RESPONSIBLE_PARTY` | `-rp` / `--responsible_party` |
| `SAS_PUBLISH_DESTINATION` | `-d` / `--destination` |

The `.env` file is git-ignored, so your credentials are never committed. If the password is not supplied by any source, the scripts prompt for it securely instead of failing. The model lists (`-l` / `--llms`, `-m` / `--embedding_models`) stay on the command line as they change per run. With the connection details in `.env`, the setup command shortens to:

```bash
python ./Model-Manager-Setup.py
```

### Authorizing the Repository

By default newly created SAS Model Manager repositories are only authorized for access for the *SAS Administrators* group, please adjust the access rights as you require it for your environment - it is recommended add authorization on a group basis.

![LLM Repository Default Authorization](../../static/LLM-Repository-Default-Authorization.png)

Running the Model Manager setup script will produce a file called *sas-viya-cli-commands.txt* which contains the following groups and rules as a template to apply authorization to your environment. Of course this is just a basic template, please read through it carefully and adjust it to your needs:

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
