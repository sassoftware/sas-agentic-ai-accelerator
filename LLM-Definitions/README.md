# LLM Definitions

This folder contains information on how to add LLMs to the repository in the SAS Model Manager. Each model is packaged so that it can be deployed using the SAS Container Runtime (SCR).

More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

Each subfolder here contains the definition for one specific LLM - the name of the folder specifies the LLM.

## Tags

Tags are being used to provide additional information and filtering options around the LLMs inside of SAS Model Manager. Below you'll find a table with short description and its impact (if any):

| Tag         | Description                                                  | Impact                                                       |
| ----------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| Python      | Indicates that the model is implemented in Python            | This is required as the whole building process is setup around Python. |
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

Here is a list of models in this repository that are [gated](https://huggingface.co/docs/hub/en/models-gated) on Hugging Face and thus require you to first accept a license - this is sometimes also related to a waiting time until you are confirmed for access.

| Model Name     | Model Provider | Hugging Face Link                                           | Note                                                         |
| -------------- | -------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |
| Llama 3.1 405B | Meta           | https://huggingface.co/meta-llama/Llama-3.1-405B-Instruct   | It is recommended to use a hosting provider, instead of hosting it yourself. |
| Llama 3.2 1B   | Meta           | https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct     |                                                              |
| Llama 3.2 3B   | Meta           | https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct     |                                                              |
| Llama 3.3 70B  | Meta           | https://huggingface.co/meta-llama/Llama-3.3-70B-Instruct    | It is recommended to use a hosting provider, instead of hosting it yourself. |
| Mistral Nemo   | Mistral        | https://huggingface.co/mistralai/Mistral-Nemo-Instruct-2407 | While it runs on just CPU a hosting provider is recommended. |

