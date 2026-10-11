/********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0
********************************************************************************/
cas mysess;
libname public cas caslib='Public';
libname casuser cas caslib='casuser';

* Configure were the log is located and where you wish the table to end up in CAS;
%let _log_path = sasserver:/export/pvs/sasdata/data/llm/llms.log;
%let _log_target_table_engine = CAS;
%let _log_target_table_lib = Public;
%let _log_target_table_name = LLM_LOGS;
%let _log_append_table = 0;
%let _log_promote_table = 1;
%let _log_save_table = 1;

* Check if the file is located on the SAS Server, if so extract the path;
data _null_;
   locationType = scan("&_log_path.", 1, ':');
   if lowCase(locationType) ne 'sasserver' then do;
      putLog 'ERROR: For this step to run please ensure the the selected file is located on the SAS Server.';
      abort 10;
   end;
   else do;
        call symputx('_log_path', scan("&_log_path.", 2, ':', 'MO'));
   end;
run;

* Ensure that the output table is in CAS;
%if &_log_target_table_engine. NE CAS %then %do;
    data _null_;
        putlog "ERROR: The table &_log_target_table. is not a CAS table.";
        putlog 'ERROR: In order for this step to run your target table needs to be in CAS';
        abort 62;
    run;
%end;

* Get the caslib name of the libname;
proc sql noPrint;
    select sysvalue into :_log_target_table_lib trimmed
        from dictionary.libnames
            where libname = upcase("&_log_target_table_lib.") and upcase(sysname) = 'CASLIB';
quit;

* Check if a CAS session exists;
* The option SESSREF names the session the CAS actions run in and keeps the name of a session that was ended, so SESSFOUND is asked whether that session is connected;
%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    %let _log_clean_cas_session = 1;
    cas llmLogMySess;
%end;
%else %do;
    %let _log_clean_cas_session = 0;
%end;

proc cas;
    session.sessionId result = sessresults;
    call symputx('_casSessionUUID', sessresults[1]);
quit;

* The number of requests found in the log. The Python code sets it as its last statement, so -1 means that the parsing did not finish;
%let _log_row_count = -1;

* What append mode finds in CAS. The output macro sets both: the table the rows are appended to (0 there is none, 1 a table of this session, 2 a promoted table) and whether a saved table could not be loaded;
%let _log_target_scope = 0;
%let _log_target_unloaded = 0;
* What the DATA step of append mode is given for the text columns that the existing table holds as CHAR. The output macro sets the three, and they stay empty where there is no such column;
%let _log_fixed_rename = ;
%let _log_fixed_assign = ;
%let _log_fixed_drop = ;

proc python restart;
submit;
import re
import os
import ast
import json
import hashlib
from datetime import datetime
try:
    import swat
    import pandas as pd
except ImportError:
    SAS.logMessage('Ensure that the SWAT and Pandas package are installed', messageType='ERROR')

#  Add certificate location to operating system list of trusted certs
os.environ['CAS_CLIENT_SSL_CA_LIST'] = os.environ['SSLCALISTLOC']

# Connect to CAS
conn = swat.CAS(hostname=SAS.sasfnc('getoption', 'cashost'), port=SAS.sasfnc('getoption', 'casport'), password=os.environ['SAS_SERVICES_TOKEN'], session=SAS.symget('_casSessionUUID'))

# The columns of the output table in the order they are written. The list is fixed,
# so a log without a single request still describes the complete table. The first
# twelve are the columns the table had before the others were added behind them.
COLUMNS = ['timestamp', 'model', 'system_prompt', 'user_prompt', 'temperature', 'top_p', 'top_k', 'max_tokens', 'prompt_length', 'output_length', 'runtime', 'response',
           'kind', 'status_code', 'failed', 'error_text', 'transaction_id', 'request_id', 'user_agent',
           'embedding_mode', 'project', 'input_hash', 'answer', 'answer_confidence', 'options', 'rag_collection', 'rag_query_hash',
           'model_id', 'caller_model_id', 'caller_project_id', 'caller_kind', 'caller_name', 'experiment_run_id', 'rag_model_id', 'match_method', 'match_candidates']

# The columns that say which model answered a request and who sent it. Nothing in a log
# names either, so the parser writes them empty and the program that links the logs
# to the models fills them.
LINK_COLUMNS = ['model_id', 'caller_model_id', 'caller_project_id', 'caller_kind', 'caller_name', 'experiment_run_id', 'rag_model_id', 'match_method', 'match_candidates']

# The request inputs that are kept and the column each one is written to. A decision
# model takes questions and a state where an LLM takes a system and a user prompt.
# The document input of an embedding model is left out on purpose: keeping it would
# copy every embedded document into the output table. Its hash is kept instead.
INPUT_COLUMNS = {
    'systemPrompt': 'system_prompt',
    'userPrompt': 'user_prompt',
    'questions': 'system_prompt',
    'state': 'user_prompt'
}

# The options that have a column of their own
OPTION_COLUMNS = ['temperature', 'top_p', 'top_k', 'max_tokens']

# The other names under which a request sets the limit of new tokens. They are read
# in this order where the request has no max_tokens.
MAX_TOKENS_ALIASES = ['max_completion_tokens', 'max_length']

