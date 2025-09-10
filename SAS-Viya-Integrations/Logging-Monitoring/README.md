# Parsing the Log

The goal is to create a table that extracts all of the relevant information from the container logs and collect everything as a CAS table.

For this two options are available:
1. The *Log-Parser-Code.sas* is a script that you can point at the logging file and it will parse all of the information into a CAS table.
2. The *LLM - Log Parser* custom step, which is just a nice little UI on top of the same SAS script - this step is located in the Custom-Steps folder.

The resulting table has the following columns:

| Column Name   | Column Label                                                 |
| ------------- | ------------------------------------------------------------ |
| timestamp     | The datetime of the request                                  |
| model         | The name of the model used                                   |
| systemPrompt  | The system prompt used by the request                        |
| userPrompt    | The user prompt used by the request                          |
| temperature   | The setting of temperature. Missing means it was not present in the request |
| max_tokens    | The setting of max new tokens. Missing means it was not present in the request |
| top_p         | The setting of top p. Missing means it was not present in the request |
| top_k         | The setting of top k. Missing means it was not present in the request |
| prompt_length | The amount of input tokens in the request                    |
| output_length | The amount of output tokens in the request                   |
| run_time      | The time for the model to generate the response in seconds   |
| response      | The response of the model to the users request               |
