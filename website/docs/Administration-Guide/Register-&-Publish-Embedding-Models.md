---
sidebar_position: 7
---

# Registering Embedding Model Definitions

The folder *Embedding-Definitions* contains information on how to add embedding models to the repository in the SAS Model Manager. Each model is packaged so that it can be deployed using the SAS Container Runtime (SCR).

More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

For registering the models to SAS Model Manager please run the script *register-Embedding.py*. Make sure that the Python environment that was created during the initial setup is still active:

```bash
# Change into the Embedding-Definitions subdirectory
cd ./Embedding-Definitions
# Run the script - make sure to update the parameter values that are passed into the script
python ./register-Embedding.py -vs sas-viya-url -u username -p password -rp responsible_party -m embedding_1 embedding_2
```

A help function is also available with more information.

If you want to add your own Embedding to the mix, please use the *_Base_Definition* folder as your template and remember to contribute back! If you are adding a new proprietary model provider please note that the default value for the API_KEY attribute should be set to the name of the provider.

## Publish the Embedding Models to the SCR Destination

Once you have registered the embedding models, you can now go ahead and publish them to the SCR publishing destination. For this the script `Embedding-Definitions/publish-Embedding.py` is provided.
Ensure that the Python environment that was created during the initial setup is still active:

```bash
# Change into the Embedding-Definitions subdirectory
cd ./Embedding-Definitions
# Run the script - make sure to update the parameter values that are passed into the script
python ./publish-Embedding.py -vs sas-viya-url -u username -p password -m embedding_1 embedding_2 -d publishing_destination
```

A help function is also available with more information.