import time
import json
import logging
import sys
from transformers import pipeline, AutoTokenizer

# If you need device identification (CPU vs GPU) please use the following snippet
import os
def get_device():
    if os.environ.get('CUDA_VISIBLE_DEVICES') is not None:
        return "cuda"
    else:
        return "cpu"
# Add this device to the model loading
device = get_device()

# Set the transformer cache directory to a writable directory
os.environ['TRANSFORMERS_CACHE'] = '/pybox/model'

checkpoint = './llama_32_3b'
tokenizer = AutoTokenizer.from_pretrained(checkpoint)
model = pipeline(
    'text-generation',
    model=checkpoint,
    torch_dtype='auto',
    device_map=device
)

# Initiate the logger to write output information to the log
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger("scoreModel")

def _scalar(value):
    """The one value behind an SCR input, whatever the caller's convention.

    CAS / DATA step and the SCR container hand each input over as a
    one-element list (or pandas Series); the MAS REST API hands over the plain
    string. A str also answers len() and [0], so indexing it does not fail - it
    silently keeps the first character."""
    if value is None:
        return ''
    if isinstance(value, (str, bytes, dict)):
        return value
    if hasattr(value, 'iloc'):  # pandas Series
        return value.iloc[0] if len(value) > 0 else ''
    if hasattr(value, '__len__') and hasattr(value, '__getitem__'):
        # list, tuple, numpy array, ...: whatever answered [0] before still does
        return value[0] if len(value) > 0 else ''
    return value

def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()
    # One value per input, whatever the caller's convention (MAS REST passes
    # plain strings, CAS/SCR one-element lists); options stays list-shaped
    # for the parsing below.
    userPrompt, systemPrompt = _scalar(userPrompt), _scalar(systemPrompt)
    options = [_scalar(options)]
    optionsDefaults = {
        "temperature": 0.6,
        "top_p": 0.9,
        "max_tokens": 256,
    }
    optionsParsed = {}
    if len(options) > 0:
        if isinstance(options, str):
            try:
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
            except json.JSONDecodeError:
                optionsParsed = {}

    options = {**optionsDefaults, **optionsParsed}
    # Implement the model call here - note that you should explicitly parse options to be int/float
    messages = [
        {"role": "system", "content": systemPrompt},
        {"role": "user", "content": userPrompt}
    ]
    output = model(
        messages,
        temperature=float(options['temperature']),
        top_p=float(options['top_p']),
        max_new_tokens=int(options['max_tokens'])
    )
    response = output[0]["generated_text"][-1]['content']
    # Collecting output metrics
    prompt_length = len(tokenizer(systemPrompt + userPrompt)['input_ids'])
    output_length = len(tokenizer(response)['input_ids'])
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length