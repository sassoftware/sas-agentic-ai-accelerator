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

parser = argparse.ArgumentParser(description='This script registers LLMs to SAS Model Manager')
parser.add_argument('-vs', '--viya_server', type=str, help='Enter the URL for the SAS Viya server. An example is example.sas.com', required=True)
parser.add_argument('-u', '--username', type=str, help='Enter your username for the SAS Viya server', required=True)
parser.add_argument('-p', '--password', type=str, help='Enter your password for the SAS Viya server', required=True)
parser.add_argument('-m','--embedding_models', nargs='+', help='List of Embedding model names. Decide on the models that you want to be registered - specify the subfolder name, that folder needs to contain a modelConfiguration.json (e.g., gemini_embedding_001 text_embedding_3_small)',  required=True)
parser.add_argument('-d', '--destination', type=str, help='Specify the name of the target publishing destination, has to be a container publishing destination - i.e. llmACR', required=True)
parser.add_argument('-k', '--verify_ssl', type=bool, default=True, help='Set to false if you have a self-signed certificat')
args = parser.parse_args()

# Specify a wait time, if your SCR jobs consume to many resources - this will add a delay between publishing in seconds
time_out = 1

# Establish a session
try:
    with Session(args.viya_server, args.username, args.password,  verify_ssl = args.verify_ssl) as s:
        destination = mp.get_destination(args.destination)
        if destination is None:
            raise ValueError(f"No valid destination name specified. Please check the name: {args.destination}")
        elif destination.destinationType not in ['azure', 'aws', 'gcp', 'privatedocker', 'AWS', 'GCP', 'privateDocker']:
            raise ValueError(f"The provided destination is not a valid SCR destination. Please check: {args.destination}")
        
        for model in args.embedding_models:
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
    print(f'Failed to establish a connection to {args.viya_server} with the user {args.username} and the password {args.password}.')
    print('Make sure that the above values are valid - if that is the case, maybe try using the option -k False, to skip SSL verification.')
    raise