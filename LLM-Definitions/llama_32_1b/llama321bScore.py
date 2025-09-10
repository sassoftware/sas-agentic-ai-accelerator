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

# Set the transformer cache directory to a writable directory
os.environ['TRANSFORMERS_CACHE'] = '/pybox/model'

checkpoint = './llama_32_1b'
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

def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()
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
        {"role": "system", "content": systemPrompt[0]},
        {"role": "user", "content": userPrompt[0]}
    ]
    output = model(
        messages,
        temperature=float(options['temperature']),
        top_p=float(options['top_p']),
        max_new_tokens=int(options['max_tokens'])
    )
    response = output[0]["generated_text"][-1]['content']
    # Collecting output metrics
    prompt_length = len(tokenizer(systemPrompt[0] + userPrompt[0])['input_ids'])
    output_length = len(tokenizer(response)['input_ids'])
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length