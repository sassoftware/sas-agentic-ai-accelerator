# Utility

This folder contains additional utility scripts to help with administration and configuration.

If you go through the setup in one go, than you shouldn't need any of the provided files here.

## [Prompt Builder JSON](./prompt-builder-json.py)

This script helps you to regenerate your Prompt Builder JSON if you deleted it by accident.

```bash
# Run the setup script with the help (-h) flag to get more information on each parameter
# Run the setup script - make sure to update the parameter values that are passed into the script
python ./prompt-builder-json.py -vs sas-viya-url -u username -p password -e endpoint_from_scr_deployment
```