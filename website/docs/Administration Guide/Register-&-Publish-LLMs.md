---
sidebar_position: 5
---

## Registering LLM Definitions

The folder *LLM-Definitions* contains information on how to add LLMs to the repository in the SAS Model Manager. Each model is packaged so that it can be deployed using the SAS Container Runtime (SCR).

More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

For registering the models to SAS Model Manager please run the script *register-LLMs.py*. Make sure that the Python environment that was created during the initial setup is still active:

```bash
# Change into the LLM-Definitions subdirectory
cd ./LLM-Definitions
# Run the script - make sure to update the parameter values that are passed into the script
python ./register-LLMs.py -vs sas-viya-url -u username -p password -rp responsible_party -e endpoint_from_scr_deployment -l llm_1 llm_2
```

A help function is also available with more information.

If you want to add your own LLM to the mix, please use the *_Base_Definition* folder as your template and remember to contribute back! If you are adding a new proprietary model provider please note that the default value for the API_KEY attribute should be set to the name of the provider, and then needs to be added to the LLM Prompt Builder object definition as well.

## Publish the LLMs to the SCR Destination

Once you have registered the LLMs, you can now go ahead and publish the LLMs to the SCR publishing destination. For this the script *LLM-Definitions/publish-LLMs.py* is provided. Make sure that the Python environment that was created during the initial setup is still active:

```bash
# Change into the LLM-Definitions subdirectory
cd ./LLM-Definitions
# Run the script - make sure to update the parameter values that are passed into the script
python ./publish-LLMs.py -vs sas-viya-url -u username -p password -l llm_1 llm_2 -d publishing_destination
```

A help function is also available with more information.