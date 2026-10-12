/*********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Get all Prompts

    This script reads what the Prompt Builder keeps in SAS Model Manager - the
      prompt templates, the decision templates and the RAG setups with their
      experiments - and writes it into five CAS tables for reporting. Every
      row carries the SAS Model Manager id of its model (modelID) and of its
      project (projectID), so the tables join with each other and with the
      log table of Log-Parser-Code.sas.

    What is read:
      - the projects tagged Prompt-Engineering (prompt templates and decision
        templates both carry that tag): of every model the files
        Prompt-Experiment-Tracker.json and Prompt-Optimization-Tracker.json
        and the manifested score code;
      - the projects tagged RAG-Engineering: the rag-setup.json of a setup
        saved by the Prompt Builder, and the pipeline.yaml and the properties
        of a model registered by the step RAG - Register Setup;
      - the projects that hold the definitions of the LLMs, the embedding
        models and the decision models (the models that answer a request).
    Nothing is written to SAS Model Manager.

    The output tables, by default in the CAS library Public (promoted and
      saved, an existing table is replaced):
      PROMPT_EXPERIMENTS    one row per experiment run and model, and one row
                            for a model without a run
                            (key: modelID, runID, model)
      PROMPT_OPTIMIZATIONS  one row per optimization or comparison
                            (key: modelID, optimizationId)
      RAG_SETUPS            one row per RAG setup (key: modelID)
      RAG_RUNS              the ingestion runs of every setup whose
                            <tables_prefix>_RUNS table is in memory, and
                            for a setup without one the runs its
                            <tables_prefix>_LEDGER table still names
                            (key: modelID, run_id)
      MODEL_CATALOG         one row per model and pattern, the table the
                            link program reads (key: modelID, role, runID,
                            optimization_id, target_model)
    An output without rows is written as well, with all its columns.
    A table name is a SAS name of 32 characters at most.

    The same tables stay in WORK:
      work._gap_all_data            (PROMPT_EXPERIMENTS)
      work._gap_all_optimizations
      work._gap_all_rag_setups
      work._gap_all_rag_runs
      work._gap_all_catalog

    A CAS session called mySess is started for the run when the SAS session
      has none that is connected, and it is ended again. A session that is
      connected is used and left as it is.

    gap is short for:
      Get All Prompts
*********************************************************************************/
/* A setting is taken from the lines below only where its macro variable is
   not set yet. A program of your own that sets a value with a LET statement
   and then includes this file keeps that value, also after this file was
   replaced by the one of a newer release. A variable that is set and empty
   stays empty. */
%macro _gap_default(_gap_name, _gap_value);
    %if %symexist(&_gap_name.) EQ 0 %then %do;
        %global &_gap_name.;
        %let &_gap_name. = &_gap_value.;
    %end;
%mend _gap_default;
/* The tag of the projects that hold prompt templates and decision templates */
%_gap_default(_gap_tag, Prompt-Engineering);
/* The tag of the projects that hold RAG setups */
%_gap_default(_gap_rag_tag, RAG-Engineering);
/* The projects that hold the models that answer a request. Each value is a
   project name, a project id or tag:<tag>; several are separated by |. An
   empty value leaves the models of that kind out of MODEL_CATALOG. */
%_gap_default(_gap_llm_project, LLM Model Project);
%_gap_default(_gap_embedding_project, Embedding Model Project);
%_gap_default(_gap_decision_project, Decision Model Project);
/* Output: the CAS library and one table name per output */
%_gap_default(_gap_out_caslib, Public);
%_gap_default(_gap_out_experiments, PROMPT_EXPERIMENTS);
%_gap_default(_gap_out_optimizations, PROMPT_OPTIMIZATIONS);
%_gap_default(_gap_out_rag_setups, RAG_SETUPS);
%_gap_default(_gap_out_rag_runs, RAG_RUNS);
%_gap_default(_gap_out_catalog, MODEL_CATALOG);
%sysmacdelete _gap_default;

* Check if a CAS session exists;
* The option SESSREF names the session the CAS actions run in and keeps the name of a session that was ended, so SESSFOUND is asked whether that session is connected. Behind Log-Parser-Code.sas in one SAS session a session is connected: starting it again made SAS warn that the session exists, and ending it took the session from the librefs of that program;
%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    %let _gap_clean_cas_session = 1;
    cas mySess;
%end;
%else %do;
    %let _gap_clean_cas_session = 0;
%end;
* 1 once the run has written an ERROR line and left an output table unwritten. The last statement of the program reads it;
%let _gap_failed = 0;

* gap core - from here to the line that ends the core the custom step LLM - Get All Prompts runs the same lines;
* Get the Viya Host URL;
%let _gap_viyaHost=%sysfunc(getoption(SERVICESBASEURL));
* The folder the Python step writes its files to and the DATA steps read them from;
%let _gap_work = %sysfunc(pathname(work));
* The Python step sets this to 0 as its last statement, so -1 means that the reading did not finish;
%let _gap_rc = -1;
* Whether the run table of a RAG setup is in memory, and whether its ledger is, set once per setup;
%let _gap_runs_exists = 0;
%let _gap_ledger_exists = 0;

proc python restart;
submit;
import ast
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
try:
    import requests
except ImportError:
    requests = None

# The files of a model that are read
EXPERIMENT_TRACKER = 'Prompt-Experiment-Tracker.json'
OPTIMIZATION_TRACKER = 'Prompt-Optimization-Tracker.json'
RAG_SETUP_FILE = 'rag-setup.json'
RAG_PIPELINE_FILE = 'pipeline.yaml'
# How a model registered by the step RAG - Register Setup is named, and its algorithm
STEP_MODEL_PREFIX = 'RAG Retrieval - '
STEP_MODEL_ALGORITHM = 'RAG'
# What marks a decision template: the algorithm and the tag of the model, the tag of its project
DECISION_TEMPLATE = 'Decision-Template'
DECISION_PROJECT_TAG = 'Decision-Engineering'
MODEL_MEDIA_TYPE = 'application/vnd.sas.models.model+json'
# Any media type, for the content of a file. Put together from its characters: side by side
# they are the end and the start of a comment in SAS code, which this Python is a part of.
ANY_MEDIA_TYPE = '/'.join(['*', '*'])
# What a manifested score code assigns in scoreModel before it calls its model
MANIFESTED_NAMES = ('llm', 'systemPrompt', 'userPrompt', 'state', 'questions')
MANIFESTED_ASSIGNMENT = re.compile(r'\s+(llm|systemPrompt|userPrompt|state|questions) = ')
# The longest text a SAS column holds, in bytes
MAX_TEXT = 32767
# The length of a text column that follows its values when the table has no value for it
MIN_TEXT = 8

# The columns of the output tables in the order they are written, as (name, type, length).
# 'n' is a number, 'c' a text of that length, 't' a text that is as long as its longest value
# and at most that length. The lists are fixed, so an output without rows still has every column.
EXPERIMENT_COLUMNS = [
    ('unique_id', 'n', 8), ('name', 'c', 256), ('description', 't', 1024), ('createdBy', 'c', 256),
    ('modifiedBy', 'c', 256), ('function', 'c', 256), ('runID', 'n', 8), ('model', 'c', 64),
    ('systemPrompt', 't', MAX_TEXT), ('userPrompt', 't', MAX_TEXT), ('response', 't', MAX_TEXT),
    ('run_time', 'n', 8), ('prompt_length', 'n', 8), ('output_length', 'n', 8), ('cost', 'n', 8),
    ('cheapest_prompt', 'n', 8), ('best_prompt', 'n', 8), ('fastest_prompt', 'n', 8),
    ('fewest_tokens_prompt', 'n', 8), ('judge_rank', 'n', 8), ('judge_best', 'n', 8), ('judge_cost', 'n', 8),
    ('judge_model', 'c', 64), ('judge_confidence', 'c', 16), ('judge_mode', 'c', 16), ('judge_panel', 'c', 512),
    ('judge_agreement', 'c', 16), ('judge_chairman_model', 'c', 64), ('variables_count', 'n', 8),
    ('integrated_llm_call', 'n', 8), ('output_variables_count', 'n', 8), ('creationTime', 'n', 8),
    ('modifiedTime', 'n', 8), ('temperature', 'n', 8), ('top_p', 'n', 8), ('top_k', 'n', 8),
    ('max_tokens', 'n', 8), ('thinking_budget', 'n', 8), ('max_completion_tokens', 'n', 8), ('seed', 'n', 8),
    ('frequency_penalty', 'n', 8), ('presence_penalty', 'n', 8), ('dimensions', 'n', 8),
    ('api_key_required', 'n', 8), ('reasoning_effort', 'c', 32), ('input_type', 'c', 32), ('normalize', 'c', 8),
    ('projectLink', 'c', 200), ('modelLink', 'c', 200),
    # Added behind the columns the table had before
    ('modelID', 'c', 36), ('projectID', 'c', 36), ('projectName', 'c', 256), ('kind', 'c', 16),
    ('expected', 't', MAX_TEXT), ('options', 't', 1024), ('variables', 't', MAX_TEXT),
    ('resolved_system_prompt', 't', MAX_TEXT), ('resolved_user_prompt', 't', MAX_TEXT),
    ('resolved_system_hash', 'c', 64), ('resolved_user_hash', 'c', 64), ('judge_reasoning', 't', 2000),
]

OPTIMIZATION_COLUMNS = [
    ('modelID', 'c', 36), ('projectID', 'c', 36), ('projectName', 'c', 256), ('name', 'c', 256),
    ('optimizationId', 'n', 8), ('startedAt', 'n', 8), ('finishedAt', 'n', 8), ('status', 'c', 32),
    ('targetModel', 't', 1024), ('optimizer', 'c', 32), ('metric', 'c', 32), ('judgeModel', 'c', 128),
    ('datasetSource', 'c', 32), ('datasetRef', 'c', 256), ('sampleCount', 'n', 8), ('trainSize', 'n', 8),
    ('validationSize', 'n', 8), ('metricBefore', 'n', 8), ('metricAfter', 'n', 8),
    ('targetCalls', 'n', 8), ('targetPromptTokens', 'n', 8), ('targetOutputTokens', 'n', 8),
    ('targetRunTime', 'n', 8), ('judgeCalls', 'n', 8), ('judgePromptTokens', 'n', 8),
    ('judgeOutputTokens', 'n', 8), ('judgeRunTime', 'n', 8), ('error', 't', 1024),
    ('type', 'c', 32), ('skippedReason', 'c', 64), ('jobId', 'c', 64), ('computeContext', 'c', 128),
]

