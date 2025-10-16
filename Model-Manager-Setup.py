# Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import argparse
try:
    import pandas as pd
except:
    print('In order to run this script you need to install the pandas package')
    raise
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
parser.add_argument('-rp', '--responsible_party', type=str, help='Enter the person that should be listed as the responsible party for the Model Studio project: Example Person or example@example.com', required=True)
parser.add_argument('-e', '--scr_endpoint', type=str, help='Enter the endpoint under which the LLM containers are published. Example: https://viya-host/llm', required=True)
parser.add_argument('-dt', '--deployment_type', type=str, default='k8s', help='Enter the type of deployment, can be k8s (LLM & Embedding is deployed in k8s) or aca (Azure Container App)', required=False)
parser.add_argument('-k', '--verify_ssl', type=str, default='true', help='Set to false if you have a self-signed certificat')
args = parser.parse_args()

# Define the LLM project attributes
project_attributes = {}
project_attributes['project_responsible_party'] = args.responsible_party
project_attributes['project_repository'] = 'LLM Repository'
project_attributes['project_repository_description'] = 'This repository is used to register LLM deployment instructions to, build, monitor and deploy use cases that take advantage of LLMs'
project_attributes['project_name'] = 'LLM Model Project'
project_attributes['project_model_function'] = 'LLM'
project_attributes['project_description'] = 'This project stores all LLMs that are available to be used in use cases. It is possible to grant access to these models on a per model basis. Along side the availability this also documents on how to deploy/call the models.'
project_attributes['project_tags'] = ['LLM-Models', 'SCR-Definitions', 'Python']

# Create the output structure JSON that is used for the LLM Prompt Builder
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

# Define the Embedding project attributes
rag_project_attributes = {}
rag_project_attributes['project_responsible_party'] = args.responsible_party
rag_project_attributes['project_repository'] = 'LLM Repository'
rag_project_attributes['project_repository_description'] = 'This repository is used to register LLM deployment instructions to, build, monitor and deploy use cases that take advantage of LLMs'
rag_project_attributes['project_name'] = 'Embedding Model Project'
rag_project_attributes['project_model_function'] = 'Embedding'
rag_project_attributes['project_description'] = 'This project stores all Embedding models that are available to be used in use cases. It is possible to grant access to these models on a per model basis. Along side the availability this also documents on how to deploy/call the models.'
rag_project_attributes['project_tags'] = ['Embedding-Models', 'SCR-Definitions', 'Python']

# Create the output structure JSON that is used for the RAG Builder
rag_builder = {
    'name': 'RAG Builder',
    'id': 'RBO',
    'width': 0,
    'type': 'ragBuilder',
    'modelRepositoryID': '',
    'embeddingProjectID': '',
    'SCREndpoint': args.scr_endpoint,
    'deploymentType': args.deployment_type
}

# Variables for the additional output file creation
llm_repository_folder = ''
llm_repository = ''

# Get the input variables
def get_project_variables(base_path):
    """
        Returns an array containing the input and output variables
        Requires the inputVar.json and outputVar.json to be available
        in the base_path location
    """
    # Add the role of input to the input variables
    input_var_JSON = pd.read_json(f"{base_path}/inputVar.json")
    project_input_variables = list(pd.DataFrame.to_dict(input_var_JSON.transpose()).values())
    for var in project_input_variables:
        var['role'] = 'input'

    # Add the role of output to the output variables
    output_var_JSON = pd.read_json(f"{base_path}/outputVar.json")
    project_output_variables = list(pd.DataFrame.to_dict(output_var_JSON.transpose()).values())
    for var in project_output_variables:
        var['role'] = 'output'

    # Join all variables into an array to register with SAS Model Manager
    project_variables = project_input_variables + project_output_variables
    return project_variables

