# Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import requests

llm_endpoint = 'server_url/llm'
llm_name = 'llama_31_405b'

system_prompt = 'You are a heplful assitant that provides a short summary of the users input'
user_prompt = 'A Boy was given permission to put his hand into a pitcher to get some filberts. But he took such a great fistful that he could not draw his hand out again. There he stood, unwilling to give up a single filbert and yet unable to get them all out at once. Vexed and disappointed he began to cry.\n\n\"My boy,\" said his mother, \"be satisfied with half the nuts you have taken and you will easily get your hand out. Then perhaps you may have some more filberts some other time.\"'
options = '{temperature:0.6,max_tokens:1024}'

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

response = requests.request('POST', f"{llm_endpoint}/{llm_name}/{llm_name}", headers=headers, data=payload)
respons_dict = json.loads(response.text)
print(f"Input tokens: {respons_dict['data']['prompt_length']}")
print(f"Output tokens: {respons_dict['data']['output_length']}")
print(f"LLM response: {respons_dict['data']['response']}")