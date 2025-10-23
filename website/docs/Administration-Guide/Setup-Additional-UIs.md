---
sidebar_position: 10
---

# Deploying the LLM Prompt Builder

This step is not required, but the LLM Prompt Builder is a tool to be able to easily test new prompts across LLMs, compare the results, version your experiments, and turn them into models for further consumption in the platform.

As first step to deploying the LLM Prompt Builder you must first deploy the SAS Portal Framework for SAS Viya.
The documentation for this setup is available in the [project documentation](https://sassoftware.github.io/sas-portal-framework-for-sas-viya/setup).

Now you can add the Prompt Builder UI to the portal by following these steps:

1. Create a new subfolder (the name doesn't matter, but I suggest to use `LLM Prompt Builder`).
2. In that subfolder upload the `llm-prompt-builder.json` file that the **Setup SAS Model Manager** chapter produced; replace any API-keys as needed.
3. Select the file and in the `Details` section under `More > URI`, then copy the `Pathname` (something like `/files/files/a652a4c2-d751-4bf7-8b72-cbce058087fe`.
4. Paste the URI in the below template and save that template as `portal-page-layout.json`:

     ```json
     {
         "general": {
             "name": "Build Prompts",
             "shorthand": "BPT",
             "visible": true,
             "numCols": 1,
             "contact": "david.weik@sas.com"
         },
         "objects": [
             {
                 "name": "LLM Prompt Builder",
                 "uri": "<llm-prompt-builderr.json URI>"
             }
         ]
     }
     ```

5.   Upload the file to the same folder.

You are now set up.