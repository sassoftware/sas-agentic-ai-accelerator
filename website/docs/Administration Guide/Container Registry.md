---
sidebar_position: 2
---

It is recommended to create a new container registry that is only used for this project to be able to separate this from other container images or if you are reusing the container registry that is also used for SAS Viya, setting permissions is recommended.

This container registry will be configured as a publishing destination for SAS Viya, more specifically for the SAS Container Runtime (SCR) images.

In Azure you need to create an app registration beside the registry. This app registration will then get the permissions to push SCR containers to the registy.

Within the app registration, a client ID and a secret needs to be defined. These information's will be used later to create the publishing destination in SAS Viya.

Please see this SAS Communities article for further information's: https://communities.sas.com/t5/SAS-Communities-Library/How-to-Publish-a-SAS-Model-to-Azure-with-SCR-A-Start-to-Finish/ta-p/768714

## Create a SAS Container Runtime Publishing Destination

For more information about the publishing destination please refer to the [SAS Documentation](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calpubdest/titlepage.htm?requestorId=2482150b-5313-402c-b184-9172cb14226c).

For the creation of the SAS Container Runtime publishing destination it is recommended to use the [SAS Viya CLI](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calcli/titlepage.htm). The command detailed below is used to create a publishing destination that is using a Azure Container Registry - this is also availble for AWS, GCP and Private Docker:
```bash
sas-viya models destination createAzure \
    --name llmACR \
    --description "LLM Azure Container Registry Publishing Destination" \
    --baseRepoURL "<name>.azurecr.io" \
    --subscriptionId <azure-subscription-ID> \
    --tenantId <azure-tenant-ID> \
    --region "<azure-region>" \
    --kubernetesCluster "<kubernetes-cluster>" \
    --resourceGroupName "<resource-group>" \
    --credDomainID "LLMACRCredDomain" \
    --credDescription "Azure LLM ACR Credentials Domain" \
    --clientId <app-registration-client-ID> \
    --clientSecret <app-registration-client-secret> \
    --identityType group \
    --identityId <group-that-should-publish>
```

Please note if this command still asks you to enter something manually than a line break wasn't recognized correctly. If this is the case please remove the \ and put the command on one line.

If you prefer to use SAS Environment Manager to create the publishing destination than please refer to this guide in the [SAS Documentation](https://go.documentation.sas.com/doc/en/sasadmincdc/default/calpubdest/p02scrqf37kexwn1gi60khpshifz.htm?requestorId=2482150b-5313-402c-b184-9172cb14226c).