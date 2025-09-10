# Interacting with the LLM Framework

[TOC]

This documentation provides an overview of how to use the different SAS files to be able to do both the prompt experimentation and also running the actual calls against the LLM.

Let us first quickly walk through the required steps:

1.   Get a list of all of the available LLMs in your environment that you have access to.
2.   Generate the base call to a specific LLM.
3.   Modify the base call with your prompt and also play with the options of the LLM.
4.   Run the modified LLM call.
5.   Track the experiment.
6.   Combine experiments into one experiment run.
7.   Evaluate what the best prompt is (optional step).
8.   Save the experiment run to a SAS Model Manager project.

## Retrieve a list of available LLMs

*Note:* This step is also available as a SAS Studio Custom Step with the name **LLM - List Available LLMs**.

Copy the content of the script **Get-List-of-available-LLMs.sas**. If your environment has been setup as specified by this framework you will not need to change any of the defaults here.

Running this script will create the table *_sll_models* in your *work* library. It contains the following three columns:

-   *name*, this is the actual name of the LLM.
-   *id*, this is the SAS Model Manager id of the model.
-   *optionsFileURI*, this is the URI of the options file which contains the definition of the available options when calling this LLM and their specified defaults.

The output table of this script is the input required for the next step.

### Non-Default Environment

If your environment has been setup differently to the defaults specified by this framework you will have to modify the following two macro variables at the top of the script accordingly:

-   *_sll_llm_repository*, here you need to specify the SAS Model Manager repository in which the LLM content is stored - the default is *LLM Repository*.
-   *_sll_llm_project*, here you need to specify the SAS Model manager project that is with in the LLM repository that stored the definition and deployment instructions for the LLMs - the default is *LLM Model Project*.

## Create LLM Call

*Note:* This step is also available as a SAS Studio Custom Step with the name **LLM - Create LLM Call**.

*Note:* If you want to rather call the LLM not with SAS Base than you can make use of the Postman collection to get the call for your LLM of choice.

Copy the content of the script **Create-LLM-Call.sas**. This script will provide you with either a file that contains base call to the LLM and/or prints the base call to the Results tab. You will have to specify a couple of options:

-   *_slo_LLM_name*, this is the name of the target LLM for which you want the LLM call to be generated. You can retrieve this from the output table from the previous step.
-   *_slo_optionsFileURI*, this is the URI of the options file which is associated with the LLM. You can retrieve this from the output table from the previous step.
-   *_slo_LLMSCR*, this is the endpoint under which the LLMs are served. If your environment has been using the defaults of this framework you can leave the default, otherwise ask your administrator for the URL. The default is *%sysfunc(getoption(SERVICESBASEURL))/llm*.
-   *_slo_result*, setting this value to 1 will print the base LLM call code to the Result tab, setting it to 0 will not do that. The default is 1.
-   *_slo_fl*, setting this value to 1 will create a filename with the base LLM call code in it, setting it to 0 will not do that. The default is 1.
-   *_slo_fl_path*, this value only needs to be set if you set *_slo_fl* to 1. You can either specify *temp*, which creates a temporary file, but it is recommend to set a path on the Server - suggested pattern is */path/\<llmName\>Call.sas*. The default is *temp*. Please note that paths in the *SAS Content* are currently not supported!

The output of this script is the input required for the next step.

## Modify the LLM Call

*Note:* This step is not yet available as a SAS Studio Custom Step.

Copy the content of the output generate by the previous step and then at the top you will first find a SAS macro variable for each option that is available for the LLM with its value being set to the default that was provided to the SAS Model Manager. It is suggested that for your first experiment run that you use the defaults, except with you are already an advanced user.

Then after all of the options you will find two macro variables that are responsible for the prompt:

-   *systemPrompt*, here you can specify the behavior of the LLM. What persona should it take on, how should it respond, you can add additional knowledge or context from which the LLM should draw when answering. As a general rule of thumb once you have found a prompt that works for your use case this part of the LLM prompt stays mostly static. The default value is *Your system prompt*.
-   *userPrompt*, here you can specify the variable part of your prompt that would in the real world change every time the model is actually called. The default value is *Your user prompt*.

# Glossary

| Term           | Definition                                                   |
| -------------- | ------------------------------------------------------------ |
| Experiment     | Means a single run of a prompt against a specific LLM with specified options |
| Experiment Run | Contains the same prompt that is sent against multiple different LLMs with specified options |

