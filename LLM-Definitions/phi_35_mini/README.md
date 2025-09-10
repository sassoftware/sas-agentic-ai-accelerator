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