# Establish a session
try:
    with Session(args.viya_server, args.username, args.password,  verify_ssl = (args.verify_ssl.lower() == 'true')) as s:
        # Check if the repository exists
        repository_exists = mr.get_repository(project_attributes['project_repository'])
        if repository_exists == None:
            data = {
                "name": project_attributes['project_repository'],
                "description": project_attributes['project_repository_description'],
                "defaultRepository": False,
                "version": 2
            }

            headers = {
                "Content-Type": "application/vnd.sas.models.repository+json",
                "Accept": "application/vnd.sas.models.repository+json"
            }
            respository_response = s.post(f'/modelRepository/repositories', data=json.dumps(data), headers=headers)
            print(f"The SAS Model Manager repository {project_attributes['project_repository']} was created.")
            if respository_response.status_code == 201:
                repository_exists = mr.get_repository(project_attributes['project_repository'])
                llm_prompt_builder['modelRepositoryID'] = respository_response.json()['id']
                rag_builder['modelRepositoryID'] = respository_response.json()['id']
                llm_repository = respository_response.json()['id']
                llm_repository_folder = respository_response.json()['folderId']
            else:
                raise ValueError(f"The {project_attributes['project_repository']} doesn't exist and couldn't be created. You might need help from your SAS Administrator - see: https://go.documentation.sas.com/doc/en/mdlmgrcdc/default/mdlmgrug/n1rip1yj5462z5n1kt9z3wzcnnb5.htm")
        
        # Check if the LLM project exsits
        project_name = mr.get_project(project_attributes['project_name'])

        # If the project doesn't exist it will be created
        if project_name == None:
            project_attributes['project_variables'] = get_project_variables('./LLM-Definitions/_Base_Definition')
            project = mr.create_project(project = project_attributes['project_name'],
                description = project_attributes['project_description'],
                repository = project_attributes['project_repository'],
                variables = project_attributes['project_variables'],
                targetLevel = 'NOMINAL',
                targetVariable = 'response',
                function = project_attributes['project_model_function'],
                modelResponsibleParty = project_attributes['project_responsible_party'],
                tags = project_attributes['project_tags'])
            llm_prompt_builder['llmProjectID'] = project.id
            # Output the file for the Prompt Builder UI
            with open('llm-prompt-builder.json', 'w') as f:
                json.dump(llm_prompt_builder, f, indent=4)
            # Output the file for the Rules and Group creation
            with open('sas-viya-cli-commands.txt', 'w') as f:
                f.write('# This script is written for Windows, update the commands accordingly\n')
                f.write('# Each command comes with a description, please read it and the documentation before running anything\n\n')
                f.write('# First a Custom Group is created called LLM Consumers - if you do not want use this group, skip this step and replace the name in subsequent commands\n')
                f.write('sas-viya identities create-group --id LLMConsumers --name "LLM Consumers" --description "This group enables a general access to the LLM repository. This group is meant for anybody that requires access to it."\n')
                f.write('# Add members to the LLM Consumers group\n')
                f.write('sas-viya identities add-member --group-id LLMConsumers --group-member-id GroupYouWantToAdd\n\n')
                f.write('# Second a Custom Group is created called Prompt Engineers - if you do not want use this group, skip this step and replace the name in subsequent commands\n')
                f.write('sas-viya identities create-group --id PromptEngineers --name "Prompt Engineers" --description "This group enables its members to create, update and delete Prompt Engineering projects in the LLM repository"\n')
                f.write('# Add members to the Prompt Engineers group\n')
                f.write('sas-viya identities add-member --group-id PromptEngineers --group-member-id GroupYouWantToAdd\n\n')
                f.write('# Create two rules that open up access to the LLM Repository for the LLM Consumers\n')
                f.write(f'sas-viya authorization create-rule -o /folders/folders/{llm_repository_folder} -g LLMConsumers -p Read,Add,Remove -d "Enables the LLM Consumers to interact with the LLM repository" --reason "You are not part of the LLM Consumers group"\n')
                f.write(f'sas-viya authorization create-rule --container-uri /folders/folders/{llm_repository_folder} -g LLMConsumers -p Read,Add,Update,Remove,Delete -d "Enables the LLM Consumers to interact with the LLM repository" --reason "You are not part of the LLM Consumers group"\n\n')
                f.write('# Create a rule to enable the Prompt Engineers to create new projects in the LLM repository\n')
                f.write(f'sas-viya authorization create-rule -o /modelRepository/repositories/{llm_repository} -g PromptEngineers -p Read,Add,Create,Update,Remove,Delete -d "Enables the group to create prompt engineering projects in the LLM repository" --reason "You are not part of the prompt engineering group"\n')
            print('The llm-promp-builder.json is a quick start for the Prompt Builder UI.')
            print('The sas-viya-cli-commands.txt is a quick start to setup up the groups and authorization for the framework.')
        
        # Check if the Embedding project exsits
        project_name = mr.get_project(rag_project_attributes['project_name'])

        # If the project doesn't exist it will be created
        if project_name == None:
            rag_project_attributes['project_variables'] = get_project_variables('./Embedding-Definitions/_Base_Definition')
            project = mr.create_project(project = rag_project_attributes['project_name'],
                description = rag_project_attributes['project_description'],
                repository = rag_project_attributes['project_repository'],
                variables = rag_project_attributes['project_variables'],
                targetLevel = 'NOMINAL',
                targetVariable = 'response',
                function = rag_project_attributes['project_model_function'],
                modelResponsibleParty = rag_project_attributes['project_responsible_party'],
                tags = rag_project_attributes['project_tags'])
            rag_builder['embeddingProjectID'] = project.id
            # Output the file for the Prompt Builder UI
            with open('rag-builder.json', 'w') as f:
                json.dump(rag_builder, f, indent=4)
            # Output the file for the RAG builder
            print('The rag-builder.json is a quick start for the RAG Builder UI.')
except:
    print(f'Failed to establish a connection to {args.viya_server} with the user {args.username} and the password {args.password}.')
    print('Make sure that the above values are valid - if that is the case, maybe try using the option -k False, to skip SSL verification.')
    raise