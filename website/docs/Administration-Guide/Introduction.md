---
sidebar_position: 1
title: Introduction
---

The Administration Guide of the SAS Agentic AI Accelerator will walk you through the setup of this project.

Setting everything up will require you to have the following permissions or someone that does:
- Your user must be part of the SAS Administrators group.
- You have to have the right to do changes to the kubernetes cluster.
- You need the ability to setup a Docker compliant container registry.

## Setup

Please follow these steps, step by step - in order to complete this setup you need both the ability to create a new Repository in the SAS Model Manager (this requires SAS Administrators rights by default), be able to modify the SAS Viya deployment, be able to access a Container Registry and be able to deploy containers in Kubernetes - usually this setup requires both a SAS administrator and a Kubernetes administrator.
In order to be able to run this you will need access to a machine with the following tools available:
- Python 3.10 or newer, for the `mdb` command line (the [Model Definition Builder](./Model-Definition-Builder.md)) that registers and publishes the models
- kubectl
- SAS Viya CLI with the authorization, identities, models and transfer plugins installed

As a first step clone this repository to the machine and change into the directory:
```bash
# Clone the repository
git clone https://github.com/sassoftware/sas-agentic-ai-accelerator.git
# Change into the directory
cd sas-agentic-ai-accelerator
```

### Python Setup
```bash
# [Optional] Create a new Python environment
python -m venv .venv
# [Optional] Showcasing the activation on Windows; on Linux/macOS: source .venv/bin/activate
.venv/Scripts/activate
# Install the mdb command line with the Viya extra (sasctl) - keep the quotes
pip install -e "Model-Definition-Builder/cli[viya]"
mdb --help
```
Then copy `.env.example` to `.env` and fill in the SAS Viya connection - see [Setup SAS Model Manager](./Setup-SAS-Model-Manager.md#envSetup).

### SAS Viya CLI Setup:

1. Download the SAS Viya CLI from the SAS Support page for your operating system - https://support.sas.com/downloads/package.htm?pid=2512. Here is a link to the [SAS Documentation](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calcli/n01xwtcatlinzrn1gztsglukb34a.htm) for the CLI.
2. Next we need to setup up a connection profile in order to be able to connect with your SAS Viya environment:
```bash
sas-viya profile init
# Enter your SAS Viya host (service endpoint), select the output type (I recommend fulljson) and enable ANSI colored output (I recommend yes)
# If successful you should see a line like: Saved 'Default' profile to /path/.sas/config.json
# Next we need to login just to confirm the connection
sas-viya auth loginCode
# If successful you should see a line like: Login succeeded. Token saved.
```
3. Next we need to install a couple of plugins (authorization, identities, models & transfer) in order to set everything up correctly - if you want to learn more about the different plugins see the [SAS Documentation](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calcli/n1vth8mtb8ipprn1prz5j26p3nvc.htm):
```bash
# Installing the required plugins - even if you have setup the CLI before I recommend you do this to ensure the plugins are up to date
# First up is the authorization plugin
sas-viya plugins install -repo SAS authorization
# Next we install the identities plugin
sas-viya plugins install -repo SAS identities
# Then the models plugin
sas-viya plugins install -repo SAS models
# Finally the transfer plugin - used to import content packages such as the optional Prompt Builder (see "Deploying the Builder UIs")
sas-viya plugins install -repo SAS transfer
```
4. Now the CLI is ready and setup for you to continue on.