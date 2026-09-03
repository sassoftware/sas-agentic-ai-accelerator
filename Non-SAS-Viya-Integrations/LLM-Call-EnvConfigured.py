# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Call an ENVIRONMENT-CONFIGURED LLM (score template azure_openai_env).

The difference from LLM-Call.py is what is NOT here: no API_KEY. Such a model
reads its key, resource/project and deployment from its own container's
environment, so a caller never holds a provider credential and never has to
know which Azure deployment is behind the endpoint.

Everything below is optional. `options` may be '{}' - the container's
environment plus the definition's defaults are a complete configuration.
"""
import json
import requests

llm_endpoint = 'server_url/llm'      # e.g. https://your-viya-host/llm
llm_name = 'azure_openai_env'

# The Viya ingress often uses an internal CA; set to True where the cert chain
# is trusted, or point REQUESTS_CA_BUNDLE at it.
verify_ssl = False

system_prompt = 'You are a helpful assistant that answers in a single short paragraph.'
user_prompt = 'Explain what a SAS Container Runtime model is and why one would deploy an LLM as one.'

# Model parameters as ONE string in the framework's {key:value,key:value} form -
# no quotes, no spaces. For this model they are all optional:
#   reasoning_effort       minimal | low | medium | high | maximum
#   max_completion_tokens  includes the model's internal reasoning tokens
options = '{reasoning_effort:low,max_completion_tokens:2000}'

# Which Azure deployment answers is the container's decision - its
# AZURE_OPENAI_DEPLOYMENT, else the definition's default - and cannot be chosen
# per call: where a model sends its requests is not a scoring option.

payload = json.dumps({
    "inputs": [
      {
        "name": "systemPrompt",
        "value": system_prompt
      },
      {
        "name": "userPrompt",
        "value": user_prompt
      },
      {
        "name": "options",
        "value": options
      }
    ]
})
headers = {'Content-Type': 'application/json'}

response = requests.request('POST', f"{llm_endpoint}/{llm_name}/{llm_name}",
                            headers=headers, data=payload, verify=verify_ssl)

# A model configured by its environment fails in one extra way the others cannot:
# a variable that was never set on the container. The score code says which one,
# so surface the body instead of raising KeyError on a missing 'data'.
if response.status_code != 200:
    raise SystemExit(f"LLM call failed with HTTP {response.status_code}:\n{response.text}")

response_dict = json.loads(response.text)
if 'data' not in response_dict:
    raise SystemExit(f"Unexpected response shape:\n{response.text}")

print(f"Input tokens: {response_dict['data']['prompt_length']}")
print(f"Output tokens: {response_dict['data']['output_length']}")
print(f"Run time: {response_dict['data']['run_time']:.2f}s")
print(f"LLM response: {response_dict['data']['response']}")
