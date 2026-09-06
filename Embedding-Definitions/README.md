# Embedding Definitions

This folder contains information on how to add Embedding models to the repository in the SAS Model Manager. Each model is packaged so that it can be deployed using the SAS Container Runtime (SCR).

More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

Each subfolder here contains the definition for one specific Embedding model - the name of the folder is the model id. A folder is generated from its `definition.yaml` by the [Model Definition Builder](../Model-Definition-Builder/README.md); the other files are derived from it and are not edited by hand.

## Adding a new Embedding model

```bash
mdb add                                                   # wizard; known embedding models are labelled [embedding]
mdb add openai text-embedding-3-small --id text_embedding_3_small --yes
mdb add hf-selfhosted --repo ibm-granite/granite-embedding-small-english-r2 --runtime sentence-transformers --id granite_embedding_small_r2 --yes
mdb add voyage voyage-3.5 --id voyage_35 --yes
```

Pass `--kind embedding` when the catalog cannot tell (an unknown model entered by hand). After adding, check `Embedding_Length` and `Input_Token_Limit` in `definition.yaml`, then `mdb generate`, `mdb sync`, `mdb validate --live` and `mdb test` as for an LLM.

## Azure embedding deployments

`mdb add azure-foundry --kind embedding --resource <res> --deployment <name>` builds an
embedding definition on the `emb_azure_openai_v1` template: the deployment name goes in
the request body, the key travels in Azure's `api-key` header, and the resource, API
style and optional gateway endpoint are read from the container's environment - never
from a scoring option. See *Azure definitions and the container environment* in
[LLM-Definitions/README.md](../LLM-Definitions/README.md) for the variables and the
deploy template. `text-embedding-3-*` deployments may declare the `dimensions` option.
