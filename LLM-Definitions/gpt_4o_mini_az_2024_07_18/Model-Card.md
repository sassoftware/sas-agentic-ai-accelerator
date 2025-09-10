# GPT-4o mini Azure OpenAI

## Details

GPT-4o mini enables a broad range of tasks with its low cost and latency, such as applications that chain or parallelize multiple model calls (e.g., calling multiple APIs), pass a large volume of context to the model (e.g., full code base or conversation history), or interact with customers through fast, real-time text responses (e.g., customer support chatbots).

Today, GPT-4o mini supports text and vision in the API, with support for text, image, video and audio inputs and outputs coming in the future. The model has a context window of 128K tokens and knowledge up to October 2023. Thanks to the improved tokenizer shared with GPT-4o, handling non-English text is now even more cost effective.

GPT-4o mini surpasses GPT-3.5 Turbo and other small models on academic benchmarks across both textual intelligence and multimodal reasoning, and supports the same range of languages as GPT-4o. It also demonstrates strong performance in function calling, which can enable developers to build applications that fetch data or take actions with external systems, and improved long-context performance compared to GPT-3.5 Turbo.

## Model ID
Availability	Lifecycle	Max request	Retirement Date
2024-07-18	Standard, Global Standard, Global Batch, Regional Provisioned Throughput, Global Provisioned Throughput, Data Zone Standard, Data Zone Provisioned Throughput, Data Zone Batch	Generally available

## Data, media and languages
Property	Description
Supported data types
Inputs	Outputs
text, image, audio	text
Supported languages	en, it, af, es, de, fr, id, ru, pl, uk, el, lv, zh, ar, tr, ja, sw, cy, ko, is, bn, ur, ne, th, pa, mr, te

## Transparency

### Model Provider

This model is provided through the Azure OpenAI service.

### Relevant documents

The following documents are applicable:

Overview of Responsible AI practices for Azure OpenAI models
Transparency Note for Azure OpenAI Service

### Acknowledgments

Leads: Jacob Menick, Kevin Lu, Shengjia Zhao, Eric Wallace, Hongyu Ren, Haitang Hu, Nick Stathas, Felipe Petroski Such

Program Lead: Mianna Chen

Contributions noted in https://openai.com/gpt-4o-contributions/

## Responsible AI Considerations

Built-in safety measures - Safety is built into our models from the beginning, and reinforced at every step of our development process. In pre-training, we filter out information that we do not want our models to learn from or output, such as hate speech, adult content, sites that primarily aggregate personal information, and spam. In post-training, we align the model's behavior to our policies using techniques such as reinforcement learning with human feedback (RLHF) to improve the accuracy and reliability of the models' responses.

GPT-4o mini has the same safety mitigations built-in as GPT-4o, which we carefully assessed using both automated and human evaluations according to our Preparedness Framework and in line with our voluntary commitments. More than 70 external experts in fields like social psychology and misinformation tested GPT-4o to identify potential risks, which we have addressed and plan to share the details of in the forthcoming GPT-4o system card and Preparedness scorecard. Insights from these expert evaluations have helped improve the safety of both GPT-4o and GPT-4o mini.

Building on these learnings, our teams also worked to improve the safety of GPT-4o mini using new techniques informed by our research. GPT-4o mini in the API is the first model to apply our instruction hierarchy method, which helps to improve the model's ability to resist jailbreaks, prompt injections, and system prompt extractions. This makes the model's responses more reliable and helps make it safer to use in applications at scale.

We'll continue to monitor how GPT-4o mini is being used and improve the model's safety as we identify new risks.

## Content Filtering

Prompts and completions are passed through a default configuration of Azure AI Content Safety classification models to detect and prevent the output of harmful content. Learn more about Azure AI Content Safety. Additional classification models and configuration options are available when you deploy an Azure OpenAI model in production; learn more.

## More details from model provider

GPT-4o mini surpasses GPT-3.5 Turbo and other small models on academic benchmarks across both textual intelligence and multimodal reasoning, and supports the same range of languages as GPT-4o. It also demonstrates strong performance in function calling, which can enable developers to build applications that fetch data or take actions with external systems, and improved long-context performance compared to GPT-3.5 Turbo.

GPT-4o mini has been evaluated across several key benchmarks.

Reasoning tasks: GPT-4o mini is better than other small models at reasoning tasks involving both text and vision, scoring 82.0% on MMLU, a textual intelligence and reasoning benchmark, as compared to 77.9% for Gemini Flash and 73.8% for Claude Haiku.

Math and coding proficiency: GPT-4o mini excels in mathematical reasoning and coding tasks, outperforming previous small models on the market. On MGSM, measuring math reasoning, GPT-4o mini scored 87.0%, compared to 75.5% for Gemini Flash and 71.7% for Claude Haiku. GPT-4o mini scored 87.2% on HumanEval, which measures coding performance, compared to 71.5% for Gemini Flash and 75.9% for Claude Haiku.

Multimodal reasoning: GPT-4o mini also shows strong performance on MMMU, a multimodal reasoning eval, scoring 59.4% compared to 56.1% for Gemini Flash and 50.2% for Claude Haiku.

Task	GPT-4o mini Score	Gemini Flash Score	Claude Haiku Score
MMLU (Reasoning Text and Vision)	82.0%	77.9%	73.8%
MGSM (Math Reasoning)	87.0%	75.5%	71.7%
HumanEval (Coding Performance)	87.2%	71.5%	75.9%
MMMU (Multimodal Reasoning)	59.4%	56.1%	50.2%
Source: GPT-4o mini: advancing cost-efficient intelligence.