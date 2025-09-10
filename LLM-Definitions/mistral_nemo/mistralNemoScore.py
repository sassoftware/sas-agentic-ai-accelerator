import time
import json
import os
import logging
import sys
from mistral_inference.transformer import Transformer
from mistral_inference.generate import generate
from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
from mistral_common.protocol.instruct.messages import UserMessage, SystemMessage
from mistral_common.protocol.instruct.request import ChatCompletionRequest

def get_device():
    if os.environ.get('CUDA_VISIBLE_DEVICES') is not None:
        return "cuda"
    else:
        return "cpu"

device = get_device()

tokenizer = MistralTokenizer.from_file(f"nemo/tekken.json")
model = Transformer.from_folder('nemo', device=device)

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
        'max_tokens': 256,
        'temperature': 0.3
    }
    optionsParsed = {}
    if len(options) > 0:
        if isinstance(options[0], str):
            try:
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
            except json.JSONDecodeError:
                optionsParsed = {}
    options = {**optionsDefaults, **optionsParsed}
    completion_request = ChatCompletionRequest(messages=[UserMessage(content=userPrompt[0]), SystemMessage(content=systemPrompt[0])])
    tokens = tokenizer.encode_chat_completion(completion_request).tokens
    out_tokens, _ = generate([tokens], model, max_tokens=int(options['max_tokens']), temperature=float(options['temperature']), eos_id=tokenizer.instruct_tokenizer.tokenizer.eos_id)
    response = tokenizer.decode(out_tokens[0])
    prompt_length = len(tokens)
    output_length = len(tokens[0])
    run_time = time.time() - started_timestamp
    # Logging the response
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length