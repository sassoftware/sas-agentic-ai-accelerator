import time
import json
import logging
import sys
import requests

model = 'gemini-1.5-flash-001'

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
        "top_k": 40,
        "top_p": 0.95,
        "max_tokens": 256
    }
    optionsParsed = {}
    if len(options) > 0:
        if isinstance(options[0], str):
            try:
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
            except json.JSONDecodeError:
                optionsParsed = {}

    options = {**optionsDefaults, **optionsParsed}
    # Implement the model call here - note that you should explicitly parse options to be int/float
    responseObject = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={options['API_KEY']}",
        headers={
            'Content-Type': 'application/json'
        },
        json={
            "contents": [
                {
                "role": "user",
                "parts": [
                    {
                    "text": userPrompt[0]
                    }
                ]
                }
            ],
            "systemInstruction": {
                "role": "user",
                "parts": [
                {
                    "text": systemPrompt[0]
                }
                ]
            },
            "generationConfig": {
                "temperature": float(options["temperature"]),
                "topK": int(options["top_k"]),
                "topP": float(options["top_p"]),
                "maxOutputTokens": int(options["max_tokens"])
            }
        }
    )
    responseJSON = responseObject.json()
    response = responseJSON['candidates'][0]['content']['parts'][0]['text']
    # Collecting output metrics
    prompt_length = responseJSON['usageMetadata']['promptTokenCount']
    output_length = responseJSON['usageMetadata']['candidatesTokenCount']
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length