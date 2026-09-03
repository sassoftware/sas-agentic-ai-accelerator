# Embedding Definitions

This folder contains information on how to add Embedding models to the repository in the SAS Model Manager. Each model is packaged so that it can be deployed using the SAS Container Runtime (SCR).

More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

Each subfolder here contains the definition for one specific Embedding model - the name of the folder specifies the embedding model.

## Adding a new Embedding model

## Azure embedding deployments

`mdb add azure-foundry --kind embedding --resource <res> --deployment <name>` builds an
embedding definition on the `emb_azure_openai_v1` template: the deployment name goes in
the request body, the key travels in Azure's `api-key` header, and the resource, API
style and optional gateway endpoint are read from the container's environment - never
from a scoring option. See *Azure definitions and the container environment* in
[LLM-Definitions/README.md](../LLM-Definitions/README.md) for the variables and the
deploy template. `text-embedding-3-*` deployments may declare the `dimensions` option.
