# SAS Viya Integrations

In this folder you find integrations that are purpose built for this framework that enable you to make use of the deployed LLMs in different SAS Viya applications often even in a Low Code/No Code fashion.

## Importing the Standard Assets [Under Heavy Constructions]

The [SAS LLM Use Case Framework.json](./SAS-LLM-Use-Case-Framework.json) is a transfer package that can be imported using the SAS Environment Manager and it contains the following - each indent represent a subfolder until the lowest level where it represents files, in square brackets is a note where to find more information on the different integrations:

```
SAS Content
  - SAS LLM Use Case Framework
    - Custom Steps [Custom Steps]
      - LLM - Log Parser.step
    - Logging and Monitoring [Logging and Monitoring]
      - Log-Parser-Code.sas
      - Monitoring Baseline
```

## List of Available Integrations

As this list is ever growing the examples have been split into different subfolders to help with the organization. The individual files are still explained here.

On the top level of this folder you will find the following:

-   *createLLMRepository.sas*, run this script from inside a SAS session on the SAS Viya server, this creates the LLM Repository in the SAS Model Manager - you will only need to run this script once per environment.

### Custom Steps

Here you find a list of available SAS Studio Custom Steps, note that for using these steps you will have to import them to SAS Content and have a SAS Studio Analyst license. Note that the steps starting with *MM* are not specific to this project, but have been build a long the way and are provided here for completeness:

-   *LLM - Create LLM Call.step*, creates the call template for an LLM with SAS code.
-   *LLM - Get All Prompts.step*, creates a unified table of all the prompting projects, prompt templates and their experiments for reporting.
-   *LLM - Get Options.step*, can be run from SAS Studio and returns code to run a specific LLM.
-   *LLM - List Available LLMs.step*, can be run from SAS Studio and returns a table with all available LLMs.
-   *LLM - Log Parser.step*, can be used to parse the log file.
-   *MM - Get All Repositories.step*, can be run from SAS Studio and returns a list of all available SAS Model Manager repositories.
-   *MM - Get Projects in Repository.step*, can be run from SAS Studio and returns a list of all available projects in a SAS Model Manager repository.
-   *MM - Get Models in Project.step*, can be run from SAS Studio and returns a list of all models in a SAS Model Manager project.
-   *MM - Get Model Information.step*, can be run from SAS Studio and  returns a six tables with a lot of information on a model in a SAS Model Manager.

### Logging - Monitoring

Here you can find everything to setup the logging and monitoring for this framework:

- *README.md*, explains how to set everything up and how to configure things
- *Get-All-Prompts*, collects all prompting related assets and turns it into a table for reporting
- *Load-Fact-Sheets*, loads the fact sheets into CAS for reporting.
- *Log-Parser-Code.sas*, if you prefer to run the log parsing as a code file

#### The Monitoring Baseline Report [Under Construction - Ignore for now]

If you have important the baseline package of this framework than you have a SAS Visual Analytics report available to you which is located under *SAS LLM Use Case Framework > Logging and Monitoring > Monitoring Baseline* if you changed the parsed log data source from *Public.LLM_LOGS* then you will have to replace the data source accordingly.

A note on prices, the report contains a calculated item called Average Price / Total Price, these two items contain big formulas that calculate the prices of the LLM usage. These formulas can be adjusted as you need it, note that most LLM providers denote their prices in millions of tokens and distinguish between input and output tokens - the data in this report is noted in individual tokens. Per default open-source models which are deployed in the SAS Open-Source Python container are priced as $0. Now you could of course add a price per second and then multiply with the runtime, but that isn't provided by default. The calculated item has a comment at the top that will help you to add additional pricing.

### SAS Code LLM Calls

Here you can find a list of different purpose build scripts to call the LLMs from SAS code:

-   *MM-Get-List-of-available-LLMs.sas* run this script from inside a SAS Session on the SAS Viya server, this a tables with all available LLMs.

-   *Get-LLM-Options.sas* run this script from inside a SAS Session on the SAS Viya server, returns the necessary SAS code and with all configuration options to call the LLM.
-   *Test-DS2-Scoring-from-SAS-Studio.sas*: run this script from inside a SAS session on the SAS Viya server, this runs one query against a LLM from Proc DS2.

### SAS Code Model Manager Interaction

Here you can find a list for general interaction with the SAS Model Manager, these scripts are not purpose build for this specific project, but where created a long the way and are provided here for completeness:

- *MM-Get-Repositories.sas* run this script from inside a SAS Session on the SAS Viya server, this returns a list of all available SAS Model Manager repositories.
- *MM-Get-Projects-in-Repository.sas* run this script from inside a SAS Session on the SAS Viya server, this returns a list of all available projects in a SAS Model Manager repository.
- *MM-Get-Models-in-Project.sas* run this script from inside a SAS Session on the SAS Viya server, this returns a list of all models in a SAS Model Manager project.
- *MM-Get-Model-Information.sas* run this script from inside a SAS Session on the SAS Viya server, this returns a six tables with a lot of information on a model in a SAS Model Manager.

### SAS Intelligent Decisioning Integration

Here you can find tools to integrate with SAS Intelligent Decisioning, note that you require a SAS Intelligent Decisioning license for the use of this tool:

-   *Create-Custom-SAS-Intelligent-Decisioning-Node.sas*: run this script from inside a SAS session on the SAS Viya server, this adds a node to the *Objects* pane inside of SAS Intelligent Decisioning.
