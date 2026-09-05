---
sidebar_position: 7
---

# Registering Embedding Model Definitions

The folder *Embedding-Definitions* contains a definition per embedding model, each packaged so that it can be deployed using the SAS Container Runtime (SCR). More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

Embedding models are registered and published with the same **`mdb`** command line as the LLMs — one tool covers both kinds — so this page is short by design. See [Register & Publish LLMs](./Register-&-Publish-LLMs.md) for the full description of `register`, `publish`, `--update`, `--wait` and `ship`. If you have not installed the CLI yet:

```bash
pip install -e "Model-Definition-Builder/cli[viya]"
```

Register and publish embedding models by their ids exactly as you would an LLM:

```bash
# Register
mdb register titan_embed_text_v2 bge_base_en_v15

# Publish (destination from -d or SAS_PUBLISH_DESTINATION in .env)
mdb publish titan_embed_text_v2 bge_base_en_v15 --wait

# ...or both at once
mdb ship titan_embed_text_v2
```

`mdb register --all` / `mdb publish --all` cover every managed definition of *both* kinds in one go, so you rarely need to think about LLM versus Embedding when registering.

If you want to add your own embedding model, use `mdb add` and remember to contribute back. If you are adding a new proprietary model provider, the `API_KEY` option's default should be set to the name of the provider.
