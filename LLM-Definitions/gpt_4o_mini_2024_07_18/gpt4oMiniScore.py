import time
import json
import logging
import sys
import requests

modelVersion = 'gpt-4o-mini-2024-07-18'
modelEndpoint = 'https://api.openai.com/v1/chat/completions'

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
        "temperature": 1,
        "top_p": 1
    }
    optionsParsed = {}
    if len(options) > 0:
        if isinstance(options[0], str):
            try:
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
            except json.JSONDecodeError:
                optionsParsed = {}

    options = {**optionsDefaults, **optionsParsed}
    responseObject = requests.post(
        modelEndpoint,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {options['API_KEY']}",
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