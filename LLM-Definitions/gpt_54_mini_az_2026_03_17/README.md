# gpt-5.4-mini from Azure OpenAI / Azure AI Foundry

## Required Items

Azure OpenAI and Azure AI Foundry provide REST APIs for interaction and response generation.

To use this model, you need:

- A resource (Azure OpenAI or Azure AI Foundry). See [Create and deploy an Azure OpenAI resource](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal).
- A deployment of the **gpt-5.4-mini** model with deployment name **`gpt-5.4-mini`** (**MUST USE THIS EXACT NAME**).
- The endpoint and API key.

## Create a Model Deployment

### Option A: Deploy via Azure AI Foundry

1. Navigate to [Azure AI Foundry](https://ai.azure.com).
1. Select your project or create a new one.
1. **Create a deployment**:
   * Search for and select **gpt-5.4-mini**.
   * Deployment name: **`gpt-5.4-mini`** (**MUST USE THIS EXACT NAME**).
   * **Deployment type**: Choose based on your needs (e.g., Global Standard).
   * **Model version**: Choose the latest available.
   * Tokens per Minute Rate Limit: Choose around 250K, if possible.
   * Click **Deploy**.

### Option B: Deploy via Azure OpenAI Studio

1. Inside your Azure OpenAI resource, click **Go to Azure OpenAI Studio** or navigate to [oai.azure.com](https://oai.azure.com).
1. Go to **Deployments** > **Create new deployment**.
1. Select **gpt-5.4-mini**:
   * Deployment name: **`gpt-5.4-mini`** (**MUST USE THIS EXACT NAME**).
   * **Model version**: Choose the latest available.
   * Click **Deploy**.

## Retrieve your Azure OpenAI or Azure AI Foundry Key and Endpoint

1. On the Azure portal, search for **Azure OpenAI** or **Azure AI Foundry**.
1. Locate your service and expand **Resource Management** > **Keys and Endpoint**.
   - **Endpoint** examples:
     - Azure OpenAI: `https://westus3.api.cognitive.microsoft.com/`
     - Azure AI Foundry: `https://myres.cognitiveservices.azure.com/`
   - Save the **hostname** (e.g., `westus3.api.cognitive.microsoft.com`). Configure it as `azure_openai_resource` in the scoring options.
   - **API Key**: Copy either key. Pass it as `API_KEY` in options.

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `azure_openai_resource` | `westus3.api.cognitive.microsoft.com` | Azure resource hostname or short name |
| `api_version` | `2025-01-01-preview` | Azure OpenAI API version |
| `endpoint_url` | `null` | Optional full URL override (ignores `azure_openai_resource` and `api_version`) |
| `temperature` | `1` | Sampling temperature 0-2 |
| `top_p` | `1` | Nucleus sampling probability 0-1 |
| `API_KEY` | — | Azure API key (required) |

### Endpoint Configuration (choose one)

**Option 1 — Full endpoint URL** (recommended for Azure AI Foundry):
```
endpoint_url:https://myres.cognitiveservices.azure.com/openai/deployments/gpt-5.4-mini/chat/completions?api-version=2025-01-01-preview
```

**Option 2 — Resource hostname** (Azure OpenAI):
```
azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview
```

### Example Options Strings

**Azure AI Foundry:**
```
{endpoint_url:https://myres.cognitiveservices.azure.com/openai/deployments/gpt-5.4-mini/chat/completions?api-version=2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

**Azure OpenAI:**
```
{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

## Testing Locally

Uncomment the example block at the bottom of `gpt54MiniAzScore.py` and run:

```python
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-actual-key}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

```
python gpt54MiniAzScore.py
```

## End
