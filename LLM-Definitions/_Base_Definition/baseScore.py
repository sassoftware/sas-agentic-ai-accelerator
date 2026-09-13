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


def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()
    userPrompt, systemPrompt, options = _scalar(userPrompt), _scalar(systemPrompt), _scalar(options)
    optionsDefaults = {
        "temperature": 1,
        "top_p": 1,
    }
    optionsParsed = {}
    if isinstance(options, str) and options.strip():
        try:
            optionsParsed = json.loads(options.replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
        except json.JSONDecodeError:
            optionsParsed = {}

    options = {**optionsDefaults, **optionsParsed}
    # Implement the model call here - note that you should explicitly parse options to be int/float
    response = ""
    # Collecting output metrics
    prompt_length = len(tokenizer.encode(systemPrompt + userPrompt))
    output_length = len(tokenizer.encode(response))
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length