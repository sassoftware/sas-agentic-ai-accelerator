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
%if %symexist(_CASNAME_) EQ 0 %then %do;
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

def parse_log_file(file_path):
    """
    Parses a structured application log file containing LLM request and response blocks.

    Extracts relevant data including:
    - Timestamp of the request
    - The model used for the request
    - System and user prompts
    - Model options (temperature, top_p, top_k, max_tokens)
    - Prompt and output lengths
    - Runtime in seconds
    - Multiline model response

    Each request block starts with 'Request: POST' and ends before the next such line.
    
    Args:
        file_path (str): Path to the log file to parse.

    Returns:
        pandas.DataFrame: A DataFrame containing one row per request with extracted fields.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    records = []
    i = 0
    while i < len(lines):
        if 'Request: POST' in lines[i]:
            entry = {
                "timestamp": None,
                "model": None,
                "system_prompt": None,
                "user_prompt": None,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "max_tokens": None,
                "prompt_length": None,
                "output_length": None,
                "runtime": None,
                "response": None
            }

            entry["timestamp"] = lines[i].split(' - ')[0].strip()
            match_endpoint = re.search(r'Request: POST\s+(.+)', lines[i])
            if match_endpoint:
                entry["model"] = match_endpoint.group(1).strip().strip('/')
            i += 1

            response_lines = []
            while i < len(lines) and 'Request: POST' not in lines[i]:
                if 'Request Data:' in lines[i]:
                    try:
                        request_data_str = lines[i].split('Request Data: ')[-1].strip()
                        request_data_dict = ast.literal_eval(request_data_str)
                        for item in request_data_dict.get('inputs', []):
                            if item['name'] == 'systemPrompt':
                                entry['system_prompt'] = item['value']
                            elif item['name'] == 'userPrompt':
                                entry['user_prompt'] = item['value']
                            elif item['name'] == 'options':
                                options_str = item['value'].strip()
                                if options_str.startswith('{') and options_str.endswith('}'):
                                    options_str = options_str[1:-1]  # Remove surrounding {}
                                for opt in options_str.split(','):
                                    if ':' in opt:
                                        key, val = opt.split(':', 1)
                                        key = key.strip()
                                        val = val.strip()
                                        if key in ['temperature', 'top_p', 'top_k', 'max_tokens']:
                                            entry[key] = val
                    except (ValueError, SyntaxError):
                        pass
                elif 'prompt_length:' in lines[i]:
                    entry["prompt_length"] = int(re.search(r'prompt_length:\s*(\d+)', lines[i]).group(1))
                elif 'output_length:' in lines[i]:
                    entry["output_length"] = int(re.search(r'output_length:\s*(\d+)', lines[i]).group(1))
                elif 'run_time:' in lines[i]:
                    entry["runtime"] = float(re.search(r'run_time:\s*([\d\.]+)', lines[i]).group(1))
                elif 'response:' in lines[i]:
                    response_line = lines[i].split('response: ', 1)[-1].strip()
                    response_lines.append(response_line)
                    i += 1
                    while i < len(lines) and not re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', lines[i]):
                        response_lines.append(lines[i].strip())
                        i += 1
                    entry["response"] = ' '.join(response_lines)
                    continue
                i += 1

            records.append(entry)
        else:
            i += 1

    df = pd.DataFrame(records)
    return df

df = parse_log_file(SAS.symget('_log_path'))

# Ensure the timestamp is suited for SAS
df['timestamp'] = pd.to_datetime(df['timestamp'])
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
    'system_prompt': 'The system prompt used by the request',
    'user_prompt': 'The user prompt used by the request',
    'temperature': 'The setting of temperature. Missing means it was not present in the request',
    'top_p': 'The setting of top p. Missing means it was not present in the request',
    'top_k': 'The setting of top k. Missing means it was not present in the request',
    'max_tokens': 'The setting of max new tokens. Missing means it was not present in the request',
    'prompt_length': 'The amount of input tokens in the request',
    'output_length': 'The amount of output tokens in the request',
    'runtime': 'The time for the model to generate the response in seconds',
    'response': 'The response of the model to the users request'
}

# Set the format to display the timestamp correctly
sas_formats = {
    'timestamp': 'DATETIME20.'
}

vars_metadata = []
for col in df.columns:
    var = {
        'name': col,
        'label': labels.get(col, col)
    }
    if col in sas_formats:
        var['format'] = sas_formats[col]
    vars_metadata.append(var)

# Upload to the casuser and then return to SAS
tbl = conn.upload_frame(
    df,
    casout=dict(
        name='_TEMP_LLM_LOGS',
        caslib='CASUSER',
        label='Extracted LLM Log Data'
    ),
    importoptions={'vars': vars_metadata}
)
endsubmit;
run; quit;

proc python terminate;
run; quit;

* Append, Replace, Copy and/or promote to CAS;
%macro _log_handle_output_table;
    proc cas;
        table.tableExists result=re /
            casLib = "&_log_target_table_lib.",
            name = "&_log_target_table_name.";

        %if &_log_append_table. %then %do;
            if re.exists GE 1 then do;
                table.append /
                    source = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'},
                    target = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."};
            end;
            else do;
                table.copyTable /
                    table = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'},
                    casOut = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."};
            end;
        %end;
        %else %do;
            if re.exists EQ 2 then do;
                table.dropTable /
                    casLib = "&_log_target_table_lib.",
                    name = "&_log_target_table_name.",
                    quiet = True;
            end;
            
            table.copyTable /
                table = {casLib = 'CASUSER', name = '_TEMP_LLM_LOGS'},
                casOut = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."};
            
            %if &_log_promote_table. %then %do;
                table.promote /
                    casLib = "&_log_target_table_lib.",
                    name = "&_log_target_table_name.",
                    targetLib = "&_log_target_table_lib.",
                    target = "&_log_target_table_name.",
                    quiet = True;
            %end;
        %end;

        %if &_log_promote_table. %then %do;
            if re.exists LE 1 then do;
                table.promote /
                    casLib = "&_log_target_table_lib.",
                    name = "&_log_target_table_name.",
                    targetLib = "&_log_target_table_lib.",
                    target = "&_log_target_table_name.",
                    quiet = True;
            end;
        %end;

        %if &_log_save_table. %then %do;
            table.save /
                table = {casLib = "&_log_target_table_lib.", name = "&_log_target_table_name."},
                casLib = "&_log_target_table_lib.",
                name = "&_log_target_table_name.",
                replace = True;
        %end;

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
%symdel _log_clean_cas_session _casSessionUUID;
%symdel _log_path _log_target_table_engine _log_target_table_lib _log_target_table_name _log_append_table _log_promote_table _log_save_table;