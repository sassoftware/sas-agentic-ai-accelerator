import time
import json
import logging
import sys
import tiktoken

# If you need device identification (CPU vs GPU) please use the following snippet
import os
def get_device():
    if os.environ.get('CUDA_VISIBLE_DEVICES') is not None:
        return "cuda"
    else:
        return "cpu"
# Add this device to the model loading
device = get_device()

# Set the transformer cache directory to a writable directory - useful to avoid error message with the transformer package
os.environ['TRANSFORMERS_CACHE'] = '/pybox/model'

# Initiate the logger to write output information to the log
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger("scoreModel")

# Specify the token encoding model
tokenizer = tiktoken.encoding_for_model('gpt-3.5-turbo')

def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()
    optionsDefaults = {
        "temperature": 1,
        "top_p": 1,
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
    response = ""
    # Collecting output metrics
    prompt_length = len(tokenizer.encode(systemPrompt[0] + userPrompt[0]))
    output_length = len(tokenizer.encode(response))
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length