RAG_SETUP_COLUMNS = [
    ('modelID', 'c', 36), ('projectID', 'c', 36), ('projectName', 'c', 256), ('name', 'c', 256),
    ('description', 't', 1024), ('creationTime', 'n', 8), ('modifiedTime', 'n', 8), ('source', 'c', 16),
    ('collection', 'c', 128), ('backend', 'c', 32), ('embedding_model', 'c', 128), ('embedding_dims', 'n', 8),
    ('chunker', 'c', 64), ('input_token_limit', 'n', 8), ('overlap_tokens', 'n', 8), ('tables_prefix', 'c', 64),
    ('tables_caslib', 'c', 64), ('source_path', 't', 1024), ('pipeline_version', 'c', 64),
    ('artifacts_folder', 't', 1024), ('job_definition_uri', 'c', 256), ('ingestion_run_id', 'c', 64),
    ('configuration', 'c', 64), ('runs_table', 'c', 64), ('createdBy', 'c', 256), ('modifiedBy', 'c', 256),
]

# The run tables that are looked for, one row per setup: this one stays in WORK. The ledger and
# the embedding model are what the runs of a setup are read from where it has no run table
RAG_RUN_SOURCE_COLUMNS = [
    ('modelID', 'c', 36), ('projectID', 'c', 36), ('tables_caslib', 'c', 64), ('runs_table', 'c', 64),
    ('collection', 'c', 128), ('ledger_table', 'c', 64), ('embedding_model', 'c', 128),
]

# What a <prefix>_RUNS table holds (RUN_COLUMNS and RUN_NUMERIC of rag_core/history.py). The run
# tables are stacked by the SAS code below, which takes the two lists and the lengths from here.
RAG_RUN_TEXTS = [
    ('run_id', 64), ('rag_project', 64), ('collection', 128), ('backend', 32), ('started_at', 40),
    ('finished_at', 40), ('status', 32), ('pipeline_version', 64), ('config_id', 64), ('embed_model', 128),
    ('chunker', 64), ('enrich_model', 128), ('error_text', 1024),
]
RAG_RUN_NUMBERS = [
    'embed_dims', 'input_token_limit', 'overlap_tokens', 'docs_new', 'docs_changed', 'docs_unchanged',
    'docs_deleted', 'docs_skipped', 'docs_excluded', 'docs_failed', 'docs_ingested', 'chunks_written',
    'chunks_retired', 'collection_chunks', 'embed_calls', 'embed_tokens', 'embed_seconds', 'enrich_calls',
    'enrich_input_tokens', 'enrich_output_tokens', 'enrich_seconds', 'enrich_failed',
]
RAG_RUN_COLUMNS = (
    [('modelID', 'c', 36), ('projectID', 'c', 36)]
    + [(name, 'c', length) for name, length in RAG_RUN_TEXTS]
    + [(name, 'n', 8) for name in RAG_RUN_NUMBERS]
    + [('source_caslib', 'c', 64), ('source_table', 'c', 64),
       # Where a row is from: the run table of its setup, or the ledger of a setup that has none
       ('run_source', 'c', 16)]
)
# What run_source holds
RUN_FROM_TABLE = 'run table'
RUN_FROM_LEDGER = 'ledger'

CATALOG_COLUMNS = [
    ('modelID', 'c', 36), ('projectID', 'c', 36), ('projectName', 'c', 256), ('name', 'c', 256),
    ('role', 'c', 16), ('runID', 'c', 16), ('target_model', 'c', 128), ('system_template', 't', MAX_TEXT),
    ('user_template', 't', MAX_TEXT), ('resolved_system_prompt', 't', MAX_TEXT),
    ('resolved_user_prompt', 't', MAX_TEXT), ('collection', 'c', 128),
    # Where the row is from - the templates of a model (score code, best run, newest run) or a RAG
    # setup (builder, step) - and the hashes of the prompts as sent, taken of the whole text. The
    # link program reads the three when the table has them.
    ('template_source', 'c', 16), ('resolved_system_hash', 'c', 64), ('resolved_user_hash', 'c', 64),
    # What tells two requests with the same prompts apart: on the row of a run and model what the
    # tracker holds of that call, on the row of an optimization its time (UTC) and its id
    ('run_time', 'n', 8), ('prompt_length', 'n', 8), ('output_length', 'n', 8),
    ('window_start', 'c', 20), ('window_end', 'c', 20), ('optimization_id', 'c', 16),
]

# The tables that are handed to SAS as a file, by the key their macro variables and files carry
TABLES = {
    'experiments': EXPERIMENT_COLUMNS,
    'optimizations': OPTIMIZATION_COLUMNS,
    'rag_setups': RAG_SETUP_COLUMNS,
    'rag_run_sources': RAG_RUN_SOURCE_COLUMNS,
    'catalog': CATALOG_COLUMNS,
}

# The options of a run that have a column of their own
OPTION_NUMBERS = ['temperature', 'top_p', 'top_k', 'max_tokens', 'thinking_budget', 'max_completion_tokens',
                  'seed', 'frequency_penalty', 'presence_penalty', 'dimensions']
OPTION_TEXTS = ['reasoning_effort', 'input_type', 'normalize']
# An option whose name says that its value is a secret and is not copied into the output: the names
# the log parser goes by. A name with token in it does when its value is no number (max_tokens is a number).
SECRET_OPTION = re.compile(r'key|secret|password|passwd|passphrase|pwd|credential|authorization|bearer|cookie', re.IGNORECASE)
# The short names of a password say it as a word of the name only (my_pass, userPass, pw - not bypass)
NAME_WORD = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+')
SECRET_WORDS = ('pass', 'pw')

