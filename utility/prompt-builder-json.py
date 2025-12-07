# Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import argparse
try:
    from sasctl import Session
    from sasctl.services import model_repository as mr
except:
    print('In order to run this script you need to install the sasctl package')
    raise

parser = argparse.ArgumentParser(description='This script sets up the LLM repository and LLM Model Project in SAS Model Manager')
parser.add_argument('-vs', '--viya_server', type=str, help='Enter the URL for the SAS Viya server. An example is example.sas.com', required=True)
parser.add_argument('-u', '--username', type=str, help='Enter your username for the SAS Viya server', required=True)
parser.add_argument('-p', '--password', type=str, help='Enter your password for the SAS Viya server', required=True)
parser.add_argument('-e', '--scr_endpoint', type=str, help='Enter the endpoint under which the LLM containers are published. Example: https://viya-host/llm', required=True)
parser.add_argument('-dt', '--deployment_type', type=str, default='k8s', help='Enter the type of deployment, can be k8s (LLM & Embedding is deployed in k8s) or aca (Azure Container App)', required=False)
parser.add_argument('-k', '--verify_ssl', type=str, default='true', help='Set to false if you have a self-signed certificat')
args = parser.parse_args()



llm_prompt_builder = {
    'name': 'LLM Prompt Builder',
    'id': 'LPB',
    'width': 0,
    'type': 'promptBuilder',
    'modelRepositoryID': '',
    'llmProjectID': '',
    'SCREndpoint': args.scr_endpoint,
    'API_KEYS': {
        'Anthropic': 'key-value',
        'OpenAI': 'key-value',
        'Google': 'key-value'
    },
    'deploymentType': args.deployment_type
}

# Establish a session
try:
    with Session(args.viya_server, args.username, args.password,  verify_ssl = (args.verify_ssl.lower() == 'true')) as s:
        repository_exists = mr.get_repository('LLM Repository')
        if repository_exists is None:
            raise Exception('LLM Repository does not exist. Please create the LLM Repository before running this script.')
        llm_prompt_builder['modelRepositoryID'] = repository_exists['id']
        project_exists = mr.get_project('LLM Model Project')
        if project_exists is None:
            raise Exception('LLM Model Project does not exist. Please create the LLM Model Project before running this script.')
        llm_prompt_builder['llmProjectID'] = project_exists['id']
        # Output the file for the Prompt Builder UI
        with open('llm-prompt-builder.json', 'w') as f:
            json.dump(llm_prompt_builder, f, indent=4)
except:
    print(f'Failed to establish a connection to {args.viya_server} with the user {args.username} and the password {args.password}.')
    print('Make sure that the above values are valid - if that is the case, maybe try using the option -k False, to skip SSL verification.')
    raise