# The numbers the score code logs and the column each one is written to. An embedding
# model logs its input tokens as tokens, an LLM and a decision model as prompt_length.
METRIC_COLUMNS = {
    'prompt_length': 'prompt_length',
    'tokens': 'prompt_length',
    'output_length': 'output_length',
    'run_time': 'runtime'
}

# Every line the containers log starts with '<timestamp> - <logger> - <level> - <message>'.
# Lines are matched from their first character, so the same words inside a prompt or
# a response are never mistaken for a log line.
LOG_LINE = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\S* - (\S+) - ([A-Z]+) - (.*)', re.DOTALL)
SCORE_LINE = re.compile(r'(prompt_length|tokens|output_length|run_time|response|answers|answer):(.*)', re.DOTALL)
LOG_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'

# The container gives every request a transaction and logs under 'transaction_<id>' what
# it does with it. The 'Response Data:' line names the same id.
TRANSACTION_LOGGER = 'transaction_'
RESPONSE_TRANSACTION = re.compile(r"'transaction_id': '([^']+)'")
# The headline of a decision as the score code logs it: '<answer> (<confidence>)'
ANSWER_LINE = re.compile(r'(.*) \(([^()\s]+)\)', re.DOTALL)

# An option whose name says that it holds a secret, and the shapes a secret has as a value
KEY_NAME = re.compile(r'key|secret|password|passwd|pwd|credential|authorization|bearer|cookie', re.IGNORECASE)
KEY_SHAPE = re.compile(r'(?:bearer|basic)\s+\S|(?:sk|pk|rk)-\S|eyJ[\w-]{8,}\.', re.IGNORECASE)
KEY_IN_TEXT = re.compile(r'\bbearer\s+(?=[\w.~+/=*-]*\d)[\w.~+/=*-]{8,}|\b(?:sk|pk|rk)-[\w*.-]{6,}|\beyJ[\w-]{8,}\.[\w.-]+', re.IGNORECASE)
LONG_RUN = re.compile(r'[A-Za-z0-9]{20,}')
REDACTED = 'REDACTED'
ERROR_TEXT_LENGTH = 500

def read_number(text):
    """
    Returns the number a logged value holds, None if it is not a number (for example 'None').
    """
    try:
        return float(text)
    except ValueError:
        return None

def text_hash(text):
    """
    Returns the hash a text is known by in the log table: the SHA-256 of its UTF-8 bytes
    in lower-case hex, after every run of white space was made one blank.

    The programs that read the table hash a text the same way, which is how the question
    of a retrieval is found again in the call to the embedding model that embedded it.
    """
    return hashlib.sha256(' '.join(text.split()).encode('utf-8')).hexdigest()

def looks_like_a_key(text):
    """
    Returns whether a value has the shape of a secret: a known prefix, or 20 and more
    letters and digits in a row that hold both.
    """
    text = text.strip().strip('"\'')
    if KEY_SHAPE.match(text):
        return True
    return any(re.search(r'\d', run) and re.search(r'[A-Za-z]', run) for run in LONG_RUN.findall(text))

def is_key_name(name, value):
    """
    Returns whether the name of an option says that its value is a secret. A name with
    'token' in it does when the value is no number (max_tokens is a number).
    """
    name = name.strip().strip('"\'')
    if KEY_NAME.search(name):
        return True
    return 'token' in name.lower() and read_number(str(value).strip().strip('"\'')) is None

def redact_value(value):
    """
    Returns options that were sent as JSON with every secret replaced.
    """
    if isinstance(value, dict):
        return {name: REDACTED if is_key_name(str(name), item) else redact_value(item) for name, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str) and looks_like_a_key(value):
        return REDACTED
    return value

def read_options(options_str):
    """
    Returns the options of a request by their names.

    The models take them as '{name:value,name:value}' or as JSON, and read them the
    same way: JSON first, otherwise every part between two commas that holds a colon.
    """
    text = options_str.strip()
    try:
        decoded = json.loads(text)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        return {str(name): value if isinstance(value, str) else json.dumps(value) for name, value in decoded.items()}
    if text.startswith('{') and text.endswith('}'):
        text = text[1:-1]  # Remove surrounding {}
    named = {}
    for opt in text.split(','):
        if ':' in opt:
            key, val = opt.split(':', 1)
            named[key.strip().strip('"\'')] = val.strip().strip('"\'')
    return named

def redact_options(options_str):
    """
    Returns the options as they were sent, with the value of every option that is a
    secret by its name (API_KEY, ...) or by its shape replaced by REDACTED.

    The collection script does this for API_KEY before the log is written. The parser
    does it again, so a log that was collected another way brings no key into the table.
    """
    text = options_str.strip()
    try:
        decoded = json.loads(text)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        redacted = redact_value(decoded)
        return text if redacted == decoded else json.dumps(redacted, ensure_ascii=False)

    opening, closing = '', ''
    if text.startswith('{') and text.endswith('}'):
        opening, text, closing = '{', text[1:-1], '}'
    parts = []
    hidden = False
    for part in text.split(','):
        name, colon, value = part.partition(':')
        if not colon:
            # A part without a colon is the rest of the value in front of it, which held a comma
            if not hidden:
                parts.append(REDACTED if looks_like_a_key(part) else part)
        elif is_key_name(name, value) or looks_like_a_key(value):
            parts.append(name + ':' + REDACTED)
            hidden = True
        else:
            parts.append(part)
            hidden = False
    return opening + ','.join(parts) + closing

