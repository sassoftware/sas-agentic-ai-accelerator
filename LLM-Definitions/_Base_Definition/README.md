# Base Definition

This folder contains the absolute basic structure that will have to be reused for each model.

If you want to add a new model please make a copy of this one, rename it to the model name (ensure URL conformant names) and then start editing the files as you need.

Short explanation of each file here:

- *inputVar.json*: contains the definition of the input variables, do not change this file.
- *modelConfiguration.json*: contains the definition of the model metadata, do change this file.
- *options.json*: contains the description of the options object that can optionally be passed to the LLM.
- *outputVar.json*: contains the definition of the output variables, do not change this file.
- *requirements.json*: contains a list of steps that need to be performed in order to change the container, that make the model run.

Note on the *modelConfiguration.json*: The tag list should contain one of the following three tags: *small, medium or large*. These are used as a sizing indicator for the final SCR deplyoment.

Rule of thumb for the sizing tag:

- *small*, use this when you are deploying a proprietary model wrapper
- *medium*, use this when you are deploying an open-source model with less than 3B parameters
- *large*, use this when you are deploying an open-source model between 3B and 8B parameters

As currently no GPUs are supported in this framework, it isn't recommended to deploy LLMs larger than 8B parameters as the performance hit is to big or the models just will not be able to run at all.

Optional files:
- *Model-Card.pdf or Model-Card.md* : contains additional information about a model to help understand how it should be used, evaluation benchmarks, etc.

## How to Add a New Proprietary Model

### Summary

This guide shows you how to add a proprietary LLM of your choice to the model repository. Key tasks include updating code, metadata, and dependencies. Once completed, you’ll register, publish, deploy, and test the model for scoring.

Read through the steps demonstrating how to register an LLM you might want to use.

### Steps

1. Navigate to the **sas-llm-ucf** folder.
1. Copy the **LLM-Definitions/_Base_Definition/** folder under **LLM-Definitions**.
1. Rename it to *new_model_name* (ensure URL-conformant names) and edit the files as needed. Use *gpt_4o_mini_az_2024_07_18* as an example, a model added recently.

### File Overview

- **`model_name.py`**: The main file. Key areas to adapt:
  - *modelVersion, modelEndpoint*. (There might be other variables, depending on the model).
  - Inside the `scoreModel` function:
    - Check or update *optionsDefaults* (a list specific to your model).
    - Adjust request headers per your LLM's API documentation.
  - Test the `scoreModel` function with sample data. Example:

    ```py
      userPrompt = ["Count to ten in French"]  # Note: The prompt should be in a list
      systemPrompt = ["You are an AI Assistant helping people learn languages"]  # Note: The prompt should be in a list
      # options list
      options =["{temperature:1,top_p:1,API_KEY:*****}"]
      ```

- **`requirements.json`**: Lists steps for the model to run (e.g., python packages, files to download). Add any packages used in `model_name.py`.
- **`modelConfiguration.json`**: Defines model metadata. Update:
  - Minimum info: **name, scoreCodeFile, description, tags**
  - Optional info: modeler, Python version, etc.
  - Note: Include one of these tags in the tag list: *small, medium, large* (used for sizing in SCR deployment).
- **`inputVar.json`**: Defines input variables. Do not modify this file.
- **`options.json`**: Describes the options object that can optionally be passed to the LLM.
- **`outputVar.json`**: Defines output variables. Do not modify this file.

### Final Steps

After adapting files in your *model_name* folder and testing the code, you need to:

1. **Register the model**: Use the Python script to register the model in SAS Model Manager.
2. **Publish the model**: Use the Python script to publish it to a container destination, Azure, for example.
3. **Deploy the model**: For example, create an Azure container or deploy to a Kubernetes pod form the published Docker image.
4. **Score the deployed model**: Score the model using data.
