# GPT-4o-mini from Azure OpenAI

## Required Items

Azure OpenAI provides a REST API for interaction and response generation.

To use an Azure OpenAI model, you need:

- A resource. See [Create and deploy an Azure OpenAI in Azure AI Foundry Models resource](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal).
- A deployment. See below
- The endpoint and API key.

## Retrieve your Azure OpenAI Key and Endpoint

Steps:

1. On the Azure portal:
   * Search for **Azure OpenAI**.
1. Locate your service.
1. Expand **Resource Management** > **Keys and Endpoint**.
    - **Endpoint**: Found in the **Keys & Endpoint** section of the Azure portal. Example: `https://westus3.api.cognitive.microsoft.com/`. If the location, e.g. `westus3` is different, **you must adapt the `location`** variable in *gpt4oMiniScore.py*!
    - **API Key**: Found in the **Keys & Endpoint** section. Copy any of the keys. You will need it when you will score the model (and through it the Azure OpenAI API endpoint).


## Create a Model Deployment

Before using the Azure OpenAI resource, deploy a Large Language Model (LLM).

1. Inside your Azure OpenAI resource from the left blade, from **Overview** click on **Go to Azure AI Foundry**. You will be redirected to [Azure AI Foundry](https://ai.azure.com), where you can manage your LLMs and Azure AI Assistants.

1. **Create a deployment**.
1. Type **gpt-4o-mini**
   * Select **Model**: **gpt-4o-mini** > Confirm.
   * Deployment name: **gpt-4o-mini** (**MUST USE THIS EXACT NAME**).
   * **Deployment type**: Global Standard.
   * **Model version**: Choose the default (e.g., 2024-07-18).
   * Location: **West US 3** (**WE RECOMMEND YOU USE THIS LOCATION**). If the location, e.g. `westus3` is different, **you must adapt the `location`** variable in *gpt4oMiniScore.py*!
   * Tokens per Minute Rate Limit: Choose around 250K, if possible.
   * Click **Deploy**.

The chat playground will open. Feel free to ask a question to test the deployment.

