# Base Definition

* [How to Add a New Proprietary Model](#how-to-add-a-new-proprietary-model)
  * [Quick Start](#quick-start)
    * [File Overview](#file-overview)
    * [Key Development Tips](#key-development-tips)
    * [SAS Model Manager Workflow](#sas-model-manager-workflow)
    * [Reference Models](#reference-models)
  * [Detailed Guide: Adding New LLM Models to the Repository](#detailed-guide-adding-new-llm-models-to-the-repository)
    * [Step 1: Develop the Scorer Code](#step-1-develop-the-scorer-code)
    * [Step 2: Define Configuration Options](#step-2-define-configuration-options)
    * [Step 3: Define Input/Output Variables](#step-3-define-inputoutput-variables)
    * [Step 4: Create Model Configuration](#step-4-create-model-configuration)
    * [Step 5: Create requirements.json](#step-5-create-requirementsjson)
    * [Step 6: Test Locally](#step-6-test-locally)
    * [Step 7: Write README](#step-7-write-readme)
    * [Step 8: Add Model Card (Optional but Recommended)](#step-8-add-model-card-optional-but-recommended)
    * [Step 9: Update Fact Sheet](#step-9-update-fact-sheet)
    * [Step 10: Register \& Deploy](#step-10-register-deploy)
* [Checklist](#checklist)
* [Tips](#tips)
* [Reference Models](#reference-models-1)

This folder contains the absolute basic structure that will have to be reused for each model.

If you want to add a new model please make a copy of this one, rename it to the model name (ensure URL conformant names) and then start editing the files as you need.

Short explanation of each file here:

- *inputVar.json*: contains the definition of the input variables, do not change this file.
- *modelConfiguration.json*: contains the definition of the model metadata, do change this file.
- *options.json*: contains the description of the options object that can optionally be passed to the LLM.
- *outputVar.json*: contains the definition of the output variables, do not change this file.
- *requirements.json*: contains a list of steps that need to be performed in order to change the container, that make the model run.

Note on the *modelConfiguration.json*: The tag list should contain one of the following three tags: *small, medium or large*. These are used as a sizing indicator for the final SCR deplyoment.

Rule of thumb for the sizing tag:

- *small*, use this when you are deploying a proprietary model wrapper
- *medium*, use this when you are deploying an open-source model with less than 3B parameters
- *large*, use this when you are deploying an open-source model between 3B and 8B parameters

As currently no GPUs are supported in this framework, it isn't recommended to deploy LLMs larger than 8B parameters as the performance hit is to big or the models just will not be able to run at all.

Optional files:
- *Model-Card.pdf or Model-Card.md* : contains additional information about a model to help understand how it should be used, evaluation benchmarks, etc.

## How to Add a New Proprietary Model

### Quick Start

1. Copy the **_Base_Definition/** folder and rename to your model name (URL-conformant, e.g., `gpt_41_az_2025_01_01`).
2. Edit the Python scorer file, configuration files, and documentation.
3. Test locally. For example, [gpt41MiniScore.py](/LLM-Definitions/gpt_41_az_2025_01_01/gpt41MiniScore.py) has a section at the bottom for local testing. Uncomment `"""` the main block, adapt the options and run the program locally.
4. Register, publish, and deploy via SAS Model Manager.

**For comprehensive step-by-step instructions**, see [ADDING_MODELS.md](../ADDING_MODELS.md) in the parent directory.

#### File Overview

Each model folder contains:

- **`{modelName}Score.py`**: The scorer implementation
  - Implements `scoreModel(userPrompt, systemPrompt, options)` function
  - Returns `(response, run_time, prompt_length, output_length)` tuple
  - Handles pandas Series (from SAS containers), JSON strings, and dict options
  - Includes error handling, logging, and timeout management
  - Example test at bottom: uncomment and run `python {modelName}Score.py` to verify locally

- **`options.json`**: Configuration schema with defaults and descriptions
  - Always include: API credentials, endpoint/resource, sampling parameters
  - Format: `{"option_name": {"default": "value", "range": "...", "description": "..."}}`

- **`inputVar.json`**: Input variable definitions for SAS Model Manager
  - Standard: userPrompt, systemPrompt, options (all strings)
  - **Do not modify** unless adding custom inputs

- **`outputVar.json`**: Output variable definitions for SAS Model Manager
  - Standard: response (string), run_time (decimal), prompt_length (decimal), output_length (decimal)
  - **Do not modify** unless adding custom outputs

- **`modelConfiguration.json`**: Model metadata
  - Update: **name**, **scoreCodeFile**, **description**, **tags**
  - Include sizing tag: `small` (proprietary wrapper), `medium` (<3B params), `large` (3B-8B params)
  - Add responsible AI context: modelPurpose, intendedUse, limitations, etc.
  - Optional: modeler, modelType, Python version

- **`requirements.json`**: Dependency management
  - List pip install steps and system-level commands
  - Example: `pip3 install requests>=2.31.0 tiktoken>=0.5.1`

- **`README.md`** (recommended): Deployment and usage guide
  - Setup instructions and credential retrieval
  - Configuration options with examples
  - Local testing code
  - Multi-region/multi-deployment benefits

- **`Model-Card.md`** (optional): Detailed model information
  - Capabilities and performance benchmarks
  - Training data, knowledge cutoff
  - Responsible AI and limitations
  - Links to provider documentation

#### Key Development Tips

- **Handle pandas Series**: SAS containers pass options as pandas Series; use `hasattr(opts, 'iloc')` to detect and extract with `.iloc[0]`
- **Flexible configuration**: Support both constructed endpoints (resource + path) and direct `endpoint_url` overrides
- **Error handling**: Log HTTP status, response text, and timeouts (e.g., 60s)
- **Token counting**: Use model-specific tokenizer; fall back gracefully if unavailable
- **Test locally first**: Uncomment `if __name__ == "__main__":` block in scorer before registering

#### SAS Model Manager Workflow

After developing your model:

1. **Register** (creates entry in SAS Model Manager):
   ```bash
   python ./register-LLMs.py --viya_server <url> --username <user> --password <pass> --llms your_model_id
   ```

2. **Publish** (creates Docker image in container registry):
   ```bash
   python ./publish-LLMs.py --viya_server <url> --username <user> --password <pass> --destination <registry> --llms your_model_id
   ```

3. **Deploy** (creates container instance from published image)

4. **Test** (score with sample data via microanalytic scoring API)

**Note**: If redeploying with code changes, delete the model in SAS Model Manager first (forces re-registration instead of skipping due to existing entry).

#### Reference Models

- **gpt_4o_mini_az_2024_07_18**: Azure OpenAI with flexible endpoint configuration
- **gpt_41_az_2025_01_01**: Supports both Azure OpenAI Service and Azure AI Foundry with pandas Series handling

### Detailed Guide: Adding New LLM Models to the Repository

This guide outlines the step-by-step process for adding a new Large Language Model (LLM) to this repository.

#### Step 1: Develop the Scorer Code

Create a Python script (`{modelName}Score.py`) that implements the `scoreModel()` function.

**Requirements:**
- Accept three parameters: `userPrompt` (list), `systemPrompt` (list), `options` (list/Series)
- Return tuple: `(response, run_time, prompt_length, output_length)`
- Include error handling (HTTP errors, JSON parsing, etc.)
- Parse options flexibly (JSON, k:v strings, pandas Series for SAS container compatibility)
- Log key metrics (endpoint, token counts, runtime)

**Example structure:**
```python
def scoreModel(userPrompt, systemPrompt, options):
    """Output: response, run_time, prompt_length, output_length"""
    started_timestamp = time.time()

    # Parse options (handle list, dict, Series)
    optionsParsed = _parse_options(options)

    # Merge with defaults
    options = {**optionsDefaults, **optionsParsed}

    # Make API call
    response = call_model_api(...)

    # Calculate metrics
    run_time = time.time() - started_timestamp
    prompt_length = count_tokens(userPrompt + systemPrompt)
    output_length = count_tokens(response)

    return response, run_time, prompt_length, output_length
```

---

#### Step 2: Define Configuration Options

Create `options.json` documenting all model parameters.

**Structure:**
```json
{
    "option_name": {
        "default": "value",
        "range": "min - max or list",
        "description": "Clear explanation"
    }
}
```

**Always include:**
- API credentials (e.g., `API_KEY`)
- Model endpoint/resource (e.g., `azure_openai_resource`, `endpoint_url`)
- Sampling parameters (e.g., `temperature`, `top_p`)

---

#### Step 3: Define Input/Output Variables

Create `inputVar.json` and `outputVar.json` describing data flowing in and out.

**inputVar.json example:**
```json
[
    {
        "name": "userPrompt",
        "description": "User query",
        "level": "nominal",
        "type": "string",
        "length": 5000
    }
]
```

**outputVar.json example:**
```json
[
    {
        "name": "response",
        "description": "LLM response text",
        "level": "nominal",
        "type": "string",
        "length": 5000
    },
    {
        "name": "run_time",
        "description": "Inference time in seconds",
        "level": "interval",
        "type": "decimal",
        "length": 8
    }
]
```

---

#### Step 4: Create Model Configuration

Create `modelConfiguration.json` with metadata.

**Key fields:**
- `name`: Model ID (e.g., `gpt_41_az_2025_01_01`)
- `scoreCodeFile`: Scorer filename
- `description`: Brief model summary
- `function`: Use case (e.g., "text generation")
- `algorithm`: Architecture (e.g., "Transformer")
- `tags`: Labels for filtering (e.g., `["LLM", "Proprietary", "Azure OpenAI"]`)
- `champion`: Boolean (is this the recommended model?)
- `modelPurpose`, `intendedUse`, `expectedBenefit`, `outOfScopeUseCases`, `limitations`: Responsible AI context

---

#### Step 5: Create requirements.json

Define Python dependencies for deployment.

**Example:**
```json
[
    {
        "step": "upgrade pip",
        "command": "pip3 -q install --upgrade pip setuptools wheel"
    },
    {
        "step": "install packages",
        "command": "pip3 -q install requests>=2.31.0 tiktoken>=0.5.1"
    }
]
```

---

#### Step 6: Test Locally

Uncomment the `if __name__ == "__main__":` block in your scorer and test:

```bash
python {modelName}Score.py
```

**Verify:**

- No syntax errors
- Correct output tuple returned
- Proper error handling
- Reasonable token counts
- Response quality

---

#### Step 7: Write README

Create comprehensive documentation with:

- **Required Items**: What's needed to use this model
- **Deployment Instructions**: Step-by-step setup (e.g., Azure portal)
- **Key Retrieval**: How to get credentials and endpoints
- **Configuration Options**: Detailed parameter descriptions
- **Usage Examples**: Multiple practical scenarios
- **Testing**: How to verify locally
- **Benefits**: Highlight key advantages

---

#### Step 8: Add Model Card (Optional but Recommended)

Create `Model-Card.md` or `Model-Card.pdf` documenting:

- Model details and capabilities
- Training data and cutoff date
- Performance benchmarks
- Limitations and responsible AI considerations
- Links to provider documentation

---

#### Step 9: Update Fact Sheet

Add entry to `llm_fact_sheet.csv`:

```csv
model_id,model,provider,description,release_date,size,deployment_type,license,cost_type,input_token_price,output_token_price,second_cost,context_length,temperature,top_p,top_k,max_tokens,knowledge_cut_off
"your_model_id","Model Name","Provider","Description text",2025-01-01,200000000000,API,Proprietary,Tokens,0.00000125,0.00001,.,1000000,1,1,.,.,.
```

**Key fields:**

- `model_id`: Matches folder name
- `provider`: Company (e.g., "Azure OpenAI")
- `size`: Parameter count (or `.` if unknown)
- `context_length`: Token limit
- `cost_type`: "Tokens" or "Seconds"
- Pricing: Use `0` for unknown, `.` for not applicable

---

#### Step 10: Register & Deploy

Register the model in SAS Model Manager:

```bash
python ./register-LLMs.py \
  --viya_server <url> \
  --username <user> \
  --password <pass> \
  --responsible_party <user> \
  --verify_ssl true \
  --scr_endpoint <scr_url> \
  --llms your_model_id
```

Publish to container registry:

```bash
python ./publish-LLMs.py \
  --viya_server <url> \
  --username <user> \
  --password <pass> \
  --destination <registry> \
  --llms your_model_id
```

Deploy container instance and test.

---

## Checklist

- [ ] Scorer code (`{modelName}Score.py`) developed and locally tested
- [ ] `options.json` with all configurable parameters
- [ ] `inputVar.json` and `outputVar.json` defined
- [ ] `modelConfiguration.json` with metadata and tags
- [ ] `requirements.json` with dependencies
- [ ] `README.md` with setup, configuration, and usage examples
- [ ] `Model-Card.md` (optional) with detailed model info
- [ ] Entry added to `llm_fact_sheet.csv`
- [ ] Model registered in SAS Model Manager
- [ ] Model published to container registry
- [ ] Container deployed and scored successfully

---

## Tips

1. **Pandas Series Compatibility**: Always handle pandas Series in option parsing (SAS containers pass data as Series)
2. **Error Handling**: Log errors clearly; include HTTP status codes and response text
3. **Timeouts**: Set reasonable request timeouts (e.g., 60s)
4. **Token Counting**: Use appropriate tokenizer for your model (fall back gracefully if unavailable)
5. **Flexible Configuration**: Support both full URLs and constructed endpoints when possible
6. **Documentation**: Write examples for both dev and production scenarios

---

## Reference Models

- **GPT-4.1 Azure**: Supports both Azure OpenAI and Azure AI Foundry
- **GPT-4o-mini Azure**: Simpler example with location-independent configuration
