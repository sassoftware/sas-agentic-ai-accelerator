# Non SAS Viya Integrations

In this folder you find integrations that are purpose built for this framework that enable you to make use of the deployed LLMs outside of SAS Viya.

## List of Available Integrations

-   *LLM-Call.py*, a baseline implementation to call the LLMs from Python.
-   *LLM-Call-DataFrame.py*, calls an LLM for each row in a data frame and adds the output variables to the rows. 
-   *LLM-Call-EnvConfigured.py*, calls an environment-configured LLM (score template `azure_openai_env`), where the key, resource/project and deployment come from the container's own environment - so the call carries no API key and need not know which Azure deployment answers it.
-   *SCR-LLM-Calls.postman_collection.json*, if you import this into [Postman](https://www.postman.com/) or the [Postman VS Code Extension](https://marketplace.visualstudio.com/items?itemName=Postman.postman-for-vscode) you can quickly start trying out the deployed LLMs. In the collection variables you have to set the SCR-LLM endpoint and a key for each proprietary LLM that you want to call.