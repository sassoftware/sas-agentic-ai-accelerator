# Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import time
import json
import os
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

parser = argparse.ArgumentParser(description='This script registers LLMs to SAS Model Manager')
parser.add_argument('-vs', '--viya_server', type=str, help='Enter the URL for the SAS Viya server. An example is example.sas.com', required=True)
parser.add_argument('-u', '--username', type=str, help='Enter your username for the SAS Viya server', required=True)
parser.add_argument('-p', '--password', type=str, help='Enter your password for the SAS Viya server', required=True)
parser.add_argument('-e', '--scr_endpoint', type=str, help='Enter the endpoint under which the LLM containers are published. Example: https://viya-host/llm', required=True)
parser.add_argument('-l','--llms', nargs='+', help='List of LLM names Decide on the models that you want to be registered - specify the subfolder name, that folder needs to contain a modelConfiguration.json (e.g., phi_3_mini_4k phi_35_mini)',  required=True)
parser.add_argument('-rp', '--responsible_party', type=str, help='Enter the person that should be listed as the responsible party for the Model Studio project: Example Person or example@example.com', required=True)
parser.add_argument('-k', '--verify_ssl', type=bool, default=True, help='Set to false if you have a self-signed certificat')
args = parser.parse_args()

# Define the project attributes
project_attributes = {}
project_attributes['project_responsible_party'] = args.responsible_party
project_attributes['project_repository'] = 'LLM Repository'
project_attributes['project_repository_description'] = 'This repository is used to register LLM deployment instructions to, build, monitor and deploy use cases that take advantage of LLMs'
project_attributes['project_name'] = 'LLM Model Project'
project_attributes['project_model_function'] = 'LLM'
project_attributes['project_description'] = 'This project stores all LLMs that are available to be used in use cases. It is possible to grant access to these models on a per model basis. Along side the availability this also documents on how to deploy/call the models.'
project_attributes['project_tags'] = ['LLM-Models', 'SCR-Definitions', 'Python']

# Import the fact sheet for additional data about the LLMs
try:
    llm_fact_sheet = pd.read_csv('./llm_fact_sheet.csv')
except:
    print('Unable to locate the llm_fact_sheet.csv in the working directory.')
    print('Please ensure that the file is available in the same directory as the register-LLMs.py script')
    raise

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

def get_model_attributes(base_path):
    """
        Returns an object containing the model attributes
    """   
    with open(f"{base_path}/modelConfiguration.json", 'r') as modelConfiguration:
        model_attributes = json.load(modelConfiguration)
    
    return model_attributes

def update_model_tags(model_attributes, model_id):
    model_details = mr.get_model_details(model_id)
    headers = {
            "Content-Type": "application/vnd.sas.models.model+json",
            "Accept": "application/vnd.sas.models.model+json",
            "If-Match": model_details._headers["ETag"]
        }
    model_response = dict(model_details.items())
    model_response['tags'] = model_attributes['tags']
    res = s.put(f'/modelRepository/models/{model_details.id}', data=json.dumps(model_response), headers=headers)
    if res.status_code == 200:
        print("The model tags have been updated")
    
