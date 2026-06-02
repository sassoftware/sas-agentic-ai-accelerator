#!/usr/bin/env python3
"""LLM Definition Builder — scaffolds a new model definition folder in LLM-Definitions/."""

import os
import json
import re
import sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DEF = os.path.join(SCRIPT_DIR, "_Base_Definition")

# ── Boilerplate responsible-AI fields (identical for every model) ─────────────

_BOILERPLATE = {
    "modelPurpose": (
        "To serve as a bridge of understanding, enabling seamless interaction between humans and "
        "machines by harnessing the power of natural language. LLMs decode the complexities of human "
        "thought, transforming them into actionable insights, creative solutions, and tailored knowledge "
        "while fostering accessibility, inclusivity, and empowerment in communication and decision-making."
    ),
    "intendedUse": (
        "Large Language Models (LLMs) are designed to process and generate human-like text, enabling a "
        "wide range of applications across domains. These models support tasks such as text summarization, "
        "question answering, content generation, translation, and conversational interactions. LLMs aim to "
        "facilitate understanding, streamline workflows, and provide accessible tools for creativity, "
        "education, and decision-making. They are built to serve diverse user needs while emphasizing "
        "usability, adaptability, and ethical considerations in deployment."
    ),
    "expectedBenefit": (
        "Large Language Models (LLMs) provide significant utility across various domains by enabling "
        "efficient processing and generation of human-like text. Key benefits include enhanced productivity "
        "through automation of repetitive tasks, improved accessibility via natural language interfaces, and "
        "support for creativity in content generation and ideation. These models contribute to knowledge "
        "dissemination by simplifying complex information and tailoring it to diverse audiences. Additionally, "
        "they foster inclusivity by enabling multilingual communication and supporting underrepresented "
        "languages. When responsibly deployed, LLMs offer scalable solutions that enhance both individual "
        "and organizational capabilities."
    ),
    "outOfScopeUseCases": (
        "Large Language Models (LLMs) are not designed for use in high-stakes decision-making contexts, "
        "such as medical diagnosis, legal advice, or financial planning, where expert oversight is required. "
        "They should not be employed to generate or propagate false, harmful, or misleading information, nor "
        "in unmoderated settings where their output could be misused or misinterpreted. These models are "
        "unsuitable for real-time systems with safety-critical implications, such as autonomous vehicles or "
        "emergency response systems, and should not be used in applications that violate privacy, such as "
        "unauthorized surveillance or data scraping. Additionally, LLMs must not be used in ways that "
        "reinforce bias, discrimination, or unethical practices, underscoring the need for careful and "
        "responsible deployment."
    ),
    "limitations": (
        "Large Language Models (LLMs) have several inherent limitations. They may produce outputs that are "
        "factually incorrect, outdated, or lack context, as they do not possess real-time knowledge or a "
        "true understanding of the information they generate. LLMs are sensitive to the quality of input "
        "prompts, and vague or poorly structured inputs can lead to irrelevant or nonsensical responses. "
        "They may inadvertently reinforce biases present in their training data or reflect inappropriate "
        "content. Additionally, LLMs cannot provide subjective judgment, emotional awareness, or ethical "
        "reasoning, which limits their reliability in nuanced or sensitive situations. Their computational "
        "demands can also pose challenges for deployment, particularly in resource-constrained environments. "
        "Regular monitoring, context-aware use, and human oversight are essential to mitigate these limitations."
    ),
}

# ── Provider constants ────────────────────────────────────────────────────────

PROVIDERS = [
    "Azure OpenAI",
    "OpenAI",
    "Anthropic",
    "Google (Gemini)",
    "Meta / HuggingFace (local/SCR)",
    "Other",
]

_PROVIDER_TAGS = {
    "Azure OpenAI":                   ["Proprietary", "Azure OpenAI", "Azure AI Foundry"],
    "OpenAI":                         ["Proprietary", "OpenAI"],
    "Anthropic":                      ["Proprietary", "Anthropic"],
    "Google (Gemini)":                ["Proprietary", "Google"],
    "Meta / HuggingFace (local/SCR)": ["Open-Source", "Meta"],
    "Other":                          [],
}

# ── Fallback var schemas (used if _Base_Definition/ is missing) ───────────────

