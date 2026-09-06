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

def _scalar(value):
    """One input, whichever convention called us: the SCR REST path and CAS pass
    a one-element list or a pandas Series, the MAS REST API and SCR's own CSV
    batch wrapper pass a plain string."""
    if isinstance(value, (str, bytes, dict)):
        return value
    if hasattr(value, 'iloc'):  # pandas Series
        return value.iloc[0] if len(value) > 0 else ''
    if hasattr(value, '__len__') and hasattr(value, '__getitem__'):
        return value[0] if len(value) > 0 else ''
    return value


def scoreModel(document, project, options):
    "Output: embedding, run_time, tokens"
    started_timestamp = time.time()
    document, project, options = _scalar(document), _scalar(project), _scalar(options)
    optionsDefaults = {
        "API_KEY": ""
    }
    optionsParsed = {}
    if isinstance(options, str) and options.strip():
        try:
            optionsParsed = json.loads(options.replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
        except json.JSONDecodeError:
            optionsParsed = {}
    options = {**optionsDefaults, **optionsParsed}
    responseObject = requests.post(
        modelEndpoint,
        headers={
            "Content-Type": "application/json"
        },
        json={
            "content": {"parts": [{"text": document}]}
        },
    )
    embedding = json.dumps(responseObject.json()['embedding']['values'])
    run_time = time.time() - started_timestamp
    tokens = 0
    # Logging the response
    logger.info(f"project: {project}")
    logger.info(f"tokens: {tokens}")
    logger.info(f"run_time: {run_time}")
    return embedding, run_time, tokens