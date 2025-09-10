---
license: apache-2.0
language:
  - en
  - fr
  - de
  - es
  - it
  - pt
  - ru
  - zh
  - ja
---

# Model Card for Mistral-Nemo-Instruct-2407

The Mistral-Nemo-Instruct-2407 Large Language Model (LLM) is an instruct fine-tuned version of the [Mistral-Nemo-Base-2407](https://huggingface.co/mistralai/Mistral-Nemo-Base-2407). Trained jointly by Mistral AI and NVIDIA, it significantly outperforms existing models smaller or similar in size.

For more details about this model please refer to our release [blog post](https://mistral.ai/news/mistral-nemo/).

## Key features
- Released under the **Apache 2 License**
- Pre-trained and instructed versions
- Trained with a **128k context window**
- Trained on a large proportion of **multilingual and code data**
- Drop-in replacement of Mistral 7B

## Model Architecture
Mistral Nemo is a transformer model, with the following architecture choices:
- **Layers:** 40
- **Dim:** 5,120
- **Head dim:** 128
- **Hidden dim:** 14,436
- **Activation Function:** SwiGLU
- **Number of heads:** 32
- **Number of kv-heads:** 8 (GQA)
- **Vocabulary size:** 2**17 ~= 128k
- **Rotary embeddings (theta = 1M)**

## Metrics

### Main Benchmarks

| Benchmark | Score |
| --- | --- |
| HellaSwag (0-shot) | 83.5% |
| Winogrande (0-shot) | 76.8% |
| OpenBookQA (0-shot) | 60.6% |
| CommonSenseQA (0-shot) | 70.4% |
| TruthfulQA (0-shot) | 50.3% |
| MMLU (5-shot) | 68.0% |
| TriviaQA (5-shot) | 73.8% |
| NaturalQuestions (5-shot) | 31.2% |

### Multilingual Benchmarks (MMLU)

| Language | Score |
| --- | --- |
| French | 62.3% |
| German | 62.7% |
| Spanish | 64.6% |
| Italian | 61.3% |
| Portuguese | 63.3% |
| Russian | 59.2% |
| Chinese | 59.0% |
| Japanese | 59.0% |

## Usage

The model can be used with three different frameworks

- [`mistral_inference`](https://github.com/mistralai/mistral-inference): See [here](https://huggingface.co/mistralai/Mistral-Nemo-Instruct-2407#mistral-inference)
- [`transformers`](https://github.com/huggingface/transformers): See [here](#transformers)
- [`NeMo`](https://github.com/NVIDIA/NeMo): See [nvidia/Mistral-NeMo-12B-Instruct](https://huggingface.co/nvidia/Mistral-NeMo-12B-Instruct)

### Mistral Inference

#### Install

It is recommended to use `mistralai/Mistral-Nemo-Instruct-2407` with [mistral-inference](https://github.com/mistralai/mistral-inference). For HF transformers code snippets, please keep scrolling.

```
pip install mistral_inference
```

#### Download

```py
from huggingface_hub import snapshot_download
from pathlib import Path

mistral_models_path = Path.home().joinpath('mistral_models', 'Nemo-Instruct')
mistral_models_path.mkdir(parents=True, exist_ok=True)

snapshot_download(repo_id="mistralai/Mistral-Nemo-Instruct-2407", allow_patterns=["params.json", "consolidated.safetensors", "tekken.json"], local_dir=mistral_models_path)
```

#### Chat

After installing `mistral_inference`, a `mistral-chat` CLI command should be available in your environment. You can chat with the model using

```
mistral-chat $HOME/mistral_models/Nemo-Instruct --instruct --max_tokens 256 --temperature 0.35
```

*E.g.* Try out something like:
```
How expensive would it be to ask a window cleaner to clean all windows in Paris. Make a reasonable guess in US Dollar.
```

#### Instruct following

```py
from mistral_inference.transformer import Transformer
from mistral_inference.generate import generate

from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
from mistral_common.protocol.instruct.messages import UserMessage
from mistral_common.protocol.instruct.request import ChatCompletionRequest

tokenizer = MistralTokenizer.from_file(f"{mistral_models_path}/tekken.json")
model = Transformer.from_folder(mistral_models_path)

prompt = "How expensive would it be to ask a window cleaner to clean all windows in Paris. Make a reasonable guess in US Dollar."

completion_request = ChatCompletionRequest(messages=[UserMessage(content=prompt)])

tokens = tokenizer.encode_chat_completion(completion_request).tokens

out_tokens, _ = generate([tokens], model, max_tokens=64, temperature=0.35, eos_id=tokenizer.instruct_tokenizer.tokenizer.eos_id)
result = tokenizer.decode(out_tokens[0])

print(result)
```

#### Function calling

```py
from mistral_common.protocol.instruct.tool_calls import Function, Tool
from mistral_inference.transformer import Transformer
from mistral_inference.generate import generate

from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
from mistral_common.protocol.instruct.messages import UserMessage
from mistral_common.protocol.instruct.request import ChatCompletionRequest


tokenizer = MistralTokenizer.from_file(f"{mistral_models_path}/tekken.json")
model = Transformer.from_folder(mistral_models_path)

completion_request = ChatCompletionRequest(
    tools=[
        Tool(
            function=Function(
                name="get_current_weather",
                description="Get the current weather",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state, e.g. San Francisco, CA",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                            "description": "The temperature unit to use. Infer this from the users location.",
                        },
                    },
                    "required": ["location", "format"],
                },
            )
        )
    ],
    messages=[
        UserMessage(content="What's the weather like today in Paris?"),
        ],
)

tokens = tokenizer.encode_chat_completion(completion_request).tokens

out_tokens, _ = generate([tokens], model, max_tokens=256, temperature=0.35, eos_id=tokenizer.instruct_tokenizer.tokenizer.eos_id)
result = tokenizer.decode(out_tokens[0])

print(result)
```

### Transformers

> [!IMPORTANT]
> NOTE: Until a new release has been made, you need to install transformers from source:
> ```sh
> pip install git+https://github.com/huggingface/transformers.git
> ```

If you want to use Hugging Face `transformers` to generate text, you can do something like this.

```py
from transformers import pipeline

messages = [
    {"role": "system", "content": "You are a pirate chatbot who always responds in pirate speak!"},
    {"role": "user", "content": "Who are you?"},
]
chatbot = pipeline("text-generation", model="mistralai/Mistral-Nemo-Instruct-2407")
chatbot(messages)
```

> [!TIP]
> Unlike previous Mistral models, Mistral Nemo requires smaller temperatures. We recommend to use a temperature of 0.3.

## Limitations

The Mistral Nemo Instruct model is a quick demonstration that the base model can be easily fine-tuned to achieve compelling performance. 
It does not have any moderation mechanisms. We're looking forward to engaging with the community on ways to
make the model finely respect guardrails, allowing for deployment in environments requiring moderated outputs.

## The Mistral AI Team

Albert Jiang, Alexandre Sablayrolles, Alexis Tacnet, Alok Kothari, Antoine Roux, Arthur Mensch, Audrey Herblin-Stoop, Augustin Garreau, Austin Birky, Bam4d, Baptiste Bout, Baudouin de Monicault, Blanche Savary, Carole Rambaud, Caroline Feldman, Devendra Singh Chaplot, Diego de las Casas, Eleonore Arcelin, Emma Bou Hanna, Etienne Metzger, Gaspard Blanchet, Gianna Lengyel, Guillaume Bour, Guillaume Lample, Harizo Rajaona, Henri Roussez, Hichem Sattouf, Ian Mack, Jean-Malo Delignon, Jessica Chudnovsky, Justus Murke, Kartik Khandelwal, Lawrence Stewart, Louis Martin, Louis Ternon, Lucile Saulnier, Lélio Renard Lavaud, Margaret Jennings, Marie Pellat, Marie Torelli, Marie-Anne Lachaux, Marjorie Janiewicz, Mickaël Seznec, Nicolas Schuhl, Niklas Muhs, Olivier de Garrigues, Patrick von Platen, Paul Jacob, Pauline Buche, Pavan Kumar Reddy, Perry Savas, Pierre Stock, Romain Sauvestre, Sagar Vaze, Sandeep Subramanian, Saurabh Garg, Sophia Yang, Szymon Antoniak, Teven Le Scao, Thibault Schueller, Thibaut Lavril, Thomas Wang, Théophile Gervet, Timothée Lacroix, Valera Nemychnikova, Wendy Shang, William El Sayed, William Marshall