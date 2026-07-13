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

# Configuration can be supplied via CLI args, environment variables, or a .env
# file. Precedence: CLI arg > environment variable > .env value > default.
import os
import getpass
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True))  # real env vars take precedence over .env
except ImportError:
    pass  # python-dotenv is optional; OS environment variables are still honored

parser = argparse.ArgumentParser(description='This script sets up the LLM repository and LLM Model Project in SAS Model Manager')
parser.add_argument('-vs', '--viya_server', type=str, default=os.environ.get('SAS_VIYA_URL'), help='URL for the SAS Viya server, e.g. example.sas.com (env: SAS_VIYA_URL)')
parser.add_argument('-u', '--username', type=str, default=os.environ.get('SAS_VIYA_USER'), help='Username for the SAS Viya server (env: SAS_VIYA_USER)')
parser.add_argument('-p', '--password', type=str, default=os.environ.get('SAS_VIYA_PASSWORD'), help='Password for the SAS Viya server (env: SAS_VIYA_PASSWORD); prompted if omitted')
parser.add_argument('-e', '--scr_endpoint', type=str, default=os.environ.get('SAS_SCR_ENDPOINT'), help='Endpoint under which the LLM containers are published, e.g. https://viya-host/llm (env: SAS_SCR_ENDPOINT)')
parser.add_argument('-dt', '--deployment_type', type=str, default=os.environ.get('SAS_DEPLOYMENT_TYPE', 'k8s'), help='Deployment type k8s (default) or aca (Azure Container App) (env: SAS_DEPLOYMENT_TYPE)')
parser.add_argument('-k', '--verify_ssl', type=str, default=os.environ.get('SAS_VIYA_VERIFY_SSL', 'true'), help='Set to false if you have a self-signed certificate (env: SAS_VIYA_VERIFY_SSL)')
args = parser.parse_args()

# Prompt for the password if it was not supplied via CLI, environment, or .env
if not args.password:
    args.password = getpass.getpass('SAS Viya password: ')

# The following are required regardless of where they are supplied from
_missing = [name for name, value in {
    '--viya_server / SAS_VIYA_URL': args.viya_server,
    '--username / SAS_VIYA_USER': args.username,
    '--scr_endpoint / SAS_SCR_ENDPOINT': args.scr_endpoint,
}.items() if not value]
if _missing:
    parser.error('Missing required configuration (provide via CLI, environment variable, or .env): ' + ', '.join(_missing))



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
    print(f'Failed to establish a connection to {args.viya_server}.')
    print('Make sure that the above values are valid - if that is the case, maybe try using the option -k False, to skip SSL verification.')
    raise