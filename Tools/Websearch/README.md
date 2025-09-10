# Tavily Web Search Tool

## Overview

Tavily offers real-time search capabilities with AI-powered result ranking, domain filtering, and customizable content extraction. The implementation includes comprehensive error handling, input validation, and result filtering to ensure reliable search operations.

Tavily's search API provides access to current web content with features like:
- Real-time web search across billions of pages
- AI-powered relevance scoring and ranking
- News-specific search capabilities
- Domain inclusion/exclusion filtering
- Raw content extraction for detailed analysis
- Built-in answer generation from search results

## File Structure

```
Websearch/
├── README                              # This documentation file
├── tavily_websearch (requests api).py  # HTTP requests-based search implementation
├── process_tavily_websearch_output.py  # Search results parser and processor
└── tavily_websearch (python api).py    # Tavily client library-based search implementation
```

## Importing into SAS Viya

### Best Practices

When deploying this web search functionality into SAS Viya, follow these recommended practices for optimal performance and maintainability:

**Model Project Organization:**
- Create single model projects and import all the zip files each as a model.
- Allows for better oragnisation of releated functions.
- Further post processing functions can be created as new functions in this model project.

## Function Descriptions

### Tavily Search Functions

This repository includes two implementations of the Tavily web search functionality:

#### `tavily_websearch (requests api).py` - HTTP Requests Implementation
Direct HTTP API integration using Python's requests library for maximum control and customization.

#### `tavily_websearch (python api).py` - Tavily Client Library Implementation  
Uses the official Tavily Python client library for simplified integration and automatic error handling.

Both implementations provide the same interface and functionality described below.

#### Parameters

##### Authentication & Query

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `api_key` | string | ✅ Yes | Your Tavily API key for authentication (sign up here to get your key https://www.tavily.com/)|
| `query` | string | ✅ Yes | The search query string |

##### Search Configuration

| Parameter | Type | Required | Valid Values | Default | Description |
|-----------|------|----------|--------------|---------|-------------|
| `topic` | string | ✅ Yes | `"general"`, `"news"` | `"general"` | Type of search to perform |
| `search_depth` | string | ✅ Yes | `"basic"`, `"advanced"` | `"advanced"` | Depth of search results |
| `max_results` | integer | ✅ Yes | 1-20 | 10 | Maximum number of results to return |

##### Time Filtering

| Parameter | Type | Required | Valid Values | Default | Description |
|-----------|------|----------|--------------|---------|-------------|
| `time_range` | string | Optional | `"day"`, `"week"`, `"month"`, `"year"`, `"d"`, `"w"`, `"m"`, `"y"` | `"month"` | Time range for results (only if `topic` = `"general"` else ignored). If empty there is no time range. |
| `days` | integer | ✅ Yes | ≥ 1 | 7 | Number of days to look back (only if `topic` = `"news"` else ignored) |

##### Content Options

| Parameter | Type | Required | Valid Values | Default | Description |
|-----------|------|----------|--------------|---------|-------------|
| `include_answer` | integer | ✅ Yes | 0 (False), non-zero (True) | 0 | Include AI-generated answer |
| `include_raw_content` | integer | ✅ Yes | 0 (False), non-zero (True) | 0 | Include raw HTML content |

##### Domain Filtering

| Parameter | Type | Required | Description | Default |
|-----------|------|----------|-------------|---------|
| `include_domains` | list or string | Optional | List of domains to include (e.g., `["cnn.com", "bbc.com"]`) | `[]` |
| `exclude_domains` | list or string | Optional | List of domains to exclude | `[]` |

##### Result Filtering

| Parameter | Type | Required | Valid Range | Default | Description |
|-----------|------|----------|-------------|---------|-------------|
| `threshold` | float | ✅ Yes | 0.0 - 1.0 | 0.5 | Minimum relevance score for results |


#### Return Values

##### success (integer)
- `1`: API call succeeded
- `0`: API call failed

##### search_results (string)
- **On Success**: JSON string containing filtered search results
- **On Error**: Empty string (`""`)

##### errors (string)
Concatenated error messages and validation warnings:
- Parameter validation errors
- API authentication failures
- Network request errors
- JSON parsing errors

#### Error Handling

The function includes comprehensive error handling and input validation:

##### Automatic Parameter Correction
- Invalid `search_depth` → defaults to `"advanced"`
- Invalid `topic` → defaults to `"general"`  
- Invalid `max_results` → defaults to `10`
- Invalid `time_range` → defaults to `"month"`
- Invalid `days` → defaults to `7`
- Invalid `threshold` → defaults to `0.5`
- Unparseable domain lists → defaults to empty list `[]`

##### Common Error Scenarios
- **Invalid API Key**: Returns `success=0` with HTTP error details
- **Network Timeout**: Returns `success=0` with timeout error
- **Malformed Response**: Returns `success=0` with parsing error
- **Rate Limiting**: Returns `success=0` with HTTP 429 details

#### Dependencies

```python
import ast
import json
import requests
```

#### Notes

- The function filters results based on the `threshold` parameter, removing results with scores below the threshold
- Domain parameters can be passed as strings (e.g., `"['domain1.com', 'domain2.com']"`) or as Python lists
- Boolean parameters use integer convention: `0` = False, any other value = True
- API timeout is set to 45 seconds
- All validation errors are non-fatal and result in parameter defaults with warning messages

### Search Results Processor (`process_tavily_websearch_output.py`)

A utility function that parses JSON search results and extracts individual result items into separate variables for downstream processing.

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `search_results` | string | ✅ Yes | JSON string from Tavily search function output |

#### Return Values

Returns a tuple with 23 values: `(answer, success, errors, sr_1, sr_2, ..., sr_20)`

- **answer** (string): AI-generated answer from search results (if available)
- **success** (integer): `1` if parsing succeeded, `0` if failed
- **errors** (string): Error messages and warnings from processing
- **sr_1 to sr_20** (strings): Individual search results as JSON strings, up to 20 results

#### Processing Features

- Automatically handles empty or invalid JSON input
- Truncates results beyond 20 items with warning message  
- Pads results to exactly 20 variables for consistent output schema
- Converts each result to individual JSON string for easy parsing