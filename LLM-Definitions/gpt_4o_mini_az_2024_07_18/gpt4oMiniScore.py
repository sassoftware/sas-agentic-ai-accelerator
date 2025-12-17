import time
import json
import logging
import sys
import requests
import tiktoken

# Requires an Azure OpenAI resource with a deployment named gpt-4o-mini
# You will require an API_KEY from your Azure OpenAI resource page
modelVersion = 'gpt-4o-mini'
tokenizer = tiktoken.encoding_for_model(modelVersion)

# Initiate the logger to write output information to the log
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger("scoreModel")

def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()

    # Default options
    optionsDefaults = {
        "temperature": 1,
        "top_p": 1,
        "azure_openai_resource": "westus3.api.cognitive.microsoft.com",  # NEW: Resource name instead of location
        "api_version": "2025-01-01-preview"  # NEW: Configurable API version
    }

    optionsParsed = {}
    if len(options) > 0:
        if isinstance(options[0], str):
            try:
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
            except json.JSONDecodeError:
                optionsParsed = {}

    options = {**optionsDefaults, **optionsParsed}

    # Build endpoint from resource name (location-independent)
    azure_resource = options['azure_openai_resource']
    api_version = options['api_version']
    modelEndpoint = f'https://{azure_resource}/openai/deployments/{modelVersion}/chat/completions?api-version={api_version}'

    logger.info(f"Using endpoint: {modelEndpoint}")

    responseObject = requests.post(
        modelEndpoint,
        headers={
            "Content-Type": "application/json",
            "api-key": f"{options['API_KEY']}",
        },
        json={
            "messages": [
                {"role": "system", "content": systemPrompt[0]},
                {"role": "user", "content": userPrompt[0]}
            ],
            "temperature": float(options["temperature"]),
            "top_p": float(options["top_p"]),
        },
    )

    response = responseObject.json()['choices'][0]['message']['content']
    prompt_length = len(tokenizer.encode(systemPrompt[0] + userPrompt[0]))
    output_length = len(tokenizer.encode(response))
    run_time = time.time() - started_timestamp

    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")

    return response, run_time, prompt_length, output_length

"""
# Example usage - scoreModel function
## Uncomment the block, adapt the API_KEY, replace '*****'
if __name__ == "__main__":
    userPrompt = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{azure_openai_resource:westus3.api.cognitive.microsoft.com,modelVersion:gpt-4o-mini,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:*****}"]
    print(options)
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)

    if response:
        print("Response:", response)
    else:
        print("No response received from the model.")

    print("Run time:", run_time)
    print("Prompt length:", prompt_length)
    print("Output length:", output_length)
    # Expected Output: Response: Sure! Here's how you count to ten in French: 1. Un 2. Deux ... 9. Neuf 10. Dix
    # Run time: 1.9966557025909424
    # Prompt length: 14
    # Output length: 56
    """