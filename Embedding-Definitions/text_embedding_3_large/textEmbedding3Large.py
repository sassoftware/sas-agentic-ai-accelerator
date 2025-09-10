import time
import json
import logging
import sys
import requests

modelVersion = 'text-embedding-3-large'
modelEndpoint = 'https://api.openai.com/v1/embeddings'

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
            "Authorization": f"Bearer {options['API_KEY']}" 
        },
        json={
            "input": document[0],
            "model": modelVersion,
            "encoding_format": "float"
        },
    )
    embedding = json.dumps(responseObject.json()['data'][0]['embedding'])
    tokens = responseObject.json()['usage']['prompt_tokens']
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"project: {project[0]}")
    logger.info(f"tokens: {tokens}")
    logger.info(f"run_time: {run_time}")
    return embedding, run_time,tokens