def redact_text(text):
    """
    Returns a text of the log with everything in it that has the shape of a secret replaced.
    """
    text = KEY_IN_TEXT.sub(REDACTED, text)
    return LONG_RUN.sub(lambda run: REDACTED if looks_like_a_key(run.group()) else run.group(), text)

def user_agent_class(user_agent):
    """
    Returns the kind of client a User-Agent header names: python-requests (the score
    code of another model, a job, a script), browser, SAS or other. The header itself
    is not kept: it carries version numbers and nothing the table needs.
    """
    agent = user_agent.strip()
    if not agent:
        return None
    if agent.lower().startswith('python-requests'):
        return 'python-requests'
    if agent.lower().startswith('mozilla/'):
        return 'browser'
    if re.search(r'\bSAS\b', agent, re.IGNORECASE):
        return 'SAS'
    return 'other'

def read_request_headers(entry, header_lines):
    """
    Adds what is kept of the request headers to the entry of their request: the
    X-Request-Id header and the kind of client. No other header is read, so no
    address, no Referer and no cookie reaches the table.

    Args:
        entry (dict): The entry of the request the headers belong to.
        header_lines (list): The text after 'Request Headers: ' and the lines that follow it, one header each.
    """
    for line in header_lines:
        name, colon, value = line.partition(':')
        name = name.strip().lower()
        if not colon:
            continue
        if name == 'x-request-id':
            entry['request_id'] = value.strip()[:64] or None
        elif name == 'user-agent':
            entry['user_agent'] = user_agent_class(value)

def envelope_in(value):
    """
    Returns the first object in a decoded JSON value that has a collection and a query, None if there is none.
    """
    if isinstance(value, dict):
        if isinstance(value.get('collection'), str) and value['collection'].strip() and isinstance(value.get('query'), str):
            return value
        children = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = envelope_in(child)
        if found is not None:
            return found
    return None

def find_retrieval_envelope(text):
    """
    Returns the envelope of a retrieval that a prompt holds, None if it holds none.

    The RAG runtime hands the results of a retrieval over as one JSON object that names
    the collection and the question ('query') beside the hits, and a prompt carries that
    object somewhere in its text. It is decoded as JSON from each '{' on, so a question
    with quotes, braces or line breaks in it is read as it was written.
    """
    if '"collection"' not in text or '"query"' not in text:
        return None
    decoder = json.JSONDecoder()
    position = text.find('{')
    while position != -1:
        try:
            value, end = decoder.raw_decode(text, position)
        except (ValueError, RecursionError):
            position = text.find('{', position + 1)
            continue
        envelope = envelope_in(value)
        if envelope is not None:
            return envelope
        position = text.find('{', end)
    return None

def kind_of(input_names):
    """
    Returns the kind of a request by the names of its inputs: an embedding model takes
    a document, a decision model questions and a state, an LLM a system and a user prompt.
    """
    if 'document' in input_names:
        return 'embedding'
    if 'questions' in input_names or 'state' in input_names:
        return 'decision'
    if 'systemPrompt' in input_names or 'userPrompt' in input_names:
        return 'llm'
    return None

def read_request_data(entry, request_data_str):
    """
    Adds the inputs of a 'Request Data:' line to the entry of its request.

    The line holds the request body as a Python literal. A line that can not be read
    (for example because it was cut off) leaves the entry as it is.

    Args:
        entry (dict): The entry of the request the line belongs to.
        request_data_str (str): The text after 'Request Data: '.
    """
    try:
        request_data = ast.literal_eval(request_data_str.strip())
    except (ValueError, SyntaxError):
        return
    if not isinstance(request_data, dict) or not isinstance(request_data.get('inputs'), list):
        return

    input_names = set()
    for item in request_data['inputs']:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            continue
        name = item['name']
        input_names.add(name)
        if item.get('value') is None:
            continue
        value = str(item['value'])
        if name in INPUT_COLUMNS:
            entry[INPUT_COLUMNS[name]] = value
        if name == 'userPrompt':
            envelope = find_retrieval_envelope(value)
            if envelope is not None:
                entry['rag_collection'] = envelope['collection'].strip()
                entry['rag_query_hash'] = text_hash(envelope['query'])
        elif name == 'document':
            entry['input_hash'] = text_hash(value)
        elif name == 'project':
            entry['project'] = value
        elif name == 'options':
            sent = read_options(value)
            for key in OPTION_COLUMNS:
                if key in sent:
                    entry[key] = sent[key]
            if 'max_tokens' not in sent:
                for key in MAX_TOKENS_ALIASES:
                    if key in sent:
                        entry['max_tokens'] = sent[key]
                        break
            if 'Embedding_Mode' in sent:
                entry['embedding_mode'] = sent['Embedding_Mode']
            entry['options'] = redact_options(value)
    entry['kind'] = kind_of(input_names)

