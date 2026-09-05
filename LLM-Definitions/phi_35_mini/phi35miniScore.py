import time
import json
import logging
import sys
import onnxruntime_genai as og

model = og.Model('cpu_and_mobile/cpu-int4-awq-block-128-acc-level-4')
tokenizer = og.Tokenizer(model)
chat_template = '<|system|>\n{systemPrompt}<|end|><|user|>\n{userPrompt} <|end|>\n<|assistant|>'

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
        "max_tokens": 2048
    }
    optionsParsed = {}
    if len(options) > 0:
        if isinstance(options[0], str):
            try:
                optionsParsed = json.loads(options[0].replace('{', '{"').replace('}', '"}').replace(':', '":"').replace(',', '","'))
            except json.JSONDecodeError:
                optionsParsed = {}
    options = {**optionsDefaults, **optionsParsed}
    prompt = f'{chat_template.format(userPrompt=userPrompt, systemPrompt=systemPrompt)}'
    input_tokens = tokenizer.encode(prompt)
    params = og.GeneratorParams(model)
    params.set_search_options(max_length=int(options['max_tokens']))
    params.input_ids = input_tokens
    output_tokens = model.generate(params)
    response = tokenizer.decode(output_tokens[0][len(input_tokens):])
    prompt_length = len(input_tokens)
    output_length = len(output_tokens[0]) - prompt_length
    run_time = time.time() - started_timestamp
    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length