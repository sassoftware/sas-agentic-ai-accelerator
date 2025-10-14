# Changelog

This changelog documents all the different updates that occur for this framework.

## [0.1.30] - 2025-10-14

No changes are requires at this time.

### Added

- None

### Changed

- None

### Fixed

- The verify_ssl couldn't previously be set to false for the Python scripts

## [0.1.29] - 2025-10-13

No changes are requires at this time.

### Added

- None

### Changed

- The register-LLMs.py script does no longer require an entry in the LLM Fact Sheet.

### Fixed

- None

## [0.1.29] - 2025-10-10

No changes are requires at this time.

### Added

- Expanded documentation pages by a lot
- Optional argument for the Model-Manager-Setup.py script to provide the ability to change to deployment: k8s or aca
- Ignore .venv added to the .gitignore

### Changed

- The Model-Manager-Setup.py script now provides bash commands, instead of PowerShell

### Fixed

- None

## [0.1.28] - 2025-09-25

No changes are requires at this time.

### Added

- None

### Changed

- None

### Fixed

- Fix argument error in publish-Embedding script and tag value in register-LLMs

## [0.1.27] - 2025-09-10

No changes are requires at this time.

### Added

- SECURITY.md and CONTRIBUTING.md
- Source code headers for copyright and license information added

### Changed

- None

### Fixed

- None

## [0.1.26] - 2025-09-08

No changes are requires at this time.

### Added

- BGE Small, Base and Large EN v1.5
- Attribution note in the main README
- The *register-LLMs.py* now supports the additional new metadata items provided by SAS Model Manager 2025.08+

### Changed

- The *register-LLMs.py* now requires an additional parameter -e which is the same as for the *publish-LLMs.py* to know the endpoint, as this will enable further metadata integration with the SAS Model Manager

### Fixed

- Typo in the Gemma Embedding model folder path

## [0.1.25] - 2025-09-07

No changes are requires at this time.

### Added

- All MiniLLM L6 v2, an open-weight embedding model
- Embedding Gemma 300M, an open-weight embedding model

### Changed

- Change model function from classification to text generation to make use of the new features within SAS Model Manager as off 2025.08

### Fixed

- None

## [0.1.24] - 2025-09-06

No changes are requires at this time.

### Added

- None

### Changed

- None

### Fixed

- Fix in the _Base_Definition_ baseScore.py LLM code

## [0.1.23] - 2025-09-02

No changes are requires at this time.

### Added

- Two images added as preperation for upcoming documentation enhancements

### Changed

- Update main README.md

### Fixed

- Updated two minor versions in the Changelog as they were stuck on 0.1.20

## [0.1.22] - 2025-09-01

No changes are requires at this time.

### Added

- README for Tools

### Changed

- None

### Fixed

- Wrongly formatted API_KEYS in embedding models

## [0.1.21] - 2025-08-29

No changes are requires at this time.

### Added

- New Tools folder and the first tool contribution for websearch

### Changed

- None

### Fixed

- None

## [0.1.20] - 2025-08-26

Run the *./SAS-Viya-Tool-Integrations/SAS-Intelligent-Decisioning-Integration/Update-Custom-SAS-Intelligent-Decisioning-Node.sas* in your environment to get the update. The script has been validated to not require any changes to your existing messages - but it has expanded the supported character limits of both the llmBody and the llmGenerated variables to the maximum length of 10,485,760 characters.

### Added

- *Update-Custom-SAS-Intelligent-Decisioning-Node.sas* is available as an update script of existing Call LLM nodes to support the increased character limit.
- In the *Troubleshooting-Guide.md* a new line that explains Duplicate Variable Error.

### Changed

- Renamed *Non-SAS-Viya-Tool-Integrations* and *SAS-Viya-Tool-Integrations* to *Non-SAS-Viya-Integrations* and *SAS-Viya-Integrations* to better reflect that these aren't tools themselves but rather integration points. The documentation has been updated accordingly.

### Fixed

- *Create-Custom-SAS-Intelligent-Decisioning-Node.sas* now provides a character limit for the *llmBody* and *llmGenerated* variables of 10,485,760 characters (the maximum supported by all publishing destinations).