# What the regular expressions of JavaScript match with \s. The Prompt Builder fills a
# {{variable}} in with such an expression, and Python counts other characters as blanks.
JS_BLANK = '[\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]*'
PROJECT_ID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
SAS_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,31}')
ISO_TIMESTAMP = re.compile(r'(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d+)?\s*(?:([+-])(\d{2}):?(\d{2}))?')
SAS_EPOCH = datetime(1960, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def text_of(value):
    """
    A value of a file as the text a column holds: nothing for a value that is missing,
    an object or a list as JSON.
    """
    if value is None:
        return ''
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return str(value)


def number_of(value):
    """
    A value as the number a column holds, None if it is none. true and false are 1 and 0.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            return None
        return value
    return None


def is_set(value):
    """
    Whether a flag of a tracker row is set: true, a number other than 0, a text.
    """
    if isinstance(value, str):
        return value.strip() != ''
    return bool(value)


def cut_text(text, length):
    """
    A text that fits a column of that many bytes. A character of several bytes is never cut in two.
    """
    encoded = text.encode('utf-8', errors='replace')
    if len(encoded) <= length:
        return text
    return encoded[:length].decode('utf-8', errors='ignore')


def text_hash(text):
    """
    The hash the log parser, this program and the link program compare texts by: the SHA-256
    of the text with every run of blanks and line ends as one blank, in lower-case hex.
    """
    return hashlib.sha256(' '.join(text.split()).encode('utf-8', errors='replace')).hexdigest()


def sas_datetime(value):
    """
    A timestamp of SAS Viya ('2026-01-05T09:15:00.123Z') as a SAS datetime in UTC, None if it is none.
    """
    match = ISO_TIMESTAMP.fullmatch(text_of(value).strip().rstrip('Zz'))
    if not match:
        return None
    day, clock, fraction, sign, hours, minutes = match.groups()
    try:
        moment = datetime.strptime(day + ' ' + clock, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    seconds = (moment - SAS_EPOCH).total_seconds() + (float('0' + fraction) if fraction else 0.0)
    if sign:
        offset = int(hours) * 3600 + int(minutes) * 60
        seconds += -offset if sign == '+' else offset
    return seconds


def utc_text(value, later=False):
    """
    A timestamp of a tracker as the text a window is written with: 2026-01-05T09:15:00Z, in UTC and
    to the second - the second before a fraction, or the one after it for the end of a window.
    Nothing for a value that is no timestamp.
    """
    seconds = sas_datetime(value)
    if seconds is None:
        return ''
    whole = int(seconds // 1) + (1 if later and seconds != seconds // 1 else 0)
    return (SAS_EPOCH + timedelta(seconds=whole)).strftime('%Y-%m-%dT%H:%M:%SZ')


def run_text(value):
    """
    The id of a run as text: 3 for the number 3, whether it was stored as 3, 3.0 or '3'.
    """
    number = number_of(value)
    if number is not None and number == int(number):
        return str(int(number))
    return text_of(value).strip()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def js_string(value):
    """
    A value the way JavaScript writes it into a text, which is what the Prompt Builder does
    with the value of a variable.
    """
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    if isinstance(value, list):
        return ','.join('' if item is None else js_string(item) for item in value)
    if isinstance(value, dict):
        return '[object Object]'
    return str(value)


def substitute_variables(text, variables):
    """
    A template with the values of the run's variables filled in, the way the Prompt Builder
    does it before it sends a prompt (substitutePromptVariables):

    - the variables are taken one after the other in the order they are stored, so a value
      that holds the {{name}} of a later variable gets that one filled in as well;
    - {{name}} matches with blanks and line ends inside the braces, and the name as written;
    - the value goes in as it is - not trimmed, whatever the type of the variable;
    - a {{name}} of no variable stays as it was typed.

    The Prompt Builder only keeps variables whose name is a valid DS2 name. The name is
    escaped here, which makes no difference for such a name.
    """
    result = text_of(text)
    for variable in variables if isinstance(variables, list) else []:
        if not isinstance(variable, dict) or not isinstance(variable.get('name'), str) or variable['name'] == '':
            continue
        value = js_string(variable['value']) if 'value' in variable else 'undefined'
        pattern = re.compile(r'\{\{' + JS_BLANK + re.escape(variable['name']) + JS_BLANK + r'\}\}')
        result = pattern.sub(lambda match, value=value: value, result)
    return result


def resolved_prompts(header):
    """
    What a run sent, from its header row: the system and the user prompt with the variables
    filled in. A decision run sends its questions as they are stored and fills the variables
    into the state only.
    """
    variables = header.get('variables')
    system = text_of(header.get('systemPrompt'))
    if header.get('mode') != 'decision':
        system = substitute_variables(system, variables)
    return system, substitute_variables(header.get('userPrompt'), variables)


def option_pairs(options):
    """
    The options of a run ('{temperature:0.2,top_p:1}') as (key, value) pairs.
    """
    if isinstance(options, dict):
        return [(str(key).strip(), text_of(value).strip()) for key, value in options.items()]
    body = text_of(options).strip()
    if body.startswith('{') and body.endswith('}'):
        body = body[1:-1]
    pairs = []
    for part in body.split(','):
        key, _, value = part.partition(':')
        if key.strip():
            pairs.append((key.strip(), value.strip()))
    return pairs


def option_columns(options):
    """
    The options that have a column of their own. A run without options has none of them,
    a run with options says in api_key_required whether a key went with them. max_length is
    what some models call max_tokens and is written there when max_tokens is not given.
    """
    if text_of(options).strip() == '':
        return {}
    columns = {'api_key_required': 0}
    max_length = None
    for key, value in option_pairs(options):
        if key in OPTION_NUMBERS:
            columns[key] = number_of(value)
        elif key in OPTION_TEXTS:
            columns[key] = value
        elif key == 'max_length':
            max_length = number_of(value)
        elif key == 'API_KEY':
            columns['api_key_required'] = 1
    if columns.get('max_tokens') is None and max_length is not None:
        columns['max_tokens'] = max_length
    return columns


def redacted_options(options):
    """
    The options as they are stored, with the value of every key-like option replaced. The
    Prompt Builder stores the name of the credential there and not the key, but a tracker that
    was written by something else may hold the key itself.
    """
    text = text_of(options)
    parts = text.split(',')
    for index, part in enumerate(parts):
        key, colon, value = part.partition(':')
        name = key.strip(' {"\'')
        plain = value.strip().rstrip('}').strip().strip('"\'')
        if colon and (SECRET_OPTION.search(name) or any(word.lower() in SECRET_WORDS for word in NAME_WORD.findall(name))
                      or ('token' in name.lower() and number_of(plain) is None)):
            closing = '}' if index == len(parts) - 1 and value.rstrip().endswith('}') else ''
            parts[index] = key + ':REDACTED' + closing
    return ','.join(parts)


# ---------------------------------------------------------------------------
# The experiment tracker
# ---------------------------------------------------------------------------

def tracker_runs(tracker):
    """
    The runs of a Prompt-Experiment-Tracker.json in the order they are stored.

    The file is one flat list: per run a header row (its model is empty) with the prompt
    templates, the variables and the judge, followed by one row per model that answered.
    The header of a run is found by its run id and its empty model. Whether its system
    prompt is empty plays no part, so a run without a system prompt keeps its own header.

    Returns a list of {'run': the run id, 'header': the header row or None, 'rows': the model rows}.
    """
    runs = []
    by_id = {}
    for row in tracker if isinstance(tracker, list) else []:
        if not isinstance(row, dict):
            continue
        key = run_text(row.get('runId'))
        if key not in by_id:
            by_id[key] = {'run': row.get('runId'), 'header': None, 'rows': []}
            runs.append(by_id[key])
        if text_of(row.get('model')).strip() == '':
            if by_id[key]['header'] is None:
                by_id[key]['header'] = row
        else:
            by_id[key]['rows'].append(row)
    return runs


def is_decision_model(model, project):
    """
    Whether a model is a decision template: by its algorithm, its tag or the tag of its project.
    """
    tags = model.get('tags') if isinstance(model.get('tags'), list) else []
    project_tags = project.get('tags') if isinstance(project.get('tags'), list) else []
    return (model.get('algorithm') == DECISION_TEMPLATE or DECISION_TEMPLATE in tags
            or DECISION_PROJECT_TAG in project_tags)


def model_columns(model, project):
    """
    The columns every row of a model carries, in whatever table.
    """
    return {
        'modelID': text_of(model.get('id')),
        'projectID': text_of(project.get('id')) or text_of(model.get('projectId')),
        'projectName': text_of(project.get('name')),
        'name': text_of(model.get('name')),
    }


def experiment_rows(model, project, tracker, viya_host):
    """
    The rows of PROMPT_EXPERIMENTS of one model: one per run and model that answered, and a
    single row without a run for a model that has none, so that every model is in the table.
    """
    host = viya_host.rstrip('/')
    common = dict(model_columns(model, project), **{
        'description': model.get('description'),
        'createdBy': model.get('createdBy'),
        'modifiedBy': model.get('modifiedBy'),
        'function': model.get('function'),
        'creationTime': sas_datetime(model.get('creationTimeStamp')),
        'modifiedTime': sas_datetime(model.get('modifiedTimeStamp')),
    })
    common['projectLink'] = host + '/SASModelManager/projects/' + common['projectID']
    common['modelLink'] = host + '/SASModelManager/models/' + common['modelID']

    rows = []
    for run in tracker_runs(tracker):
        header = run['header'] or {}
        manifest = header.get('manifest') if isinstance(header.get('manifest'), dict) else {}
        of_run = {
            'runID': number_of(run['run']),
            'systemPrompt': header.get('systemPrompt'),
            'userPrompt': header.get('userPrompt'),
            'kind': 'decision' if header.get('mode') == 'decision' else 'prompt',
            'expected': header.get('expected') if isinstance(header.get('expected'), dict) else None,
            'variables': header.get('variables') if isinstance(header.get('variables'), list) else None,
            'judge_model': header.get('judge_model'),
            'judge_confidence': header.get('judge_confidence'),
            'judge_mode': header.get('judge_mode'),
            'judge_panel': header.get('judge_panel'),
            'judge_agreement': header.get('judge_agreement'),
            'judge_chairman_model': header.get('judge_chairman_model'),
            'judge_cost': header.get('judge_cost'),
            'judge_reasoning': header.get('judge_reasoning'),
        }
        if run['header'] is not None:
            variables = header.get('variables')
            outputs = manifest.get('outputVariables')
            of_run['variables_count'] = len(variables) if isinstance(variables, list) else 0
            of_run['integrated_llm_call'] = 1 if manifest.get('integratedLLMCall') else 0
            of_run['output_variables_count'] = len(outputs) if isinstance(outputs, list) else 0
            system, user = resolved_prompts(header)
            of_run.update({
                'resolved_system_prompt': system, 'resolved_user_prompt': user,
                'resolved_system_hash': text_hash(system), 'resolved_user_hash': text_hash(user),
            })
        for answer in run['rows']:
            row = dict(common, **of_run)
            row.update({
                'model': answer.get('model'),
                'response': answer.get('response'),
                'options': redacted_options(answer.get('options')),
            })
            for name in ('run_time', 'prompt_length', 'output_length', 'cost', 'cheapest_prompt', 'best_prompt',
                         'fastest_prompt', 'fewest_tokens_prompt', 'judge_rank', 'judge_best'):
                row[name] = answer.get(name)
            row.update(option_columns(answer.get('options')))
            rows.append(row)
    if not rows:
        rows.append(dict(common, kind='decision' if is_decision_model(model, project) else 'prompt'))
    return rows


def optimization_rows(model, project, tracker):
    """
    The rows of PROMPT_OPTIMIZATIONS of one model: one per entry of its Prompt-Optimization-Tracker.json.
    """
    entries = tracker if isinstance(tracker, list) else [tracker] if isinstance(tracker, dict) else []
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row = model_columns(model, project)
        for name in ('optimizationId', 'status', 'targetModel', 'optimizer', 'metric', 'judgeModel',
                     'datasetSource', 'datasetRef', 'sampleCount', 'trainSize', 'validationSize',
                     'metricBefore', 'metricAfter', 'error', 'type', 'skippedReason', 'jobId', 'computeContext'):
            row[name] = entry.get(name)
        row['startedAt'] = sas_datetime(entry.get('startedAt'))
        row['finishedAt'] = sas_datetime(entry.get('finishedAt'))
        usage = entry.get('usage') if isinstance(entry.get('usage'), dict) else {}
        for role in ('target', 'judge'):
            spent = usage.get(role) if isinstance(usage.get(role), dict) else {}
            row[role + 'Calls'] = spent.get('calls')
            row[role + 'PromptTokens'] = spent.get('promptTokens')
            row[role + 'OutputTokens'] = spent.get('outputTokens')
            row[role + 'RunTime'] = spent.get('runTime')
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# The manifested score code
# ---------------------------------------------------------------------------

def template_of(node):
    """
    The template a prompt of a manifested score code was built from, None for an expression
    that is none.

    The Prompt Builder writes a prompt as a text or as an f-string in which every variable
    stands as {str(name).strip()}. That placeholder becomes {{name}} again, a brace of the
    text stays a brace. Score code of earlier releases escapes the finished text with a chain
    of .replace(...) - the text in front of the chain is the template.
    """
    while (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'replace'):
        node = node.func.value
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if not isinstance(node, ast.JoinedStr):
        return None
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
            continue
        inner = value.value if isinstance(value, ast.FormattedValue) else None
        # str(name).strip()
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == 'strip'
                and isinstance(inner.func.value, ast.Call) and isinstance(inner.func.value.func, ast.Name)
                and inner.func.value.func.id == 'str' and len(inner.func.value.args) == 1):
            inner = inner.func.value.args[0]
        if not isinstance(inner, ast.Name):
            return None
        parts.append('{{' + inner.id + '}}')
    return ''.join(parts)


def score_assignments(source):
    """
    The values scoreModel assigns to the names in MANIFESTED_NAMES, as {name: expression}; the
    first assignment of a name counts.

    The code is parsed, never run. A score code that does not compile as a whole - which a
    release of the Prompt Builder wrote for some decision templates - is read statement by
    statement: each of the assignments is a statement of its own and is parsed alone.
    """
    def keep(statement, found):
        if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id in MANIFESTED_NAMES):
            found.setdefault(statement.targets[0].id, statement.value)

    found = {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        tree = None
    if tree is not None:
        for function in tree.body:
            if isinstance(function, ast.FunctionDef) and function.name == 'scoreModel':
                for statement in function.body:
                    keep(statement, found)
                break
        return found

    lines = source.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith('def scoreModel(')), None)
    for index in range(start + 1 if start is not None else len(lines), len(lines)):
        if not MANIFESTED_ASSIGNMENT.match(lines[index]):
            continue
        # A prompt in triple quotes goes on over the next lines: the statement ends where it parses
        for end in range(index + 1, min(index + 2000, len(lines)) + 1):
            try:
                statement = ast.parse('\n'.join([lines[index].lstrip()] + lines[index + 1:end]))
            except (SyntaxError, ValueError):
                continue
            if len(statement.body) == 1:
                keep(statement.body[0], found)
            break
    return found


def manifested_templates(source):
    """
    What the score code of a manifested prompt template or decision template sends, read from
    the code without running it: the model it calls (llm) and the templates of its two inputs -
    systemPrompt and userPrompt, or for a decision template questions and state.

    Returns {'role', 'target_model', 'system_template', 'user_template'}, or None for a code
    that is not such a score code.
    """
    found = {}
    for name, expression in score_assignments(text_of(source)).items():
        template = template_of(expression)
        if template is not None:
            found[name] = template
    if 'questions' in found and 'state' in found:
        role, system, user = 'decision', found['questions'], found['state']
    elif 'systemPrompt' in found and 'userPrompt' in found:
        role, system, user = 'prompt', found['systemPrompt'], found['userPrompt']
    else:
        return None
    return {'role': role, 'target_model': found.get('llm', ''), 'system_template': system, 'user_template': user}


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

def catalog_template_row(model, project, tracker, manifested):
    """
    The catalog row of the template of a model itself, None for a model that has none.

    The template is what the manifested score code sends, because that is what runs once the
    model is published. A model without such a score code gives the templates of its newest
    run that is marked as the best prompt, and without one those of its newest run.
    template_source says which of the three it was.
    """
    row = dict(model_columns(model, project), runID='')
    if manifested:
        row.update(manifested, template_source='score code')
        return row
    runs = [run for run in tracker_runs(tracker) if run['header'] is not None]
    if not runs:
        return None

    def newest(candidates):
        return max(candidates, key=lambda run: (number_of(run['run']) is not None, number_of(run['run']) or 0))

    best = [run for run in runs if any(is_set(answer.get('best_prompt')) for answer in run['rows'])]
    run = newest(best or runs)
    winners = [answer for answer in run['rows'] if is_set(answer.get('best_prompt'))]
    row.update({
        'role': 'decision' if run['header'].get('mode') == 'decision' else 'prompt',
        'target_model': winners[0].get('model') if winners else '',
        'system_template': run['header'].get('systemPrompt'),
        'user_template': run['header'].get('userPrompt'),
        'template_source': 'best run' if best else 'newest run',
    })
    return row


def catalog_run_rows(model, project, tracker):
    """
    The catalog rows of the experiment runs of a model: one per run and model that answered,
    with the templates of the run and what was sent - the prompts with the variables filled in.
    """
    rows = []
    for run in tracker_runs(tracker):
        header = run['header']
        if header is None:
            continue
        system, user = resolved_prompts(header)
        for answer in run['rows']:
            rows.append(dict(model_columns(model, project), **{
                'role': 'decision' if header.get('mode') == 'decision' else 'prompt',
                'runID': run_text(run['run']),
                'target_model': answer.get('model'),
                'system_template': header.get('systemPrompt'),
                'user_template': header.get('userPrompt'),
                'resolved_system_prompt': system,
                'resolved_user_prompt': user,
                'resolved_system_hash': text_hash(system),
                'resolved_user_hash': text_hash(user),
                # What the tracker measured of this call. The log holds the same run time for the
                # request, which tells the request of this run from another one with the same prompts
                'run_time': answer.get('run_time'),
                'prompt_length': answer.get('prompt_length'),
                'output_length': answer.get('output_length'),
            }))
    return rows


def judge_models(header):
    """
    The models that judged a run, from its header row: the judge, or the panel of a council and
    its chairman. Each name once, in that order.
    """
    names = [text_of(header.get('judge_model'))]
    names += text_of(header.get('judge_panel')).split(',')
    names.append(text_of(header.get('judge_chairman_model')))
    found = []
    for name in names:
        if name.strip() and name.strip() not in found:
            found.append(name.strip())
    return found


def catalog_judge_rows(model, project, tracker):
    """
    The catalog rows of the judge calls of a model: one per run that was judged and model that
    judged it. The Prompt Builder sends a judge the task of the run - its system prompt and its
    user prompt as they were sent - inside the user prompt of the judge call, so the two
    resolved prompts are texts the logged user prompt of such a call holds. target_model is
    the judge, the model the call went to.
    """
    rows = []
    for run in tracker_runs(tracker):
        header = run['header']
        if header is None:
            continue
        system, user = resolved_prompts(header)
        for judge in judge_models(header):
            rows.append(dict(model_columns(model, project), **{
                'role': 'judge',
                'runID': run_text(run['run']),
                'target_model': judge,
                'resolved_system_prompt': system,
                'resolved_user_prompt': user,
                'resolved_system_hash': text_hash(system),
                'resolved_user_hash': text_hash(user),
            }))
    return rows


def counted_optimization_ids(tracker):
    """
    The optimization_id of every entry of an optimization tracker in the catalog, in the order
    of the entries: its optimizationId, and for an entry whose id an earlier entry holds the id
    with a counter - <id>#2 for the second, <id>#3 for the third. A counter is passed over when
    the id it would make is the optimizationId of an entry of the tracker or was given before:
    of the ids 1, 1 and 1#2 the second entry is 1#3, so that no two entries have one id. An
    entry without an optimizationId is counted the same way, its id is the counter alone: the
    first has none, the second #2. The ids are compared as the column holds them: an id that is
    longer than the column is cut to it, and makes room for its counter.
    """
    width = next(length for name, _, length in CATALOG_COLUMNS if name == 'optimization_id')
    entries = [entry for entry in (tracker if isinstance(tracker, list) else [tracker] if isinstance(tracker, dict) else [])
               if isinstance(entry, dict)]
    own = [cut_text(run_text(entry.get('optimizationId')), width) for entry in entries]
    taken = set(own)
    held = {}
    counted = []
    for identifier in own:
        held[identifier] = held.get(identifier, 0) + 1
        if held[identifier] > 1:
            first = identifier
            identifier = cut_text(first, width - len(f'#{held[first]}')) + f'#{held[first]}'
            while identifier in taken:
                held[first] += 1
                identifier = cut_text(first, width - len(f'#{held[first]}')) + f'#{held[first]}'
            taken.add(identifier)
        counted.append(identifier)
    return counted


def catalog_optimization_rows(model, project, tracker):
    """
    The catalog rows of the optimizations of a model: one per entry of its optimization tracker
    and model the optimization called - the target models, and the judge when it had one.

    The calls of an optimization are found by their model and their time: window_start and
    window_end are when the job started and finished, in UTC. resolved_system_prompt holds the
    system prompt the optimization started from, which the optimizer writes into the system
    prompt of the calls to the target and into the user prompt of the calls to its judge - as
    long as it has not proposed another instruction. The templates are the optimized prompt
    where the tracker holds one.

    optimization_id is the optimizationId of the entry and a part of the key of the catalog.
    A tracker that holds an id on more than one entry would give rows with one key: the rows
    of the second entry with an id carry it with a counter, <id>#2, of the third <id>#3. No
    entry is left out, as each one has a window of its own that its calls are found by.
    """
    entries = tracker if isinstance(tracker, list) else [tracker] if isinstance(tracker, dict) else []
    rows = []
    for entry, identifier in zip([entry for entry in entries if isinstance(entry, dict)], counted_optimization_ids(tracker)):
        baseline = entry.get('baselinePrompt') if isinstance(entry.get('baselinePrompt'), dict) else {}
        optimized = entry.get('optimizedPrompt') if isinstance(entry.get('optimizedPrompt'), dict) else {}
        called = []
        for name in text_of(entry.get('targetModel')).split(',') + [text_of(entry.get('judgeModel'))]:
            if name.strip() and name.strip() not in called:
                called.append(name.strip())
        started = text_of(baseline.get('systemPrompt'))
        for name in called:
            rows.append(dict(model_columns(model, project), **{
                'role': 'optimization',
                'runID': '',
                'target_model': name,
                'system_template': optimized.get('systemPrompt'),
                'user_template': optimized.get('userPrompt'),
                'resolved_system_prompt': started,
                'resolved_system_hash': text_hash(started) if started else '',
                'window_start': utc_text(entry.get('startedAt')),
                'window_end': utc_text(entry.get('finishedAt'), later=True),
                'optimization_id': identifier,
            }))
    return rows


def repeated_optimization_ids(tracker):
    """
    The ids an optimization tracker holds on more than one entry, each once and in their order,
    and the number of its entries without an id when there is more than one of them (else 0).
    An entry without an id is no id that is held twice, so it is counted and not listed.
    """
    entries = tracker if isinstance(tracker, list) else [tracker] if isinstance(tracker, dict) else []
    identifiers = [run_text(entry.get('optimizationId')) for entry in entries if isinstance(entry, dict)]
    repeated = [identifier for position, identifier in enumerate(identifiers)
                if identifier and identifiers.count(identifier) > 1 and identifier not in identifiers[:position]]
    without = identifiers.count('')
    return repeated, without if without > 1 else 0


def catalog_answer_row(model, project, role):
    """
    The catalog row of a model that answers requests: its name is the name of its container,
    which is the model a log row names.
    """
    return dict(model_columns(model, project), role=role, runID='')


def catalog_setup_row(setup):
    """
    The catalog row of a RAG setup, from its row of RAG_SETUPS: the collection it searches,
    the embedding model its questions and documents are sent to, and in template_source who
    wrote the model - builder or step. A setup that was saved in the Prompt Builder and
    registered with the step RAG - Register Setup is two models with one collection, and the
    link program takes the one the Builder saved.
    """
    return {
        'modelID': setup['modelID'], 'projectID': setup['projectID'], 'projectName': setup['projectName'],
        'name': setup['name'], 'role': 'rag', 'runID': '', 'target_model': setup.get('embedding_model'),
        'collection': setup.get('collection'), 'template_source': setup.get('source'),
    }


# ---------------------------------------------------------------------------
# RAG setups
# ---------------------------------------------------------------------------

def parse_pipeline_yaml(text):
    """
    The pipeline.yaml the step RAG - Register Setup writes, as {section: {key: value}} with the
    values of the first level under their own key. The file has two levels and one value per
    line, a value is plain text or a JSON string - nothing else of YAML is read.
    """
    result = {}
    section = None
    for line in text_of(text).splitlines():
        if not line.strip() or line.lstrip().startswith('#') or ':' not in line:
            continue
        key, _, value = line.strip().partition(':')
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if line[0] in ' \t':
            if section is not None:
                result[section][key.strip()] = value
        elif value == '':
            section = key.strip()
            result[section] = {}
        else:
            section = None
            result[key.strip()] = value
    return result


def property_map(properties):
    """
    The properties of a model ([{'name': ..., 'value': ...}]) as {name: value}.
    """
    return {text_of(item.get('name')): text_of(item.get('value'))
            for item in (properties if isinstance(properties, list) else [])
            if isinstance(item, dict)}


def rag_setup_row(model, project, setup=None, pipeline=None, properties=None):
    """
    The row of RAG_SETUPS of one model.

    A setup saved by the Prompt Builder is described by its rag-setup.json (setup). A model
    registered by the step RAG - Register Setup has none: it is described by the pipeline.yaml
    the step stores with it (pipeline, as parse_pipeline_yaml returns it), by its properties
    and by its training table, which the step sets to <CAS server>/<caslib>/<prefix>_LEDGER.
    """
    row = dict(model_columns(model, project), **{
        'description': model.get('description'),
        'creationTime': sas_datetime(model.get('creationTimeStamp')),
        'modifiedTime': sas_datetime(model.get('modifiedTimeStamp')),
        'createdBy': model.get('createdBy'),
        'modifiedBy': model.get('modifiedBy'),
    })

    def part(source, name):
        return source.get(name) if isinstance(source, dict) and isinstance(source.get(name), dict) else {}

    if setup is not None:
        setup = setup if isinstance(setup, dict) else {}
        row.update({
            'source': 'builder',
            'collection': part(setup, 'store').get('collection'),
            'backend': part(setup, 'store').get('backend'),
            'embedding_model': part(setup, 'embedding').get('model'),
            'embedding_dims': part(setup, 'embedding').get('dims'),
            'chunker': part(setup, 'chunking').get('chunker'),
            'input_token_limit': part(setup, 'chunking').get('inputTokenLimit'),
            'overlap_tokens': part(setup, 'chunking').get('overlapTokens'),
            'tables_prefix': part(setup, 'tables').get('prefix'),
            'tables_caslib': part(setup, 'tables').get('caslib'),
            'source_path': part(setup, 'source').get('path'),
            'pipeline_version': setup.get('pipelineVersion'),
            'artifacts_folder': setup.get('artifactsFolder'),
            'job_definition_uri': part(setup, 'job').get('definitionUri'),
        })
    else:
        pipeline = pipeline if isinstance(pipeline, dict) else {}
        properties = properties if isinstance(properties, dict) else {}
        ledger = text_of(model.get('trainTable')).split('/')
        from_ledger = len(ledger) == 3 and ledger[2].upper().endswith('_LEDGER')
        row.update({
            'source': 'step',
            'collection': part(pipeline, 'store').get('collection') or properties.get('collection'),
            'backend': part(pipeline, 'store').get('backend'),
            'embedding_model': part(pipeline, 'embedding').get('model'),
            'embedding_dims': part(pipeline, 'embedding').get('dimensions'),
            'chunker': part(pipeline, 'chunking').get('chunker'),
            'input_token_limit': part(pipeline, 'chunking').get('input_token_limit'),
            'overlap_tokens': part(pipeline, 'chunking').get('overlap_tokens'),
            'tables_prefix': part(pipeline, 'tables').get('project') or (ledger[2][:-7] if from_ledger else ''),
            'tables_caslib': part(pipeline, 'tables').get('caslib') or (ledger[1] if from_ledger else ''),
            'source_path': part(pipeline, 'source').get('location'),
            'pipeline_version': part(pipeline, 'run').get('pipeline_version') or properties.get('pipelineVersion'),
            'ingestion_run_id': part(pipeline, 'run').get('ingestion_run_id') or properties.get('ingestionRunId'),
            'configuration': part(pipeline, 'run').get('configuration') or properties.get('configuration'),
        })
    prefix = text_of(row.get('tables_prefix')).strip()
    row['runs_table'] = prefix + '_RUNS' if prefix else ''
    return row


def rag_run_sources(setups):
    """
    The run tables to look for, from the rows of RAG_SETUPS: one per setup that names a table
    prefix and a caslib. Returns (rows, problems), a problem being a setup whose table can not
    be named in SAS code.
    """
    rows = []
    problems = []
    for setup in setups:
        caslib = text_of(setup.get('tables_caslib')).strip()
        table = text_of(setup.get('runs_table')).strip()
        if not caslib or not table:
            continue
        if not SAS_NAME.fullmatch(table) or re.search('["\'&%;]', caslib):
            problems.append(f"The run table {caslib}.{table} of the RAG setup {setup.get('name')} can not be "
                            "named in SAS code and was left out")
            continue
        collection = text_of(setup.get('collection')).strip()
        ledger = text_of(setup.get('tables_prefix')).strip() + '_LEDGER'
        model = text_of(setup.get('embedding_model')).strip()
        rows.append({
            'modelID': setup['modelID'], 'projectID': setup['projectID'], 'tables_caslib': caslib,
            'runs_table': table,
            # The run table of a setup can be missing: the ingestion job of a setup of the Prompt
            # Builder published none before it was given the publication of the run history, and it
            # publishes none while that history is switched off. The ledger of the setup names the
            # run that listed each document, which is where the SAS code below takes the runs from
            # then. A name that can not stand in SAS code is left out
            'ledger_table': ledger if SAS_NAME.fullmatch(ledger) else '',
            'embedding_model': model if re.fullmatch(r'[A-Za-z0-9_.\-]+', model) else '',
            # rag_runs is shared by every collection of a vector store, and a published run table can hold
            # more than one of them: only the runs of the setup's own collection are taken. A collection
            # that can not stand in SAS code is no filter
            'collection': collection if re.fullmatch(r'[A-Za-z0-9_.\-]+', collection) else '',
        })
    return rows, problems


# ---------------------------------------------------------------------------
# Reading SAS Model Manager
# ---------------------------------------------------------------------------

def filter_literal(text):
    """
    A text as a literal of the filter language: in single quotes, or in double quotes when it holds one.
    """
    return "'" + text + "'" if "'" not in text else '"' + text + '"'


def filtered(path, expression):
    """
    The path of a listing with a filter.
    """
    return path + '?filter=' + quote(expression, safe="(),'")


def list_all(get_json, path, limit):
    """
    Every item of a listing: the pages are asked for one after the other until the count the
    listing gives is reached. A page without a new item ends the reading as well, which is
    also how a listing that gives no count ends - a page that is not full does not say that it
    was the last one, as a service may hand out smaller pages than it was asked for.
    """
    items = []
    seen = set()
    asked = 0
    while True:
        page = get_json(path + ('&' if '?' in path else '?') + '&'.join([f'start={asked}', f'limit={limit}']))
        page = page if isinstance(page, dict) else {}
        batch = page.get('items') if isinstance(page.get('items'), list) else []
        fresh = [item for item in batch if not isinstance(item, dict) or item.get('id') is None
                 or item.get('id') not in seen]
        seen.update(item.get('id') for item in batch if isinstance(item, dict) and item.get('id') is not None)
        items.extend(fresh)
        asked += len(batch)
        count = page.get('count')
        if not fresh:
            break
        if isinstance(count, int) and not isinstance(count, bool) and asked >= count:
            break
    return items


def reason_of(error):
    return f'{type(error).__name__}: {error}'[:300]


def pull(get_json, get_text, settings, say):
    """
    Reads the projects, models and files and returns the rows of every table as {key: rows}.

    get_json(path, accept) and get_text(path) read from SAS Viya and raise when a request
    fails; say(type, message) writes to the SAS log. A listing of projects that fails ends
    the pull (the exception is raised), so that no output is replaced by a table that lacks
    what could not be listed. A project whose models, or a model whose files, can not be read
    gives a WARNING with its name and the pull goes on.
    """
    limit = settings['page_limit']
    tables = {key: [] for key in TABLES}

    def listing(path, what):
        try:
            return list_all(get_json, path, limit)
        except Exception as error:
            say('WARNING', f'{what} could not be listed and is left out: {reason_of(error)}')
            return None

    def models_of(project):
        return listing(f"/modelRepository/projects/{project.get('id')}/models",
                       f"The models of the project {project.get('name')}") or []

    def contents_of(model):
        return listing(f"/modelRepository/models/{model.get('id')}/contents",
                       f"The files of the model {model.get('name')} ({model.get('id')})") or []

    def file_of(model, contents, name, read=json.loads):
        """What a file of a model holds: None when the model has none or it can not be read."""
        for item in contents:
            if isinstance(item, dict) and item.get('name') == name:
                try:
                    return read(get_text(text_of(item.get('fileUri')) + '/content'))
                except Exception as error:
                    say('WARNING', f"The file {name} of the model {model.get('name')} ({model.get('id')}) "
                                   f"could not be read and is left out: {reason_of(error)}")
                    return None
        return None

    def manifested_of(model, contents):
        """What the manifested score code of a model sends, None for a model without one."""
        files = [item for item in contents if isinstance(item, dict) and text_of(item.get('name')).endswith('.py')]
        files.sort(key=lambda item: text_of(item.get('role')).lower() != 'score')
        for item in files:
            manifested = manifested_templates(file_of(model, [item], item.get('name'), read=str))
            if manifested:
                return manifested
        return None

    # Prompt templates and decision templates
    projects = list_all(get_json, filtered('/modelRepository/projects', f"eq(tags,{filter_literal(settings['tag'])})"), limit)
    model_count = 0
    for project in projects:
        for model in models_of(project):
            model_count += 1
            contents = contents_of(model)
            tracker = file_of(model, contents, EXPERIMENT_TRACKER)
            tables['experiments'].extend(experiment_rows(model, project, tracker, settings['viya_host']))
            optimizations = file_of(model, contents, OPTIMIZATION_TRACKER)
            tables['optimizations'].extend(optimization_rows(model, project, optimizations))
            template = catalog_template_row(model, project, tracker, manifested_of(model, contents))
            if template:
                tables['catalog'].append(template)
            tables['catalog'].extend(catalog_run_rows(model, project, tracker))
            # The calls the Prompt Builder makes for a prompt and not with it: its judges and its optimizations
            tables['catalog'].extend(catalog_judge_rows(model, project, tracker))
            tables['catalog'].extend(catalog_optimization_rows(model, project, optimizations))
            repeated, without = repeated_optimization_ids(optimizations)
            if repeated or without:
                found = []
                if repeated:
                    found.append(f"the optimizationId {', '.join(repeated[:5])}{' and others' if len(repeated) > 5 else ''} "
                                 "on more than one entry")
                if without:
                    found.append(f"{without} entries without an optimizationId")
                say('WARNING', f"The file {OPTIMIZATION_TRACKER} of the model {model.get('name')} ({model.get('id')}) holds "
                               f"{' and '.join(found)}. In the model catalog the rows of a later such entry carry the id with "
                               "a counter, as in 1#2 - for an entry without an id the counter alone -, so that no two rows of "
                               "the catalog have the same key. The table of the optimizations holds the entries as the tracker "
                               "has them.")
    say('NOTE', f"{len(projects)} project(s) with the tag {settings['tag']} and {model_count} model(s) in them were read.")
    for index, row in enumerate(tables['experiments']):
        row['unique_id'] = index + 1

    # RAG setups
    rag_projects = []
    if settings['rag_tag']:
        rag_projects = list_all(get_json, filtered('/modelRepository/projects', f"eq(tags,{filter_literal(settings['rag_tag'])})"), limit)
    for project in rag_projects:
        for model in models_of(project):
            contents = contents_of(model)
            names = [item.get('name') for item in contents if isinstance(item, dict)]
            # Whether a WARNING of this model has said that it is left out of the catalog
            explained = False
            if RAG_SETUP_FILE in names:
                setup = file_of(model, contents, RAG_SETUP_FILE)
                row = rag_setup_row(model, project, setup=setup if isinstance(setup, dict) else {})
            elif (text_of(model.get('name')).startswith(STEP_MODEL_PREFIX)
                  or model.get('algorithm') == STEP_MODEL_ALGORITHM or RAG_PIPELINE_FILE in names):
                properties = model.get('properties')
                unread = ''
                if not isinstance(properties, list):
                    # The listing of the models does not have to carry the properties, the model does
                    try:
                        detail = get_json(f"/modelRepository/models/{model.get('id')}", MODEL_MEDIA_TYPE)
                        properties = detail.get('properties') if isinstance(detail, dict) else None
                    except Exception as error:
                        unread = (f"The properties of the model {model.get('name')} ({model.get('id')}) "
                                  f"could not be read: {reason_of(error)}")
                row = rag_setup_row(model, project,
                                    pipeline=file_of(model, contents, RAG_PIPELINE_FILE, read=parse_pipeline_yaml),
                                    properties=property_map(properties))
                if unread and text_of(row.get('collection')).strip():
                    say('WARNING', unread)
                elif unread:
                    explained = True
                    say('WARNING', unread + '. The setup has no collection without them and is left out of the model '
                                            'catalog, where no request could be linked to it. Its row of the RAG '
                                            'setups stays.')
            else:
                say('NOTE', f"The model {model.get('name')} of the project {project.get('name')} holds no RAG setup "
                            "and is left out.")
                continue
            tables['rag_setups'].append(row)
            if text_of(row.get('collection')).strip():
                tables['catalog'].append(catalog_setup_row(row))
            elif not explained:
                say('WARNING', f"The RAG setup {model.get('name')} ({model.get('id')}) names no collection and is left "
                               "out of the model catalog, where no request could be linked to it. Its row of the RAG "
                               "setups stays.")
    if settings['rag_tag']:
        say('NOTE', f"{len(rag_projects)} project(s) with the tag {settings['rag_tag']} and "
                    f"{len(tables['rag_setups'])} RAG setup(s) in them were read.")
    tables['rag_run_sources'], problems = rag_run_sources(tables['rag_setups'])
    for problem in problems:
        say('WARNING', problem)

    # The models that answer: the definitions of the LLMs, the embedding models and the decision models
    for role, where in (('llm', 'llm_project'), ('embedding', 'embedding_project'),
                        ('decision_model', 'decision_project')):
        for reference in [part.strip() for part in settings[where].split('|') if part.strip()]:
            try:
                if PROJECT_ID.fullmatch(reference):
                    found = [get_json(f'/modelRepository/projects/{reference}', 'application/json')]
                elif reference.lower().startswith('tag:'):
                    found = list_all(get_json, filtered('/modelRepository/projects',
                                                        f"eq(tags,{filter_literal(reference[4:].strip())})"), limit)
                else:
                    # The filter narrows the listing, the name itself is compared here
                    found = [project for project in
                             list_all(get_json, filtered('/modelRepository/projects',
                                                         f"eq(name,{filter_literal(reference)})"), limit)
                             if isinstance(project, dict) and project.get('name') == reference]
            except Exception as error:
                say('WARNING', f"The project {reference} of the {role} models could not be read, its models are "
                               f"not in the catalog: {reason_of(error)}")
                continue
            found = [project for project in found if isinstance(project, dict) and project.get('id')]
            if not found:
                say('WARNING', f"No project {reference} was found, the {role} models it would hold are not in the catalog.")
            for project in found:
                tables['catalog'].extend(catalog_answer_row(model, project, role) for model in models_of(project))
    return tables


# ---------------------------------------------------------------------------
# Handing the tables to SAS
# ---------------------------------------------------------------------------

def table_rows(rows, columns):
    """
    The rows with exactly the columns of their table, every value in the type and inside the
    length of its column.
    """
    return [{name: number_of(row.get(name)) if kind == 'n' else cut_text(text_of(row.get(name)), length)
             for name, kind, length in columns} for row in rows]


def column_lengths(rows, columns):
    """
    The length of every text column of a table: the stated one, or for a column that follows
    its values the bytes of the longest value in these rows.
    """
    lengths = {}
    for name, kind, length in columns:
        if kind == 'c':
            lengths[name] = length
        elif kind == 't':
            longest = max((len(row[name].encode('utf-8', errors='replace')) for row in rows), default=0)
            lengths[name] = min(max(longest, MIN_TEXT), length)
    return lengths


def length_statement(columns, lengths):
    """
    The columns as the body of a SAS LENGTH statement, which also states their order.
    """
    return ' '.join(f'{name} 8' if kind == 'n' else f'{name} ${lengths[name]}' for name, kind, _ in columns)


def json_map(columns, lengths):
    """
    The map the JSON libname engine reads a table file with. It states the type and the length
    of every column, so neither depends on what the rows happen to hold.
    """
    variables = [{'NAME': 'ordinal_root', 'TYPE': 'ORDINAL', 'PATH': '/root'}]
    for name, kind, _ in columns:
        variable = {'NAME': name, 'TYPE': 'NUMERIC' if kind == 'n' else 'CHARACTER', 'PATH': '/root/' + name}
        if kind != 'n':
            variable['CURRENT_LENGTH'] = lengths[name]
        variables.append(variable)
    return {'DATASETS': [{'DSNAME': 'root', 'TABLEPATH': '/root', 'VARIABLES': variables}]}


def check_output_names(names):
    """
    Why the output tables can not be written under these names, '' when they can: every output
    needs a name, two outputs under one name would overwrite each other, and a name has to be
    one a CAS libref can address - the SAS code looks at every table it wrote through one, and
    so does whoever reads the table in SAS. CAS itself takes a longer name: such a table was
    written, promoted and saved and then reported as not written.
    """
    used = {}
    for output, name in names.items():
        if not name:
            return f'The output {output} has no table name.'
        if len(name) > 32:
            return (f'The table name {name} of the output {output} has {len(name)} characters. A SAS libref '
                    'addresses a CAS table by a name of 32 characters at most, nothing was read or written.')
        if not SAS_NAME.fullmatch(name):
            return (f'The table name {name} of the output {output} is no SAS name: letters, digits and '
                    'underscores, and no digit in front. Nothing was read or written.')
        if name.upper() in used:
            return f'The outputs {used[name.upper()]} and {output} have the same table name {name}.'
        used[name.upper()] = output
    return ''


# SAS.logMessage and SAS.symput put their text into SAS statements. There a semicolon ends
# the statement and the rest of the text runs as code, a percent sign or an ampersand starts
# a macro call, a quote or the start of a comment swallows the code that follows. The two
# marks of a comment are put together from their halves, so that this block holds neither.
MESSAGE_LIMIT = 900
NOT_FOR_SAS = (("'", '`'), ('"', '`'), ('%', 'pct '), ('&', '+'), (';', ','), ('/' + '*', '/ *'), ('*' + '/', '* /'))


def sas_safe(value, limit=MESSAGE_LIMIT):
    """A text that can stand in a SAS statement: on one line, without a character that is no
    text, without a quote, a macro trigger, a semicolon or a comment mark, not longer than
    the limit and with its parentheses as brackets when they do not pair up."""
    cleaned = ' '.join(str(value).split())
    cleaned = ''.join(character if character.isprintable() else ' ' for character in cleaned)
    for bad, replacement in NOT_FOR_SAS:
        cleaned = cleaned.replace(bad, replacement)
    cleaned = ' '.join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit - 3].rstrip() + '...'
    depth = 0
    for character in cleaned:
        depth += 1 if character == '(' else -1 if character == ')' else 0
        if depth < 0:
            break
    if depth != 0:
        cleaned = cleaned.replace('(', '[').replace(')', ']')
    return cleaned


# The longest value of a macro variable. The LENGTH statement of a table is longer than a message
MACRO_LIMIT = 65534


def say(message_type, message):
    """Writes one line to the SAS log. Every message of this program takes this way."""
    SAS.logMessage(sas_safe(message), messageType=message_type)


def hand_over(name, value):
    """Sets a macro variable. Everything the SAS code is handed takes this way."""
    SAS.symput(sas_safe(name), sas_safe(value, MACRO_LIMIT))


def setting(name):
    try:
        value = SAS.symget(name)
    except Exception:
        value = None
    return '' if value is None else str(value).strip()


def run_pull():
    """
    Reads SAS Model Manager and hands the tables to SAS: per table a JSON file and its map in
    the WORK folder, the number of rows and the LENGTH statement as macro variables.
    """
    if not setting('_gap_out_caslib'):
        # Nothing can be written without a CAS library, so nothing is read. Why there is none was said before.
        return
    problem = check_output_names({key: setting('_gap_out_' + key) for key in
                                  ('experiments', 'optimizations', 'rag_setups', 'rag_runs', 'catalog')})
    if problem:
        say('ERROR', problem)
        return
    if requests is None:
        say('ERROR', 'The Python package requests is needed to read SAS Model Manager and is not installed.')
        return
    settings = {
        'viya_host': setting('_gap_viyaHost'),
        'tag': setting('_gap_tag'),
        'rag_tag': setting('_gap_rag_tag'),
        'llm_project': setting('_gap_llm_project'),
        'embedding_project': setting('_gap_embedding_project'),
        'decision_project': setting('_gap_decision_project'),
        'page_limit': 100,
    }
    if not settings['tag']:
        say('ERROR', 'The tag of the projects that hold the prompt templates is empty.')
        return
    base = settings['viya_host'].rstrip('/')
    token = os.environ.get('SAS_SERVICES_TOKEN', '')
    verify = os.environ.get('SSLCALISTLOC') or os.environ.get('CAS_CLIENT_SSL_CA_LIST') or True

    def get_bytes(path, accept):
        response = requests.get(base + path, headers={'Authorization': 'Bearer ' + token, 'Accept': accept},
                                verify=verify, timeout=120)
        if response.status_code >= 300:
            raise RuntimeError(f'HTTP {response.status_code} for {path}')
        return response.content

    def get_text(path):
        # Decoded here: a file that is served as text without a character set is UTF-8 all the same
        return get_bytes(path, ANY_MEDIA_TYPE).decode('utf-8-sig', errors='replace')

    def get_json(path, accept='application/json'):
        return json.loads(get_bytes(path, accept).decode('utf-8-sig', errors='replace'))

    try:
        tables = pull(get_json, get_text, settings, say)
    except Exception as error:
        say('ERROR', f'SAS Model Manager could not be read: {reason_of(error)}')
        return

    folder = setting('_gap_work')
    for key, columns in TABLES.items():
        rows = table_rows(tables[key], columns)
        lengths = column_lengths(rows, columns)
        with open(os.path.join(folder, f'_gap_{key}.json'), 'w', encoding='utf-8') as file:
            json.dump(rows, file)
        with open(os.path.join(folder, f'_gap_{key}.map'), 'w', encoding='utf-8') as file:
            json.dump(json_map(columns, lengths), file)
        hand_over(f'_gap_rows_{key}', str(len(rows)))
        hand_over(f'_gap_length_{key}', length_statement(columns, lengths))
    hand_over('_gap_length_rag_runs', length_statement(RAG_RUN_COLUMNS, column_lengths([], RAG_RUN_COLUMNS)))
    hand_over('_gap_runs_texts', ' '.join(name for name, _ in RAG_RUN_TEXTS))
    hand_over('_gap_runs_numbers', ' '.join(RAG_RUN_NUMBERS))
    hand_over('_gap_rc', '0')


run_pull()
endsubmit;
run; quit;

proc python terminate;
run; quit;

* Reads one table the Python step wrote into WORK. The LENGTH statement comes from the Python step
  and gives the table its columns, also when the table has no rows and nothing is read;
%macro _gap_read(_gap_key, _gap_table);
    %if &&_gap_rows_&_gap_key. GT 0 %then %do;
        filename _gapjs "&_gap_work./_gap_&_gap_key..json";
        filename _gapmp "&_gap_work./_gap_&_gap_key..map";
        libname _gapjs json fileref=_gapjs map=_gapmp;

        data work.&_gap_table.;
            length &&_gap_length_&_gap_key.;
            set _gapjs.root(drop=ordinal_root);
        run;

        libname _gapjs clear;
        filename _gapjs clear;
        filename _gapmp clear;
    %end;
    %else %do;
        data work.&_gap_table.;
            length &&_gap_length_&_gap_key.;
            call missing(of _all_);
            stop;
        run;
    %end;
%mend _gap_read;

* Takes the runs of one RAG setup from its ledger, for a setup whose run table is not in memory.
  The ingestion job of a setup of the Prompt Builder keeps its runs in the vector store. It
  published the ledger only before it was given the publication of the run history, and it
  publishes the ledger only while that history is switched off. The run table is read where
  it is in memory and the ledger is not asked then, so a run that both name is one row.
  A row of the ledger names the run that listed its document last
  (run_id) and when the row was written (updated_at), and the id of a run holds the second the
  run started in - run-<seconds since 1970>-<digits>, which is where rag_core takes the start
  of a run from. Every run the ledger still names gives one row, from that start to the latest
  of its rows. The macro runs inside _gap_stack_rag_runs and reads the setup from its variables.
  The ledger is read, never loaded or changed;
%macro _gap_runs_of_ledger;
    %local _gap_lhave _gap_lruns;

    libname _gaprn cas caslib="&_gap_rs_caslib.";

    proc contents data=_gaprn.&_gap_rs_ledger. out=work._gap_runs_columns(keep=name) noprint;
    run;

    %let _gap_lhave =;
    proc sql noprint;
        select upcase(name) into :_gap_lhave separated by ' ' from work._gap_runs_columns;
    quit;

    %if %sysfunc(indexw(%str( &_gap_lhave.), RUN_ID)) EQ 0 or %sysfunc(indexw(%str( &_gap_lhave.), UPDATED_AT)) EQ 0 %then %do;
        %put NOTE: The run table &_gap_rs_caslib..&_gap_rs_table. is not in memory and the table &_gap_rs_caslib..&_gap_rs_ledger. has no columns run_id and updated_at, its runs are not in &_gap_out_rag_runs..;
    %end;
    %else %do;
        * The two columns as text, whatever the ledger holds them as;
        data work._gap_ledger_rows;
            length _gap_run $64 _gap_at $40;
            set _gaprn.&_gap_rs_ledger.(keep=run_id updated_at rename=(run_id=_gap_s1 updated_at=_gap_s2));
            _gap_run = cats(_gap_s1);
            _gap_at = cats(_gap_s2);
            if _gap_run NE '';
            keep _gap_run _gap_at;
        run;

        proc sql;
            create table work._gap_ledger_runs as
                select _gap_run, max(_gap_at) as _gap_last length=40
                    from work._gap_ledger_rows
                        group by _gap_run;
        quit;

        data work._gap_runs_one;
            length &_gap_length_rag_runs.;
            set work._gap_ledger_runs;
            call missing(of &_gap_runs_texts. &_gap_runs_numbers.);
            modelID = "&_gap_rs_model.";
            projectID = "&_gap_rs_project.";
            run_id = _gap_run;
            collection = "&_gap_rs_collection.";
            embed_model = "&_gap_rs_embed.";
            * The id of a run holds the second it started in, in UTC;
            _gap_epoch = input(scan(_gap_run, 2, '-'), ?? best32.);
            if 1000000000 < _gap_epoch < 4000000000 then
                started_at = translate(put(_gap_epoch + '01JAN1970:00:00:00'dt, e8601dt19.), ' ', 'T');
            * A row of the ledger is written with the time 2026-01-05T09:15:00Z;
            if _gap_last NE '' then finished_at = translate(substr(_gap_last, 1, 19), ' ', 'T');
            source_caslib = "&_gap_rs_caslib.";
            source_table = "&_gap_rs_ledger.";
            run_source = "ledger";
            drop _gap_run _gap_last _gap_epoch;
        run;

        %let _gap_lruns = 0;
        proc sql noprint;
            select count(*) into :_gap_lruns trimmed from work._gap_runs_one;
        quit;

        proc append base=work._gap_all_rag_runs data=work._gap_runs_one;
        run;

        %put NOTE: The run table &_gap_rs_caslib..&_gap_rs_table. is not in memory. &_gap_lruns. run(s) were taken from the ledger &_gap_rs_caslib..&_gap_rs_ledger. - the runs its rows still name, each from the start its id holds to the latest of its rows.;
    %end;

    libname _gaprn clear;
%mend _gap_runs_of_ledger;

* Stacks the <tables_prefix>_RUNS table of every RAG setup into work._gap_all_rag_runs. Only a
  table that is in memory is read: nothing is loaded and no table of a setup is changed. A run
  table is published by the release that wrote it, so a column can be missing or be text where
  a later release writes a number - every column is read as what RAG_RUNS holds it as. A setup
  whose run table is not in memory gives the runs its ledger still names, if that is in memory;
%macro _gap_stack_rag_runs;
    %local _gap_i _gap_j _gap_col _gap_columns _gap_texts _gap_have _gap_keep _gap_rename
        _gap_rs_model _gap_rs_project _gap_rs_caslib _gap_rs_table _gap_rs_collection
        _gap_rs_ledger _gap_rs_embed;

    data work._gap_all_rag_runs;
        length &_gap_length_rag_runs.;
        call missing(of _all_);
        stop;
    run;

    %let _gap_columns = &_gap_runs_texts. &_gap_runs_numbers.;
    %let _gap_texts = %sysfunc(countw(&_gap_runs_texts., %str( )));

    %do _gap_i = 1 %to &_gap_rows_rag_run_sources.;
        data _null_;
            set work._gap_rag_run_sources(firstObs=&_gap_i. obs=&_gap_i.);
            call symputx('_gap_rs_model', modelID, 'L');
            call symputx('_gap_rs_project', projectID, 'L');
            call symputx('_gap_rs_caslib', tables_caslib, 'L');
            call symputx('_gap_rs_table', runs_table, 'L');
            call symputx('_gap_rs_collection', collection, 'L');
            call symputx('_gap_rs_ledger', ledger_table, 'L');
            call symputx('_gap_rs_embed', embedding_model, 'L');
        run;

        * -1 the caslib is not known to this session, 0 the table is not in memory, 1 or 2 it is;
        %let _gap_runs_exists = 0;
        %let _gap_ledger_exists = 0;
        proc cas;
            runsExist = -1;
            ledgerExists = 0;
            if upcase("&_gap_rs_caslib.") EQ 'CASUSER' then runsExist = 0;
            table.caslibInfo result=casLibraries;
            do casLibrary over casLibraries.CASLibInfo;
                if upcase(casLibrary.Name) EQ upcase("&_gap_rs_caslib.") then runsExist = 0;
            end;
            if runsExist EQ 0 then do;
                table.tableExists result=runTable /
                    casLib = "&_gap_rs_caslib.",
                    name = "&_gap_rs_table.";
                runsExist = runTable.exists;
                %if %length(&_gap_rs_ledger.) GT 0 %then %do;
                    table.tableExists result=ledgerTable /
                        casLib = "&_gap_rs_caslib.",
                        name = "&_gap_rs_ledger.";
                    ledgerExists = ledgerTable.exists;
                %end;
            end;
            call symputx('_gap_runs_exists', runsExist);
            call symputx('_gap_ledger_exists', ledgerExists);
        quit;

        %if &_gap_runs_exists. LT 0 %then %do;
            %put NOTE: The CAS library &_gap_rs_caslib. of the run table &_gap_rs_table. is not available in this session, its runs are not in &_gap_out_rag_runs..;
        %end;
        %else %if &_gap_runs_exists. EQ 0 %then %do;
            %if &_gap_ledger_exists. GT 0 %then %do;
                %_gap_runs_of_ledger;
            %end;
            %else %do;
                %put NOTE: The run table &_gap_rs_caslib..&_gap_rs_table. is not in memory, its runs are not in &_gap_out_rag_runs..;
            %end;
        %end;
        %else %do;
            libname _gaprn cas caslib="&_gap_rs_caslib.";

            proc contents data=_gaprn.&_gap_rs_table. out=work._gap_runs_columns(keep=name) noprint;
            run;

            %let _gap_have =;
            proc sql noprint;
                select upcase(name) into :_gap_have separated by ' ' from work._gap_runs_columns;
            quit;

            * The columns of RAG_RUNS this table has, each read under a name of its own;
            %let _gap_keep =;
            %let _gap_rename =;
            %do _gap_j = 1 %to %sysfunc(countw(&_gap_columns., %str( )));
                %let _gap_col = %scan(&_gap_columns., &_gap_j., %str( ));
                %if %sysfunc(indexw(%str( &_gap_have.), %upcase(&_gap_col.))) %then %do;
                    %let _gap_keep = &_gap_keep. &_gap_col.;
                    %let _gap_rename = &_gap_rename. &_gap_col.=_gap_s&_gap_j.;
                %end;
            %end;

            %if %sysfunc(indexw(%str( &_gap_have.), RUN_ID)) EQ 0 %then %do;
                %put NOTE: The table &_gap_rs_caslib..&_gap_rs_table. has no column run_id, it is not read as a run table.;
            %end;
            %else %do;
                data work._gap_runs_one;
                    length &_gap_length_rag_runs.;
                    set _gaprn.&_gap_rs_table.(keep=&_gap_keep. rename=(&_gap_rename.));
                    modelID = "&_gap_rs_model.";
                    projectID = "&_gap_rs_project.";
                    source_caslib = "&_gap_rs_caslib.";
                    source_table = "&_gap_rs_table.";
                    run_source = "run table";
                    %do _gap_j = 1 %to %sysfunc(countw(&_gap_columns., %str( )));
                        %let _gap_col = %scan(&_gap_columns., &_gap_j., %str( ));
                        %if %sysfunc(indexw(%str( &_gap_have.), %upcase(&_gap_col.))) EQ 0 %then %do;
                            call missing(&_gap_col.);
                        %end;
                        %else %if &_gap_j. LE &_gap_texts. %then %do;
                            &_gap_col. = cats(_gap_s&_gap_j.);
                        %end;
                        %else %do;
                            &_gap_col. = input(cats(_gap_s&_gap_j.), ?? best32.);
                        %end;
                    %end;
                    %if %length(&_gap_rs_collection.) GT 0 %then %do;
                        * A run table can hold the runs of other collections of the same vector store;
                        if collection NE '' and collection NE "&_gap_rs_collection." then delete;
                    %end;
                    drop _gap_s:;
                run;

                proc append base=work._gap_all_rag_runs data=work._gap_runs_one;
                run;
            %end;

            libname _gaprn clear;
        %end;
    %end;

    proc datasets lib=work nolist nowarn;
        delete _gap_runs_one _gap_runs_columns _gap_ledger_rows _gap_ledger_runs;
    quit;
%mend _gap_stack_rag_runs;

* Replaces one output table in CAS, promotes and saves it;
%macro _gap_publish(_gap_table, _gap_name);
    proc casUtil inCASlib="&_gap_out_caslib." outCASLib="&_gap_out_caslib.";
        dropTable casData="&_gap_name." quiet;
        load data=work.&_gap_table. casOut="&_gap_name.";
        promote casData="&_gap_name." casOut="&_gap_name.";
        save casData="&_gap_name." casOut="&_gap_name..sashdat" replace;
    quit;
%mend _gap_publish;

* Says in one line what an output table was written with, or that it is not there;
%macro _gap_written(_gap_table, _gap_name);
    %local _gap_dsid _gap_obs _gap_vars _gap_close;

    %let _gap_dsid = %sysfunc(open(work.&_gap_table.));
    %let _gap_obs = %sysfunc(attrn(&_gap_dsid., nlobs));
    %let _gap_vars = %sysfunc(attrn(&_gap_dsid., nvars));
    %let _gap_close = %sysfunc(close(&_gap_dsid.));
    %if %sysfunc(exist(_gapout.&_gap_name.)) %then %do;
        %put NOTE: &_gap_out_caslib..&_gap_name. was written with &_gap_obs. rows and &_gap_vars. columns.;
    %end;
    %else %do;
        %put ERROR: The table &_gap_out_caslib..&_gap_name. was not written.;
        %let _gap_failed = 1;
    %end;
%mend _gap_written;

%macro _gap_write_tables;
    * Without a CAS library there is nothing to write to, which was said where it was found;
    %if %length(&_gap_out_caslib.) EQ 0 %then %return;
    %if &_gap_rc. NE 0 %then %do;
        %put ERROR: The experiments could not be read from SAS Model Manager, the output tables in &_gap_out_caslib. were left as they are.;
        %let _gap_failed = 1;
        %return;
    %end;

    %_gap_read(experiments, _gap_all_data);
    %_gap_read(optimizations, _gap_all_optimizations);
    %_gap_read(rag_setups, _gap_all_rag_setups);
    %_gap_read(rag_run_sources, _gap_rag_run_sources);
    %_gap_read(catalog, _gap_all_catalog);
    %_gap_stack_rag_runs;

    proc datasets lib=work nolist;
        modify _gap_all_data;
            format creationTime modifiedTime datetime22.8;
            label unique_id = 'Unique ID'
                name = 'Prompt Name'
                description = 'Description'
                createdBy = 'Created By'
                modifiedBy = 'Modified By'
                function = 'Function'
                modelLink = 'Model Link'
                projectLink = 'Project Link'
                creationTime = 'Creation Time Stamp'
                modifiedTime = 'Modified Time Stamp'
                runID = 'Run ID'
                model = 'Model'
                systemPrompt = 'System Prompt'
                userPrompt = 'User Prompt'
                response = 'Response'
                run_time = 'Run Time (s)'
                prompt_length = 'Prompt Length (tokens)'
                output_length = 'Output Length (tokens)'
                cost = 'Estimated Call Cost'
                cheapest_prompt = 'Cheapest Prompt in Run'
                best_prompt = 'Best Prompt in Run'
                fastest_prompt = 'Fastest Prompt in Run'
                fewest_tokens_prompt = 'Fewest Tokens Prompt in Run'
                judge_rank = 'Judge Rank in Run'
                judge_best = 'Best Prompt per Judge'
                judge_cost = 'Estimated Judging Cost'
                judge_model = 'Judge Model'
                judge_confidence = 'Judge Confidence'
                judge_mode = 'Judge Mode'
                judge_panel = 'Judge Panel'
                judge_agreement = 'Judge Agreement'
                judge_chairman_model = 'Judge Chairman'
                temperature = 'Temperature'
                top_p = 'Top P'
                top_k = 'Top K'
                max_tokens = 'Max Tokens'
                thinking_budget = 'Thinking Budget'
                max_completion_tokens = 'Max Completion Tokens'
                seed = 'Seed'
                frequency_penalty = 'Frequency Penalty'
                presence_penalty = 'Presence Penalty'
                reasoning_effort = 'Reasoning Effort'
                input_type = 'Input Type'
                dimensions = 'Dimensions'
                normalize = 'Normalize'
                variables_count = 'Custom Input Variables (count)'
                integrated_llm_call = 'Integrated LLM Call'
                output_variables_count = 'Parsed Output Variables (count)'
                api_key_required = 'API Key Required'
                modelID = 'Model ID'
                projectID = 'Project ID'
                projectName = 'Project Name'
                kind = 'Kind (prompt or decision)'
                expected = 'Expected Answers (JSON)'
                options = 'Options'
                variables = 'Variables of the Run (JSON)'
                resolved_system_prompt = 'System Prompt as Sent'
                resolved_user_prompt = 'User Prompt as Sent'
                resolved_system_hash = 'Hash of the System Prompt as Sent'
                resolved_user_hash = 'Hash of the User Prompt as Sent'
                judge_reasoning = 'Judge Reasoning';
        modify _gap_all_optimizations;
            format startedAt finishedAt datetime20.;
            label modelID = 'Model ID'
                projectID = 'Project ID'
                projectName = 'Project Name'
                name = 'Prompt Name'
                optimizationId = 'Optimization ID'
                startedAt = 'Started'
                finishedAt = 'Finished'
                status = 'Status'
                targetModel = 'Target Model'
                optimizer = 'Optimizer'
                metric = 'Metric'
                judgeModel = 'Judge Model'
                datasetSource = 'Dataset Source'
                datasetRef = 'Dataset'
                sampleCount = 'Samples'
                trainSize = 'Training Examples'
                validationSize = 'Validation Examples'
                metricBefore = 'Metric Before'
                metricAfter = 'Metric After'
                targetCalls = 'Target Model Calls'
                targetPromptTokens = 'Target Model Input Tokens'
                targetOutputTokens = 'Target Model Output Tokens'
                targetRunTime = 'Target Model Run Time (s)'
                judgeCalls = 'Judge Model Calls'
                judgePromptTokens = 'Judge Model Input Tokens'
                judgeOutputTokens = 'Judge Model Output Tokens'
                judgeRunTime = 'Judge Model Run Time (s)'
                error = 'Error'
                type = 'Type (empty or comparison)'
                skippedReason = 'Why the Optimizer Did Not Run'
                jobId = 'Job ID'
                computeContext = 'Compute Context';
        modify _gap_all_rag_setups;
            format creationTime modifiedTime datetime20.;
            label modelID = 'Model ID'
                projectID = 'Project ID'
                projectName = 'Project Name'
                name = 'RAG Setup'
                description = 'Description'
                creationTime = 'Creation Time Stamp'
                modifiedTime = 'Modified Time Stamp'
                source = 'Saved by (builder or step)'
                collection = 'Collection'
                backend = 'Vector Database'
                embedding_model = 'Embedding Model'
                embedding_dims = 'Embedding Dimensions'
                chunker = 'Chunker'
                input_token_limit = 'Input Token Limit'
                overlap_tokens = 'Overlap Tokens'
                tables_prefix = 'Table Prefix'
                tables_caslib = 'CAS Library of the Tables'
                source_path = 'Source Path'
                pipeline_version = 'Pipeline Version'
                artifacts_folder = 'Artifacts Folder'
                job_definition_uri = 'Job Definition'
                ingestion_run_id = 'Ingestion Run of the Registration'
                configuration = 'Configuration'
                runs_table = 'Run Table'
                createdBy = 'Created By'
                modifiedBy = 'Modified By';
        modify _gap_all_rag_runs;
            label modelID = 'Model ID'
                projectID = 'Project ID'
                run_id = 'Run ID'
                rag_project = 'RAG Project'
                collection = 'Collection'
                backend = 'Vector Database'
                started_at = 'Run Start'
                finished_at = 'Run End'
                status = 'Run Status'
                embed_model = 'Embedding Model'
                enrich_model = 'Enrichment LLM'
                source_caslib = 'CAS Library of the Run Table'
                source_table = 'Run Table'
                run_source = 'Where the Run Is From (run table or ledger)';
        modify _gap_all_catalog;
            label modelID = 'Model ID'
                projectID = 'Project ID'
                projectName = 'Project Name'
                name = 'Model Name'
                role = 'Role'
                runID = 'Run ID'
                target_model = 'Model the Pattern Is Sent to'
                system_template = 'System Prompt Template'
                user_template = 'User Prompt Template'
                resolved_system_prompt = 'System Prompt as Sent'
                resolved_user_prompt = 'User Prompt as Sent'
                collection = 'Collection'
                template_source = 'Where the Template or the Setup Is From'
                resolved_system_hash = 'Hash of the System Prompt as Sent'
                resolved_user_hash = 'Hash of the User Prompt as Sent'
                run_time = 'Run Time of the Call in the Run (s)'
                prompt_length = 'Prompt Length of the Call in the Run (tokens)'
                output_length = 'Output Length of the Call in the Run (tokens)'
                window_start = 'Start of the Optimization (UTC)'
                window_end = 'End of the Optimization (UTC)'
                optimization_id = 'Optimization ID';
    quit;

    libname _gapout cas caslib="&_gap_out_caslib.";
    %_gap_publish(_gap_all_data, &_gap_out_experiments.);
    %_gap_publish(_gap_all_optimizations, &_gap_out_optimizations.);
    %_gap_publish(_gap_all_rag_setups, &_gap_out_rag_setups.);
    %_gap_publish(_gap_all_rag_runs, &_gap_out_rag_runs.);
    %_gap_publish(_gap_all_catalog, &_gap_out_catalog.);

    * The last lines of the run: one per output table;
    %_gap_written(_gap_all_data, &_gap_out_experiments.);
    %_gap_written(_gap_all_optimizations, &_gap_out_optimizations.);
    %_gap_written(_gap_all_rag_setups, &_gap_out_rag_setups.);
    %_gap_written(_gap_all_rag_runs, &_gap_out_rag_runs.);
    %_gap_written(_gap_all_catalog, &_gap_out_catalog.);
    libname _gapout clear;

    proc datasets lib=work nolist nowarn;
        delete _gap_rag_run_sources;
    quit;
%mend _gap_write_tables;

%_gap_write_tables;

%sysmacdelete _gap_read;
%sysmacdelete _gap_runs_of_ledger;
%sysmacdelete _gap_stack_rag_runs;
%sysmacdelete _gap_publish;
%sysmacdelete _gap_written;
%sysmacdelete _gap_write_tables;
%symdel _gap_viyaHost _gap_work _gap_rc _gap_runs_exists _gap_ledger_exists _gap_runs_texts _gap_runs_numbers
    _gap_rows_experiments _gap_rows_optimizations _gap_rows_rag_setups _gap_rows_rag_run_sources _gap_rows_catalog
    _gap_length_experiments _gap_length_optimizations _gap_length_rag_setups _gap_length_rag_run_sources
    _gap_length_catalog _gap_length_rag_runs / nowarn;
* end of the gap core;

* Clean up;
%if &_gap_clean_cas_session. %then %do;
    cas mySess terminate;
%end;
* A pull that wrote an ERROR line and left an output table unwritten ends with the condition code of a failed job, so that SAS Job Execution, a schedule and a flow report it as failed and not as completed. A pull with a WARNING - a model that could not be read, a setup that is left out - ends as before. Nothing follows but the macro variables that are deleted;
%if &_gap_failed. EQ 1 %then %do;
    %let SYSCC = 1012;
%end;
%symdel _gap_tag _gap_rag_tag _gap_llm_project _gap_embedding_project _gap_decision_project
    _gap_out_caslib _gap_out_experiments _gap_out_optimizations _gap_out_rag_setups _gap_out_rag_runs
    _gap_out_catalog _gap_clean_cas_session _gap_failed / nowarn;
