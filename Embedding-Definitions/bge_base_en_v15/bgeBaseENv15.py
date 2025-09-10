import time
import json
import logging
import sys
import os
from sentence_transformers import SentenceTransformer

modelVersion = 'bge_base_en_v15'
modelEndpoint = f'https://endpoint/{modelVersion}'

# Set the transformer cache directory to a writable directory
os.environ['TRANSFORMERS_CACHE'] = '/pybox/model'

checkpoint = f'./{modelVersion}'
model = SentenceTransformer(checkpoint)

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
    optionsDefaults = {}
    if len(options) > 0:
        if isinstance(options[0], str):
            try:
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
            except json.JSONDecodeError:
                optionsParsed = {}
    options = {**optionsDefaults, **optionsParsed}
    embeddingObject = model.encode(document[0], normalize_embeddings=True)
    embedding = json.dumps(embeddingObject[0])
    run_time = time.time() - started_timestamp
    tokens = model.tokenize(embeddingObject)['input_ids'].size(dim=1) - 2
    # Logging the response
    logger.info(f"project: {project[0]}")
    logger.info(f"tokens: {tokens}")
    logger.info(f"run_time: {run_time}")
    return embedding, run_time