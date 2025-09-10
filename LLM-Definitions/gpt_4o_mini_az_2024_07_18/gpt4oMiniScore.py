import time
import json
import logging
import sys
import requests

# Requires an Azure OpenAI resource deployed in {location}
# and a model deployment named gpt-4o-mini
# You will require an API_KEY from your Azure OpenAI resource page to work with this model
modelVersion = 'gpt-4o-mini'
location = 'westus3' # ADAPT to the location of your Azure OpenAI deployment!
modelEndpoint = f'https://{location}.api.cognitive.microsoft.com/openai/deployments/gpt-4o-mini/chat/completions?api-version=2025-01-01-preview'

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
    optionsDefaults = {
        "temperature": 0.7,
        "top_p": 0.95
    }
    optionsParsed = {}
    if len(options) > 0:
        if isinstance(options[0], str):
            try:
                # optionsParsed expects a list
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
                # optionsParsed expects JSON
                # optionsParsed = json.loads(options[0])
            except json.JSONDecodeError:
                optionsParsed = {}

    options = {**optionsDefaults, **optionsParsed}
    responseObject = requests.post(
        modelEndpoint,
        headers={
            "Content-Type": "application/json",
            "api-key": f"{options['API_KEY']}",
        },
        json={
            "model": modelVersion,
            "messages": [{"role": "system", "content": systemPrompt[0]},
                {"role": "user", "content": userPrompt[0]}],
            "temperature": float(options["temperature"]),
            "top_p": float(options["top_p"]),
        },
    )
    response = responseObject.json()['choices'][0]['message']['content']
    prompt_length = responseObject.json()['usage']['prompt_tokens']
    output_length = responseObject.json()['usage']['completion_tokens']
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length

"""
# Example usage - scoreModel function
# Uncomment the block, adapt the API_KEY, replace '*****'
if __name__ == "__main__":
    userPrompt = ["Count to ten in French"]  # Note: The prompt should be in a list
    systemPrompt = ["You are an AI Assistant helping people learn languages"]  # Note: The prompt should be in a list
    options =["{temperature:1,top_p:1,API_KEY:*****}"] # Note: The options should be in a list
    print (options)
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)

    # Print the response and additional information
    if response:
        print("Response:", response)
    else:
        print("No response received from the model.")

    print("Run time:", run_time)
    print("Prompt length:", prompt_length)
    print("Output length:", output_length)
"""
