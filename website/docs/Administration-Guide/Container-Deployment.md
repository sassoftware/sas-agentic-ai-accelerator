---
sidebar_position: 8
title: Container Deployment
---

## Configurations for SCR deployment

For the creation of the SAS Container Runtime publishing destination it is recommended to use the [SAS Viya CLI](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calcli/titlepage.htm). The command detailed below is used to create a publishing destination that is using a Azure Container Registry.

### Kubernetes Deployment

`mdb deploy <model_id> --registry <registry> --host <ingress-host>` renders a ready-to-apply manifest from the templates in the *SCR-LLM-Deployment-YAML* folder (see [Model Definition Builder](./Model-Definition-Builder.md#deployment-yaml-and-ci-pipelines)): `deploy-modelName-template.yaml` for hosted-API models, `deploy-modelName-env-template.yaml` for Azure definitions that read their resource, key and deployment from the container environment, and `deploy-modelName-PV-template.yaml` plus `llm-weights-pvc-template.yaml` / `stage-weights-job-template.yaml` for self-hosted models with staged weights ([Serving Open-Weight Models](./Serving-Open-Weight-Models.md)). The templates were written for Azure Kubernetes Service (spot-node affinity and tolerations) and are easily adapted. The following assumptions are made:

-   Namespace called *llm* in which the SCR containers will be deployed.
-   No resource limits are currently imposed, that is why we recommend having a separate node pool for this workload - in non production environments it is recommended to use a spot-instance with a lot of available CPU and RAM, e.g. Standard_D64s_v5.
-   The URL endpoint schema looks like this *host/llm/model_name* here the container will be reached, that means the full address for a container is *host/llm/model_name/model_name*.
-   The templates set `SAS_SCR_LOG_LEVEL_SCR_IO` to `TRACE`, which is what the [log parser](./Logging-&-Monitoring.md) reads - at that level every request and response, prompts included, is written to the container's standard output. Lower it if your logs must not carry prompt text, and accept that the usage report then has no token counts.
-   A republished image with the same `:latest` tag is only picked up when the pod is recreated. On a node pool without spare CPU a `kubectl rollout restart` can deadlock (the new pod cannot schedule while the old one holds the resources); scaling the deployment to 0 and back to 1 always works.

### Azure Container Apps/Instances

If you want to deploy the LLM containers as Azure Container Apps or Azure Container Instances than please make sure that when you follow the [Deploying the Builder UIs](./Setup-Additional-UIs.md) you set the object's *deploymentType* to *aca* (the value recorded in your `llm-prompt-builder.json`).

Please also note that the attribute *SCREndpoint* contains the value *randomString.region.azurecontainerapps.io* from the https://model.randomString.region.azurecontainerapps.io/model URL of your Azure Container App.