import time
import json
import logging
import sys
from transformers import AutoModelForCausalLM, AutoTokenizer

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

checkpoint = './qwen_25_7b'
tokenizer = AutoTokenizer.from_pretrained(checkpoint)
model = AutoModelForCausalLM.from_pretrained(
    checkpoint,
    torch_dtype='auto',
    device_map=device,
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
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 512
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
    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )
    model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
    generated_ids = model.generate(
        **model_inputs,
        temperature=float(options['temperature']),
        top_p=float(options['top_p']),
        max_new_tokens=int(options['max_new_tokens'])
    )
    generated_ids = [
        output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
    ]
    response = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
    # Collecting output metrics
    prompt_length = len(model_inputs[0])
    output_length = len(generated_ids[0])
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length