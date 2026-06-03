import time
import json
import logging
import sys
import requests
import tiktoken

# Requires an Azure OpenAI / Azure AI Foundry deployment
modelVersion = 'gpt-5.4-mini'
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
        "api_version": "2024-12-01-preview",
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


# Example usage — uncomment, replace API_KEY, and run: python gpt54MiniAzScore.py
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
