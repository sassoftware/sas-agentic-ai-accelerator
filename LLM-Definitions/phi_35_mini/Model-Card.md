---
license: mit
license_link: https://huggingface.co/microsoft/Phi-3.5-mini-instruct/resolve/main/LICENSE
language:
- multilingual
pipeline_tag: text-generation
tags:
- nlp
- code
widget:
- messages:
  - role: user
    content: Can you provide ways to eat combinations of bananas and dragonfruits?
library_name: transformers
---

## Model Summary

Phi-3.5-mini is a lightweight, state-of-the-art open model built upon datasets used for Phi-3 - synthetic data and filtered publicly available websites - with a focus on very high-quality, reasoning dense data. The model belongs to the Phi-3 model family and supports 128K token context length. The model underwent a rigorous enhancement process, incorporating both supervised fine-tuning, proximal policy optimization, and direct preference optimization to ensure precise instruction adherence and robust safety measures.

🏡 [Phi-3 Portal](https://azure.microsoft.com/en-us/products/phi-3) <br>
📰 [Phi-3 Microsoft Blog](https://aka.ms/phi3.5-techblog) <br>
📖 [Phi-3 Technical Report](https://arxiv.org/abs/2404.14219) <br>
👩‍🍳 [Phi-3 Cookbook](https://github.com/microsoft/Phi-3CookBook) <br>
🖥️ [Try It](https://aka.ms/try-phi3.5mini) <br>

**Phi-3.5**: [[mini-instruct](https://huggingface.co/microsoft/Phi-3.5-mini-instruct) | [onnx](https://huggingface.co/microsoft/Phi-3.5-mini-instruct-onnx)]; [[MoE-instruct]](https://huggingface.co/microsoft/Phi-3.5-MoE-instruct); [[vision-instruct]](https://huggingface.co/microsoft/Phi-3.5-vision-instruct)

## Intended Uses

### Primary Use Cases

The model is intended for commercial and research use in multiple languages. The model provides uses for general purpose AI systems and applications which require:

1) Memory/compute constrained environments
2) Latency bound scenarios
3) Strong reasoning (especially code, math and logic)

Our model is designed to accelerate research on language and multimodal models, for use as a building block for generative AI powered features. 

### Use Case Considerations

Our models are not specifically designed or evaluated for all downstream purposes. Developers should consider common limitations of language models as they select use cases, and evaluate and mitigate for accuracy, safety, and fariness before using within a specific downstream use case, particularly for high risk scenarios. Developers should be aware of and adhere to applicable laws or regulations (including privacy, trade compliance laws, etc.) that are relevant to their use case.

***Nothing contained in this Model Card should be interpreted as or deemed a restriction or modification to the license the model is released under.*** 

## Release Notes 

This is an update over the June 2024 instruction-tuned Phi-3 Mini release based on valuable user feedback. The model used additional post-training data leading to substantial gains on multilingual, multi-turn conversation quality, and reasoning capability. We believe most use cases will benefit from this release, but we encourage users to test in their particular AI applications. We appreciate the enthusiastic adoption of the Phi-3 model family, and continue to welcome all feedback from the community.

### Multilingual

The table below highlights multilingual capability of the Phi-3.5 Mini on multilingual MMLU, MEGA, and multilingual MMLU-pro datasets. Overall, we observed that even with just 3.8B active parameters, the model is  competitive on multilingual tasks in comparison to other models with a much bigger active parameters.  

| Benchmark                  | Phi-3.5 Mini-Ins | Phi-3.0-Mini-128k-Instruct (June2024) | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|----------------------------|------------------|-----------------------|--------------------------|---------------------------|------------------|----------------|------------------|-------------------------------|
| Multilingual MMLU          | 55.4             | 51.08                 | 47.4                     | 58.9                      | 56.2             | 63.8           | 77.2             | 72.9                          |
| Multilingual MMLU-Pro      | 30.9             | 30.21                 | 15.0                     | 34.0                      | 21.4             | 43.0           | 57.9             | 53.2                          |
| MGSM                       | 47.9             | 41.56                 | 31.8                     | 63.3                      | 56.7             | 75.1           | 75.8             | 81.7                          |
| MEGA MLQA                  | 61.7             | 55.5                  | 43.9                     | 61.2                      | 45.2             | 54.4           | 61.6             | 70.0                          |
| MEGA TyDi QA               | 62.2             | 55.9                  | 54.0                     | 63.7                      | 54.5             | 65.6           | 63.6             | 81.8                          |
| MEGA UDPOS                 | 46.5             | 48.1                  | 57.2                     | 58.2                      | 54.1             | 56.6           | 62.4             | 66.0                          |
| MEGA XCOPA                 | 63.1             | 62.4                  | 58.8                     | 10.8                      | 21.1             | 31.2           | 95.0             | 90.3                          |
| MEGA XStoryCloze           | 73.5             | 73.6                  | 75.5                     | 92.3                      | 71.0             | 87.0           | 20.7             | 96.6                          |
| **Average** | **55.2** | **52.3** | **47.9** | **55.3** | **47.5** | **59.6** | **64.3** | **76.6** |

The table below shows Multilingual MMLU scores in some of the supported languages. For more multi-lingual benchmarks and details, see [Appendix A](#appendix-a).

| Benchmark | Phi-3.5 Mini-Ins | Phi-3.0-Mini-128k-Instruct (June2024) | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|-----------|------------------|-----------------------|--------------------------|---------------------------|------------------|----------------|------------------|-------------------------------|
| Arabic    | 44.2             | 35.4                  | 33.7                     | 45.3                      | 49.1             | 56.3           | 73.6             | 67.1                          |
| Chinese   | 52.6             | 46.9                  | 45.9                     | 58.2                      | 54.4             | 62.7           | 66.7             | 70.8                          |
| Dutch     | 57.7             | 48.0                  | 51.3                     | 60.1                      | 55.9             | 66.7           | 80.6             | 74.2                          |
| French    | 61.1             | 61.7                  | 53.0                     | 63.8                      | 62.8             | 67.0           | 82.9             | 75.6                          |
| German    | 62.4             | 61.3                  | 50.1                     | 64.5                      | 59.9             | 65.7           | 79.5             | 74.3                          |
| Italian   | 62.8             | 63.1                  | 52.5                     | 64.1                      | 55.9             | 65.7           | 82.6             | 75.9                          |
| Russian   | 50.4             | 45.3                  | 48.9                     | 59.0                      | 57.4             | 63.2           | 78.7             | 72.6                          |
| Spanish   | 62.6             | 61.3                  | 53.9                     | 64.3                      | 62.6             | 66.0           | 80.0             | 75.5                          |
| Ukrainian | 45.2             | 36.7                  | 46.9                     | 56.6                      | 52.9             | 62.0           | 77.4             | 72.6                          |

### Long Context

Phi-3.5-mini supports 128K context length, therefore the model is capable of several long context tasks including long document/meeting summarization, long document QA, long document information retrieval. We see that Phi-3.5-mini is clearly better than Gemma-2 family which only supports 8K context length. Phi-3.5-mini is competitive with other much larger open-weight models such as Llama-3.1-8B-instruct, Mistral-7B-instruct-v0.3, and Mistral-Nemo-12B-instruct-2407.

| Benchmark | Phi-3.5-mini-instruct | Llama-3.1-8B-instruct | Mistral-7B-instruct-v0.3 | Mistral-Nemo-12B-instruct-2407 | Gemini-1.5-Flash | GPT-4o-mini-2024-07-18 (Chat) |
|--|--|--|--|--|--|--|
| GovReport | 25.9 | 25.1 | 26.0 | 25.6 | 27.8 | 24.8 |
| QMSum | 21.3 | 21.6 | 21.3 | 22.1 | 24.0 | 21.7 |
| Qasper | 41.9 | 37.2 | 31.4 | 30.7 | 43.5 | 39.8 |
| SQuALITY | 25.3 | 26.2 | 25.9 | 25.8 | 23.5 | 23.8 |
| SummScreenFD | 16.0 | 17.6 | 17.5 | 18.2 | 16.3 | 17.0 |
| **Average** | **26.1** | **25.5** | **24.4** | **24.5** | **27.0** | **25.4** |

RULER: a retrieval-based benchmark for long context understanding
| Model | 4K | 8K | 16K | 32K | 64K | 128K | Average |
|--|--|--|--|--|--|--|--|
| **Phi-3.5-mini-instruct** | 94.3 | 91.1 | 90.7 | 87.1 | 78.0 | 63.6 | **84.1** |
| **Llama-3.1-8B-instruct** | 95.5 | 93.8 | 91.6 | 87.4 | 84.7 | 77.0 | **88.3** |
| **Mistral-Nemo-12B-instruct-2407** | 87.8 | 87.2 | 87.7 | 69.0 | 46.8 | 19.0 | **66.2** |

RepoQA: a benchmark for long context code understanding
| Model | Python | C++ | Rust | Java | TypeScript | Average |
|--|--|--|--|--|--|--|
| **Phi-3.5-mini-instruct** | 86 | 67 | 73 | 77 | 82 | **77** |
| **Llama-3.1-8B-instruct** | 80 | 65 | 73 | 76 | 63 | **71** |
| **Mistral-7B-instruct-v0.3** | 61 | 57 | 51 | 61 | 80 | **62** |

## Usage

### Requirements
Phi-3 family has been integrated in the `4.43.0` version of `transformers`. The current `transformers` version can be verified with: `pip list | grep transformers`.

Examples of required packages:
```
flash_attn==2.5.8
torch==2.3.1
accelerate==0.31.0
transformers==4.43.0
```

Phi-3.5-mini-instruct is also available in [Azure AI Studio](https://aka.ms/try-phi3.5mini)

### Tokenizer

Phi-3.5-mini-Instruct supports a vocabulary size of up to `32064` tokens. The [tokenizer files](https://huggingface.co/microsoft/Phi-3.5-mini-instruct/blob/main/added_tokens.json) already provide placeholder tokens that can be used for downstream fine-tuning, but they can also be extended up to the model's vocabulary size.

### Input Formats
Given the nature of the training data, the Phi-3.5-mini-instruct model is best suited for prompts using the chat format as follows:

```
<|system|>
You are a helpful assistant.<|end|>
<|user|>
How to explain Internet for a medieval knight?<|end|>
<|assistant|>
```

### Loading the model locally
After obtaining the Phi-3.5-mini-instruct model checkpoint, users can use this sample code for inference.

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

torch.random.manual_seed(0)

model = AutoModelForCausalLM.from_pretrained(
    "microsoft/Phi-3.5-mini-instruct", 
    device_map="cuda", 
    torch_dtype="auto", 
    trust_remote_code=True, 
)
tokenizer = AutoTokenizer.from_pretrained("microsoft/Phi-3.5-mini-instruct")

messages = [
    {"role": "system", "content": "You are a helpful AI assistant."},
    {"role": "user", "content": "Can you provide ways to eat combinations of bananas and dragonfruits?"},
    {"role": "assistant", "content": "Sure! Here are some ways to eat bananas and dragonfruits together: 1. Banana and dragonfruit smoothie: Blend bananas and dragonfruits together with some milk and honey. 2. Banana and dragonfruit salad: Mix sliced bananas and dragonfruits together with some lemon juice and honey."},
    {"role": "user", "content": "What about solving an 2x + 3 = 7 equation?"},
]

pipe = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
)

generation_args = {
    "max_new_tokens": 500,
    "return_full_text": False,
    "temperature": 0.0,
    "do_sample": False,
}

output = pipe(messages, **generation_args)
print(output[0]['generated_text'])
```

Notes: If you want to use flash attention, call _AutoModelForCausalLM.from_pretrained()_ with _attn_implementation="flash_attention_2"_

## Responsible AI Considerations

Like other language models, the Phi family of models can potentially behave in ways that are unfair, unreliable, or offensive. Some of the limiting behaviors to be aware of include:  
+ Quality of Service: The Phi models are trained primarily on English text and some additional multilingual text. Languages other than English will experience worse performance as well as performance disparities across non-English. English language varieties with less representation in the training data might experience worse performance than standard American English.   
+ Multilingual performance and safety gaps: We believe it is important to make language models more widely available across different languages, but the Phi 3 models still exhibit challenges common across multilingual releases. As with any deployment of LLMs, developers will be better positioned to test for performance or safety gaps for their linguistic and cultural context and customize the model with additional fine-tuning and appropriate safeguards.
+ Representation of Harms & Perpetuation of Stereotypes: These models can over- or under-represent groups of people, erase representation of some groups, or reinforce demeaning or negative stereotypes. Despite safety post-training, these limitations may still be present due to differing levels of representation of different groups, cultural contexts, or prevalence of examples of negative stereotypes in training data that reflect real-world patterns and societal biases. 
+ Inappropriate or Offensive Content: These models may produce other types of inappropriate or offensive content, which may make it inappropriate to deploy for sensitive contexts without additional mitigations that are specific to the case. 
+ Information Reliability: Language models can generate nonsensical content or fabricate content that might sound reasonable but is inaccurate or outdated.  
+ Limited Scope for Code: Majority of Phi-3 training data is based in Python and use common packages such as "typing, math, random, collections, datetime, itertools". If the model generates Python scripts that utilize other packages or scripts in other languages, we strongly recommend users manually verify all API uses.
+ Long Conversation: Phi-3 models, like other models, can in some cases generate responses that are repetitive, unhelpful, or inconsistent in very long chat sessions in both English and non-English languages. Developers are encouraged to place appropriate mitigations, like limiting conversation turns to account for the possible conversational drift

Developers should apply responsible AI best practices, including mapping, measuring, and mitigating risks associated with their specific use case and cultural, linguistic context. Phi-3 family of models are general purpose models. As developers plan to deploy these models for specific use cases, they are encouraged to fine-tune the models for their use case and leverage the models as part of broader AI systems with language-specific safeguards in place. Important areas for consideration include:  

+ Allocation: Models may not be suitable for scenarios that could have consequential impact on legal status or the allocation of resources or life opportunities (ex: housing, employment, credit, etc.) without further assessments and additional debiasing techniques.
+ High-Risk Scenarios: Developers should assess the suitability of using models in high-risk scenarios where unfair, unreliable or offensive outputs might be extremely costly or lead to harm. This includes providing advice in sensitive or expert domains where accuracy and reliability are critical (ex: legal or health advice). Additional safeguards should be implemented at the application level according to the deployment context. 
+ Misinformation: Models may produce inaccurate information. Developers should follow transparency best practices and inform end-users they are interacting with an AI system. At the application level, developers can build feedback mechanisms and pipelines to ground responses in use-case specific, contextual information, a technique known as Retrieval Augmented Generation (RAG).   
+ Generation of Harmful Content: Developers should assess outputs for their context and use available safety classifiers or custom solutions appropriate for their use case. 
+ Misuse: Other forms of misuse such as fraud, spam, or malware production may be possible, and developers should ensure that their applications do not violate applicable laws and regulations.

## Training

### Model

**Architecture:** Phi-3.5-mini has 3.8B parameters and is a dense decoder-only Transformer model using the same tokenizer as Phi-3 Mini.<br>
**Inputs:** Text. It is best suited for prompts using chat format.<br>
**Context length:** 128K tokens<br>
**GPUs:** 512 H100-80G<br>
**Training time:** 10 days<br>
**Training data:** 3.4T tokens<br>
**Outputs:** Generated text in response to the input<br>
**Dates:** Trained between June and August 2024<br>
**Status:** This is a static model trained on an offline dataset with cutoff date October 2023 for publicly available data. Future versions of the tuned models may be released as we improve models.<br>
**Supported languages:** Arabic, Chinese, Czech, Danish, Dutch, English, Finnish, French, German, Hebrew, Hungarian, Italian, Japanese, Korean, Norwegian, Polish, Portuguese, Russian, Spanish, Swedish, Thai, Turkish, Ukrainian<br>
**Release date:** August 2024<br>

### Training Datasets
Our training data includes a wide variety of sources, totaling 3.4 trillion tokens, and is a combination of 
1) publicly available documents filtered rigorously for quality, selected high-quality educational data, and code;
2) newly created synthetic, “textbook-like” data for the purpose of teaching math, coding, common sense reasoning, general knowledge of the world (science, daily activities, theory of mind, etc.);
3) high quality chat format supervised data covering various topics to reflect human preferences on different aspects such as instruct-following, truthfulness, honesty and helpfulness. 

We are focusing on the quality of data that could potentially improve the reasoning ability for the model, and we filter the publicly available documents to contain the correct level of knowledge. As an example, the result of a game in premier league in a particular day might be good training data for frontier models, but we need to remove such information to leave more model capacity for reasoning for the small size models. More details about data can be found in the [Phi-3 Technical Report](https://arxiv.org/pdf/2404.14219).

### Fine-tuning

A basic example of multi-GPUs supervised fine-tuning (SFT) with TRL and Accelerate modules is provided [here](https://huggingface.co/microsoft/Phi-3.5-mini-instruct/resolve/main/sample_finetune.py).

## Benchmarks

We report the results under completion format for Phi-3.5-mini on standard open-source benchmarks measuring the model's reasoning ability (both common sense reasoning and logical reasoning). We compare to Mistral-7B-Instruct-v0.3,  Mistral-Nemo-12B-Ins-2407, Llama-3.1-8B-Ins, Gemma-2-9B-Ins, Gemini 1.5 Flash, and GPT-4o-mini-2024-07-18 (Chat).

All the reported numbers are produced with the exact same pipeline to ensure that the numbers are comparable. These numbers might differ from other published numbers due to slightly different choices in the evaluation.

As is now standard, we use few-shot prompts to evaluate the models, at temperature 0. 
The prompts and number of shots are part of a Microsoft internal tool to evaluate language models, and in particular we did no optimization to the pipeline for Phi-3.
More specifically, we do not change prompts, pick different few-shot examples, change prompt format, or do any other form of optimization for the model.

The number of k–shot examples is listed per-benchmark. At the high-level overview of the model quality on representative benchmarks: 

| Category       | Benchmark                | Phi-3.5 Mini-Ins | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|----------------|--------------------------|------------------|--------------------------|---------------------------|------------------|----------------|------------------|------------------------------|
| Popular aggregated benchmark | Arena Hard | 37 | 18.1 | 39.4 | 25.7 | 42 | 55.2 | 75 |
|                | BigBench Hard CoT (0-shot) | 69 | 33.4 | 60.2 | 63.4 | 63.5 | 66.7 | 80.4 |
|                | MMLU (5-shot) | 69 | 60.3 | 67.2 | 68.1 | 71.3 | 78.7 | 77.2 |
|                | MMLU-Pro (0-shot, CoT) | 47.4 | 18 | 40.7 | 44 | 50.1 | 57.2 | 62.8 |
| Reasoning      | ARC Challenge (10-shot) | 84.6 | 77.9 | 84.8 | 83.1 | 89.8 | 92.8 | 93.5 |
|                | BoolQ (2-shot) | 78 | 80.5 | 82.5 | 82.8 | 85.7 | 85.8 | 88.7 |
|                | GPQA (0-shot, CoT) | 30.4 | 15.6 | 28.6 | 26.3 | 29.2 | 37.5 | 41.1 |
|                | HellaSwag (5-shot) | 69.4 | 71.6 | 76.7 | 73.5 | 80.9 | 67.5 | 87.1 |
|                | OpenBookQA (10-shot) | 79.2 | 78 | 84.4 | 84.8 | 89.6 | 89 | 90 |
|                | PIQA (5-shot) | 81 | 73.4 | 83.5 | 81.2 | 83.7 | 87.5 | 88.7 |
|                | Social IQA (5-shot) | 74.7 | 73 | 75.3 | 71.8 | 74.7 | 77.8 | 82.9 |
|                | TruthfulQA (MC2) (10-shot) | 64 | 64.7 | 68.1 | 69.2 | 76.6 | 76.6 | 78.2 |
|                | WinoGrande (5-shot) | 68.5 | 58.1 | 70.4 | 64.7 | 74 | 74.7 | 76.9 |
| Multilingual   | Multilingual MMLU (5-shot) | 55.4 | 47.4 | 58.9 | 56.2 | 63.8 | 77.2 | 72.9 |
|                | MGSM (0-shot CoT) | 47.9 | 31.8 | 63.3 | 56.7 | 76.4 | 75.8 | 81.7 |
| Math           | GSM8K (8-shot, CoT) | 86.2 | 54.4 | 84.2 | 82.4 | 84.9 | 82.4 | 91.3 |
|                | MATH (0-shot, CoT) | 48.5 | 19 | 31.2 | 47.6 | 50.9 | 38 | 70.2 |
| Long context   | Qasper | 41.9 | 31.4 | 30.7 | 37.2 | 13.9 | 43.5 | 39.8 |
|                | SQuALITY | 24.3 | 25.9 | 25.8 | 26.2 | 0 | 23.5 | 23.8 |
| Code Generation| HumanEval (0-shot) | 62.8 | 35.4 | 63.4 | 66.5 | 61 | 74.4 | 86.6 |
|                | MBPP (3-shot) | 69.6 | 50.4 | 68.1 | 69.4 | 69.3 | 77.5 | 84.1 |
| **Average** | | **61.4** | **48.5** | **61.3** | **61.0** | **63.3** | **68.5** | **74.9** |

We take a closer look at different categories across public benchmark datasets at the table below:

| Category                   | Phi-3.5 Mini-Ins | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|----------------------------|------------------|--------------------------|---------------------------|------------------|----------------|------------------|------------------------------|
| Popular aggregated benchmark | 55.6 | 32.5 | 51.9 | 50.3 | 56.7 | 64.5 | 73.9 |
| Reasoning                  | 70.1 | 65.2 | 72.2 | 70.5 | 75.4 | 77.7 | 80 |
| Language understanding     | 62.6 | 62.8 | 67 | 62.9 | 72.8 | 66.6 | 76.8 |
| Robustness                 | 59.7 | 53.4 | 65.2 | 59.8 | 64.7 | 68.9 | 77.5 |
| Long context               | 26.1 | 25.5 | 24.4 | 24.5 | 0 | 27 | 25.4 |
| Math                       | 67.4 | 36.7 | 57.7 | 65 | 67.9 | 60.2 | 80.8 |
| Code generation            | 62 | 43.1 | 56.9 | 65.8 | 58.3 | 66.8 | 69.9 |
| Multilingual               | 55.2 | 47.9 | 55.3 | 47.5 | 59.6 | 64.3 | 76.6 |

Overall, the model with only 3.8B-param achieves a similar level of multilingual language understanding and reasoning ability as much larger models.
However, it is still fundamentally limited by its size for certain tasks. 
The model simply does not have the capacity to store too much factual knowledge, therefore, users may experience factual incorrectness. 
However, we believe such weakness can be resolved by augmenting Phi-3.5 with a search engine, particularly when using the model under RAG settings.  

## Safety Evaluation and Red-Teaming

We leveraged various evaluation techniques including red teaming, adversarial conversation simulations, and multilingual safety evaluation benchmark datasets to 
evaluate Phi-3.5 models' propensity to produce undesirable outputs across multiple languages and risk categories. 
Several approaches were used to compensate for the limitations of one approach alone. Findings across the various evaluation methods indicate that safety 
post-training that was done as detailed in the [Phi-3 Safety Post-Training paper](https://arxiv.org/pdf/2407.13833) had a positive impact across multiple languages and risk categories as observed by 
refusal rates (refusal to output undesirable outputs) and robustness to jailbreak techniques. Note, however, while comprehensive red team evaluations were conducted 
across all models in the prior release of Phi models, red teaming was largely focused on Phi-3.5 MOE across multiple languages and risk categories for this release as 
it is the largest and more capable model of the three models. Details on prior red team evaluations across Phi models can be found in the [Phi-3 Safety Post-Training paper](https://arxiv.org/pdf/2407.13833). 
For this release, insights from red teaming indicate that the models may refuse to generate undesirable outputs in English, even when the request for undesirable output 
is in another language. Models may also be more susceptible to longer multi-turn jailbreak techniques across both English and non-English languages. These findings 
highlight the need for industry-wide investment in the development of high-quality safety evaluation datasets across multiple languages, including low resource languages, 
and risk areas that account for cultural nuances where those languages are spoken.


## Software
* [PyTorch](https://github.com/pytorch/pytorch)
* [Transformers](https://github.com/huggingface/transformers)
* [Flash-Attention](https://github.com/HazyResearch/flash-attention)

## Hardware
Note that by default, the Phi-3.5-mini-instruct model uses flash attention, which requires certain types of GPU hardware to run. We have tested on the following GPU types:
* NVIDIA A100
* NVIDIA A6000
* NVIDIA H100

If you want to run the model on:
* NVIDIA V100 or earlier generation GPUs: call AutoModelForCausalLM.from_pretrained() with attn_implementation="eager"
  
## License
The model is licensed under the [MIT license](./LICENSE).

## Trademarks
This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft’s Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party’s policies.


## Appendix A

#### MGSM

| Languages | Phi-3.5-Mini-Instruct | Phi-3.0-Mini-128k-Instruct (June2024) | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|-----------|------------------------|---------------------------------------|--------------------------|---------------------------|------------------|----------------|------------------|-------------------------------|
| German    | 69.6                   | 65.2                                  | 42.4                     | 74.4                      | 68.4             | 76.8           | 81.6             | 82.8                          |
| English   | 85.2                   | 83.2                                  | 60.0                     | 86.0                      | 81.2             | 88.8           | 90.8             | 90.8                          |
| Spanish   | 79.2                   | 77.6                                  | 46.4                     | 75.6                      | 66.4             | 82.4           | 84.8             | 86.8                          |
| French    | 71.6                   | 72.8                                  | 47.2                     | 70.4                      | 66.8             | 74.4           | 77.2             | 81.6                          |
| Japanese  | 50.0                   | 35.2                                  | 22.8                     | 62.4                      | 49.2             | 67.6           | 77.6             | 80.4                          |
| Russian   | 67.2                   | 51.6                                  | 43.2                     | 73.6                      | 67.2             | 78.4           | 84.8             | 86.4                          |
| Thai      | 29.6                   | 6.4                                   | 18.4                     | 53.2                      | 56.0             | 76.8           | 87.6             | 81.6                          |
| Chinese   | 60.0                   | 52.8                                  | 42.4                     | 66.4                      | 68.0             | 72.8           | 82.0             | 82.0                          |

#### Multilingual MMLU-pro 

| Languages  | Phi-3.5-Mini-Instruct | Phi-3.0-Mini-128k-Instruct (June2024) | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|------------|-----------------------|---------------------------------------|--------------------------|---------------------------|------------------|----------------|------------------|-------------------------------|
| Czech      | 24.9                  | 26.3                                  | 14.6                     | 30.6                      | 23.0             | 40.5           | 59.0             | 40.9                          |
| English    | 47.7                  | 46.2                                  | 17.7                     | 39.8                      | 43.1             | 49.0           | 66.1             | 62.7                          |
| Finnish    | 22.3                  | 20.5                                  | 11.5                     | 30.4                      | 9.7              | 37.5           | 54.5             | 50.1                          |
| Norwegian  | 29.9                  | 27.8                                  | 14.4                     | 33.2                      | 22.2             | 44.4           | 60.7             | 59.1                          |
| Polish     | 25.7                  | 26.4                                  | 16.3                     | 33.6                      | 9.2              | 41.7           | 53.9             | 42.8                          |
| Portuguese | 38.7                  | 37.6                                  | 15.3                     | 36.0                      | 29.3             | 43.5           | 54.0             | 56.9                          |
| Swedish    | 30.7                  | 28.1                                  | 15.5                     | 34.3                      | 16.9             | 42.6           | 57.7             | 55.5                          |

#### MEGA 

##### MLQA

| Languages | Phi-3.5-Mini-Instruct | Phi-3.0-Mini-128k-Instruct (June2024) | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|-----------|-----------------------|---------------------------------------|--------------------------|---------------------------|------------------|----------------|------------------|-------------------------------|
| Arabic    | 54.3                  | 32.7                                  | 23.5                     | 31.4                      | 31.5             | 57.4           | 63.8             | 64.0                          |
| Chinese   | 36.1                  | 31.8                                  | 22.4                     | 27.4                      | 18.6             | 45.4           | 38.1             | 38.9                          |
| English   | 80.3                  | 78.9                                  | 68.2                     | 75.5                      | 67.2             | 82.9           | 69.5             | 82.2                          |
| German    | 61.8                  | 59.1                                  | 49.0                     | 57.8                      | 38.9             | 63.8           | 55.9             | 64.1                          |
| Spanish   | 68.8                  | 67.0                                  | 50.3                     | 63.6                      | 52.7             | 72.8           | 59.6             | 70.1                          |

##### TyDi QA 

| Languages | Phi-3.5-Mini-Instruct | Phi-3.0-Mini-128k-Instruct (June2024) | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|-----------|-----------------------|---------------------------------------|--------------------------|---------------------------|------------------|----------------|------------------|-------------------------------|
| Arabic    | 69.7                  | 54.4                                  | 52.5                     | 49.8                      | 33.7             | 81.1           | 78.8             | 84.9                          |
| English   | 82.0                  | 82.0                                  | 60.5                     | 77.3                      | 65.1             | 82.4           | 60.9             | 81.8                          |
| Finnish   | 70.3                  | 64.3                                  | 68.6                     | 57.1                      | 74.4             | 85.7           | 73.5             | 84.8                          |
| Japanese  | 65.4                  | 56.7                                  | 45.3                     | 54.8                      | 34.1             | 74.6           | 59.7             | 73.3                          |
| Korean    | 74.0                  | 60.4                                  | 54.5                     | 54.2                      | 54.9             | 83.8           | 60.7             | 82.3                          |
| Russian   | 63.5                  | 62.7                                  | 52.3                     | 55.7                      | 27.4             | 69.8           | 60.1             | 72.5                          |
| Thai      | 64.4                  | 49.0                                  | 51.8                     | 43.5                      | 48.5             | 81.4           | 71.6             | 78.2                          |

##### XCOPA 

| Languages | Phi-3.5-Mini-Instruct | Phi-3.0-Mini-128k-Instruct (June2024) | Mistral-7B-Instruct-v0.3 | Mistral-Nemo-12B-Ins-2407 | Llama-3.1-8B-Ins | Gemma-2-9B-Ins | Gemini 1.5 Flash | GPT-4o-mini-2024-07-18 (Chat) |
|-----------|-----------------------|---------------------------------------|--------------------------|---------------------------|------------------|----------------|------------------|-------------------------------|
| English   | 94.6                  | 94.6                                  | 85.6                     | 94.4                      | 37.6             | 63.8           | 92.0             | 98.2                          |
| Italian   | 86.8                  | 84.8                                  | 76.8                     | 83.2                      | 16.2             | 37.2           | 85.6             | 97.6                          |
| Turkish   | 58.6                  | 57.2                                  | 61.6                     | 56.6                      | 38.4             | 60.2           | 91.4             | 94.6                          |


## Appendix B: Korean benchmarks

The prompt is the same as the [CLIcK paper](https://arxiv.org/abs/2403.06412) prompt. The experimental results below were given with max_tokens=512 (zero-shot), max_tokens=1024 (5-shot), temperature=0.01. No system prompt used.

- GPT-4o: 2024-05-13 version
- GPT-4o-mini: 2024-07-18 version
- GPT-4-turbo: 2024-04-09 version
- GPT-3.5-turbo: 2023-06-13 version

The overall Korean benchmarks show that the Phi-3.5-Mini-Instruct with only 3.8B params outperforms Llama-3.1-8B-Instruct.

| Benchmarks               |   Phi-3.5-Mini-Instruct |  Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:-------------------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| CLIcK                    |                   42.99 |                           29.12 |                   47.82 |    80.46 |         68.5  |         72.82 |           50.98 |
| HAERAE 1.0               |                   44.21 |                           36.41 |                   53.9  |    85.7  |         76.4  |         77.76 |           52.67 |
| KMMLU (0-shot, CoT)      |                   35.87 |                           30.82 |                   38.54 |    64.26 |         52.63 |         58.75 |           40.3  |
| KMMLU (5-shot)           |                   37.35 |                           29.98 |                   20.21 |    64.28 |         51.62 |         59.29 |           42.28 |
| KMMLU-HARD (0-shot, CoT) |                   24    |                           25.68 |                   24.03 |    39.62 |         24.56 |         30.56 |           20.97 | 
| KMMLU-HARD (5-shot)      |                   24.76 |                           25.73 |                   15.81 |    40.94 |         24.63 |         31.12 |           21.19 |
| **Average**                  |                   **35.62** |                           **29.99** |                   **29.29** |    **62.54** |         **50.08** |         **56.74** |           **39.61** | 

#### CLIcK (Cultural and Linguistic Intelligence in Korean)

##### Accuracy by supercategory
| supercategory   |   Phi-3.5-Mini-Instruct |   Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:----------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| Culture         |                   43.77 |                           29.74 |                   51.15 |    81.89 |         70.95 |         73.61 |           53.38 |
| Language        |                   41.38 |                           27.85 |                   40.92 |    77.54 |         63.54 |         71.23 |           46    |
| **Overall**     |                   42.99 |                           29.12 |                   47.82 |    80.46 |         68.5  |         72.82 |           50.98 |

##### Accuracy by category
| supercategory   | category    |   Phi-3.5-Mini-Instruct |   Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:----------------|:------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| Culture         | Economy     |                   61.02 |                           28.81 |                   66.1  |    94.92 |         83.05 |         89.83 |           64.41 |
| Culture         | Geography   |                   45.8  |                           29.01 |                   54.2  |    80.15 |         77.86 |         82.44 |           53.44 |
| Culture         | History     |                   26.15 |                           30    |                   29.64 |    66.92 |         48.4  |         46.4  |           31.79 |
| Culture         | Law         |                   32.42 |                           22.83 |                   44.29 |    70.78 |         57.53 |         61.19 |           41.55 |
| Culture         | Politics    |                   54.76 |                           33.33 |                   59.52 |    88.1  |         83.33 |         89.29 |           65.48 |
| Culture         | Pop Culture |                   60.98 |                           34.15 |                   60.98 |    97.56 |         85.37 |         92.68 |           75.61 |
| Culture         | Society     |                   54.37 |                           31.72 |                   65.05 |    92.88 |         85.44 |         86.73 |           71.2  |
| Culture         | Tradition   |                   47.75 |                           31.98 |                   54.95 |    87.39 |         74.77 |         79.28 |           55.86 |
| Language        | Functional  |                   37.6  |                           24    |                   32.8  |    84.8  |         64.8  |         80    |           40    |
| Language        | Grammar     |                   27.5  |                           23.33 |                   22.92 |    57.08 |         42.5  |         47.5  |           30    |
| Language        | Textual     |                   54.74 |                           33.33 |                   59.65 |    91.58 |         80.7  |         87.37 |           62.11 |

#### HAERAE

| category              |   Phi-3.5-Mini-Instruct |   Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:----------------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| General Knowledge     |                   31.25 |                           28.41 |                   34.66 |    77.27 |         53.41 |         66.48 |           40.91 |
| History               |                   32.45 |                           22.34 |                   44.15 |    92.02 |         84.57 |         78.72 |           30.32 |
| Loan Words            |                   47.93 |                           35.5  |                   63.31 |    79.88 |         76.33 |         78.11 |           59.17 |
| Rare Words            |                   55.06 |                           42.96 |                   63.21 |    87.9  |         81.98 |         79.01 |           61.23 |
| Reading Comprehension |                   42.95 |                           41.16 |                   51.9  |    85.46 |         77.18 |         80.09 |           56.15 |
| Standard Nomenclature |                   44.44 |                           32.68 |                   58.82 |    88.89 |         75.82 |         79.08 |           53.59 |
| **Overall**           |                   44.21 |                           36.41 |                   53.9  |    85.7  |         76.4  |         77.76 |           52.67 |

#### KMMLU (0-shot, CoT)

| supercategory   |   Phi-3.5-Mini-Instruct |   Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:----------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| Applied Science |                   35.8  |                           31.68 |                   37.03 |    61.52 |         49.29 |         55.98 |           38.47 |
| HUMSS           |                   31.56 |                           26.47 |                   37.29 |    69.45 |         56.59 |         63    |           40.9  |
| Other           |                   35.45 |                           31.01 |                   39.15 |    63.79 |         52.35 |         57.53 |           40.19 |
| STEM            |                   38.54 |                           31.9  |                   40.42 |    65.16 |         54.74 |         60.84 |           42.24 |
| **Overall**     |                   35.87 |                           30.82 |                   38.54 |    64.26 |         52.63 |         58.75 |           40.3  |

#### KMMLU (5-shot)

| supercategory   |   Phi-3.5-Mini-Instruct |   Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:----------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| Applied Science |                   37.42 |                           29.98 |                   19.24 |    61.47 |         48.66 |         56.85 |           40.22 |
| HUMSS           |                   34.72 |                           27.27 |                   22.5  |    68.79 |         55.95 |         63.68 |           43.35 |
| Other           |                   37.04 |                           30.76 |                   20.95 |    64.21 |         51.1  |         57.85 |           41.92 |
| STEM            |                   38.9  |                           30.73 |                   19.55 |    65.28 |         53.29 |         61.08 |           44.43 |
| **Overall**     |                   37.35 |                           29.98 |                   20.21 |    64.28 |         51.62 |         59.29 |           42.28 |

#### KMMLU-HARD (0-shot, CoT)

| supercategory   |   Phi-3.5-Mini-Instruct |   Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:----------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| Applied Science |                   27.08 |                           26.17 |                   26.25 |    37.12 |         22.25 |         29.17 |           21.07 |
| HUMSS           |                   20.21 |                           24.38 |                   20.21 |    41.97 |         23.31 |         31.51 |           19.44 |
| Other           |                   23.05 |                           24.82 |                   23.88 |    40.39 |         26.48 |         29.59 |           22.22 |
| STEM            |                   24.36 |                           26.91 |                   24.64 |    39.82 |         26.36 |         32.18 |           20.91 |
| **Overall**     |                   24    |                           25.68 |                   24.03 |    39.62 |         24.56 |         30.56 |           20.97 |

#### KMMLU-HARD (5-shot)

| supercategory   |   Phi-3.5-Mini-Instruct |   Phi-3.0-Mini-128k-Instruct (June2024) |   Llama-3.1-8B-Instruct |   GPT-4o |   GPT-4o-mini |   GPT-4-turbo |   GPT-3.5-turbo |
|:----------------|------------------------:|--------------------------------:|------------------------:|---------:|--------------:|--------------:|----------------:|
| Applied Science |                   25    |                           29    |                   12    |    31    |         21    |         25    |           20    |
| HUMSS           |                   21.89 |                           19.92 |                   14    |    43.98 |         23.47 |         33.53 |           19.53 |
| Other           |                   23.26 |                           27.27 |                   12.83 |    39.84 |         28.34 |         29.68 |           23.22 |
| STEM            |                   20.5  |                           25.25 |                   12.75 |    40.25 |         23.25 |         27.25 |           19.75 |
| **Overall**     |                   24.76 |                           25.73 |                   15.81 |    40.94 |         24.63 |         31.12 |           21.19 |