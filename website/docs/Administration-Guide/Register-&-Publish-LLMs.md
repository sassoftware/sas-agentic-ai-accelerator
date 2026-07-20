---
sidebar_position: 5
title: Register & Publish LLMs
---

## Registering LLM Definitions

The folder *LLM-Definitions* contains a definition per model, each packaged so that it can be deployed using the SAS Container Runtime (SCR). More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

Registering and publishing is done with the **`mdb`** command line, part of the [Model Definition Builder](Model-Definition-Builder.md). If you have not installed it yet:

```bash
pip install -e Model-Definition-Builder/cli[viya]
```

To register one or more models into SAS Model Manager:

```bash
# Register specific models
mdb register gpt_41_mini claude_sonnet_4_5

# ...or register every managed LLM and Embedding definition
mdb register --all
```

`mdb register` reads the SAS Viya connection from your `.env` (see [Providing credentials without the command line](./Setup-SAS-Model-Manager.md#envSetup)); if the password is not supplied you are prompted for it securely. It creates the `LLM Repository` and the model projects on first use if they do not exist yet, so a fresh environment needs no separate setup step.

:::tip Updating an already-registered model
Re-run with `--update` to refresh a model in place — a new minor version, the content replaced, and the attributes and tags refreshed, without deleting and recreating it:

```bash
mdb register gpt_41_mini --update
```
:::

If you want to add your own LLM, use `mdb add` (an interactive wizard, or non-interactive flags) rather than editing files by hand, and remember to contribute back. If you are adding a new proprietary model provider, the `API_KEY` option's default should be set to the name of the provider, which also needs to be added to the LLM Prompt Builder object definition.

## Publishing the LLMs to the SCR Destination

Once the models are registered, publish them to the SCR publishing destination:

```bash
# Publish specific models and wait for each image build to finish
mdb publish gpt_41_mini claude_sonnet_4_5 --wait

# ...or publish everything
mdb publish --all --wait
```

The `--destination` (`-d`) can be given on the command line or, more conveniently, set once as `SAS_PUBLISH_DESTINATION` in your `.env`. `--wait` polls the SCR image build to completion so you learn immediately whether it succeeded.

:::tip Register and publish in one step
`mdb ship <model_id>` runs a live provider smoke test, `register --update` and `publish --wait` in a single, resumable command:

```bash
mdb ship gpt_41_mini
```
:::

Run any command with `--help` for the full set of options.
