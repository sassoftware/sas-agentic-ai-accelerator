---
sidebar_position: 1
title: Introduction
---

The User Guide describes the tools you work with once an administrator has deployed the SAS Agentic AI Accelerator - see the [Administration Guide](../Administration-Guide/Introduction.md) for that part.

- [Model Definition Builder](./Model-Definition-Builder.md) - add an LLM or embedding model to the environment with one `definition.yaml` and the `mdb` command line, check it locally, and register and publish it to SAS Model Manager and the SAS Container Runtime.
- [Prompt Builder](./Prompt-Builder.md) - test a prompt across several LLMs at once, let a judge model rank the responses, keep a versioned experiment history in SAS Model Manager, optimize the prompt with DSPy, and turn the best prompt into a scoreable model for SAS Intelligent Decisioning.
- [RAG](./RAG.md) - turn a folder of documents into a governed vector-store collection with the RAG custom steps or the RAG Builder, ask it questions, and publish the retrieval as a model a decision can call.

The SAS code, custom steps and Python samples behind these tools are listed in the repository's [SAS Viya Integrations](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations) and [Non-SAS Viya Integrations](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/Non-SAS-Viya-Integrations) folders.