def read_error(entry, message, more_lines):
    """
    Marks the entry of a request as failed and keeps the first error the log holds for it.

    The line the container writes for an exception ends with a colon and names no cause:
    the last line of the traceback behind it is added, which is the exception itself.

    Args:
        entry (dict): The entry of the request the line belongs to.
        message (str): The message of the ERROR line.
        more_lines (list): The lines that follow it and are no log lines of their own.
    """
    entry['_errors'] = True
    if entry['error_text'] is not None:
        return
    text = message.strip()
    if text.endswith(':'):
        text += ' ' + next((line for line in reversed(more_lines) if line), '')
    entry['error_text'] = redact_text(' '.join(text.split()))[:ERROR_TEXT_LENGTH] or None

def transaction_of(logger, message):
    """
    Returns the transaction a log line names, None for a line that names none.
    """
    if logger.startswith(TRANSACTION_LOGGER):
        return logger[len(TRANSACTION_LOGGER):]
    if message.startswith('Response Data: '):
        match_transaction = RESPONSE_TRANSACTION.search(message)
        if match_transaction:
            return match_transaction.group(1)
    return None

def parse_log_file(file_path):
    """
    Parses a structured application log file containing the request and response blocks
    of LLMs, decision models and embedding models.

    Extracts relevant data including:
    - Timestamp of the request
    - The model used for the request and the kind of the request
    - System and user prompts (for a decision model the questions and the state)
    - Model options (temperature, top_p, top_k, max_tokens) and the options as they were sent, without keys
    - Prompt and output lengths (for an embedding model the tokens as prompt length)
    - Runtime in seconds
    - Multiline model response (for a decision model the answers with their confidence)
    - The status code of the response, whether the request failed and its first error
    - The transaction id of the container, the X-Request-Id header and the kind of client
    - For an embedding model its mode, the project and the hash of the embedded text
    - For an LLM whose user prompt holds the results of a retrieval, the collection and the hash of the question

    Each request block starts with 'Request: POST' and ends with its 'Response: Status Code'
    line or, for a request that failed, with the 'Error occurred in the model scoring function'
    line of its transaction. A request that has neither ends before the next 'Request: POST'
    or before the first line that is older than itself. Lines between two blocks belong to
    no request and are skipped.

    The lines of the score code and the 'Response: Status Code' line name no request. While
    one request is running they are its lines. When a second request starts before the first
    one has ended - which shows in a later line that names the transaction of the first -
    such a line is given to neither, and the requests keep what the lines that name their
    transaction say.

    Args:
        file_path (str): Path to the log file to parse.

    Returns:
        pandas.DataFrame: A DataFrame containing one row per request with the columns in COLUMNS.
    """
    # A byte that is not UTF-8 is replaced, so one damaged line does not cost the whole file
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    # The last line that names each transaction: a request that a later line still names is running
    last_named = {}
    for number, line in enumerate(lines):
        match_line = LOG_LINE.match(line)
        if match_line:
            transaction = transaction_of(match_line.group(2), match_line.group(4))
            if transaction is not None:
                last_named[transaction] = number

    records = []
    # The transactions that are running, oldest first. One of a request holds its entry.
    # One that no request line was waiting for holds none: its lines are followed to
    # know when it ends, and nothing is kept of it.
    running = []
    # The requests that ended before a transaction line named them
    displaced = []
    # The 'Response:' lines that could not be given to a request and whose 'Response Data:' line is still to come
    unpaired = 0
    unmatched_requests = 0

    def mark_overlap():
        if len(running) > 1:
            for slot in running:
                if slot['entry'] is not None:
                    slot['entry']['_overlapped'] = True

    def mark_unattributed():
        for slot in running:
            if slot['entry'] is not None:
                slot['entry']['_unattributed'] = True

    def forget(slot):
        # What was read for this request may be the line of another one: only its request line is kept.
        # The other one is a request that ended without a transaction when this one started
        entry = slot['entry']
        for column in COLUMNS:
            if column not in ('timestamp', 'model'):
                entry[column] = None
        entry['_errors'] = False
        entry['_unattributed'] = True
        for other in slot['displaced']:
            other['_unattributed'] = True
        slot['entry'] = None

    i = 0
    while i < len(lines):
        number = i
        match_line = LOG_LINE.match(lines[i].rstrip('\r\n'))
        i += 1
        if not match_line:
            # A request in a line that does not start as a log line, for example behind a prefix of the log pipeline
            if 'Request: POST' in lines[number]:
                unmatched_requests += 1
            continue
        timestamp, logger, level, message = match_line.groups()
        # A message can span several lines (the request headers, a response, a traceback), it ends at the next line that starts with a timestamp
        more_lines = []
        while i < len(lines) and not LOG_TIMESTAMP.match(lines[i]):
            more_lines.append(lines[i].strip())
            i += 1
        # A line that is older than a running request belongs to the next container's part of the file
        if running and timestamp < max(slot['start'] for slot in running):
            del running[:]
            del displaced[:]

        if message.startswith('Request: '):
            scoring_request = message.startswith('Request: POST')
            model = (message[len('Request: POST'):].strip().strip('/') or None) if scoring_request else None
            # A container serves one model, so a request to another model is the next container's part of the file
            if scoring_request and any(slot['model'] not in (None, model) for slot in running):
                del running[:]
                del displaced[:]
            # A request ends here unless a later line names its transaction
            still_running = []
            for slot in running:
                if slot['transaction'] is not None and last_named.get(slot['transaction'], -1) > number:
                    still_running.append(slot)
                elif slot['transaction'] is None and slot['entry'] is not None:
                    displaced.append(slot['entry'])
            running[:] = still_running
            if not scoring_request:
                continue
            entry = dict.fromkeys(COLUMNS)
            entry['timestamp'] = timestamp
            entry['model'] = model
            entry.update(_errors=False, _overlapped=False, _unattributed=False)
            records.append(entry)
            running.append({'entry': entry, 'transaction': None, 'model': model, 'start': timestamp,
                            'headers': False, 'data': False, 'displaced': list(displaced)})
            del displaced[:]
            mark_overlap()
        elif message.startswith('Request Headers: ') or message.startswith('Request Data: '):
            # The first lines of a request name nothing. They belong to the request that has no transaction yet
            slot = next((slot for slot in running if slot['transaction'] is None and slot['entry'] is not None), None)
            if slot is None:
                continue
            part = 'headers' if message.startswith('Request Headers: ') else 'data'
            if slot[part]:
                # A second line of the same kind is the line of another request
                forget(slot)
            elif part == 'headers':
                read_request_headers(slot['entry'], [message[len('Request Headers: '):]] + more_lines)
            else:
                read_request_data(slot['entry'], message[len('Request Data: '):])
            slot[part] = True
        elif message.startswith('Response: Status Code'):
            # The line names no request, the 'Response Data:' line behind it names the transaction
            transaction = None
            if i < len(lines):
                next_line = LOG_LINE.match(lines[i].rstrip('\r\n'))
                if next_line:
                    transaction = transaction_of('', next_line.group(4))
            unpaired = min(unpaired, len(running))
            slot = None
            if transaction is not None and unpaired == 0:
                slot = next((slot for slot in running if slot['transaction'] == transaction), None)
                if slot is None and len(running) == 1 and running[0]['transaction'] is None:
                    # A request without lines of a transaction: the 'Response Data:' line is the first to name it
                    slot = running[0]
                    slot['transaction'] = transaction
                    if slot['entry'] is not None:
                        slot['entry']['transaction_id'] = transaction
            elif transaction is None and len(running) == 1:
                slot = running[0]
            elif running:
                # Two 'Response:' lines in a row, or one that stands alone while several requests are running:
                # the status is the status of none of them, and the next 'Response Data:' line is not the line of this one
                unpaired += 1
                mark_unattributed()
            if slot is not None:
                if slot['entry'] is not None:
                    slot['entry']['status_code'] = read_number(message[len('Response: Status Code'):])
                running.remove(slot)
        elif message.startswith('Response Data: '):
            # Reached with its transaction still running only when the 'Response:' line in front of it was not taken as its line
            slot = next((slot for slot in running if slot['transaction'] == transaction_of('', message)), None)
            if slot is not None:
                if slot['entry'] is not None:
                    slot['entry']['_unattributed'] = True
                running.remove(slot)
                unpaired = max(0, unpaired - 1)
        elif logger.startswith(TRANSACTION_LOGGER):
            transaction = logger[len(TRANSACTION_LOGGER):]
            slot = next((slot for slot in running if slot['transaction'] == transaction), None)
            if slot is None:
                slot = next((slot for slot in running if slot['transaction'] is None), None)
                if slot is not None:
                    slot['transaction'] = transaction
                    if slot['entry'] is not None:
                        slot['entry']['transaction_id'] = transaction
                else:
                    # A transaction no request line is waiting for. Either the file starts inside a request,
                    # or two requests wrote their first lines at the same time: then the request that ended
                    # without a transaction is still running, and the lines read for the one after it may be its lines
                    for entry in displaced:
                        entry['_unattributed'] = True
                    del displaced[:]
                    newest = next((slot for slot in reversed(running) if slot['entry'] is not None), None)
                    if newest is not None and newest['displaced']:
                        forget(newest)
                    slot = {'entry': None, 'transaction': transaction, 'model': None, 'start': timestamp,
                            'headers': True, 'data': True, 'displaced': []}
                    running.append(slot)
                    mark_overlap()
            if level == 'ERROR':
                if slot['entry'] is not None:
                    read_error(slot['entry'], message, more_lines)
                # The last line a container writes for a request that failed, there is no 'Response:' line
                if message.startswith('Error occurred in the model scoring function'):
                    running.remove(slot)
        else:
            match_score = SCORE_LINE.match(message)
            if not match_score and level != 'ERROR':
                continue
            # The lines of the score code name no request: they are the lines of the one request that is running
            if len(running) != 1:
                mark_unattributed()
                continue
            entry = running[0]['entry']
            if entry is None:
                continue
            if level == 'ERROR':
                read_error(entry, message, more_lines)
                continue
            key, value = match_score.groups()
            if key in METRIC_COLUMNS:
                entry[METRIC_COLUMNS[key]] = read_number(value)
            elif key == 'answer':
                # An answer can be empty, which leaves the blank in front of the bracket
                match_answer = ANSWER_LINE.fullmatch(value.rstrip())
                if match_answer:
                    entry['answer'] = match_answer.group(1).strip() or None
                    entry['answer_confidence'] = read_number(match_answer.group(2))
                else:
                    entry['answer'] = value.strip() or None
            else:
                # The response of an LLM can span several lines
                entry['response'] = ' '.join([value.strip()] + more_lines)

    # A request failed when the log holds an error for it or no response. For a request that
    # overlapped another one the log can hold a response that could not be given to it: nothing is said then
    overlapped = 0
    unattributed = 0
    for entry in records:
        if entry['status_code'] is not None:
            entry['failed'] = 1 if entry['_errors'] or entry['status_code'] >= 400 else 0
        elif entry['_errors'] or not (entry['_overlapped'] or entry['_unattributed']):
            entry['failed'] = 1
        overlapped += 1 if entry['_overlapped'] or entry['_unattributed'] else 0
        unattributed += 1 if entry['_unattributed'] else 0

    # Requests that were all skipped must not pass as a log without requests
    if not records and unmatched_requests > 0:
        SAS.logMessage(f"No request was read: 'Request: POST' was found in {unmatched_requests} line(s) that do not start as a log line ('<timestamp> - <logger> - <level> - '). The parser needs the lines as the containers write them, with nothing in front.", messageType='WARNING')
    if overlapped > 0:
        SAS.logMessage(f"{overlapped} request(s) were running at the same time as another request of their container. The lines of the score code and the 'Response:' line name no request, so a line that was written while more than one request was running was given to none of them: {unattributed} request(s) were running when that happened and can have missing values.")

    df = pd.DataFrame(records, columns=COLUMNS)
    return df

