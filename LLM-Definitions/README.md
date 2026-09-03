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

## Azure definitions and the container environment

Where an Azure container sends its requests is a property of the deployment, not
of the caller, so it is never a scoring option. Every definition built on an Azure
template - `azure-foundry` chat and embedding, and `azure-foundry-env` - resolves
its connection as

    container environment variable > the definition's default

| Variable | What it sets |
| -------- | ------------ |
| `AZURE_OPENAI_RESOURCE` | The resource/project host that serves the deployment. Any Azure flavor (`openai.azure.com`, `cognitiveservices.azure.com`, `services.ai.azure.com`) or a bare resource name. Baked as the default only when the definition was created with `--commit-resource`. |
| `AZURE_OPENAI_API_VERSION` | Optional. Blank uses the GA v1 endpoint; a version selects the legacy `/openai/deployments/<name>/...` route some resources or policies still require. |
| `AZURE_OPENAI_ENDPOINT` | Optional. A full URL that replaces the built one - for a gateway in front of Azure. It must include the route. |

so **one published image serves any subscription or project**. `mdb deploy` renders
`SCR-LLM-Deployment-YAML/deploy-modelName-env-template.yaml` - the Deployment
that carries this env block - for every Azure definition. A value that resolves to
nothing fails naming the variable that would have supplied it, rather than as an
opaque 401 or 404 from Azure, and the resolved endpoint is logged on every call.

### Environment-configured definitions

A definition built on the **environment-configured** template (`mdb add
azure-foundry-env ...`, score template `azure_openai_env`) goes one step further:
the key and the deployment come from the container too, so one registered model
serves any Azure *model*, not just any subscription.

| Variable | What it sets |
| -------- | ------------ |
| `AZURE_OPENAI_API_KEY` | The key. Never baked into the image or the definition; the deploy template reads it from a Kubernetes secret. |
| `AZURE_OPENAI_DEPLOYMENT` | The deployment name - what Azure calls the model. Blank uses the definition's own default. |

Such a definition declares **no `API_KEY` option**, so the Prompt Builder neither
gates it on a credential-domain entry nor sends a key - the container supplies it.
Passing an `API_KEY` option anyway still wins, which keeps a credential-domain
caller working unchanged. Re-pointing a container at a different subscription,
project or model is a change to its environment plus a rollout restart - no
regeneration, rebuild or re-publish.

Because the payload is still built from the definition's `options`, a definition
whose default deployment is a reasoning model (`reasoning_effort`,
`max_completion_tokens`) cannot be re-pointed at a chat-only deployment that
rejects those parameters, and vice versa - keep one definition per option shape.

## Models that require the Hugging Face token

Here is a list of models in this repository that are [gated](https://huggingface.co/docs/hub/en/models-gated) on Hugging Face and thus require you to first accept a license - this is sometimes also related to a waiting time until you are confirmed for access.

| Model Name     | Model Provider | Hugging Face Link                                           | Note                                                         |
| -------------- | -------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |
| Llama 3.1 405B | Meta           | https://huggingface.co/meta-llama/Llama-3.1-405B-Instruct   | It is recommended to use a hosting provider, instead of hosting it yourself. |
| Llama 3.2 1B   | Meta           | https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct     |                                                              |
| Llama 3.2 3B   | Meta           | https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct     |                                                              |
| Llama 3.3 70B  | Meta           | https://huggingface.co/meta-llama/Llama-3.3-70B-Instruct    | It is recommended to use a hosting provider, instead of hosting it yourself. |
| Mistral Nemo   | Mistral        | https://huggingface.co/mistralai/Mistral-Nemo-Instruct-2407 | While it runs on just CPU a hosting provider is recommended. |