## [0.1.19] - 2025-08-14

No changes are requires at this time.

### Added

- *Token-Calculator.html* now has an additional input field were you can change to estimate how often a prompt is run per day

### Changed

- None

### Fixed

- None

## [0.1.18] - 2025-08-13

No changes are requires at this time.

### Added

- Gemini Flash Lite 2.5, Flash 2.5 and Pro 2.5 have been added
- The *llm_facht_sheet.csv* was updated alongside the introduced models
- Implemented [Issue 30](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/30) - requires update of the Portal Framework, by switching to the aaia branch
- Tag documentation for LLM Definitions

### Changed

- Gemini Flash 1.5 001 and 002 are deprecated by Google and have received that tag accordingly
- Claude 2.0 and 2.1 used a Legacy tag, this has been changed to make use of the deprecated feature

### Fixed

- None

## [0.1.17] - 2025-08-11

The *Token-Calculator.html* and *LLM-Details-Page.html* need to be uploaded to a webserver (e.g. the one used for the Prompt Builder UI or where the customers stores Data Driven Content object sources).

### Added

- *Token-Calculator.html*, in report utility to calculate the tokens used up by a prompt and then multiplies it with the pricing data from the *llm_fact_sheet.csv*
- *LLM-Details-Page.html*, in report utility to display the information from the *llm_fact_sheet.csv* as a type of super light weight model card

### Changed

- In *Load-Fact-Sheets.sas* the default path has been updated.
- *LLM - Get All Prompts.step* has been updated to remove warning as the macro variable was spelled incorrectly.

### Fixed

- Implemented fix suggested by [Issue 29](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues?show=eyJpaWQiOiIyOSIsImZ1bGxfcGF0aCI6IkRhdmlkLldlaWsvc2FzLWxsbS11Y2YiLCJpZCI6NjE0OTh9)
- Fix typo for phi_35_mini model id in *llm_fact_sheet.csv*

## [0.1.16] - 2025-08-10

No changes are requires at this time.

### Added

- Update the LLM Fact Sheet to include all current models
- Add Load-Fact-Sheets.sas programm to load the data to CAS

### Changed

- Removed tiktoken dependency for OpenAI models, as tokens are included in the response. This will improve the total processing time

### Fixed

- None

## [0.1.15] - 2025-08-09

No changes are requires at this time.

### Added

- Get-All-Prompts.sas retrieves all prompting projects, models and their experiments and turns it into a table for reporting
- LLM - Get ALL Prompts custom step introduced, that does the same as the script, just wrapped in a custom step
- LLM Fact Sheet entries for all Anthropic and Google models

### Changed

- None

### Fixed

- None

## [0.1.14] - 2025-08-05

No changes are requires at this time.

### Added

- register-Embedding.py to register Embedding models to SAS Model Manager
- publish-Embedding.py to publish Embedding models to SCR
- Model-Manger-Setup.py now also creates the Embedding Model Project
- .gitignore now ignores the rag-builer.json which is used for the RAG Builder UI
- README.md was updated to refelect these changes
- Added additional embedding models from Voyage.ai

### Changed

- The LLM base example was called _Base-Definitions for consistency this name was update to \_Base\_Definitions

### Fixed

- None

## [0.1.13] - 2025-08-04

This update requires you to update requires you to switch from the prompt builder provider here to https://github.com/sassoftware/sas-portal-framework-for-sas-viya

### Added

- Add Embedding Definitions

### Changed

- Removed LLM Prompt Builder content from this repository and moved it to https://github.com/sassoftware/sas-portal-framework-for-sas-viya
- Leading and Trailing blanks are now removed from the variables in the manifested prompts

### Fixed

- The name of the manifested prompt was based on the name of the prompt, this has now been fixed to adhere to proper Python package names

## [0.1.12] - 2025-07-14

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- New button that provides a link to the model, if one is selected
- A Troubleshooting-Guide.md was added

### Changed

- None

### Fixed

- None

### Removed

- The README.md chapter **Modifying the SAS Portal Framework for SAS Viya** has been removed as the Prompt Builder is now part of the main repository

## [0.1.11] - 2025-06-06

No updating required as this update is a design phase.

### Added

