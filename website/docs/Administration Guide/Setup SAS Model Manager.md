---
sidebar_position: 4
---

The SAS Agentic AI Accelerator will create an additional model repository in your environment called **LLM Repository**. This repository will be used both to store the project which contains the LLMs, all the different prompting projects, the embedding models and the RAG setups. The project is created using the script *Model-Manager-Setup.py* which is located in the root folder of the repository.

This script creates the new SAS Model Manager repository and the SAS Model Manager projects for you that serve as the home for all LLM and Embedding related models. You need to run the script from within the pulled repository. Make sure that the Python environment that was created during the initial setup is still active:

```bash
# Run the setup script with the help (-h) flag to get more information on each parameter
# Run the setup script - make sure to update the parameter values that are passed into the script
python ./Model-Manager-Setup.py -vs sas-viya-url -u username -p password -rp responsible_party -e endpoint_from_scr_deployment
```

