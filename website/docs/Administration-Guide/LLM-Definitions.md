---
sidebar_position: 6
title: LLM Definitions
---

This documentation page contains information about the folder *LLM-Definitions*, which in turn contains information on how to add LLMs to the repository in the SAS Model Manager. Each model is packaged so that it can be deployed using the SAS Container Runtime (SCR). More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

Each subfolder there contains the definition for one specific LLM - the name of the folder is the model id. A folder is generated from its `definition.yaml` by the [Model Definition Builder](./Model-Definition-Builder.md) (`mdb add`, `mdb generate`); the files inside - the score script, `inputVar.json`/`outputVar.json`, `modelConfiguration.json`, `options.json`, `requirements.json`, `README.md`, `Model-Card.md` - are derived from it and should not be edited by hand. The `_Base_Definition` folder is a reference copy of that file set from before `mdb`, kept for orientation.

## Tags

Tags are being used to provide additional information and filtering options around the LLMs inside of SAS Model Manager. Below you'll find a table with short description and its impact (if any):

| Tag         | Description                                                  | Impact                                                       |
| ----------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| Open-Source | Indicates that the model has an open-source license          | -                                                            |
| Proprietary | Indicates that the model is proprietary                      | -                                                            |
| deprecated  | Indicates that the models is no longer supported             | The model will not show up in the Prompt Builder UI          |
| small       | Indicates the required resources for serving this model is small | Can be used for when publishing to SCR as a sizing indication |
| medium      | Indicates the required resources for serving this model is medium | Can be used for when publishing to SCR as a sizing indication |
| large       | Indicates the required resources for serving this model is large | Can be used for when publishing to SCR as a sizing indication |
| LLM         | Indicates that the model has more than 7 billion parameters  | -                                                            |
| SLM         | Indicates that the model has less than or equal to 7 billion parameters | -                                                            |

There is a lot more tags available like MIT-License, Apache-2, Google, etc. these are used to showcase the specific model license and the model provider but have no further impact and new once are added as the market evolves.

## Models that require the Hugging Face token

Here is a list of models in this repository that are [gated](https://huggingface.co/docs/hub/en/models-gated) on Hugging Face and thus require you to first accept a license - this is sometimes also related to a waiting time until you are confirmed for access. These three still download their weights during the container build (their `requirements.json` is hand-maintained); the supported way to serve a gated model is to stage the weights on the shared volume instead - see [Serving Open-Weight Models](./Serving-Open-Weight-Models.md). Llama 3.3 70B is served through OpenRouter and needs no Hugging Face access.

| Model Name     | Model Provider | Hugging Face Link                                           | Note                                                         |
| -------------- | -------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |
| Llama 3.2 1B   | Meta           | https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct     |                                                              |
| Llama 3.2 3B   | Meta           | https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct     |                                                              |
| Mistral Nemo   | Mistral        | https://huggingface.co/mistralai/Mistral-Nemo-Instruct-2407 | While it runs on just CPU a hosting provider is recommended. |