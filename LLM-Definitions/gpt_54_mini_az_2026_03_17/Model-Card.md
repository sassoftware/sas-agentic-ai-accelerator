# GPT-4.1 Azure OpenAI / Azure AI Foundry

## Details

The GPT-4.1 series is the latest iteration of the GPT-4o model family. This iteration of models is specifically targeted for better coding and instruction following, making it better at handling complex technical and coding problems.

**Direct from Azure models** - GPT-4.1 is a select portfolio model curated for its market-differentiated capabilities:
- **Secure and managed by Microsoft**: Purchase and manage models directly through Azure with a single license, consistent support, and no third-party dependencies, backed by Azure's enterprise-grade infrastructure.
- **Streamlined operations**: Benefit from unified billing, governance, and seamless PTU portability across models hosted on Azure - all part of Microsoft Foundry.
- **Future-ready flexibility**: Access the latest models as they become available, and easily test, deploy, or switch between them within Microsoft Foundry; reducing integration effort.
- **Cost control and optimization**: Scale on demand with pay-as-you-go flexibility or reserve PTUs for predictable performance and savings.

## Key Capabilities

- **Text and image processing**: Multimodal input support
- **JSON Mode**: Structured JSON output generation
- **Parallel function calling**: Execute multiple function calls simultaneously
- **Enhanced accuracy and responsiveness**: Parity with English text and coding tasks compared to GPT-4 Turbo with Vision
- **Superior multilingual performance**: Improved performance in non-English languages and vision tasks
- **Complex structured outputs**: Support for sophisticated output formatting

## Context and Output

GPT-4.1 increases the context token limit up to **1M input tokens** with separate billing for:
- Small context: 128k tokens
- Large context: up to 1M tokens

As with the previous GPT-4o model family, it supports a **16k output size**.

## Model ID

The GPT-4.1 model is available through Azure OpenAI and Azure AI Foundry.

**Availability**: Standard, Global Standard, Global Batch, Regional Provisioned Throughput, Global Provisioned Throughput, Data Zone Standard, Data Zone Provisioned Throughput, Data Zone Batch

**Lifecycle**: Generally available (Preview)

**Training cut-off date**: Not supplied by provider

## Data, Media and Languages

**Supported data types:**
- **Inputs**: Text, image
- **Outputs**: Text (up to 16k tokens)

**Input Formats**: Text, image processing

**Output Formats**: Text with JSON Mode and support for complex structured outputs

**Supported languages**: Superior performance in non-English languages and vision tasks. Includes support for: en, it, af, es, de, fr, id, ru, pl, uk, el, lv, zh, ar, tr, ja, sw, cy, ko, is, bn, ur, ne, th, pa, mr, te, and many more.

## Use Cases

### Key Use Cases

This iteration of models is specifically targeted for:
- **Complex coding problems**: Better at handling technical programming challenges
- **Instruction following**: Enhanced accuracy in following detailed instructions
- **Technical documentation**: Processing and generating complex technical content
- **Function calling**: Build applications that fetch data or take actions with external systems
- **Long-context processing**: Handle large codebases or conversation histories (up to 1M tokens)

### Out of Scope Use Cases

Prompts and completions are passed through a default configuration of Azure AI Content Safety classification models to detect and prevent the output of harmful content. Learn more about [Azure AI Content Safety](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/). Additional classification models and configuration options are available when you deploy an Azure OpenAI model in production.

## Pricing

Pricing is based on a number of factors, including deployment type and tokens used:
- **Small context** (up to 128k tokens): Standard pricing
- **Large context** (128k - 1M tokens): Separate billing for extended context

See [Azure OpenAI pricing details](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/) for current rates.

## Transparency

### Model Provider

This model is provided through the Azure OpenAI service and Azure AI Foundry.

### Distribution Channels

- Azure OpenAI Service
- Azure AI Foundry

### Relevant Documents

The following documents are applicable:

- [Overview of Responsible AI practices for Azure OpenAI models](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/overview)
- [Transparency Note for Azure OpenAI Service](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/transparency-note)
- [Introducing the GPT-4.1 Series](https://techcommunity.microsoft.com/blog/azure-ai-services-blog/introducing-gpt-4-1-openais-new-flagship-multimodal-model-now-in-preview-on-azu/4357395): OpenAI's new flagship multimodal model now in preview on Azure

### Model Architecture

The provider has not supplied detailed architecture information.

## Responsible AI Considerations

### Built-in Safety Measures

Safety is built into GPT-4.1 from the beginning, and reinforced at every step of the development process:

- **Pre-training filtering**: Content filtering to exclude hate speech, adult content, personal information aggregation, and spam
- **Post-training alignment**: Reinforcement learning with human feedback (RLHF) to improve accuracy and reliability
- **Expert evaluation**: Assessed using both automated and human evaluations according to Azure's Preparedness Framework
- **Instruction hierarchy**: Enhanced ability to resist jailbreaks, prompt injections, and system prompt extractions
- **Continuous monitoring**: Ongoing safety improvements as new risks are identified

### Content Filtering

Prompts and completions are passed through a default configuration of Azure AI Content Safety classification models to detect and prevent the output of harmful content. Learn more about Azure AI Content Safety. Additional classification models and configuration options are available when you deploy an Azure OpenAI model in production; learn more.

## Additional Information

### Training and Testing

The provider has not supplied detailed training, testing, and validation information.

### Performance

GPT-4.1 is specifically optimized for:
- **Enhanced coding capabilities**: Better handling of complex technical and coding problems
- **Instruction following**: Improved accuracy in following detailed instructions
- **Parity with GPT-4 Turbo with Vision**: Equivalent performance on English text and coding tasks
- **Superior multilingual performance**: Enhanced performance in non-English languages and vision tasks

### Learn More

For the latest information and updates on GPT-4.1, visit the [Azure AI Foundry Model Catalog](https://ai.azure.com).