def register_model(base_path):
    model_attributes = get_model_attributes(base_path)
    model_data = llm_fact_sheet[llm_fact_sheet['model_id'] == base_path]
    if model_data.empty:
        print(f"The selected LLM: {base_path} is not vailable in the llm_fact_sheet.csv. This is a required entry!")
    else:
        # Add additional model metadata from the llm_fact_sheet.csv
        model_attributes['llmmodelType'] = model_attributes.get('llmmodelType', 'GPT')
        model_data_dict = model_data.to_dict('records')[0]
        model_attributes['provider'] = model_data_dict.get('provider', 'Unkown')
        model_attributes['endPoint'] = f"{args.scr_endpoint}/{base_path}/{base_path}"
        cost_estimation_type = model_data_dict.get('cost_type', 'Tokens')
        costPerCall = 0
        if (cost_estimation_type == 'Tokens'):
            costPerCall = (float(model_data_dict.get('input_token_price', '0')) + float(model_data_dict.get('output_token_price', '0'))) / 2
        elif (cost_estimation_type == 'Seconds'):
            costPerCall = float(model_data_dict.get('second_cost', '0'))
        else:
            print(f"The cost type for LLMs has to be either Tokens or Seconds, the value provided was: {cost_estimation_type} for {base_path}")
        model_attributes['costPerCall'] = costPerCall
        model_object = mr.create_model(model = model_attributes, project = project_attributes['project_name'])
    
        time.sleep(1)
        # Score script
        file = open(f"{base_path}/{model_attributes['scoreCodeFile']}", 'rb')
        mr.add_model_content(model_object,
                        file, 
                        name = model_attributes['name'] + '.py',
                        role = 'score')
        file.close()

        # Dependencies
        file = open(f"{base_path}/requirements.json", 'rb')
        mr.add_model_content(model_object,
                        file, 
                        name = 'requirements.json',
                        role = 'python pickle')
        file.close()

        # Output variables
        file = open(f"{base_path}/outputVar.json", 'rb')
        mr.add_model_content(model_object, file, name = 'outputVar.json')
        file.close()
    
        # Input variables
        file = open(f"{base_path}/inputVar.json", 'rb')
        mr.add_model_content(model_object, file, name = 'inputVar.json')
        file.close()

        # Options information
        file = open(f"{base_path}/options.json", 'rb')
        mr.add_model_content(model_object, file, name = 'options.json', role='documentation')
        file.close()

        # Upload the optional model card
        if os.path.exists(f"{base_path}/Model-Card.pdf"):
            file = open(f"{base_path}/Model-Card.pdf", 'rb')
            mr.add_model_content(model_object, file, name = 'Model-Card.pdf', role = 'documentation')
        elif os.path.exists(f"{base_path}/Model-Card.md"):
            file = open(f"{base_path}/Model-Card.md", 'rb')
            mr.add_model_content(model_object, file, name = 'Model-Card.md', role = 'documentation')
        else:
            print(f"No model card added for {base_path}")

        # Upload toknizer filesif they exist
        if os.path.exists(f"{base_path}/tokenizer_config.json"):
            file = open(f"{base_path}/tokenizer_config.json", 'rb')
            mr.add_model_content(model_object, file, name = 'tokenizer_config.json', role = 'documentation')
        if os.path.exists(f"{base_path}/special_tokens_map.json"):
            file = open(f"{base_path}/special_tokens_map.json", 'rb')
            mr.add_model_content(model_object, file, name = 'special_tokens_map.json', role = 'documentation')
        if os.path.exists(f"{base_path}/tokenizer.json"):
            file = open(f"{base_path}/tokenizer.json", 'rb')
            mr.add_model_content(model_object, file, name = 'tokenizer.json', role = 'documentation')

        update_model_tags(model_attributes, model_object.id)
        print(f"Link to the model in SAS Model Manager: {args.viya_server}/SASModelManager/models/{model_object.id}")
        return model_object

# Establish a session
try:
    with Session(args.viya_server, args.username, args.password,  verify_ssl = args.verify_ssl) as s:
        # Check if the repository exists
        repository_exists = mr.get_repository(project_attributes['project_repository'])
        if repository_exists == None:
            raise ValueError(f"The SAS Model Manager repository {project_attributes['project_repository']} doesn't exist. Please ensure that the setup instructions were followed correctly.")
        
        # Check if the project exsits
        project_name = mr.get_project(project_attributes['project_name'])
        if project_name == None:
            raise ValueError(f"The SAS Model Manager project {project_attributes['project_name']} doesn't exist. Please ensure that the setup instructions were followed correctly.")

        # Iterate over the models that are listed for registration
        for model in args.llms:
            print(f"Working on registering {model}")
            registered_models = mr.list_models(filter = f"eq(projectName, '{project_name}')")

            # If there are no models then it will be registered
            if len(registered_models) == 0:
                model_name = register_model(model)
            else:
                # If there are models check if the model already exists
                modelExistsCheck = False
                for registered_model in registered_models:
                    # If it exsits give the user a link to it
                    if registered_model.name == model:
                        modelExistsCheck = True
                        print(f"The {model} is already registered and will be skipped.")
                        print(f"Link to the model in SAS Model Manager: {args.viya_server}/SASModelManager/models/{registered_model.id}")
                if modelExistsCheck == False:
                    model_name = register_model(model)
except:
    print(f'Failed to establish a connection to {args.viya_server} with the user {args.username} and the password {args.password}.')
    print('Make sure that the above values are valid - if that is the case, maybe try using the option -k False, to skip SSL verification.')
    raise