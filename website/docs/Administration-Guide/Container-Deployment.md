---
sidebar_position: 8
---

## Configurations for SCR deployment

For the creation of the SAS Container Runtime publishing destination it is recommended to use the [SAS Viya CLI](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calcli/titlepage.htm). The command detailed below is used to create a publishing destination that is using a Azure Container Registry:

### Kubernetes deployment

There are example deployment YAMLs provided in the *SCR-LLM-Deployment-YAML* folder which are build for Azure. The following assumptions are made:

-   Namespace called *llm* in which the SCR containers will be deployed.
-   No resource limits are currently imposed, that is why we recommend having a separate node pool for this workload - in non production environments it is recommended to use a spot-instance with a lot of available CPU and RAM, e.g. Standard_D64s_v5.
-   The URL endpoint schema looks like this *host/llm/model_name* here the container will be reached, that means the full address for a container is *host/llm/model_name/model_name*.

### Azure Container Apps/Instances

If you want to deploy the LLM containers as Azure Container Apps or Azure Container Instances than please make sure that when you follow the [Deploying the LLM Prompt Builder](./Setup-Additional-UIs.md) you set the object's *deploymentType* to *aca* (the value recorded in your `llm-prompt-builder.json`).

Please also note that the attribute *SCREndpoint* contains the value *randomString.region.azurecontainerapps.io* from the https://model.randomString.region.azurecontainerapps.io/model URL of your Azure Container App.