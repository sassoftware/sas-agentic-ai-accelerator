/********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0
********************************************************************************/
* A CAS session for the librefs below. A session that is connected is used as it is: starting mysess a second time in one SAS session makes SAS warn that the session exists. The session stays when the program ends, and so do the librefs;
%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    cas mysess;
%end;
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
* 1 until one of the two checks below refuses the run. A refusal writes its one ERROR line and sets this to 0: no log is read and nothing is written behind it. An ABORT statement put SAS into syntax check mode instead, where the program went on and wrote a further ERROR line about a log it had not read;
%let _log_ready = 1;
* 1 once the program has written an ERROR line for a table it did not write: the saved table that could not be loaded, the new table that could not be created. The last statement of the program reads it;
%let _log_failed = 0;
data _null_;
   locationType = scan("&_log_path.", 1, ':');
   if lowCase(locationType) ne 'sasserver' then do;
      putLog 'ERROR: For this step to run please ensure the the selected file is located on the SAS Server.';
      call symputx('_log_ready', '0');
   end;
   else do;
        call symputx('_log_path', scan("&_log_path.", 2, ':', 'MO'));
   end;
run;

* Ensure that the output table is in CAS;
%if &_log_target_table_engine. NE CAS %then %do;
    %put ERROR: The table &_log_target_table_lib..&_log_target_table_name. is not a CAS table. For this step to run the target table needs to be in CAS, no log was read.;
    %let _log_ready = 0;
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
* The return code of the DATA step of append mode (SYSERR). The output macro sets it behind that step, and the new table only takes the place of the existing one when it is 0 or the 4 of a warning;
%let _log_append_syserr = 0;

proc python restart;
submit;
import re
import os
import ast
import json
import bisect
import hashlib
from datetime import datetime

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


def say(message, message_type='NOTE'):
    """Writes one line to the SAS log. Every message of this program takes this way."""
    SAS.logMessage(sas_safe(message), messageType=message_type)


def put_number(name, value):
    """Hands a whole number to a macro variable. Nothing but whole numbers is handed to SAS that way."""
    SAS.symput(sas_safe(name), sas_safe(str(int(value))))


try:
    import swat
    import pandas as pd
except ImportError:
    say('Ensure that the SWAT and Pandas package are installed', 'ERROR')

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
# A file holds the lines of one container after the lines of another, and one collection after
# another (kubectl logs per pod, appended). Where one part ends and the next begins the time goes
# back. Inside a part it can go back as well: a line is stamped when it is made and written a
# moment later, so a line can carry the second before the line in front of it. A line that is
# older by more seconds than this is the first line of the next part.
STEP_BACK = 1

# The container gives every request a transaction and logs under 'transaction_<id>' what
# it does with it. The 'Response Data:' line names the same id.
TRANSACTION_LOGGER = 'transaction_'
RESPONSE_TRANSACTION = re.compile(r"'transaction_id': '([^']+)'")
# The first line of a transaction and the line that follows the score code. Both name the model
FIRST_TRANSACTION_LINE = 'Score request received from POST'
SCORED_LINE = re.compile(r'scoreModel function from the (\S+)\.py file')
# The first lines a container writes for a request behind its request line, in their order.
# The two 'Randomly generated' lines count where a log holds them.
HEAD_LINES = ['Request Headers: ', 'Request Data: ']
OPTIONAL_HEAD_LINES = ['Randomly generated client_id is', 'Randomly generated transaction_id is']
# The headline of a decision as the score code logs it: '<answer> (<confidence>)'
ANSWER_LINE = re.compile(r'(.*) \(([^()\s]+)\)', re.DOTALL)

# An option whose name says that it holds a secret, and the shapes a secret has as a value
KEY_NAME = re.compile(r'key|secret|password|passwd|passphrase|pwd|credential|authorization|bearer|cookie', re.IGNORECASE)
# The short names of a password say it as a word of the name only (my_pass, userPass, pw - not bypass)
NAME_WORD = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+')
KEY_WORDS = ('pass', 'pw')
KEY_SHAPE = re.compile(r'(?:bearer|basic)\s+\S|(?:sk|pk|rk)-\S|eyJ[\w-]{8,}\.', re.IGNORECASE)
KEY_IN_TEXT = re.compile(r'\bbearer\s+(?=[\w.~+/=*-]*\d)[\w.~+/=*-]{8,}|\b(?:sk|pk|rk)-[\w*.-]{6,}|\beyJ[\w-]{8,}\.[\w.-]+', re.IGNORECASE)
LONG_RUN = re.compile(r'[A-Za-z0-9]{20,}')
# A secret inside the value of an option: the user and the password of an address, a header
# that carries one with everything behind its name, and what follows bearer or basic
URL_CREDENTIALS = re.compile(r'(?<=://)[^\s/@:]*:[^\s/@]*@')
HEADER_IN_VALUE = re.compile(r'((?:proxy-)?authorization|(?:set-)?cookie|x-api-key|api[-_]?key)(\s*[:=]\s*)\S.*', re.IGNORECASE | re.DOTALL)
BEARER_IN_VALUE = re.compile(r'\b(?:bearer|basic)\s+\S+', re.IGNORECASE)
REDACTED = 'REDACTED'
ERROR_TEXT_LENGTH = 500

