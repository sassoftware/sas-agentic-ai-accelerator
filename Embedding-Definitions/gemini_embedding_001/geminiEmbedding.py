import time
import json
import logging
import sys
import requests

modelVersion = 'gemini-embedding-001'
modelEndpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{modelVersion}:embedContent"

# Initiate the logger to write output information to the log
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger("scoreModel")

def scoreModel(document, project, options):
    "Output: embedding, run_time, tokens"
    started_timestamp = time.time()
    optionsDefaults = {
        "API_KEY": ""
    }
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
            "x-goog-api-key": options["API_KEY"],
        },
        json={
            "content": {"parts": [{"text": document[0]}]}
        },
    )
    embedding = json.dumps(responseObject.json()['embedding']['values'])
    # Get token count via an additional request, as Google doesn't provide it in the embedding resposne
    responseObject = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{modelVersion}:countTokens?key={options['API_KEY']}",
        headers= {"Content-Type": "application/json"},
        json= {
            "contents": [{"parts": [{"text": document[0]}]}]
        }
    )
    print(responseObject.json())
    tokens = responseObject.json()['totalTokens']
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"project: {project[0]}")
    logger.info(f"tokens: {tokens}")
    logger.info(f"run_time: {run_time}")
    return embedding, run_time