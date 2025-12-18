# GPT-4.1 from Azure OpenAI / Azure AI Foundry

Source: https://ai.azure.com/explore/models/gpt-4.1/version/2025-04-14/registry/azure-openai

## Required Items

Azure OpenAI and Azure AI Foundry provide REST APIs for interaction and response generation.

To use a GPT-4.1 model, you need:

- A resource (Azure OpenAI or Azure AI Foundry). See [Create and deploy an Azure OpenAI resource](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal).
- A deployment of the GPT-4.1 model. See below.
- The endpoint and API key.

## Create a Model Deployment

Before using the Azure OpenAI or Azure AI Foundry resource, deploy the GPT-4.1 model.

### Option A: Deploy via Azure AI Foundry

1. Navigate to [Azure AI Foundry](https://ai.azure.com).
1. Select your project or create a new one.
1. **Create a deployment**:
   * Search for and select **GPT-4.1**.
   * Deployment name: **gpt-4.1** (**MUST USE THIS EXACT NAME**).
   * **Deployment type**: Choose based on your needs (e.g., Global Standard).
   * **Model version**: Choose the latest available.
   * Tokens per Minute Rate Limit: Choose around 250K, if possible.
   * Click **Deploy**.

### Option B: Deploy via Azure OpenAI Studio

1. Inside your Azure OpenAI resource, click **Go to Azure OpenAI Studio** or navigate directly to [oai.azure.com](https://oai.azure.com).
1. Go to **Deployments** > **Create new deployment**.
1. Select **GPT-4.1**:
   * Deployment name: **gpt-4.1** (**MUST USE THIS EXACT NAME**).
   * **Model version**: Choose the latest available.
   * Location: Choose your preferred region.
   * Click **Deploy**.

The chat playground will open. Feel free to ask a question to test the deployment.

## Retrieve your Azure OpenAI or Azure AI Foundry Key and Endpoint

Steps:

1. On the Azure portal:
   * Search for **Azure OpenAI** or **Azure AI Foundry**.
1. Locate your service.
1. Expand **Resource Management** > **Keys and Endpoint**.
    - **Endpoint**: Found in the **Keys & Endpoint** section of the Azure portal.
      - Azure OpenAI example: `https://westus3.api.cognitive.microsoft.com/`
      - Azure AI Foundry example: `https://sbxbotres.cognitiveservices.azure.com/`
    - **Note**: Save the hostname (e.g., `westus3.api.cognitive.microsoft.com` or `sbxbotres.cognitiveservices.azure.com`). You'll configure this as the `azure_openai_resource` option when scoring the model.
    - **API Key**: Found in the **Keys & Endpoint** section. Copy any of the keys. You will need it when scoring the model (passed as `API_KEY` in options).

## Configuration Options

This model is **location-independent** and supports both Azure OpenAI and Azure AI Foundry deployments. Configure your endpoint at runtime through the `options` parameter - no code changes required for different Azure regions or deployment types.

### Required Options:

- **`API_KEY`**: Your API key from the Azure portal (required for authentication)

### Endpoint Configuration (choose one):

**Option 1: Full Endpoint URL Override** (recommended for Azure AI Foundry)
- **`endpoint_url`**: Complete chat completions endpoint URL
  - Example (Azure AI Foundry): `https://sbxbotres.cognitiveservices.azure.com/openai/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview`
  - Example (Azure OpenAI): `https://westus3.api.cognitive.microsoft.com/openai/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview`
  - When set, this is used directly (ignores `azure_openai_resource` and `api_version`)

**Option 2: Resource Hostname** (constructed endpoint)
- **`azure_openai_resource`**: Your Azure resource hostname
  - Azure AI Foundry: `sbxbotres.cognitiveservices.azure.com`
  - Azure OpenAI: `westus3.api.cognitive.microsoft.com` or short name `my-openai-westus3` (auto-appends `.openai.azure.com`)
  - Find this in the **Keys & Endpoint** section of your resource
- **`api_version`**: Azure OpenAI API version (default: `2025-01-01-preview`)
  - Used only when `endpoint_url` is not provided

### Optional Parameters:

- **`temperature`**: Sampling temperature 0-2 (default: `1`)
  - Higher values (0.8) make output more random
  - Lower values (0.2) make output more focused and deterministic
- **`top_p`**: Nucleus sampling 0-1 (default: `1`)
  - Controls diversity via nucleus sampling
  - 0.1 means only tokens in top 10% probability are considered

### Example Options Strings:

**Azure AI Foundry with full endpoint URL:**
```
{endpoint_url:https://sbxbotres.cognitiveservices.azure.com/openai/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

**Azure OpenAI with resource hostname:**
```
{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

**Azure OpenAI with short resource name:**
```
{azure_openai_resource:my-openai-westus3,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

### Testing Locally:

To test the model wrapper locally, uncomment the example code at the bottom of `gpt41MiniScore.py`:

**Using Azure AI Foundry:**
```python
if __name__ == "__main__":
    userPrompt = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{endpoint_url:https://sbxbotres.cognitiveservices.azure.com/openai/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-actual-key}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

**Using Azure OpenAI:**
```python
if __name__ == "__main__":
    userPrompt = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-actual-key}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

Run: `python gpt41MiniScore.py`

## Benefits of This Approach

- **Dual deployment support**: Works with both Azure OpenAI and Azure AI Foundry
- **Multi-region support**: Works with any Azure region without code changes
- **Flexible configuration**: Choose between full endpoint URL or constructed endpoint
- **Configurable at runtime**: Endpoint and API version configured via options
- **Development flexibility**: Different endpoints for dev/test/prod environments
- **Future-proof**: Easy to update API versions as Azure releases new features

## End
