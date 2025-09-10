import time
import json
import logging
import sys
import requests
import tiktoken

modelVersion = 'claude-3-haiku-20240307'
modelEndpoint = 'https://api.anthropic.com/v1/messages'
tokenizer = tiktoken.encoding_for_model('gpt-3.5-turbo')

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
        "top_p": 1,
        "max_tokens": 1000,
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
            "x-api-key": options["API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": modelVersion,
            "system": systemPrompt[0],
            "messages": [{"role": "user", "content": userPrompt[0]}],
            "max_tokens": int(options["max_tokens"]),
            "temperature": float(options["temperature"]),
            "top_p": float(options["top_p"]),
        },
    )
    response = responseObject.json()['content'][0]['text']
    prompt_length = len(tokenizer.encode(systemPrompt[0] + userPrompt[0]))
    output_length = len(tokenizer.encode(response))
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length