# SAS Agentic AI Accelerator

The SAS Agentic AI Accelerator provides a method for building AI agents leveraging SAS Viya technology.
It is designed to help users move more quickly from use-case idea to production, utilizing *No/Low/Yes* Code interfaces and full governance as a way to build agents that balance autonomy and trust.

It includes:

- The full code + documentation to deploy this in your SAS Viya Environment
- All the integrations that are built (SAS Studio Custom Steps, a no code prompt engineering UI, SAS Intelligent Decisioning Node, SAS Macros, Postman Collection and so much more)
- Pre-build deployment recipes for LLMs
- Pre-build deployment recipes for embedding models

The accelerator builds only on SAS Viya standard components and does not use any unsupported APIs or otherwise undocumented features.

The full documentation can be found [here](https://sassoftware.github.io/sas-agentic-ai-accelerator).

## License & Attribution

Except for the the contents of the `/static` folder, this project is licensed under the [Apache 2.0 License](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/LICENSE). 
Elements in the `/static` folder are owned by SAS and are not released under an open source license. SAS and all other SAS Institute Inc. product or service names are registered trademarks or trademarks of SAS Institute Inc. in the USA and other countries. ® indicates USA registration.

Separate commercial licenses for SAS software (e.g., SAS Viya) are not included and are required to use these capabilities with SAS software.

All third-party trademarks referenced belong to their respective owners and are only used here for identification and reference purposes, and not to imply any affiliation or endorsement by the trademark owners.

This project requires the usage of the following:

-   Python, see the Python license [here](https://docs.python.org/3/license.html)
-   Pandas, under the BSD 3-Clause License
-   HuggingFace Hub, under the Apache License 2.0
-   sasctl, under the Apache License 2.0
-   kubectl, under the Apache License 2.0
-   js-tiktoken, under the MIT license - only applicable if you use the provided LLM Usage Report for SAS Visual Analytics

This project is also makes use of the [SAS Portal Framework for SAS Viya](https://github.com/sassoftware/sas-portal-framework-for-sas-viya).

> [!NOTE]  
> These models, for which this project only provides deployment instructions, have their own licenses and dependencies that you should check before deployment.
