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
    - **Endpoint**: Found in the **Keys & Endpoint** section of the Azure portal. Example: `https://westus3.api.cognitive.microsoft.com/`.
    - **Note**: Save the full endpoint URL (e.g., `westus3.api.cognitive.microsoft.com`). You'll configure this as the `azure_openai_resource` option when scoring the model.
    - **API Key**: Found in the **Keys & Endpoint** section. Copy any of the keys. You will need it when scoring the model (passed as `API_KEY` in options).

## Create a Model Deployment

Before using the Azure OpenAI resource, deploy a Large Language Model (LLM).

1. Inside your Azure OpenAI resource from the left blade, from **Overview** click on **Go to Azure AI Foundry**. You will be redirected to [Azure AI Foundry](https://ai.azure.com), where you can manage your LLMs and Azure AI Assistants.

1. **Create a deployment**.
1. Type **gpt-4o-mini**
   * Select **Model**: **gpt-4o-mini** > Confirm.
   * Deployment name: **gpt-4o-mini** (**MUST USE THIS EXACT NAME**).
   * **Deployment type**: Global Standard.
   * **Model version**: Choose the default (e.g., 2024-07-18).
   * Location: **West US 3** (recommended, but any location works - see Configuration Options below).
   * Tokens per Minute Rate Limit: Choose around 250K, if possible.
   * Click **Deploy**.

The chat playground will open. Feel free to ask a question to test the deployment.

## Configuration Options

This model is **location-independent**. Configure your Azure OpenAI endpoint at runtime through the `options` parameter - no code changes required for different Azure regions.

### Required Options:

- **`azure_openai_resource`**: Your Azure OpenAI endpoint hostname (e.g., `westus3.api.cognitive.microsoft.com`.
   - Find this in the **Keys & Endpoint** section of your Azure OpenAI resource
   - Extract just the hostname from the full endpoint URL
   - Note, it will work with Azure AI Foundry resources (e.g., `your-resource-name.cognitiveservices.azure.com`) as well.
- **`API_KEY`**: Your API key from the Azure portal

### Optional Options:

- **`modelVersion`**: Deployment name (default: `gpt-4o-mini`)
- **`api_version`**: Azure OpenAI API version (default: `2025-01-01-preview`)
- **`temperature`**: Sampling temperature 0-2 (default: `1`)
  - Higher values (0.8) make output more random
  - Lower values (0.2) make output more focused and deterministic
- **`top_p`**: Nucleus sampling 0-1 (default: `1`)
  - Controls diversity via nucleus sampling
  - 0.1 means only tokens in top 10% probability are considered

### Example Options String:

```
{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

### Testing Locally:

To test the model wrapper locally, uncomment the example code at the bottom of `gpt4oMiniScore.py`:

```python
if __name__ == "__main__":
    userPrompt = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{azure_openai_resource:westus3.api.cognitive.microsoft.com,modelVersion:gpt-4o-mini,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-actual-key}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

Run: `python gpt4oMiniScore.py`

## Benefits of This Approach

- **Multi-region support**: Works with any Azure region without code changes
- **Configurable at runtime**: Endpoint and API version configured via options
- **Development flexibility**: Different endpoints for dev/test/prod environments
- **Future-proof**: Easy to update API versions as Azure releases new features.

