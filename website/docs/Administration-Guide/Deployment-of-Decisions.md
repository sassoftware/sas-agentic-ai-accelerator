---
sidebar_position: 11
---

This page details considerations when deploying the decisions in MAS/SCR.

Every prompt template checks for an environment variable called *LLMCONTAINERPATH*. If this variable does not exist it will default to the value that was provided as an endpoint for the LLM Prompt Builder where that prompt template was created. Setting this environment variable will enable you to deploy the same decision across multiple environments and also changing the LLM endpoints in case you have fully isolated environments.

## Kubernetes vs Azure Container Apps/Instances

Please note that it is not supported to change the type of deployment between environments so if you have the LLM containers deployed in Kubernetes in one environment, they will have to be deployed in the same fashion in all other environments as well.

If you have selected a Kubernetes based deployment set the variable value to the corresponding of this: *https://base-url/llm*.

If you have selected an Azure Container Apps/Instaces based deployment set the variable value to the corresponding value of this: *randomString.region.azurecontainerapps.io* from the https://model.randomString.region.azurecontainerapps.io/model URL.

## MAS vs SCR

Please note that you do not have to set this environment variable if you are ok with utilizing the hardcoded value provided by LLM Prompt Builder.

If you are deploying the decisions to a SCR destination than just add the *LLMCONTAINERPATH* environment variable as part of the **env** definition in the *YAML* file. That means you can give every instance potentially a different endpoint!

If you are deploying the decisions to MAS you will have to add the *LLMCONTAINERPATH* environment variable to the *sas-microanalytic-score* pod via a PatchTransformer. That means you can only have one instance of this per MAS deployment and if it is set once it will apply to all.

Here is an example of a PatchTransformer:
```yaml
apiVersion: builtin
kind: PatchTransformer
metadata:
  name: mas-llm-transformer
patch: |-  
  # Add LLM environment variable
  - op: add
    path: /spec/template/spec/containers/0/env/-
    value:
      name: LLMCONTAINERPATH
      value: <YOURVALUE>
target:
  group: apps
  kind: Deployment
  name: sas-microanalytic-score
  version: v1
```