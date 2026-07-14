# Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import time
import argparse
try:
    from sasctl import Session
    from sasctl.services import model_publish as mp
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

parser = argparse.ArgumentParser(description='This script registers LLMs to SAS Model Manager')
parser.add_argument('-vs', '--viya_server', type=str, default=os.environ.get('SAS_VIYA_URL'), help='URL for the SAS Viya server, e.g. example.sas.com (env: SAS_VIYA_URL)')
parser.add_argument('-u', '--username', type=str, default=os.environ.get('SAS_VIYA_USER'), help='Username for the SAS Viya server (env: SAS_VIYA_USER)')
parser.add_argument('-p', '--password', type=str, default=os.environ.get('SAS_VIYA_PASSWORD'), help='Password for the SAS Viya server (env: SAS_VIYA_PASSWORD); prompted if omitted')
parser.add_argument('-l','--llms', nargs='+', help='List of LLM names Decide on the models that you want to be registered - specify the subfolder name, that folder needs to contain a modelConfiguration.json (e.g., phi_3_mini_4k phi_35_mini)',  required=True)
parser.add_argument('-d', '--destination', type=str, default=os.environ.get('SAS_PUBLISH_DESTINATION'), help='Name of the target container publishing destination, e.g. llmACR (env: SAS_PUBLISH_DESTINATION)')
parser.add_argument('-k', '--verify_ssl', type=str, default=os.environ.get('SAS_VIYA_VERIFY_SSL', 'true'), help='Set to false if you have a self-signed certificate (env: SAS_VIYA_VERIFY_SSL)')
args = parser.parse_args()

# Prompt for the password if it was not supplied via CLI, environment, or .env
if not args.password:
    args.password = getpass.getpass('SAS Viya password: ')

# The following are required regardless of where they are supplied from
_missing = [name for name, value in {
    '--viya_server / SAS_VIYA_URL': args.viya_server,
    '--username / SAS_VIYA_USER': args.username,
    '--destination / SAS_PUBLISH_DESTINATION': args.destination,
}.items() if not value]
if _missing:
    parser.error('Missing required configuration (provide via CLI, environment variable, or .env): ' + ', '.join(_missing))

# Specify a wait time, if your SCR jobs consume to many resources - this will add a delay between publishing in seconds
time_out = 1

# Establish a session
try:
    with Session(args.viya_server, args.username, args.password,  verify_ssl = (args.verify_ssl.lower() == 'true')) as s:
        destination = mp.get_destination(args.destination)
        if destination is None:
            raise ValueError(f"No valid destination name specified. Please check the name: {args.destination}")
        elif destination.destinationType not in ['azure', 'aws', 'gcp', 'privatedocker', 'AWS', 'GCP', 'privateDocker']:
            raise ValueError(f"The provided destination is not a valid SCR destination. Please check: {args.destination}")
        
        for model in args.llms:
            model_details = mr.get_model_details(model)
            headers = {
                "Content-Type": "application/vnd.sas.models.publishing.request.asynchronous+json",
                "Accept": "application/vnd.sas.models.publishing.publish+json"
            }
            tag_comparison = set(model_details['tags']) & set(['small', 'medium', 'large'])
            if tag_comparison:
                sizing_tag = tag_comparison.pop()
            else:
                print('No sizing tag found, defaulting to small')
                sizing_tag = "small"
            model_response = dict(model_details.items())
            model_response['tags'] = sizing_tag
            payload = f'''{{
                "destinationName": "{args.destination}",
                "modelContents": [
                    {{
                        "modelName": "{model}",
                        "publishLevel": "model",
                        "sourceUri": "/modelRepository/models/{model_details['id']}"
                    }}
                ],
                "name": "{model}",
                "notes": "Published by LLM Framework",
                "tags": ["{sizing_tag}"]
            }}'''
            res = s.post('/modelManagement/publish?force=true', data=payload, headers=headers)
            if res.status_code == 201:
                print(f'Waiting for {time_out} seconds before continuing')
                time.sleep(time_out)
                print(f'The model {model} is being published to the destination {args.destination}. Depending on the model, this can take several minutes.')
            else:
                print(f'The publishing of mode {model} to the publishing destination {args.destination} failed with the status_code {res.status_code}')
except:
    print(f'Failed to establish a connection to {args.viya_server}.')
    print('Make sure that the above values are valid - if that is the case, maybe try using the option -k False, to skip SSL verification.')
    raise