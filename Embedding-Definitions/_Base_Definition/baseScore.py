import time
import json
import logging
import sys
import requests

modelVersion = 'name'
modelEndpoint = f'https://endpoint/{modelVersion}'

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
            "Content-Type": "application/json"
        },
        json={
            "content": {"parts": [{"text": document[0]}]}
        },
    )
    embedding = json.dumps(responseObject.json()['embedding']['values'])
    run_time = time.time() - started_timestamp
    tokens = 0
    # Logging the response
    logger.info(f"project: {project[0]}")
    logger.info(f"tokens: {tokens}")
    logger.info(f"run_time: {run_time}")
    return embedding, run_time