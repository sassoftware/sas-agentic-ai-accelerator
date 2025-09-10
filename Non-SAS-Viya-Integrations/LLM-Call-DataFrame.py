# Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import requests
import pandas as pd

llm_endpoint = 'server_url/llm'
llm_name = 'llama_31_405b'

system_prompt = 'You are a heplful assitant that provides a short summary of the users input'
options = '{temperature:0.6,max_tokens:1024}'

def call_LLM_per_Row(userPromptVar=''):
    """
    Function to call the LLM for each row in a data frame, while passing a variable into the user prompt.

    Parameters
    ----------
    userPromptVar : str, optional
        A string variable inserted into the prompt to customize it. Defaults to an empty string.

    Returns
    -------
    tuple
        A tuple containing:
          - int: The length of the prompt sent to the LLM (`prompt_length`).
          - int: The length of the generated output (`output_length`).
          - str: The LLM-generated response text.
    """
    user_prompt = f"A Boy was given permission to put his hand into a pitcher to get some filberts {userPromptVar} times. But he took such a great fistful that he could not draw his hand out again. There he stood, unwilling to give up a single filbert and yet unable to get them all out at once. Vexed and disappointed he began to cry.\n\n\"My boy,\" said his mother, \"be satisfied with half the nuts you have taken and you will easily get your hand out. Then perhaps you may have some more filberts some other time.\""
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
    return respons_dict['data']['prompt_length'], respons_dict['data']['output_length'], respons_dict['data']['response']

# Create the example data set
df = pd.DataFrame({
    'value1': [1, 2, 3, 4, 5],
    'value2': [10, 20, 30, 40, 50]
})

# Call the function on each row
df[['prompt_length', 'output_length', 'response']] = df['value1'].apply(lambda x: pd.Series(call_LLM_per_Row(x)))

# Display the results
print(df)
# Or if you are running in SAS return it as a SAS dataset
SAS.df2sd(df, 'work.LLM_Results')