_INPUT_VAR = [
    {"name": "userPrompt",   "description": "Contains the variable information for the LLM prompt",           "level": "nominal", "type": "string", "length": 5000},
    {"name": "systemPrompt", "description": "Contains the the behavior on how the LLM should act and respond", "level": "nominal", "type": "string", "length": 1000},
    {"name": "options",      "description": "This is an optional argument to detail model parameters",         "level": "nominal", "type": "string", "length": 1000},
]
_OUTPUT_VAR = [
    {"name": "response",      "description": "Contains the text generated by the LLM", "level": "nominal",  "type": "string",  "length": 5000},
    {"name": "run_time",      "description": "Inference time of the LLM",              "level": "interval", "type": "decimal", "length": 8},
    {"name": "prompt_length", "description": "Token amount of the prompt input",        "level": "interval", "type": "decimal", "length": 8},
    {"name": "output_length", "description": "Token amount of the LLM output",          "level": "interval", "type": "decimal", "length": 8},
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else (str(default) if default is not None else "")


def slugify(s):
    s = re.sub(r'[\.\-/ ]+', '_', s.lower())
    s = re.sub(r'[^a-z0-9_]', '', s)
    return re.sub(r'_+', '_', s).strip('_')


def to_folder_name(provider, model_version):
    today = date.today().strftime("%Y_%m_%d")
    slug = slugify(model_version)
    if provider == "Azure OpenAI":
        return f"{slug}_az_{today}"
    if provider == "OpenAI":
        return f"{slug}_{today}"
    return slug


def to_score_name(folder_name):
    parts = folder_name.split('_')
    return parts[0] + ''.join(p.capitalize() for p in parts[1:]) + 'Score.py'


def validate_folder(name):
    if not name:
        return "Folder name cannot be empty."
    if not re.match(r'^[a-z0-9_]+$', name):
        return "Must be lowercase letters, digits, and underscores only."
    return None

# ── File content generators ───────────────────────────────────────────────────

def make_model_config(folder_name, score_file, description, modeler, tags):
    return {
        "name": folder_name,
        "scoreCodeFile": score_file,
        "description": description,
        "toolVersion": "3.11.9",
        "targetVariable": "response",
        "targetLevel": "NOMINAL",
        "trainCodeType": "Python",
        "modeler": modeler,
        "function": "text generation",
        "algorithm": "Transformer",
        "tool": "Python 3",
        "scoreCodeType": "Python",
        "champion": False,
        "tags": tags,
        **_BOILERPLATE,
    }


def make_options(provider):
    tt = {
        "temperature": {"default": 1, "range": "0 - 2",
                        "description": "What sampling temperature to use, between 0 and 2. Higher values like 0.8 will make the output more random, while lower values like 0.2 will make it more focused and deterministic."},
        "top_p":       {"default": 1, "range": "0 - 1",
                        "description": "An alternative to sampling with temperature, called nucleus sampling, where the model considers the results of the tokens with top_p probability mass. So 0.1 means only the tokens comprising the top 10% probability mass are considered."},
    }
    if provider == "Azure OpenAI":
        return {
            "azure_openai_resource": {
                "default": "westus3.api.cognitive.microsoft.com",
                "range": "*******",
                "description": (
                    "Either a resource name (e.g., 'my-openai-westus3' -> host 'my-openai-westus3.openai.azure.com') "
                    "or a full host (e.g., 'sbxbotres.cognitiveservices.azure.com'). Used to construct "
                    "https://{host}/openai/deployments/{modelVersion}/chat/completions?api-version={api_version} "
                    "when 'endpoint_url' is not provided."
                ),
            },
            "api_version": {
                "default": "2025-01-01-preview",
                "range": "2023-05-15 - 2025-01-01-preview",
                "description": "Azure OpenAI API version. Use latest stable version for new features.",
            },
            "endpoint_url": {
                "default": None,
                "range": "*******",
                "description": (
                    "Optional full URL override for the chat completions endpoint, e.g., "
                    "'https://sbxbotres.cognitiveservices.azure.com/openai/deployments/__MODEL_VERSION__/chat/completions?api-version=2025-01-01-preview'. "
                    "If set, this is used directly."
                ),
            },
            **tt,
            "API_KEY": {
                "default": "AzureOpenAI",
                "range": "*******",
                "description": "Azure OpenAI API key from the Azure portal Keys & Endpoint section.",
            },
        }
    if provider == "OpenAI":
        return {
            **tt,
            "API_KEY": {
                "default": "OpenAI",
                "range": "sk-****",
                "description": "OpenAI API key from platform.openai.com.",
            },
        }
    if provider == "Anthropic":
        return {
            "max_tokens": {
                "default": 1000,
                "range": "1 - 200000",
                "description": "Limits the amount of output tokens generated by the model. Required by the Anthropic API.",
            },
            "temperature": {"default": 1, "range": "0 - 1",
                            "description": "Sampling temperature (Anthropic range is 0-1)."},
            "top_p":       {"default": 1, "range": "0 - 1",
                            "description": "Nucleus sampling probability."},
            "API_KEY": {
                "default": "Anthropic",
                "range": "sk-ant-****",
                "description": "Anthropic API key from console.anthropic.com.",
            },
        }
    if provider == "Google (Gemini)":
        return {
            **tt,
            "top_k": {
                "default": 40,
                "range": "1 - 100",
                "description": "The topK parameter changes how the model selects tokens for output. A topK of 1 means the selected token is the most probable among all the tokens in the model's vocabulary (greedy decoding), while a topK of 3 means that the next token is selected from among the 3 most probable tokens.",
            },
            "max_tokens": {
                "default": 256,
                "range": "1 - 8192",
                "description": "Parameter to define how long the output can get.",
            },
            "API_KEY": {
                "default": "Google",
                "range": "AIza****",
                "description": "Google AI Studio API key from aistudio.google.com.",
            },
        }
    return {
        **tt,
        "API_KEY": {
            "default": "ProviderName",
            "range": "sk-****",
            "description": "API key for the model provider.",
        },
    }


def make_requirements(provider):
    step0 = {"step": "upgrade pip before pip install",
             "command": "pip3 -q install --upgrade pip setuptools wheel"}
    if provider in ("Azure OpenAI", "OpenAI"):
        return [step0, {"step": "install common packages",
                        "command": "pip3 -q install requests>=2.31.0 tiktoken>=0.5.1"}]
    if provider == "Anthropic":
        return [step0, {"step": "install common packages",
                        "command": "pip3 -q install requests>=2.31.0 tiktoken>=0.5.1"}]
    if provider == "Google (Gemini)":
        return [step0, {"step": "install common packages",
                        "command": "pip3 -q install requests>=2.31.0"}]
    return [step0, {"step": "install common packages",
                    "command": "pip3 -q install requests tiktoken numpy==1.26.4"}]


# ── Score.py templates ────────────────────────────────────────────────────────
# Tokens: __MODEL_VERSION__  __SCORE_FILE__

_AZURE_SCORE = r'''import time
import json
import logging
import sys
import requests
import tiktoken

# Requires an Azure OpenAI / Azure AI Foundry deployment
modelVersion = '__MODEL_VERSION__'
try:
    tokenizer = tiktoken.encoding_for_model(modelVersion)
except Exception:
    tokenizer = tiktoken.get_encoding("o200k_base")

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)],
                    format='%(levelname)s - %(message)s')
logger = logging.getLogger("scoreModel")


def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()

    optionsDefaults = {
        "temperature": 1,
        "top_p": 1,
        "azure_openai_resource": "westus3.api.cognitive.microsoft.com",
        "api_version": "2025-01-01-preview",
        "endpoint_url": None,
        "API_KEY": None,
    }

    def _parse_options(opts):
        if opts is None or (hasattr(opts, '__len__') and len(opts) == 0):
            return {}
        raw = opts.iloc[0] if hasattr(opts, 'iloc') else (opts[0] if len(opts) > 0 else None)
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                pass
            parsed = {}
            s = raw.strip()
            if s.startswith('{') and s.endswith('}'):
                s = s[1:-1]
            for piece in [p for p in s.split(',') if p.strip()]:
                if ':' not in piece:
                    continue
                k, v = piece.split(':', 1)
                k, v = k.strip().strip('"\''), v.strip().strip('"\'')
                if v.lower() in ("true", "false"):
                    parsed[k] = v.lower() == "true"
                else:
                    try:
                        parsed[k] = float(v) if '.' in v else int(v)
                    except Exception:
                        parsed[k] = v
            return parsed
        return {}

    options = {**optionsDefaults, **_parse_options(options)}

    if options.get('endpoint_url'):
        modelEndpoint = options['endpoint_url']
    else:
        host = (options.get('azure_openai_resource') or '').strip()
        if host and '.' not in host:
            host = f"{host}.openai.azure.com"
        modelEndpoint = f'https://{host}/openai/deployments/{modelVersion}/chat/completions?api-version={options["api_version"]}'

    logger.info(f"Using endpoint: {modelEndpoint}")

    headers = {"Content-Type": "application/json", "api-key": options.get("API_KEY") or ""}
    payload = {
        "messages": [
            {"role": "system", "content": systemPrompt[0]},
            {"role": "user",   "content": userPrompt[0]},
        ],
        "temperature": float(options["temperature"]),
        "top_p":       float(options["top_p"]),
    }

    responseObject = requests.post(modelEndpoint, headers=headers, json=payload, timeout=60)
    try:
        responseJson = responseObject.json()
    except Exception:
        logger.error(f"Non-JSON response: {responseObject.status_code} - {responseObject.text}")
        raise

    if not (200 <= responseObject.status_code < 300):
        logger.error(f"HTTP {responseObject.status_code}: {json.dumps(responseJson)}")
        raise RuntimeError(f"Request failed: {responseObject.status_code}")

    response = responseJson['choices'][0]['message']['content']
    prompt_length = len(tokenizer.encode(systemPrompt[0] + userPrompt[0]))
    output_length = len(tokenizer.encode(response))
    run_time = time.time() - started_timestamp

    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length


# Example usage — uncomment, replace API_KEY, and run: python __SCORE_FILE__
"""
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:***your_actual_API_key_here***}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
    print("Response:", response)
    print("Run time:", run_time)
    print("Prompt length:", prompt_length)
    print("Output length:", output_length)
    # Expected Output: Response: Sure! Here's how you count to ten in French: 1. Un 2. Deux ... 9. Neuf 10. Dix
"""
'''

_OPENAI_SCORE = r'''import time
import json
import logging
import sys
import requests
import tiktoken

modelVersion = '__MODEL_VERSION__'
modelEndpoint = 'https://api.openai.com/v1/chat/completions'
try:
    tokenizer = tiktoken.encoding_for_model(modelVersion)
except Exception:
    tokenizer = tiktoken.get_encoding("o200k_base")

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)],
                    format='%(levelname)s - %(message)s')
logger = logging.getLogger("scoreModel")


def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()

    optionsDefaults = {
        "temperature": 1,
        "top_p": 1,
        "API_KEY": None,
    }

    def _parse_options(opts):
        if opts is None or (hasattr(opts, '__len__') and len(opts) == 0):
            return {}
        raw = opts.iloc[0] if hasattr(opts, 'iloc') else (opts[0] if len(opts) > 0 else None)
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                pass
            parsed = {}
            s = raw.strip()
            if s.startswith('{') and s.endswith('}'):
                s = s[1:-1]
            for piece in [p for p in s.split(',') if p.strip()]:
                if ':' not in piece:
                    continue
                k, v = piece.split(':', 1)
                k, v = k.strip().strip('"\''), v.strip().strip('"\'')
                if v.lower() in ("true", "false"):
                    parsed[k] = v.lower() == "true"
                else:
                    try:
                        parsed[k] = float(v) if '.' in v else int(v)
                    except Exception:
                        parsed[k] = v
            return parsed
        return {}

    options = {**optionsDefaults, **_parse_options(options)}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {options['API_KEY']}",
    }
    payload = {
        "model": modelVersion,
        "messages": [
            {"role": "system", "content": systemPrompt[0]},
            {"role": "user",   "content": userPrompt[0]},
        ],
        "temperature": float(options["temperature"]),
        "top_p":       float(options["top_p"]),
    }

    responseObject = requests.post(modelEndpoint, headers=headers, json=payload, timeout=60)
    try:
        responseJson = responseObject.json()
    except Exception:
        logger.error(f"Non-JSON response: {responseObject.status_code} - {responseObject.text}")
        raise

    if not (200 <= responseObject.status_code < 300):
        logger.error(f"HTTP {responseObject.status_code}: {json.dumps(responseJson)}")
        raise RuntimeError(f"Request failed: {responseObject.status_code}")

    response = responseJson['choices'][0]['message']['content']
    prompt_length = responseJson['usage']['prompt_tokens']
    output_length = responseJson['usage']['completion_tokens']
    run_time = time.time() - started_timestamp

    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length


# Example usage — uncomment, replace API_KEY, and run: python __SCORE_FILE__
"""
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{temperature:1,top_p:1,API_KEY:***your_actual_API_key_here***}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
    print("Response:", response)
    print("Run time:", run_time)
    print("Prompt length:", prompt_length)
    print("Output length:", output_length)
"""
'''

_ANTHROPIC_SCORE = r'''import time
import json
import logging
import sys
import requests
import tiktoken

modelVersion = '__MODEL_VERSION__'
modelEndpoint = 'https://api.anthropic.com/v1/messages'
tokenizer = tiktoken.encoding_for_model('gpt-3.5-turbo')

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)],
                    format='%(levelname)s - %(message)s')
logger = logging.getLogger("scoreModel")


def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()

    optionsDefaults = {
        "temperature": 1,
        "top_p": 1,
        "max_tokens": 1000,
        "API_KEY": None,
    }

    def _parse_options(opts):
        if opts is None or (hasattr(opts, '__len__') and len(opts) == 0):
            return {}
        raw = opts.iloc[0] if hasattr(opts, 'iloc') else (opts[0] if len(opts) > 0 else None)
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                pass
            parsed = {}
            s = raw.strip()
            if s.startswith('{') and s.endswith('}'):
                s = s[1:-1]
            for piece in [p for p in s.split(',') if p.strip()]:
                if ':' not in piece:
                    continue
                k, v = piece.split(':', 1)
                k, v = k.strip().strip('"\''), v.strip().strip('"\'')
                if v.lower() in ("true", "false"):
                    parsed[k] = v.lower() == "true"
                else:
                    try:
                        parsed[k] = float(v) if '.' in v else int(v)
                    except Exception:
                        parsed[k] = v
            return parsed
        return {}

    options = {**optionsDefaults, **_parse_options(options)}

    headers = {
        "Content-Type": "application/json",
        "x-api-key": options["API_KEY"],
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": modelVersion,
        "system": systemPrompt[0],
        "messages": [{"role": "user", "content": userPrompt[0]}],
        "max_tokens": int(options["max_tokens"]),
        "temperature": float(options["temperature"]),
        "top_p": float(options["top_p"]),
    }

    responseObject = requests.post(modelEndpoint, headers=headers, json=payload, timeout=60)
    try:
        responseJson = responseObject.json()
    except Exception:
        logger.error(f"Non-JSON response: {responseObject.status_code} - {responseObject.text}")
        raise

    if not (200 <= responseObject.status_code < 300):
        logger.error(f"HTTP {responseObject.status_code}: {json.dumps(responseJson)}")
        raise RuntimeError(f"Request failed: {responseObject.status_code}")

    response = responseJson['content'][0]['text']
    prompt_length = len(tokenizer.encode(systemPrompt[0] + userPrompt[0]))
    output_length = len(tokenizer.encode(response))
    run_time = time.time() - started_timestamp

    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length


# Example usage — uncomment, replace API_KEY, and run: python __SCORE_FILE__
"""
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{max_tokens:1000,temperature:1,top_p:1,API_KEY:***your_actual_API_key_here***}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
    print("Response:", response)
    print("Run time:", run_time)
    print("Prompt length:", prompt_length)
    print("Output length:", output_length)
"""
'''

_GEMINI_SCORE = r'''import time
import json
import logging
import sys
import requests

modelVersion = '__MODEL_VERSION__'

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)],
                    format='%(levelname)s - %(message)s')
logger = logging.getLogger("scoreModel")


def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()

    optionsDefaults = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 40,
        "max_tokens": 256,
        "API_KEY": None,
    }

    def _parse_options(opts):
        if opts is None or (hasattr(opts, '__len__') and len(opts) == 0):
            return {}
        raw = opts.iloc[0] if hasattr(opts, 'iloc') else (opts[0] if len(opts) > 0 else None)
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                pass
            parsed = {}
            s = raw.strip()
            if s.startswith('{') and s.endswith('}'):
                s = s[1:-1]
            for piece in [p for p in s.split(',') if p.strip()]:
                if ':' not in piece:
                    continue
                k, v = piece.split(':', 1)
                k, v = k.strip().strip('"\''), v.strip().strip('"\'')
                if v.lower() in ("true", "false"):
                    parsed[k] = v.lower() == "true"
                else:
                    try:
                        parsed[k] = float(v) if '.' in v else int(v)
                    except Exception:
                        parsed[k] = v
            return parsed
        return {}

    options = {**optionsDefaults, **_parse_options(options)}

    modelEndpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{modelVersion}:generateContent?key={options['API_KEY']}"

    payload = {
        "contents": [{"role": "user", "parts": [{"text": userPrompt[0]}]}],
        "systemInstruction": {"role": "user", "parts": [{"text": systemPrompt[0]}]},
        "generationConfig": {
            "temperature":     float(options["temperature"]),
            "topK":            int(options["top_k"]),
            "topP":            float(options["top_p"]),
            "maxOutputTokens": int(options["max_tokens"]),
        },
    }

    responseObject = requests.post(modelEndpoint, headers={"Content-Type": "application/json"},
                                   json=payload, timeout=60)
    try:
        responseJson = responseObject.json()
    except Exception:
        logger.error(f"Non-JSON response: {responseObject.status_code} - {responseObject.text}")
        raise

    if not (200 <= responseObject.status_code < 300):
        logger.error(f"HTTP {responseObject.status_code}: {json.dumps(responseJson)}")
        raise RuntimeError(f"Request failed: {responseObject.status_code}")

    response = responseJson['candidates'][0]['content']['parts'][0]['text']
    prompt_length = responseJson['usageMetadata']['promptTokenCount']
    output_length = responseJson['usageMetadata']['candidatesTokenCount']
    run_time = time.time() - started_timestamp

    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length


# Example usage — uncomment, replace API_KEY, and run: python __SCORE_FILE__
"""
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{temperature:1,top_p:0.95,top_k:40,max_tokens:256,API_KEY:***your_actual_API_key_here***}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
    print("Response:", response)
    print("Run time:", run_time)
    print("Prompt length:", prompt_length)
    print("Output length:", output_length)
"""
'''

_OTHER_SCORE = r'''import time
import json
import logging
import sys
import requests
import tiktoken

# TODO: set the correct model version and endpoint for your provider
modelVersion = '__MODEL_VERSION__'
modelEndpoint = 'https://your-provider-endpoint/v1/chat/completions'
try:
    tokenizer = tiktoken.encoding_for_model('gpt-3.5-turbo')
except Exception:
    tokenizer = tiktoken.get_encoding("cl100k_base")

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)],
                    format='%(levelname)s - %(message)s')
logger = logging.getLogger("scoreModel")


def scoreModel(userPrompt, systemPrompt, options):
    "Output: response, run_time, prompt_length, output_length"
    started_timestamp = time.time()

    optionsDefaults = {
        "temperature": 1,
        "top_p": 1,
        "API_KEY": None,
    }

    def _parse_options(opts):
        if opts is None or (hasattr(opts, '__len__') and len(opts) == 0):
            return {}
        raw = opts.iloc[0] if hasattr(opts, 'iloc') else (opts[0] if len(opts) > 0 else None)
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                pass
            parsed = {}
            s = raw.strip()
            if s.startswith('{') and s.endswith('}'):
                s = s[1:-1]
            for piece in [p for p in s.split(',') if p.strip()]:
                if ':' not in piece:
                    continue
                k, v = piece.split(':', 1)
                k, v = k.strip().strip('"\''), v.strip().strip('"\'')
                if v.lower() in ("true", "false"):
                    parsed[k] = v.lower() == "true"
                else:
                    try:
                        parsed[k] = float(v) if '.' in v else int(v)
                    except Exception:
                        parsed[k] = v
            return parsed
        return {}

    options = {**optionsDefaults, **_parse_options(options)}

    # TODO: implement the API call for your provider
    response = ""
    prompt_length = len(tokenizer.encode(systemPrompt[0] + userPrompt[0]))
    output_length = len(tokenizer.encode(response))
    run_time = time.time() - started_timestamp

    logger.info(f"prompt_length: {prompt_length}")
    logger.info(f"output_length: {output_length}")
    logger.info(f"run_time: {run_time}")
    logger.info(f"response: {response}")
    return response, run_time, prompt_length, output_length


# Example usage — uncomment and run: python __SCORE_FILE__
"""
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{temperature:1,top_p:1,API_KEY:***your_actual_API_key_here***}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
    print("Response:", response)
    print("Run time:", run_time)
"""
'''

_SCORE_TEMPLATES = {
    "Azure OpenAI":                   _AZURE_SCORE,
    "OpenAI":                         _OPENAI_SCORE,
    "Anthropic":                      _ANTHROPIC_SCORE,
    "Google (Gemini)":                _GEMINI_SCORE,
    "Meta / HuggingFace (local/SCR)": _OTHER_SCORE,
    "Other":                          _OTHER_SCORE,
}


def make_score(provider, model_version, score_file):
    tmpl = _SCORE_TEMPLATES.get(provider, _OTHER_SCORE)
    return tmpl.replace('__MODEL_VERSION__', model_version).replace('__SCORE_FILE__', score_file)


# ── README templates ──────────────────────────────────────────────────────────
# Tokens: __DISPLAY_NAME__  __MODEL_VERSION__  __SCORE_FILE__

_AZURE_README = '''# __DISPLAY_NAME__ from Azure OpenAI / Azure AI Foundry

## Required Items

Azure OpenAI and Azure AI Foundry provide REST APIs for interaction and response generation.

To use this model, you need:

- A resource (Azure OpenAI or Azure AI Foundry). See [Create and deploy an Azure OpenAI resource](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal).
- A deployment of the **__DISPLAY_NAME__** model with deployment name **`__MODEL_VERSION__`** (**MUST USE THIS EXACT NAME**).
- The endpoint and API key.

## Create a Model Deployment

### Option A: Deploy via Azure AI Foundry

1. Navigate to [Azure AI Foundry](https://ai.azure.com).
1. Select your project or create a new one.
1. **Create a deployment**:
   * Search for and select **__DISPLAY_NAME__**.
   * Deployment name: **`__MODEL_VERSION__`** (**MUST USE THIS EXACT NAME**).
   * **Deployment type**: Choose based on your needs (e.g., Global Standard).
   * **Model version**: Choose the latest available.
   * Tokens per Minute Rate Limit: Choose around 250K, if possible.
   * Click **Deploy**.

### Option B: Deploy via Azure OpenAI Studio

1. Inside your Azure OpenAI resource, click **Go to Azure OpenAI Studio** or navigate to [oai.azure.com](https://oai.azure.com).
1. Go to **Deployments** > **Create new deployment**.
1. Select **__DISPLAY_NAME__**:
   * Deployment name: **`__MODEL_VERSION__`** (**MUST USE THIS EXACT NAME**).
   * **Model version**: Choose the latest available.
   * Click **Deploy**.

## Retrieve your Azure OpenAI or Azure AI Foundry Key and Endpoint

1. On the Azure portal, search for **Azure OpenAI** or **Azure AI Foundry**.
1. Locate your service and expand **Resource Management** > **Keys and Endpoint**.
   - **Endpoint** examples:
     - Azure OpenAI: `https://westus3.api.cognitive.microsoft.com/`
     - Azure AI Foundry: `https://sbxbotres.cognitiveservices.azure.com/`
   - Save the **hostname** (e.g., `westus3.api.cognitive.microsoft.com`). Configure it as `azure_openai_resource` in the scoring options.
   - **API Key**: Copy either key. Pass it as `API_KEY` in options.

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `azure_openai_resource` | `westus3.api.cognitive.microsoft.com` | Azure resource hostname or short name |
| `api_version` | `2025-01-01-preview` | Azure OpenAI API version |
| `endpoint_url` | `null` | Optional full URL override (ignores `azure_openai_resource` and `api_version`) |
| `temperature` | `1` | Sampling temperature 0-2 |
| `top_p` | `1` | Nucleus sampling probability 0-1 |
| `API_KEY` | — | Azure API key (required) |

### Endpoint Configuration (choose one)

**Option 1 — Full endpoint URL** (recommended for Azure AI Foundry):
```
endpoint_url:https://sbxbotres.cognitiveservices.azure.com/openai/deployments/__MODEL_VERSION__/chat/completions?api-version=2025-01-01-preview
```

**Option 2 — Resource hostname** (Azure OpenAI):
```
azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview
```

### Example Options Strings

**Azure AI Foundry:**
```
{endpoint_url:https://sbxbotres.cognitiveservices.azure.com/openai/deployments/__MODEL_VERSION__/chat/completions?api-version=2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

**Azure OpenAI:**
```
{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-key-here}
```

## Testing Locally

Uncomment the example block at the bottom of `__SCORE_FILE__` and run:

```python
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{azure_openai_resource:westus3.api.cognitive.microsoft.com,api_version:2025-01-01-preview,temperature:1,top_p:1,API_KEY:your-actual-key}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

```
python __SCORE_FILE__
```

## End
'''

_OPENAI_README = '''# __DISPLAY_NAME__ from OpenAI

## Required Items

To use this model, you need:

- An OpenAI account and API key from [platform.openai.com](https://platform.openai.com).
- Access to the **__MODEL_VERSION__** model (check your tier for availability).

## Retrieve your OpenAI API Key

1. Sign in at [platform.openai.com](https://platform.openai.com).
1. Navigate to **API keys** > **Create new secret key**.
1. Copy the key — you will only see it once.
1. Pass it as `API_KEY` in the scoring options.

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `temperature` | `1` | Sampling temperature 0-2 |
| `top_p` | `1` | Nucleus sampling probability 0-1 |
| `API_KEY` | — | OpenAI API key (required) |

### Example Options String

```
{temperature:1,top_p:1,API_KEY:sk-your-actual-key-here}
```

## Testing Locally

Uncomment the example block at the bottom of `__SCORE_FILE__` and run:

```python
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{temperature:1,top_p:1,API_KEY:sk-your-actual-key-here}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

```
python __SCORE_FILE__
```

## End
'''

_ANTHROPIC_README = '''# __DISPLAY_NAME__ from Anthropic

## Required Items

To use this model, you need:

- An Anthropic account and API key from [console.anthropic.com](https://console.anthropic.com).
- Access to the **__MODEL_VERSION__** model.

## Retrieve your Anthropic API Key

1. Sign in at [console.anthropic.com](https://console.anthropic.com).
1. Navigate to **API Keys** > **Create Key**.
1. Copy the key — you will only see it once.
1. Pass it as `API_KEY` in the scoring options.

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `max_tokens` | `1000` | Maximum tokens to generate (required by Anthropic API) |
| `temperature` | `1` | Sampling temperature 0-1 |
| `top_p` | `1` | Nucleus sampling probability 0-1 |
| `API_KEY` | — | Anthropic API key (required) |

### Example Options String

```
{max_tokens:1000,temperature:1,top_p:1,API_KEY:sk-ant-your-actual-key-here}
```

## Testing Locally

Uncomment the example block at the bottom of `__SCORE_FILE__` and run:

```python
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{max_tokens:1000,temperature:1,top_p:1,API_KEY:sk-ant-your-actual-key-here}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

```
python __SCORE_FILE__
```

## End
'''

_GEMINI_README = '''# __DISPLAY_NAME__ from Google

## Required Items

To use this model, you need:

- A Google account and API key from [Google AI Studio](https://aistudio.google.com).
- Access to the **__MODEL_VERSION__** model.

## Retrieve your Google AI Studio API Key

1. Sign in at [aistudio.google.com](https://aistudio.google.com).
1. Click **Get API key** > **Create API key**.
1. Copy the key and pass it as `API_KEY` in the scoring options.

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `temperature` | `1` | Sampling temperature 0-2 |
| `top_p` | `0.95` | Nucleus sampling probability 0-1 |
| `top_k` | `40` | Top-K sampling (1-100) |
| `max_tokens` | `256` | Maximum output tokens |
| `API_KEY` | — | Google AI Studio API key (required) |

### Example Options String

```
{temperature:1,top_p:0.95,top_k:40,max_tokens:256,API_KEY:AIza-your-actual-key-here}
```

## Testing Locally

Uncomment the example block at the bottom of `__SCORE_FILE__` and run:

```python
if __name__ == "__main__":
    userPrompt   = ["Count to ten in French"]
    systemPrompt = ["You are an AI Assistant helping people learn languages"]
    options = ["{temperature:1,top_p:0.95,top_k:40,max_tokens:256,API_KEY:AIza-your-actual-key-here}"]
    response, run_time, prompt_length, output_length = scoreModel(userPrompt, systemPrompt, options)
```

```
python __SCORE_FILE__
```

## End
'''

_GENERIC_README = '''# __DISPLAY_NAME__

## Required Items

- API access to **__MODEL_VERSION__**.
- API key / credentials for your provider.

## Configuration Options

| Option | Default | Description |
|---|---|---|
| `temperature` | `1` | Sampling temperature |
| `top_p` | `1` | Nucleus sampling probability |
| `API_KEY` | — | Provider API key (required) |

### Example Options String

```
{temperature:1,top_p:1,API_KEY:your-actual-key-here}
```

## Testing Locally

Uncomment the example block at the bottom of `__SCORE_FILE__` and update `modelEndpoint` and the auth headers before running:

```
python __SCORE_FILE__
```

## End
'''

_README_TEMPLATES = {
    "Azure OpenAI":                   _AZURE_README,
    "OpenAI":                         _OPENAI_README,
    "Anthropic":                      _ANTHROPIC_README,
    "Google (Gemini)":                _GEMINI_README,
    "Meta / HuggingFace (local/SCR)": _GENERIC_README,
    "Other":                          _GENERIC_README,
}


def make_readme(provider, display_name, model_version, score_file):
    tmpl = _README_TEMPLATES.get(provider, _GENERIC_README)
    return (tmpl
            .replace('__DISPLAY_NAME__', display_name)
            .replace('__MODEL_VERSION__', model_version)
            .replace('__SCORE_FILE__', score_file))


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _write_json(dest, filename, data):
    with open(os.path.join(dest, filename), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)


def _write_text(dest, filename, content):
    with open(os.path.join(dest, filename), 'w', encoding='utf-8') as f:
        f.write(content)


def _read_base(filename, fallback):
    """Read a file from _Base_Definition/ or return a JSON-serialised fallback."""
    path = os.path.join(BASE_DEF, filename)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return f.read()
    return json.dumps(fallback, indent=4)


# ── Main interactive flow ─────────────────────────────────────────────────────

def main():
    print()
    print("=" * 52)
    print("  LLM Definition Builder")
    print("=" * 52)
    print()

    # 1. Provider
    print("Which provider?")
    for i, p in enumerate(PROVIDERS, 1):
        print(f"  {i}. {p}")
    while True:
        choice = ask("", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(PROVIDERS):
            provider = PROVIDERS[int(choice) - 1]
            break
        print(f"  Please enter a number 1–{len(PROVIDERS)}.")
    print(f"  → {provider}")
    print()

    # 2. Model version / API ID
    while True:
        model_version = ask("Model version / API ID (e.g. gpt-4.1, gpt-5.4-mini)")
        if model_version:
            break
        print("  Required — cannot be empty.")

    # 3. Display name
    display_name = ask("Display name (e.g. GPT-5.4 Mini)", default=model_version)

    # 4. Description
    description = ask("Short description", default=f"{display_name} language model.")

    # 5. LLM vs SLM
    while True:
        size_class = ask("LLM (>7B params) or SLM (<=7B)?", default="LLM").upper().strip()
        if size_class in ("LLM", "SLM"):
            break
        print("  Enter LLM or SLM.")

    # 6. Size tag
    while True:
        size_tag = ask("Size tag (small / medium / large)", default="small").lower().strip()
        if size_tag in ("small", "medium", "large"):
            break
        print("  Enter small, medium, or large.")

    # 7. Modeler
    modeler = ask("Modeler", default="sbxbot")
    print()

    # 8. Folder name
    suggested_folder = to_folder_name(provider, model_version)
    while True:
        folder_name = ask("Folder name", default=suggested_folder)
        err = validate_folder(folder_name)
        if not err:
            break
        print(f"  Error: {err}")

    # 9. Score file name
    suggested_score = to_score_name(folder_name)
    score_file = ask("Score file name", default=suggested_score)
    if not score_file:
        score_file = suggested_score
    if not score_file.endswith('.py'):
        score_file += '.py'
    print()

    # Validate destination
    dest = os.path.join(SCRIPT_DIR, folder_name)
    if os.path.exists(dest):
        print(f"Error: '{folder_name}' already exists. Choose a different folder name.")
        sys.exit(1)

    # Assemble tags: [size_class, ...provider_tags, size_tag]
    tags = [size_class] + _PROVIDER_TAGS.get(provider, []) + [size_tag]

    # Create folder and write files
    print(f"Creating LLM-Definitions/{folder_name}/ ...")
    os.makedirs(dest)
    written = []

    _write_json(dest, "modelConfiguration.json",
                make_model_config(folder_name, score_file, description, modeler, tags))
    written.append("modelConfiguration.json")

    _write_text(dest, score_file, make_score(provider, model_version, score_file))
    written.append(score_file)

    _write_text(dest, "inputVar.json",  _read_base("inputVar.json",  _INPUT_VAR))
    written.append("inputVar.json")

    _write_text(dest, "outputVar.json", _read_base("outputVar.json", _OUTPUT_VAR))
    written.append("outputVar.json")

    _write_json(dest, "options.json",      make_options(provider))
    written.append("options.json")

    _write_json(dest, "requirements.json", make_requirements(provider))
    written.append("requirements.json")

    _write_text(dest, "README.md",
                make_readme(provider, display_name, model_version, score_file))
    written.append("README.md")

    print()
    for f in written:
        print(f"  [OK] {f}")
    print()
    print(f"Done. Review LLM-Definitions/{folder_name}/ then run register-LLMs.py")
    print("to register the model in SAS Model Manager.")
    print()


if __name__ == "__main__":
    main()