df = parse_log_file(SAS.symget('_log_path'))

# Ensure the timestamp is suited for SAS
df['timestamp'] = pd.to_datetime(df['timestamp'], format=TIMESTAMP_FORMAT)
sas_epoch = datetime(1960, 1, 1)
df['timestamp'] = (df['timestamp'] - sas_epoch).dt.total_seconds()

# Ensure that numeric values are correct
numeric_columns = ['temperature', 'top_p', 'top_k', 'max_tokens', 'prompt_length', 'output_length', 'runtime', 'status_code', 'failed', 'answer_confidence', 'match_candidates']
for col in numeric_columns:
    df[col] = pd.to_numeric(df[col], errors='coerce')

# Add column labels
labels = {
    'timestamp': 'The datetime of the request',
    'model': 'The name of the model used',
    'system_prompt': 'The system prompt used by the request. For a decision model the questions',
    'user_prompt': 'The user prompt used by the request. For a decision model the state',
    'temperature': 'The setting of temperature. Missing means it was not present in the request',
    'top_p': 'The setting of top p. Missing means it was not present in the request',
    'top_k': 'The setting of top k. Missing means it was not present in the request',
    'max_tokens': 'The setting of max new tokens. Missing means it was not present in the request',
    'prompt_length': 'The amount of input tokens in the request',
    'output_length': 'The amount of output tokens in the request',
    'runtime': 'The time for the model to generate the response in seconds',
    'response': 'The response of the model to the users request. For a decision model the answers',
    'kind': 'The kind of the request: llm, embedding or decision',
    'status_code': 'The HTTP status code of the response. Missing means the log holds no response for the request',
    'failed': '1 if the log holds an error or no response for the request, otherwise 0',
    'error_text': 'The first error the log holds for the request, shortened',
    'transaction_id': 'The transaction ID the container gave the request',
    'request_id': 'The X-Request-Id header of the request',
    'user_agent': 'The kind of client that sent the request: python-requests, browser, SAS or other',
    'embedding_mode': 'The Embedding_Mode option of a call to an embedding model: query or document',
    'project': 'The project input of a call to an embedding model',
    'input_hash': 'The SHA-256 hash of the text an embedding model was given. The text itself is not kept',
    'answer': 'The answer of a decision model to its first question',
    'answer_confidence': 'The confidence of the answer of a decision model to its first question',
    'options': 'The options of the request as they were sent, with keys replaced by REDACTED',
    'rag_collection': 'The collection of the retrieval whose results the user prompt holds',
    'rag_query_hash': 'The SHA-256 hash of the question of the retrieval whose results the user prompt holds',
    'model_id': 'The Model Manager ID of the model that answered. Empty until the logs are linked',
    'caller_model_id': 'The Model Manager ID of the prompt, decision template or RAG setup that sent the request. Empty until the logs are linked',
    'caller_project_id': 'The Model Manager ID of the project of the caller. Empty until the logs are linked',
    'caller_kind': 'The kind of the caller: prompt, decision or rag. Empty until the logs are linked',
    'caller_name': 'The name of the caller. Empty until the logs are linked',
    'experiment_run_id': 'The run of the experiment tracker the request belongs to. Empty until the logs are linked',
    'rag_model_id': 'The Model Manager ID of the RAG setup whose collection the user prompt holds. Empty until the logs are linked',
    'match_method': 'How the caller was found. Empty until the logs are linked',
    'match_candidates': 'The number of callers the request fits. Missing until the logs are linked'
}