- Added Claude 2.0 as a first test

### Changed

- None

### Fixed

- None

### Removed

- Evals from the fact sheets - mabye an idea for the future in a different sheet


## [0.1.10] - 2025-06-05

No updating required as this update is a design phase.

### Added

- Base attributes for all default included models

### Changed

- Added two additional attributes to the llm_fact_sheet.csv model_id and deployment_type

### Fixed

- None

### Removed

- None

## [0.1.9] - 2025-06-04

No updating required as this update is a design phase.

### Added

- Start desgining the LLM fact sheet which will be the new base for further reporting

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.8] - 2025-06-03

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- One new line in the language file to explain best prompt
- Icon is displayed next to the model name if for best response + with hover text

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.7] - 2025-06-02

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- Two new lines in the language file to explain fastest and fewest token prompts
- Icons are displayed next to the model name if they had the fastest and/or fewest token prompts
- Icons display an hover text to explain themselves

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.6] - 2025-06-01

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- Base implemention for fastest prompt and fewest token prompt has been added (no UI support yet)

### Changed

- [Change display order of Prompt Experiments](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/7)

### Fixed

- None

### Removed

- None

## [0.1.5] - 2025-05-31

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- None

### Changed

- LLM calls are now done in parallel, instead of in sequence - this should lead to a big performance uplift for prompt engineers
- No more leading and trailing new lines in the manifested model

### Fixed

- Added missing semi-colons
- Fix hardcoded model in the model variable deletion
- [Having to escape special characters e.g. \\n](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/3)

### Removed

- None

## [0.1.4] - 2025-05-30

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- Model Responses are now renderd as Markdown instead of plain text if the model response contains Markdown syntax.

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.3] - 2025-05-29

### Added

- [Explain HF token](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/10)
- Documentation was added on how to add Proprietary models
- A template for gpt_4o_mini_az_2024_07_18 was added showcasing how to deploy GPT models using Azure Cognitive Services
- Add default transfer package to implement [Logging and Monitoring Assets](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/21)

### Changed

- Updated the Base-Definition options.json to be a collection of all options that are currently used across the models
- Improved the perfomance and robustness of the log parser code and custom step by moving to Python for processing
- Moved the createLLMRepository.sas script into the MM specific subfolder along with its documentation
- Moved the LLM - Log Parser.step into the Custom Step repository to be more consistent

### Fixed

- Typos in documentation

### Removed

- None

## [0.1.2] - 2025-05-28

### Added

- Two new utility functions have been added to the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js) - get-model-variables.js and delete-model-variable.js
- New utitlity function has been added to the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js) - validate-ds2-variable-name.js

### Changed

- The userPrompt variable now has a description
- When using the prompt variable functionality in the userPrompt the variable is now checked for DS2 variable name compliance

### Fixed

- [Unneccessary API_KEY in userPrompt](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/2)
- The prompt experiment tracker file was called Prompt-Example-Tracker.json - it has been renamed to Prompt-Experiment-Tracker.json
- Fixed an issue in the create-model-content.js utility function via the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js)
- [Missing comma after top_p](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/1)
- When you manifest a prompt a couple of times it would lead to the creation of duplicate variables, this has been fixed
- Fixed an issue where if only one input variable was provided it was not added
- Fixed an issue where if the user used a semi-colon for the last variable it created an empty input variable
- [Prompt Expierments stay when changing/creating projects/prompts](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/11)
- [Catch and Return API errors](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/6)
- [Ensure valid variable names](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/14)

### Removed

- None

## [0.1.1] - 2025-05-27

### Added

- Added CHANGELOG.md to the repository to communicated updates better in the future
- Added rules to .gitignore to ignore the SAS Viya CLI if present in the repository
- Added rule to .gitignore to ignore the SAS Viya CLI setup commands
- Added documentation on setting up the SAS Viya CLI
- Added documentation on the SAS Viya CLI setup commands
- Added the generation of the SAS VIYA CLI setup commands to the Model-Manager-Setup.py script
- Added additional error messages to all Python scripts
- Added gpt-4o-mini-2025-01-01-preview, as an example for using Azure AI Foundry

### Changed

- None

### Fixed

- None

### Removed

- None