# The results of a retrieval as the RAG runtime writes them into a prompt: one JSON object
# with these keys (retrieve_context.py). A prompt is searched for it from a limited number
# of opening braces, and the search ends when it has decoded a limited number of characters.
ENVELOPE_KEYS = ('query', 'hits', 'graph_context', 'retrieval_mode', 'collection', 'ingestion_run_id')
ENVELOPE_STARTS = 500
ENVELOPE_CHARACTERS = 2000000

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
    A character that has no UTF-8 bytes - half of a surrogate pair in a request body -
    is hashed as a question mark, as those programs do.
    """
    return hashlib.sha256(' '.join(text.split()).encode('utf-8', errors='replace')).hexdigest()

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
    if any(word.lower() in KEY_WORDS for word in NAME_WORD.findall(name)):
        return True
    return 'token' in name.lower() and read_number(str(value).strip().strip('"\'')) is None

def closing_of(text):
    """
    Returns what closes a list of options at the end of a value: its brackets and the blanks around them.
    """
    return text[len(text.rstrip('}] \t\r\n')):]

def redact_embedded(text):
    """
    Returns a value with the secrets replaced that stand inside it, and whether what was
    replaced runs to the end of the value: the user and the password of an address
    (scheme://user:password@host), a header that carries a secret with all that follows
    its name, and what follows bearer or basic.
    """
    text = URL_CREDENTIALS.sub(REDACTED + '@', text)
    to_the_end = HEADER_IN_VALUE.search(text) is not None
    text = HEADER_IN_VALUE.sub(lambda header: header.group(1) + header.group(2) + REDACTED + closing_of(header.group()), text)
    return BEARER_IN_VALUE.sub(REDACTED, text), to_the_end

def redact_value(value):
    """
    Returns options that were sent as JSON with every secret replaced.
    """
    if isinstance(value, dict):
        return {name: REDACTED if is_key_name(str(name), item) else redact_value(item) for name, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return REDACTED if looks_like_a_key(value) else redact_embedded(value)[0]
    return value

def read_options(options_str):
    """
    Returns the options of a request by their names.

    The models take them as '{name:value,name:value}' or as JSON, and read them the
    same way: JSON first, otherwise every part between two commas that holds a colon.
    Nested deeper than Python decodes, the options are read the second way.
    """
    text = options_str.strip()
    try:
        decoded = json.loads(text)
        if isinstance(decoded, dict):
            return {str(name): value if isinstance(value, str) else json.dumps(value) for name, value in decoded.items()}
    except (ValueError, RecursionError):
        pass
    if text.startswith('{') and text.endswith('}'):
        text = text[1:-1]  # Remove surrounding {}
    named = {}
    for opt in text.split(','):
        if ':' in opt:
            key, val = opt.split(':', 1)
            named[key.strip().strip('"\'')] = val.strip().strip('"\'')
    return named

def redact_part(part):
    """
    Returns one 'name:value' of the options with its secret replaced, and whether the
    value was replaced to its end - what follows up to the next name is then the rest
    of the secret, which held a comma.

    The value of an option can be a list of options of its own ('client:{id:1,secret:x}').
    The name that decides is the one in front of the value, and the brackets that close
    such a list stay where they were.
    """
    start = 0
    colon = part.find(':')
    while True:
        # The value behind this colon opens a list that holds a name of its own: that name is the next one
        opened = colon + 1
        while opened < len(part) and part[opened].isspace():
            opened += 1
        inner = part.find(':', opened + 1) if part[opened:opened + 1] in ('{', '[') else -1
        if inner == -1:
            break
        start, colon = opened + 1, inner
    front, name, value = part[:start], part[start:colon], part[colon + 1:]
    closing = closing_of(value)
    plain = value[:len(value) - len(closing)]
    if is_key_name(name.strip().lstrip('{['), plain) or looks_like_a_key(plain):
        return front + name + ':' + REDACTED + closing, True
    plain, to_the_end = redact_embedded(plain)
    return front + name + ':' + plain + closing, to_the_end

def redact_options(options_str):
    """
    Returns the options as they were sent, with the value of every option that is a
    secret by its name (API_KEY, ...) or by its shape replaced by REDACTED, and with
    the secrets replaced that stand inside a value.

    The collection script does this for API_KEY before the log is written. The parser
    does it again, so a log that was collected another way brings no key into the table.
    """
    text = options_str.strip()
    try:
        decoded = json.loads(text)
        if isinstance(decoded, dict):
            redacted = redact_value(decoded)
            return text if redacted == decoded else json.dumps(redacted, ensure_ascii=False)
    except (ValueError, RecursionError):
        # No JSON, or JSON that is nested deeper than Python decodes: read as names and values
        pass

    opening, closing = '', ''
    if text.startswith('{') and text.endswith('}'):
        opening, text, closing = '{', text[1:-1], '}'
    parts = []
    hidden = False
    for part in text.split(','):
        if ':' not in part:
            # A part without a colon is the rest of the value in front of it, which held a comma
            if hidden and parts:
                parts[-1] += closing_of(part).strip()
            elif not hidden:
                closers = closing_of(part)
                parts.append(REDACTED + closers if looks_like_a_key(part[:len(part) - len(closers)]) else part)
        else:
            redacted, hidden = redact_part(part)
            parts.append(redacted)
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

def is_envelope(value):
    """
    Returns whether a decoded JSON value is the envelope of a retrieval: an object with every
    key the RAG runtime writes, a collection, a question that is a text and a list of hits.
    """
    return (isinstance(value, dict) and all(key in value for key in ENVELOPE_KEYS)
            and isinstance(value['collection'], str) and value['collection'].strip() != ''
            and isinstance(value['query'], str) and isinstance(value['hits'], list))

def envelope_in(value):
    """
    Returns the last envelope of a retrieval in a decoded JSON value, None if there is none.
    """
    found = None
    waiting = [value]
    while waiting:
        item = waiting.pop()
        if is_envelope(item):
            found = item
        elif isinstance(item, dict):
            waiting.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            waiting.extend(reversed(item))
    return found

def find_retrieval_envelope(text):
    """
    Returns the envelope of a retrieval that a prompt holds, None if it holds none.

    The RAG runtime hands the results of a retrieval over as one JSON object that names
    the collection and the question ('query') beside the hits, and a prompt carries that
    object somewhere in its text. It is decoded as JSON from a '{' on, so a question
    with quotes, braces or line breaks in it is read as it was written.

    Only an object with all the keys of the runtime counts, and of several the last one:
    what a user typed in front of the retrieval of a prompt does not set the columns. The
    search tries ENVELOPE_STARTS opening braces and decodes ENVELOPE_CHARACTERS characters
    at most. A prompt that needs more keeps the envelope found by then, which can be none.
    """
    if any('"' + key + '"' not in text for key in ENVELOPE_KEYS):
        return None
    decoder = json.JSONDecoder()
    found = None
    starts = 0
    decoded = 0
    position = text.find('{')
    while position != -1 and starts < ENVELOPE_STARTS and decoded < ENVELOPE_CHARACTERS:
        starts += 1
        try:
            value, end = decoder.raw_decode(text, position)
        except ValueError as error:
            # Counted up to where the decoding gave up
            decoded += max(1, getattr(error, 'pos', position) - position)
            position = text.find('{', position + 1)
            continue
        except RecursionError:
            decoded += len(text) - position
            position = text.find('{', position + 1)
            continue
        decoded += end - position
        envelope = envelope_in(value)
        if envelope is not None:
            found = envelope
        position = text.find('{', end)
    return found

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
    (for example because it was cut off, or because it is nested deeper than Python
    reads) leaves the entry as it is, and an input that can not be read leaves the other
    inputs of the request as they were read. Both mark the entry as one that holds a
    value that could not be read.

    Args:
        entry (dict): The entry of the request the line belongs to.
        request_data_str (str): The text after 'Request Data: '.
    """
    try:
        request_data = ast.literal_eval(request_data_str.strip())
    except Exception:
        entry['_unreadable'] = True
        return
    if not isinstance(request_data, dict) or not isinstance(request_data.get('inputs'), list):
        entry['_unreadable'] = True
        return

    input_names = set()
    for item in request_data['inputs']:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            continue
        name = item['name']
        input_names.add(name)
        if item.get('value') is None:
            continue
        try:
            read_input(entry, name, str(item['value']))
        except Exception:
            entry['_unreadable'] = True
    entry['kind'] = kind_of(input_names)

def read_input(entry, name, value):
    """
    Adds one input of a request to the entry of its request.
    """
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

def model_named(message):
    """
    Returns the model a line of a transaction names, None for a line that names none.
    """
    if message.startswith(FIRST_TRANSACTION_LINE):
        return message[len(FIRST_TRANSACTION_LINE):].strip().strip('/') or None
    match_model = SCORED_LINE.search(message)
    return match_model.group(1) if match_model else None

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
    line of its transaction. A request that has neither ends at the next request line that no
    line of its transaction follows. Lines between two blocks belong to no request and are
    skipped.

    The lines of a container stand together in the file, one container after the other and one
    collection after the other, and where a part ends the time goes back. A line that is more
    than STEP_BACK seconds older than a request that is running or waiting starts the next
    part: the log ends there for these requests, without a response and without an error, so
    failed stays missing for them - as it does for a request the file ends on. A transaction
    of theirs that a later line still names is followed to its end and given nothing more.
    A line that is older by STEP_BACK seconds at most can be a line of the same container that
    was written late. It is the line of its transaction when it names one of this part; any
    other such line is given to no request, and the requests that are running or waiting are
    counted with the ones the log does not decide.

    Only the lines of a transaction and the 'Response Data:' line name the request they belong
    to. Every other line is given to a request where the lines around it leave one request it
    can be the line of, and to none otherwise:

    - The first lines of a request (headers, request data, the two 'Randomly generated' lines)
      are the lines of the request whose request line stands in front of them, as long as no
      other request line stands in between. A first line that no request is waiting for shows
      that requests wrote their first lines at the same time: every request since the one whose
      first lines broke off keeps its request line only.
    - The first line of a transaction belongs to the one request of its model that has all its
      first lines and no transaction yet. Where two such requests are waiting, the transaction
      is the transaction of neither and both keep what their first lines say.
    - The lines of the score code and the 'Response: Status Code' line are the lines of the one
      transaction that is running. While a second one is running - of the same container or of
      another one whose lines are mixed in - such a line is given to neither.
    - The lines of a response up to the next line of its transaction are text, also where one of
      them starts like a log line. A line that starts a request there is taken as one when a
      'Request Headers:' line follows it.

    Args:
        file_path (str): Path to the log file to parse.

    Returns:
        pandas.DataFrame: A DataFrame containing one row per request with the columns in COLUMNS.
    """
    # A byte that is not UTF-8 is replaced, so one damaged line does not cost the whole file
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    # The log lines of the file as (first line, line behind its last, timestamp, logger, level, message).
    # A message can span several lines (the request headers, a response, a traceback), it ends at the next line that starts with a timestamp
    units = []
    unmatched_requests = 0
    i = 0
    while i < len(lines):
        match_line = LOG_LINE.match(lines[i].rstrip('\r\n'))
        if not match_line:
            # A request in a line that does not start as a log line, for example behind a prefix of the log pipeline
            if 'Request: POST' in lines[i]:
                unmatched_requests += 1
            i += 1
            continue
        first = i
        i += 1
        while i < len(lines) and not LOG_TIMESTAMP.match(lines[i]):
            i += 1
        units.append((first, i) + match_line.groups())

    # What is known of the file before a line is given to a request: the lines that name each transaction,
    # the lines that start one, and which first lines the containers of this file write
    named = {}
    opened = {}
    names_transactions = False
    written = set()
    for index, unit in enumerate(units):
        logger, message = unit[3], unit[5]
        transaction = transaction_of(logger, message)
        if transaction is not None:
            named.setdefault(transaction, []).append(index)
            if logger.startswith(TRANSACTION_LOGGER):
                names_transactions = True
                if message.startswith(FIRST_TRANSACTION_LINE):
                    opened.setdefault(transaction, []).append(index)
        else:
            written.update(start for start in OPTIONAL_HEAD_LINES if message.startswith(start))
    head_lines = HEAD_LINES + [start for start in OPTIONAL_HEAD_LINES if start in written]
    complete = len(head_lines)

    def last_naming(transaction, index):
        # The last line that names the transaction of the request that holds it at this line. A client can give
        # its requests an id of its own, so the next 'Score request received' line of the id is another request
        starts = opened.get(transaction, [])
        position = bisect.bisect_right(starts, index)
        limit = starts[position] if position < len(starts) else len(units)
        names = named.get(transaction, [])
        position = bisect.bisect_left(names, limit) - 1
        return max(index, names[position]) if position >= 0 else index

    records = []
    # The transactions that are running, oldest first. One of a request holds its entry. One that no
    # request was waiting for holds none: its lines are followed to know when it ends, and nothing is kept of it
    running = []
    # The requests whose first lines are read and that have no transaction yet
    pending = []
    # The entries of the requests whose first lines broke off at the request line of another request, and of every request since
    stopped = []
    doubted = []
    # Of the requests whose first lines were not all there when they broke off: the entry, and the entry of the request at whose request line they did
    broken = []
    # By model, the requests that are waiting for a transaction and can not be told apart any more: the ones that
    # have all their first lines (waiting) and the ones whose first lines are not known to be theirs (blurred)
    waiting = {}
    blurred = {}
    # The transactions a line has named so far, and the ones a line of this part of the file has named
    started = set()
    here = set()
    # The 'Response:' lines that could not be given to a request and whose 'Response Data:' line is still to come,
    # and the time of the newest request that can not be told apart
    state = {'unpaired': 0, 'newest': ''}

    def flag(entry):
        if entry is not None:
            entry['_unattributed'] = True

    def seconds_back(older, newer):
        # The seconds a time lies before another one. A time that is no date lies further back than any
        try:
            return (datetime.strptime(newer, TIMESTAMP_FORMAT) - datetime.strptime(older, TIMESTAMP_FORMAT)).total_seconds()
        except ValueError:
            return float('inf')

    def end_of_the_log():
        # The lines of a container end here - at the end of its part or of the file - while these requests are
        # running or waiting: the log holds neither a response nor an error for them, and does not say that they failed
        cut = set()
        for entry in [slot['entry'] for slot in running] + [head['entry'] for head in pending]:
            if entry is not None:
                entry['_cut'] = True
                cut.add(id(entry))
        # A request whose first lines broke off at the request line of one of these: the two wrote their first lines at
        # the same time, and the lines that would have shown it - the rest of the first lines - are not in this part
        for entry, behind in reversed(broken):
            if behind is not None and id(behind) in cut:
                entry['_cut'] = True
                cut.add(id(entry))
        del broken[:]

    def wipe(entry):
        # What was read for this request may be the line of another one: only its request line is kept
        for column in COLUMNS:
            if column not in ('timestamp', 'model'):
                entry[column] = None
        entry['_errors'] = False
        entry['_unattributed'] = True

    def guarded(read, entry, *arguments):
        # One request whose content can not be read never stops the file: the row is kept with what was read
        try:
            read(entry, *arguments)
        except Exception:
            entry['_unreadable'] = True

    def anonymous(counts, model=None):
        return sum(counts.values()) if model is None else counts.get(model, 0)

    def make_anonymous(counts, head):
        counts[head['model']] = counts.get(head['model'], 0) + 1
        state['newest'] = max(state['newest'], head['start'])

    def next_part(position):
        end_of_the_log()
        # A transaction that a later line still names has not ended with its part: the file holds its rest further
        # down - behind an overlap of two collections, or because the lines of several containers are mixed. It is
        # followed to its end and given nothing more, so that a line that names no request is not taken for the
        # line of the one other request that is running then
        carried = [slot for slot in running if slot['last'] >= position]
        for slot in carried:
            slot['entry'] = None
            slot['carried'] = True
        running[:] = carried
        del pending[:]
        del stopped[:]
        del doubted[:]
        waiting.clear()
        blurred.clear()
        state.update(unpaired=0, newest='')
        here.clear()
        here.update(slot['transaction'] for slot in carried)

    def mark_overlap():
        entries = [slot['entry'] for slot in running] + [head['entry'] for head in pending if head['scoring']]
        if len(entries) + anonymous(waiting) + anonymous(blurred) > 1:
            for entry in entries:
                if entry is not None:
                    entry['_overlapped'] = True

    def mark_unattributed():
        for slot in running:
            flag(slot['entry'])

    def retract():
        # A first line that no request is waiting for: a request whose first lines broke off was not over, and its
        # lines and the lines of the requests since can have been read as each other's. They keep their request line
        in_doubt = set(id(entry) for entry in doubted)
        for entry in stopped + doubted:
            wipe(entry)
        for slot in running:
            if slot['entry'] is not None and id(slot['entry']) in in_doubt:
                slot['entry'] = None
        for head in list(pending):
            if not head['scoring']:
                pending.remove(head)
            elif id(head['entry']) in in_doubt:
                pending.remove(head)
                make_anonymous(blurred, head)
        for entry in stopped:
            make_anonymous(blurred, {'model': entry['model'], 'start': entry['timestamp']})
        del stopped[:]
        del doubted[:]

    def take_transaction(model, bindable):
        # Returns the entry of the request a transaction that starts here belongs to, None where the log does not decide it
        heads = [head for head in pending if head['scoring'] and (model is None or head['model'] in (None, model))]
        ready = [head for head in heads if head['stage'] >= complete]
        if not ready and len(heads) == 1:
            # A container that does not write every first line: the one request of the model is the one
            ready = heads
        if bindable and len(ready) == 1 and anonymous(waiting, model) + anonymous(blurred, model) == 0:
            pending.remove(ready[0])
            return ready[0]['entry']
        for head in heads:
            flag(head['entry'])
        for head in ready:
            pending.remove(head)
            make_anonymous(waiting, head)
        # One of the requests that can not be told apart has its transaction now
        for counts in (waiting, blurred):
            for key in ([model] if model is not None else list(counts)):
                if counts.get(key, 0) > 0:
                    counts[key] -= 1
                    return None
        return None

    def starts_a_request(position, closes):
        # Whether a line that reads as a request line inside a response is one: its 'Request Headers:' line follows it.
        # Where the line of the transaction or the end of the file follows, nothing says that it is none
        while True:
            position += 1
            if position >= closes or position >= len(units):
                return True
            if units[position][5].startswith(HEAD_LINES[0]):
                return True
            if not units[position][5].startswith('Request: '):
                return False

    index = 0
    while index < len(units):
        first, behind, timestamp, logger, level, message = units[index]
        more_lines = [line.strip() for line in lines[first + 1:behind]]
        this = index
        index += 1

        # A line that is older than a request that is running or waiting. By more than a line of one container is
        # written late: the next part of the file begins. By less: a line of this part that was written late, or the
        # first line of the next part. A line that names a transaction of this part is the line of that transaction.
        # Of any other line the log does not say which of the two it is
        newest = [slot['start'] for slot in running if not slot['carried']] + [head['start'] for head in pending]
        if anonymous(waiting) + anonymous(blurred) > 0:
            newest.append(state['newest'])
        late = False
        if newest and timestamp < max(newest):
            if seconds_back(timestamp, max(newest)) > STEP_BACK:
                next_part(this)
            else:
                late = transaction_of(logger, message) not in here

        kind = next((position for position, start in enumerate(head_lines) if message.startswith(start)), None)

        if late:
            # The requests that are running or waiting can have written the line, and they can be over where it
            # stands: they are counted with the requests the log does not decide. A request line and the first lines
            # of a request are read by their order, as they are tied to the request line in front of them, and so is
            # the line that starts a transaction. Where a part begins in the middle of a request it begins with the
            # lines of the score code, the 'Response:' line or a later line of a transaction: such a line is given
            # to no request, and such a transaction is followed and bound to none
            for slot in running:
                flag(slot['entry'])
            for head in pending:
                flag(head['entry'])
            if kind is None and not message.startswith('Request: ') and not logger.startswith(TRANSACTION_LOGGER):
                continue

        if message.startswith('Request: '):
            scoring_request = message.startswith('Request: POST')
            model = (message[len('Request: POST'):].strip().strip('/') or None) if scoring_request else None
            # A request ends here unless a later line names its transaction. Where the next such line is older than
            # this one by more than a line is written late, it stands in another part of the file: the lines of the
            # container ended in this part while the request was running, which is all the log says of it
            for slot in list(running):
                if slot['last'] <= this:
                    running.remove(slot)
                    continue
                names = named[slot['transaction']]
                following = max(units[position][2] for position in names[bisect.bisect_right(names, this):bisect.bisect_right(names, slot['last'])])
                if following < timestamp and seconds_back(following, timestamp) > STEP_BACK:
                    if slot['entry'] is not None:
                        slot['entry']['_cut'] = True
                    running.remove(slot)
            # First lines that are not all there have broken off. A request that has them all is waiting for its
            # transaction - unless the log names no transaction at all and is read by the order of its lines
            broke_off = []
            for head in list(pending):
                if not head['scoring']:
                    pending.remove(head)
                elif head['stage'] < complete or not names_transactions:
                    pending.remove(head)
                    stopped.append(head['entry'])
                    if head['stage'] < complete:
                        broke_off.append(head['entry'])
            entry = None
            if scoring_request:
                entry = dict.fromkeys(COLUMNS)
                entry['timestamp'] = timestamp
                entry['model'] = model
                entry.update(_errors=False, _overlapped=False, _unattributed=False, _unreadable=False, _cut=False)
                records.append(entry)
                if stopped:
                    doubted.append(entry)
            broken.extend((earlier, entry) for earlier in broke_off)
            pending.append({'entry': entry, 'model': model, 'start': timestamp, 'stage': 0,
                            'needs': complete if scoring_request else 1, 'scoring': scoring_request})
            mark_overlap()
        elif kind is not None:
            # The first lines of a request name nothing. They belong to the request whose first lines have got this far
            head = next((head for head in pending if head['stage'] == kind and kind < head['needs']), None)
            if head is not None and anonymous(blurred) == 0:
                head['stage'] += 1
                if head['entry'] is not None and kind == 0:
                    guarded(read_request_headers, head['entry'], [message[len(head_lines[0]):]] + more_lines)
                elif head['entry'] is not None and kind == 1:
                    guarded(read_request_data, head['entry'], message[len(head_lines[1]):])
                if not head['scoring'] and head['stage'] >= head['needs']:
                    pending.remove(head)
            elif head is not None:
                # A request whose first lines are not known to be its own can have written this line as well
                pending.remove(head)
                if head['scoring']:
                    wipe(head['entry'])
                    make_anonymous(blurred, head)
            elif anonymous(blurred) == 0 and stopped:
                retract()
        elif message.startswith('Response: Status Code'):
            # The line names no request, the 'Response Data:' line behind it names the transaction
            transaction = transaction_of('', units[index][5]) if index < len(units) else None
            state['unpaired'] = min(state['unpaired'], len(running))
            slot = None
            # In a log that names no transaction but in this line: the one request whose first lines were read
            heads = [head for head in pending if head['scoring']]
            head = None
            if len(heads) == 1 and not running and not names_transactions and anonymous(waiting) + anonymous(blurred) == 0:
                head = heads[0]
            if transaction is not None and state['unpaired'] == 0:
                slot = next((slot for slot in running if slot['transaction'] == transaction), None)
                if slot is not None or transaction in started:
                    head = None
            elif transaction is None and len(running) == 1:
                slot, head = running[0], None
            elif transaction is None and not running:
                pass
            else:
                head = None
                if running:
                    # Two 'Response:' lines in a row, or one that stands alone while several requests are running:
                    # the status is the status of none of them, and the next 'Response Data:' line is not the line of this one
                    state['unpaired'] += 1
                    mark_unattributed()
            if slot is not None:
                if slot['entry'] is not None:
                    slot['entry']['status_code'] = read_number(message[len('Response: Status Code'):])
                running.remove(slot)
            elif head is not None:
                # The 'Response Data:' line is the first to name its transaction
                head['entry']['status_code'] = read_number(message[len('Response: Status Code'):])
                if transaction is not None:
                    head['entry']['transaction_id'] = transaction
                    started.add(transaction)
                    here.add(transaction)
                pending.remove(head)
        elif message.startswith('Response Data: '):
            # Reached with its transaction still running only when the 'Response:' line in front of it was not taken as its line
            slot = next((slot for slot in running if slot['transaction'] == transaction_of('', message)), None)
            if slot is not None:
                flag(slot['entry'])
                running.remove(slot)
                state['unpaired'] = max(0, state['unpaired'] - 1)
        elif logger.startswith(TRANSACTION_LOGGER):
            transaction = logger[len(TRANSACTION_LOGGER):]
            opens = message.startswith(FIRST_TRANSACTION_LINE)
            slot = next((slot for slot in running if slot['transaction'] == transaction), None)
            if slot is not None and opens:
                # A second request under an id that is still running: no line tells the two apart from here on
                flag(slot['entry'])
                slot['entry'] = None
                slot['last'] = last_naming(transaction, this)
                take_transaction(model_named(message), False)
            elif slot is None:
                entry = None
                if opens or transaction not in started:
                    # The rest of a transaction at a place where the next part can have begun is bound to no request
                    entry = take_transaction(model_named(message), opens or not late)
                # Otherwise the rest of a request that ended further up, in another part of the file: nothing is kept of it
                if entry is not None:
                    entry['transaction_id'] = transaction
                slot = {'entry': entry, 'transaction': transaction, 'start': entry['timestamp'] if entry is not None else timestamp,
                        'last': last_naming(transaction, this), 'carried': False}
                running.append(slot)
                mark_overlap()
            started.add(transaction)
            here.add(transaction)
            if level == 'ERROR':
                if slot['entry'] is not None:
                    guarded(read_error, slot['entry'], message, more_lines)
                # The last line a container writes for a request that failed, there is no 'Response:' line
                if message.startswith('Error occurred in the model scoring function'):
                    running.remove(slot)
        else:
            match_score = SCORE_LINE.match(message)
            if not match_score and level != 'ERROR':
                continue
            # The lines of the score code name no request: they are the lines of the one transaction that is running
            entry = None
            if len(running) == 1:
                entry = running[0]['entry']
            elif running:
                mark_unattributed()
            else:
                heads = [head for head in pending if head['scoring']]
                if len(heads) == 1 and not names_transactions and anonymous(waiting) + anonymous(blurred) == 0:
                    # A log that names no transaction is read by the order of its lines
                    entry = heads[0]['entry']
                else:
                    for head in heads:
                        flag(head['entry'])
            if entry is None:
                continue
            if level == 'ERROR':
                guarded(read_error, entry, message, more_lines)
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
                # The response of an LLM can span several lines, and one of them can start like a log line. While
                # this is the only request around, everything up to the next line of its transaction is its text
                slot = running[0] if running else None
                if (slot is not None and slot['last'] > this and not pending and not stopped
                        and anonymous(waiting) + anonymous(blurred) == 0):
                    names = named[slot['transaction']]
                    closes = names[bisect.bisect_right(names, this)]
                    while index < closes and not (units[index][5].startswith('Request: ') and starts_a_request(index, closes)):
                        index += 1
                    behind = units[index][0] if index < len(units) else len(lines)
                entry['response'] = ' '.join([value.strip()] + [line.strip() for line in lines[first + 1:behind]])

    end_of_the_log()

    # A request failed when the log holds an error for it, or no response although its container went on. For a
    # request that ran at the same time as another one the log can hold a response that could not be given to it,
    # and for a request the lines of its container end on it holds none yet: nothing is said then
    overlapped = 0
    unattributed = 0
    undecided = 0
    ended = 0
    unreadable = 0
    replaced = 0
    for entry in records:
        mixed = entry['_overlapped'] or entry['_unattributed']
        if entry['status_code'] is not None:
            entry['failed'] = 1 if entry['_errors'] or entry['status_code'] >= 400 else 0
        elif entry['_errors'] or not (mixed or entry['_cut']):
            entry['failed'] = 1
        overlapped += 1 if mixed else 0
        unattributed += 1 if entry['_unattributed'] else 0
        undecided += 1 if entry['failed'] is None and mixed else 0
        ended += 1 if entry['failed'] is None and not mixed else 0
        unreadable += 1 if entry['_unreadable'] else 0
        # A text that has no UTF-8 bytes can not be uploaded: the rows are handed to CAS as a UTF-8 file. Half of
        # a surrogate pair in a request body is such a text, and it becomes a question mark as it does in a hash
        changed = False
        for column in COLUMNS:
            if isinstance(entry[column], str):
                try:
                    entry[column].encode('utf-8')
                except UnicodeEncodeError:
                    entry[column] = entry[column].encode('utf-8', errors='replace').decode('utf-8')
                    changed = True
        replaced += 1 if changed else 0

    # Requests that were all skipped must not pass as a log without requests
    if not records and unmatched_requests > 0:
        say(f"No request was read: 'Request: POST' was found in {unmatched_requests} line(s) that do not start as a log line ('<timestamp> - <logger> - <level> - '). The parser needs the lines as the containers write them, with nothing in front.", 'WARNING')
    if overlapped > 0:
        say(f"{overlapped} request(s) were running at the same time as another request - of their container, or of another container whose lines are mixed with theirs in the file. The first lines of a request, the lines of the score code and the 'Response:' line name no request, so a line that the log does not decide was given to none of them: {unattributed} request(s) can have missing values, and failed is missing for {undecided} request(s).")
    if ended > 0:
        say(f"{ended} request(s) were running where the lines of their container end - at the end of the file, or at the end of a part of it, where the time of the lines goes back. The log holds neither a response nor an error for them, and failed is missing for them.")
    if unreadable > 0:
        say(f"{unreadable} request(s) hold a value that could not be read, for example a 'Request Data:' line that was cut off or options that are nested deeper than Python decodes. The rows were kept with what was read.")
    if replaced > 0:
        say(f"{replaced} request(s) hold a character that has no UTF-8 bytes in a text column - half of a surrogate pair in a request body. It was replaced by a question mark, the way the hash of a text counts it.")

    df = pd.DataFrame(records, columns=COLUMNS)
    return df

# A run that one of the checks of the SAS code refused reads no log: _log_row_count stays -1, and the macro
# that handles the output table returns before it says anything
if SAS.symget('_log_ready') == '1':
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

    put_number('_log_row_count', len(df))
endsubmit;
run; quit;

proc python terminate;
run; quit;

* Append, Replace, Copy and/or promote to CAS;
%macro _log_handle_output_table;
    /* A run that was refused has said so in its one ERROR line: there is no log that could not be parsed and no table to drop */
    %if &_log_ready. NE 1 %then %return;

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
                %let _log_append_syserr = &syserr.;
                libname _llmtgt clear;
                libname _llmsrc clear;
            %end;
        %end;

        proc cas;
            %if &_log_target_unloaded. %then %do;
                /* Starting a new table here would end in a save over the file that could not be loaded */
                print (error) "The saved table &_log_target_table_lib..&_log_target_table_name. could not be loaded, nothing was appended and the saved file was left as it is.";
                tableWritten = False;
                call symputx('_log_failed', '1');
            %end;
            %else %do;
                /* The new table is built beside the existing one, which is only dropped once its replacement exists */
                stagedComplete = True;
                %if &_log_target_scope. GE 1 %then %do;
                    stagedTable = '_TEMP_LLM_LOGS_ALL';
                    /* The DATA step that appended is asked for its result before its table takes the place of the
                       existing one: its return code, and the rows it wrote against the rows of the two tables it
                       read. The listing that counts the rows also gives the label of the existing table */
                    existingRows = -1;
                    uploadedRows = -1;
                    stagedRows = -1;
                    existingLabel = '';
                    table.tableInfo result=targetTables /
                        casLib = "&_log_target_table_lib.";
                    if exists(targetTables, 'TableInfo') then do;
                        do listedTable over targetTables.TableInfo;
                            if upcase(listedTable.Name) EQ upcase("&_log_target_table_name.") then do;
                                existingRows = listedTable.Rows;
                                existingLabel = listedTable.Label;
                            end;
                            if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS_ALL' then stagedRows = listedTable.Rows;
                        end;
                    end;
                    table.tableInfo result=uploadTables /
                        casLib = 'CASUSER';
                    if exists(uploadTables, 'TableInfo') then do;
                        do listedTable over uploadTables.TableInfo;
                            if upcase(listedTable.Name) EQ '_TEMP_LLM_LOGS' then uploadedRows = listedTable.Rows;
                        end;
                    end;
                    if &_log_append_syserr. GT 4 or existingRows LT 0 or uploadedRows LT 0 or stagedRows NE existingRows + uploadedRows then stagedComplete = False;
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

                if staged.exists GE 1 and stagedComplete then do;
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

                    %if &_log_target_scope. GE 1 %then %do;
                        /* A DATA step writes a table without a label. The new table gets the label the existing
                           one had, and the label of a table this program starts where it had none */
                        if existingLabel EQ '' then existingLabel = 'Extracted LLM Log Data';
                        table.alterTable /
                            casLib = "&_log_target_table_lib.",
                            name = "&_log_target_table_name.",
                            label = existingLabel;
                    %end;

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
                    %if &_log_target_scope. GE 1 %then %do;
                        print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is. The DATA step that appends ended with the return code &_log_append_syserr. and wrote " || (string) stagedRows || " rows, the table held " || (string) existingRows || " and the log " || (string) uploadedRows || " (-1 stands for a table that was not found).";
                        call symputx('_log_failed', '1');
                        /* What the DATA step left behind is no table of anybody */
                        table.dropTable /
                            casLib = "&_log_target_table_lib.",
                            name = '_TEMP_LLM_LOGS_ALL',
                            quiet = True;
                    %end;
                    %else %do;
                        print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is.";
                        call symputx('_log_failed', '1');
                    %end;
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
* A run that wrote an ERROR line and no table ends with the condition code of a failed job, so that SAS Job Execution, a schedule and a flow report it as failed and not as completed: a refusal and a log that could not be parsed leave _log_row_count below 0, and a table that could not be written sets _log_failed. A run with a WARNING and a log without requests end as before. Nothing follows but the macro variables that are deleted;
%if &_log_row_count. LT 0 or &_log_failed. EQ 1 %then %do;
    %let SYSCC = 1012;
%end;
%symdel _log_ready _log_failed _log_clean_cas_session _casSessionUUID _log_row_count _log_target_scope _log_target_unloaded _log_fixed_rename _log_fixed_assign _log_fixed_drop _log_append_syserr;
%symdel _log_path _log_target_table_engine _log_target_table_lib _log_target_table_name _log_append_table _log_promote_table _log_save_table;