# Set the format to display the timestamp correctly
sas_formats = {
    'timestamp': 'DATETIME20.'
}

# The type is stated for every column, so it never depends on what a log happens to
# hold (a log of embedding calls only has no prompt and no response at all, and the
# columns of the link are empty in every log)
vars_metadata = []
for col in df.columns:
    var = {
        'name': col,
        'label': labels.get(col, col),
        'type': 'double' if col == 'timestamp' or col in numeric_columns else 'varchar'
    }
    if col in sas_formats:
        var['format'] = sas_formats[col]
    vars_metadata.append(var)

# Upload to the casuser and then return to SAS. A log without requests has nothing
# to upload and the output table stays as it is.
if len(df) > 0:
    tbl = conn.upload_frame(
        df,
        casout=dict(
            name='_TEMP_LLM_LOGS',
            caslib='CASUSER',
            label='Extracted LLM Log Data',
            replace=True
        ),
        importoptions={'vars': vars_metadata}
    )

SAS.symput('_log_row_count', str(len(df)))
endsubmit;
run; quit;

proc python terminate;
run; quit;

* Append, Replace, Copy and/or promote to CAS;
%macro _log_handle_output_table;
    %if &_log_row_count. GT 0 %then %do;
        %if &_log_append_table. %then %do;
            proc cas;
                table.tableExists result=re /
                    casLib = "&_log_target_table_lib.",
                    name = "&_log_target_table_name.";

                /* A table that was saved but is not in memory, which is how a restart of the CAS server
                   leaves it, is loaded back before the rows are appended. tableExists only sees the
                   tables in memory, so without this a new table would be started and saved over the
                   file that holds the rows of all runs before */
                savedFile = '';
                if re.exists EQ 0 then do;
                    /* The whole caslib is listed, so a file that does not exist is not an error. The file
                       keeps the case the table name was saved with and a CAS table name has none: the
                       exact name is taken when there is one, otherwise the same name in another case */
                    table.fileInfo result=listing status=listed /
                        casLib = "&_log_target_table_lib.";
                    if listed.severity EQ 0 then do;
                        if exists(listing, 'FileInfo') then do;
                            do savedEntry over listing.FileInfo;
                                if savedEntry.Name EQ "&_log_target_table_name..sashdat" then savedFile = savedEntry.Name;
                                else if savedFile EQ '' and upcase(savedEntry.Name) EQ upcase("&_log_target_table_name..sashdat") then savedFile = savedEntry.Name;
                            end;
                        end;
                    end;
                    if savedFile NE '' then do;
                        table.loadTable /
                            casLib = "&_log_target_table_lib.",
                            path = savedFile,
                            casOut = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."};
                        table.tableExists result=re /
                            casLib = "&_log_target_table_lib.",
                            name = "&_log_target_table_name.";
                    end;
                end;

                /* What the statements below go by: the table the rows are appended to (0 there is none,
                   1 a table of this session, 2 a promoted table) and a saved table that could not be loaded */
                if re.exists GE 2 then do;
                    call symputx('_log_target_scope', '2');
                end;
                else if re.exists EQ 1 then do;
                    call symputx('_log_target_scope', '1');
                end;
                else if savedFile NE '' then do;
                    call symputx('_log_target_unloaded', '1');
                end;

                /* The table of an earlier run that did not get to its end must not pass for the one of this run */
                table.dropTable /
                    casLib = "&_log_target_table_lib.",
                    name = '_TEMP_LLM_LOGS_ALL',
                    quiet = True;
            run; quit;

            %if &_log_target_scope. GE 1 %then %do;
                /* A column can not be CHAR in one table of a SET statement and VARCHAR in the other. The rows
                   of the log hold their text as VARCHAR, so the existing table is asked for its columns, and
                   each text column of the parser that it holds as CHAR is read under another name and handed
                   over to the VARCHAR column in the DATA step below */
                %if %sysfunc(exist(work._log_target_columns)) %then %do;
                    proc delete data=work._log_target_columns;
                    run;
                %end;
                proc cas;
                    table.columnInfo result=targetColumns /
                        table = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."};
                    saveresult targetColumns dataout=work._log_target_columns;
                quit;
                %if %sysfunc(exist(work._log_target_columns)) %then %do;
                    data _null_;
                        set work._log_target_columns end=lastColumn;
                        length fixedRename fixedAssign fixedDrop $ 4000;
                        retain fixedRename fixedAssign fixedDrop '';
                        if lowcase(strip(Type)) EQ 'char' and lowcase(strip(Column)) in (
                            'model' 'system_prompt' 'user_prompt' 'response' 'kind' 'error_text' 'transaction_id' 'request_id' 'user_agent'
                            'embedding_mode' 'project' 'input_hash' 'answer' 'options' 'rag_collection' 'rag_query_hash'
                            'model_id' 'caller_model_id' 'caller_project_id' 'caller_kind' 'caller_name' 'experiment_run_id' 'rag_model_id' 'match_method'
                        ) then do;
                            fixedRename = catx(' ', fixedRename, cats(Column, '=_fx_', Column));
                            fixedAssign = catx(' ', fixedAssign, cats(Column, '=trimn(_fx_', Column, ');'));
                            fixedDrop = catx(' ', fixedDrop, cats('_fx_', Column));
                        end;
                        if lastColumn and fixedRename NE '' then do;
                            call symputx('_log_fixed_rename', cats('rename=(', fixedRename, ')'));
                            call symputx('_log_fixed_assign', catx(' ', 'if _log_existing_row then do;', fixedAssign, 'end;'));
                            call symputx('_log_fixed_drop', catx(' ', 'drop', fixedDrop, ';'));
                        end;
                    run;
                    proc delete data=work._log_target_columns;
                    run;
                %end;

                /* The existing rows and the new ones become one table beside the existing one. The LENGTH
                   statement names the columns of the parser in their order and with their types, so they
                   stand in front, a text column is a VARCHAR whatever length it has in the existing table,
                   and a column the existing table does not have yet is added with missing values in its
                   rows. A column only the existing table has follows behind them and is missing in the new rows */
                libname _llmtgt cas caslib="&_log_target_table_lib.";
                libname _llmsrc cas caslib='CASUSER';
                data _llmtgt._TEMP_LLM_LOGS_ALL;
                    length timestamp 8 model varchar(*) system_prompt varchar(*) user_prompt varchar(*)
                           temperature 8 top_p 8 top_k 8 max_tokens 8 prompt_length 8 output_length 8 runtime 8 response varchar(*)
                           kind varchar(*) status_code 8 failed 8 error_text varchar(*) transaction_id varchar(*) request_id varchar(*) user_agent varchar(*)
                           embedding_mode varchar(*) project varchar(*) input_hash varchar(*) answer varchar(*) answer_confidence 8 options varchar(*)
                           rag_collection varchar(*) rag_query_hash varchar(*)
                           model_id varchar(*) caller_model_id varchar(*) caller_project_id varchar(*) caller_kind varchar(*) caller_name varchar(*)
                           experiment_run_id varchar(*) rag_model_id varchar(*) match_method varchar(*) match_candidates 8;
                    set _llmtgt."&_log_target_table_name."n(&_log_fixed_rename. in=_log_existing_row) _llmsrc._TEMP_LLM_LOGS;
                    &_log_fixed_assign.
                    &_log_fixed_drop.
                run;
                libname _llmtgt clear;
                libname _llmsrc clear;
            %end;
        %end;

        proc cas;
            %if &_log_target_unloaded. %then %do;
                /* Starting a new table here would end in a save over the file that could not be loaded */
                print (error) "The saved table &_log_target_table_lib..&_log_target_table_name. could not be loaded, nothing was appended and the saved file was left as it is.";
                tableWritten = False;
            %end;
            %else %do;
                /* The new table is built beside the existing one, which is only dropped once its replacement exists */
                %if &_log_target_scope. GE 1 %then %do;
                    stagedTable = '_TEMP_LLM_LOGS_ALL';
                %end;
                %else %do;
                    stagedTable = "&_log_target_table_name._NEW";
                    table.copyTable /
                        table = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'},
                        casOut = {casLib = "&_log_target_table_lib.", name = stagedTable, replace = True};
                %end;
                table.tableExists result=staged /
                    casLib = "&_log_target_table_lib.",
                    name = stagedTable;

                if staged.exists GE 1 then do;
                    /* Dropped twice, as a session table of that name hides a promoted one */
                    table.dropTable /
                        casLib = "&_log_target_table_lib.",
                        name = "&_log_target_table_name.",
                        quiet = True;
                    table.dropTable /
                        casLib = "&_log_target_table_lib.",
                        name = "&_log_target_table_name.",
                        quiet = True;
                    table.alterTable /
                        casLib = "&_log_target_table_lib.",
                        name = stagedTable,
                        rename = "&_log_target_table_name.";

                    /* A table that was promoted when the rows were appended to it is promoted again */
                    %if &_log_promote_table. or &_log_target_scope. EQ 2 %then %do;
                        table.promote /
                            casLib = "&_log_target_table_lib.",
                            name = "&_log_target_table_name.",
                            targetLib = "&_log_target_table_lib.",
                            target = "&_log_target_table_name.",
                            quiet = True;
                    %end;
                    tableWritten = True;
                end;
                else do;
                    print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is.";
                    tableWritten = False;
                end;
            %end;

            %if &_log_save_table. %then %do;
                if tableWritten then do;
                    table.save /
                        table = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."},
                        casLib = "&_log_target_table_lib.",
                        name = "&_log_target_table_name.",
                        replace = True;
                end;
            %end;
        run; quit;
    %end;
    %else %if &_log_row_count. EQ 0 %then %do;
        %put NOTE: The log contains no requests, the table &_log_target_table_lib..&_log_target_table_name. was left as it is.;
    %end;
    %else %do;
        %put ERROR: The log could not be parsed, the table &_log_target_table_lib..&_log_target_table_name. was left as it is.;
    %end;

    proc cas;
        table.dropTable /
                casLib = 'CASUSER',
                table = '_TEMP_LLM_LOGS',
                quiet = True;
    run; quit;
%mend _log_handle_output_table;

%_log_handle_output_table;

* Clean up;
%if &_log_clean_cas_session. %then %do;
    cas llmLogMySess terminate;
%end;
%sysmacdelete _log_handle_output_table;
%symdel _log_clean_cas_session _casSessionUUID _log_row_count _log_target_scope _log_target_unloaded _log_fixed_rename _log_fixed_assign _log_fixed_drop;
%symdel _log_path _log_target_table_engine _log_target_table_lib _log_target_table_name _log_append_table _log_promote_table _log_save_table;
