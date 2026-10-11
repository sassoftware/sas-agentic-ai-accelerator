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

proc python restart;
submit;
import re
import os
import ast
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
# so a log without a single request still describes the complete table.
COLUMNS = ['timestamp', 'model', 'system_prompt', 'user_prompt', 'temperature', 'top_p', 'top_k', 'max_tokens', 'prompt_length', 'output_length', 'runtime', 'response']

# The request inputs that are kept and the column each one is written to. A decision
# model takes questions and a state where an LLM takes a system and a user prompt.
# The document input of an embedding model is left out on purpose: keeping it would
# copy every embedded document into the output table.
INPUT_COLUMNS = {
    'systemPrompt': 'system_prompt',
    'userPrompt': 'user_prompt',
    'questions': 'system_prompt',
    'state': 'user_prompt'
}

# The options that have a column of their own
OPTION_COLUMNS = ['temperature', 'top_p', 'top_k', 'max_tokens']

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
LOG_LINE = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\S* - \S+ - [A-Z]+ - (.*)', re.DOTALL)
SCORE_LINE = re.compile(r'(prompt_length|tokens|output_length|run_time|response|answers):(.*)', re.DOTALL)
LOG_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'

def read_number(text):
    """
    Returns the number a logged value holds, None if it is not a number (for example 'None').
    """
    try:
        return float(text)
    except ValueError:
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

    for item in request_data['inputs']:
        if not isinstance(item, dict) or item.get('value') is None:
            continue
        name = item.get('name')
        value = str(item['value'])
        if name in INPUT_COLUMNS:
            entry[INPUT_COLUMNS[name]] = value
        elif name == 'options':
            options_str = value.strip()
            if options_str.startswith('{') and options_str.endswith('}'):
                options_str = options_str[1:-1]  # Remove surrounding {}
            for opt in options_str.split(','):
                if ':' in opt:
                    key, val = opt.split(':', 1)
                    key = key.strip()
                    val = val.strip()
                    if key in OPTION_COLUMNS:
                        entry[key] = val

def parse_log_file(file_path):
    """
    Parses a structured application log file containing the request and response blocks
    of LLMs, decision models and embedding models.

    Extracts relevant data including:
    - Timestamp of the request
    - The model used for the request
    - System and user prompts (for a decision model the questions and the state)
    - Model options (temperature, top_p, top_k, max_tokens)
    - Prompt and output lengths (for an embedding model the tokens as prompt length)
    - Runtime in seconds
    - Multiline model response (for a decision model the answers with their confidence)

    Each request block starts with 'Request: POST' and ends with its 'Response: Status Code'
    line or, if that line is missing, before the next 'Request: POST' or before the first
    line that is older than the request. Lines between two blocks belong to no request
    and are skipped.

    Args:
        file_path (str): Path to the log file to parse.

    Returns:
        pandas.DataFrame: A DataFrame containing one row per request with the columns in COLUMNS.
    """
    # A byte that is not UTF-8 is replaced, so one damaged line does not cost the whole file
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    records = []
    entry = None
    unmatched_requests = 0
    i = 0
    while i < len(lines):
        match_line = LOG_LINE.match(lines[i].rstrip('\r\n'))
        i += 1
        if not match_line:
            # A request in a line that does not start as a log line, for example behind a prefix of the log pipeline
            if 'Request: POST' in lines[i - 1]:
                unmatched_requests += 1
            continue
        timestamp, message = match_line.groups()
        # A line that is older than the open request belongs to the next container's part of the file
        if entry is not None and timestamp < entry['timestamp']:
            entry = None

        if message.startswith('Request: POST'):
            entry = dict.fromkeys(COLUMNS)
            entry['timestamp'] = timestamp
            entry['model'] = message[len('Request: POST'):].strip().strip('/') or None
            records.append(entry)
        elif entry is None:
            continue
        elif message.startswith('Response: Status Code'):
            entry = None
        elif message.startswith('Request Data: '):
            read_request_data(entry, message[len('Request Data: '):])
        else:
            match_score = SCORE_LINE.match(message)
            if not match_score:
                continue
            key, value = match_score.groups()
            if key in METRIC_COLUMNS:
                entry[METRIC_COLUMNS[key]] = read_number(value)
            else:
                # The response of an LLM can span several lines, it ends at the next line that starts with a timestamp
                response_lines = [value.strip()]
                while i < len(lines) and not LOG_TIMESTAMP.match(lines[i]):
                    response_lines.append(lines[i].strip())
                    i += 1
                entry['response'] = ' '.join(response_lines)

    # Requests that were all skipped must not pass as a log without requests
    if not records and unmatched_requests > 0:
        SAS.logMessage(f"No request was read: 'Request: POST' was found in {unmatched_requests} line(s) that do not start as a log line ('<timestamp> - <logger> - <level> - '). The parser needs the lines as the containers write them, with nothing in front.", messageType='WARNING')

    df = pd.DataFrame(records, columns=COLUMNS)
    return df

df = parse_log_file(SAS.symget('_log_path'))

# Ensure the timestamp is suited for SAS
df['timestamp'] = pd.to_datetime(df['timestamp'], format=TIMESTAMP_FORMAT)
sas_epoch = datetime(1960, 1, 1)
df['timestamp'] = (df['timestamp'] - sas_epoch).dt.total_seconds()

# Ensure that numeric values are correct
numeric_columns = ['temperature', 'top_p', 'top_k', 'max_tokens', 'prompt_length', 'output_length', 'runtime']
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
    'response': 'The response of the model to the users request. For a decision model the answers'
}

# Set the format to display the timestamp correctly
sas_formats = {
    'timestamp': 'DATETIME20.'
}

# The type is stated for every column, so it never depends on what a log happens to
# hold (a log of embedding calls only has no prompt and no response at all)
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
        proc cas;
            table.tableExists result=re /
                casLib = "&_log_target_table_lib.",
                name = "&_log_target_table_name.";

            %if &_log_append_table. %then %do;
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

                tableWritten = True;
                if re.exists GE 1 then do;
                    table.append /
                        source = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'},
                        target = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."};
                end;
                else if savedFile NE '' then do;
                    /* Starting a new table here would end in a save over the file that could not be loaded */
                    print (error) "The saved table &_log_target_table_lib..&_log_target_table_name. could not be loaded, nothing was appended and the saved file was left as it is.";
                    tableWritten = False;
                end;
                else do;
                    table.copyTable /
                        table = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'},
                        casOut = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."};
                end;

                %if &_log_promote_table. %then %do;
                    if tableWritten and re.exists LE 1 then do;
                        table.promote /
                            casLib = "&_log_target_table_lib.",
                            name = "&_log_target_table_name.",
                            targetLib = "&_log_target_table_lib.",
                            target = "&_log_target_table_name.",
                            quiet = True;
                    end;
                %end;
            %end;
            %else %do;
                /* The new table is built beside the existing one, which is only dropped once its replacement exists */
                table.copyTable /
                    table = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'},
                    casOut = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name._NEW", replace = True};
                table.tableExists result=staged /
                    casLib = "&_log_target_table_lib.",
                    name = "&_log_target_table_name._NEW";

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
                        name = "&_log_target_table_name._NEW",
                        rename = "&_log_target_table_name.";

                    %if &_log_promote_table. %then %do;
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
%symdel _log_clean_cas_session _casSessionUUID _log_row_count;
%symdel _log_path _log_target_table_engine _log_target_table_lib _log_target_table_name _log_append_table _log_promote_table _log_save_table;
