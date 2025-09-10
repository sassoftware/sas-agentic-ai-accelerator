## Create a SAS Model Manager Repository

This step is only an example of how you could set up a repository using SAS code - it is recommended that you use the *Model-Manager-Setup.py* script in the root directory.

Open up SAS Studio on your target SAS Viya environment and run the *createLLMRepository.sas* script.

There is *principal* macro variable at the top of the macro variable please replace it with the group ID that you want to give access to this repository too - the default is set to the SAS Administrator group.

Feel free to change the other macro variables at the top of the script as well. Note, that if you change the name of the repository you will have to propagate this change throughout the different scripts and tools.

Here is a list of the suggest values:

-   Model Repository Name: *LLM Repository*
-   Model Repository Project Name: *LLM Model Project*
-   SCR Publishing Destination Name: *llmACR*
-   SCR Endpoint: */llm*