/********************************************************************************
    Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Link Logs To Models

    Fills the link columns of the log table that Log-Parser-Code.sas writes:
    for every request the SAS Model Manager model that answered it (model_id)
    and the manifested prompt, decision template or RAG setup that made the
    call (caller_model_id, caller_project_id, caller_kind, caller_name,
    experiment_run_id, rag_model_id), with the rule that decided in
    match_method and the number of candidates in match_candidates.

    No request and no log line names its caller, so every link is made by
    content: the prompts, questions, hashes and run times of the log row are
    compared with the templates, the experiment runs and the calls of
    optimizations and judges in the table MODEL_CATALOG that
    Get-All-Prompts.sas writes, and with the ingestion runs of RAG_RUNS.
    A row that fits nothing gets match_method = none, a row that fits two
    callers equally well gets match_method = ambiguous.

    The order of the programs is: collect the log, Log-Parser-Code.sas,
    Get-All-Prompts.sas, this program.

    Every other column of the log table stays in CAS and is not rewritten:
    the program numbers the rows in a copy in CASUSER, reads what it compares
    into Python, uploads the link columns and merges them back by that number.
    A run that links no row leaves the table as it is.

    lnk is short for:
      Link
*********************************************************************************/

* The log table the parser wrote. Its link columns are filled in place;
%let _lnk_log_caslib = Public;
%let _lnk_log_table = LLM_LOGS;
* The catalog of models, prompts, decision templates and RAG setups that Get-All-Prompts.sas writes;
%let _lnk_catalog_caslib = Public;
%let _lnk_catalog_table = MODEL_CATALOG;
* The ingestion runs of the RAG setups that Get-All-Prompts.sas stacks. Without the table no embedding row is linked to an ingestion run;
%let _lnk_runs_caslib = Public;
%let _lnk_runs_table = RAG_RUNS;
* 0 links only the rows without a match_method and leaves every other row as it is, 1 links all rows again;
%let _lnk_redo_all = 0;
* A template takes part with at least this many characters of fixed text (whitespace is not counted), an experiment run with at least as many characters in its prompts, a call of an optimization or of a judge with as many in the texts the catalog holds of it;
%let _lnk_min_literal = 20;
* The hours the clock of the containers is ahead of UTC. The ingestion runs and the windows of the optimizations are recorded in UTC;
%let _lnk_utc_offset = 0;
* The seconds within which the prompt that carries a retrieval follows the embedding of its query. A query embedding without such a prompt is not linked;
%let _lnk_query_window = 300;
* A table that is promoted stays promoted and a table that has a saved file is saved again, whatever these two say. They decide for a table that is not promoted and for one that has no saved file;
%let _lnk_promote_table = 1;
%let _lnk_save_table = 1;

* Check if a CAS session exists;
* The option SESSREF names the session the CAS actions run in and keeps the name of a session that was ended, so SESSFOUND is asked whether that session is connected;
%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;
    %let _lnk_clean_cas_session = 1;
    cas lnkMySess;
%end;
%else %do;
    %let _lnk_clean_cas_session = 0;
%end;

proc cas;
    session.sessionId result = sessresults;
    call symputx('_casSessionUUID', sessresults[1]);
quit;

* What the log table is before it is linked: 2 a promoted table, 1 a table of this session, 0 not in memory;
%let _lnk_log_scope = 0;
proc cas;
    table.tableExists result=found /
        casLib = "&_lnk_log_caslib.",
        name = "&_lnk_log_table.";
    call symputx('_lnk_log_scope', found.exists);
run; quit;

* Whether the CAS library holds a saved file of the log table. Asked only when the table is not saved anyway;
%let _lnk_log_saved = 0;
%if &_lnk_save_table. NE 1 %then %do;
    proc cas;
        table.fileInfo result=listing status=listed /
            casLib = "&_lnk_log_caslib.";
        if listed.severity EQ 0 then do;
            if exists(listing, 'FileInfo') then do;
                do savedEntry over listing.FileInfo;
                    if upcase(savedEntry.Name) EQ upcase("&_lnk_log_table..sashdat") then call symputx('_lnk_log_saved', 1);
                end;
            end;
        end;
    run; quit;
%end;

* Sets the macro variable named in _lnk_flag to 1 when the table is in memory, after loading it from its saved file when it is not;
%macro _lnk_ensure_loaded(_lnk_lib, _lnk_name, _lnk_flag);
    proc cas;
        table.tableExists result=found /
            casLib = "&_lnk_lib.",
            name = "&_lnk_name.";

        /* A table that was saved but is not in memory, which is how a restart of the CAS server
           leaves it, is loaded back. The whole caslib is listed, so a file that does not exist is
           not an error. The file keeps the case the table name was saved with and a CAS table name
           has none: the exact name is taken when there is one, otherwise the same name in another case */
        if found.exists EQ 0 then do;
            savedFile = '';
            table.fileInfo result=listing status=listed /
                casLib = "&_lnk_lib.";
            if listed.severity EQ 0 then do;
                if exists(listing, 'FileInfo') then do;
                    do savedEntry over listing.FileInfo;
                        if savedEntry.Name EQ "&_lnk_name..sashdat" then savedFile = savedEntry.Name;
                        else if savedFile EQ '' and upcase(savedEntry.Name) EQ upcase("&_lnk_name..sashdat") then savedFile = savedEntry.Name;
                    end;
                end;
            end;
            if savedFile NE '' then do;
                table.loadTable /
                    casLib = "&_lnk_lib.",
                    path = savedFile,
                    casOut = {casLib = "&_lnk_lib.", name = "&_lnk_name."};
                table.tableExists result=found /
                    casLib = "&_lnk_lib.",
                    name = "&_lnk_name.";
            end;
        end;

        if found.exists GE 1 then call symputx("&_lnk_flag.", 1);
        else call symputx("&_lnk_flag.", 0);
    run; quit;
%mend _lnk_ensure_loaded;

%let _lnk_log_exists = 0;
%let _lnk_catalog_exists = 0;
%let _lnk_runs_exists = 0;
%_lnk_ensure_loaded(&_lnk_log_caslib., &_lnk_log_table., _lnk_log_exists);
%_lnk_ensure_loaded(&_lnk_catalog_caslib., &_lnk_catalog_table., _lnk_catalog_exists);
%_lnk_ensure_loaded(&_lnk_runs_caslib., &_lnk_runs_table., _lnk_runs_exists);

* Without the log table or the catalog nothing is linked. Each case is one ERROR line and the Python below is skipped;
%let _lnk_ready = 1;
%if &_lnk_log_exists. NE 1 %then %do;
    %put ERROR: The log table &_lnk_log_caslib..&_lnk_log_table. was not found. Run Log-Parser-Code.sas or the step LLM - Log Parser first, it writes the table that is linked here.;
    %let _lnk_ready = 0;
%end;
%if &_lnk_ready. EQ 1 and &_lnk_catalog_exists. NE 1 %then %do;
    %put ERROR: The catalog table &_lnk_catalog_caslib..&_lnk_catalog_table. was not found. Run Get-All-Prompts.sas first, it writes the catalog the links are made from. The table &_lnk_log_caslib..&_lnk_log_table. was left as it is.;
    %let _lnk_ready = 0;
%end;
%if &_lnk_ready. EQ 1 and &_lnk_runs_exists. NE 1 %then %do;
    %put NOTE: The table &_lnk_runs_caslib..&_lnk_runs_table. was not found, so no embedding row is linked to an ingestion run.;
%end;

* The number of rows of the log table. The Python code sets it as its last statement, so -1 means that the links were not made;
%let _lnk_row_count = -1;
* 1 when the Python code linked no row and built no new table: the log table, its promotion and its saved file stay as they are;
%let _lnk_unchanged = 0;
* 1 when the Python code refused the tables and wrote the ERROR line itself;
%let _lnk_refused = 0;
* 1 when the new table could not be put in the place of the log table. The last statement of the program reads it;
%let _lnk_failed = 0;

proc python restart;
submit;
import hashlib
import json
import math
import os
import re
import time
from bisect import bisect_left
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# What is handed to SAS
# ---------------------------------------------------------------------------

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


def setting(name):
    """A setting of the SAS program as text, the empty text when it is not set."""
    try:
        value = SAS.symget(name)
    except Exception:
        value = None
    return '' if value is None else str(value).strip()


def number_setting(name, default):
    """A setting that is a number. A value that is none gives the default and a WARNING."""
    raw = setting(name)
    try:
        value = float(raw)
    except ValueError:
        value = float('nan')
    if value != value or value in (float('inf'), float('-inf')):
        say(f"The setting {name} is {raw or 'empty'}, which is no number. {default} is taken in its place.", 'WARNING')
        return float(default)
    return value


try:
    import swat
    import pandas as pd
except ImportError:
    say('Ensure that the SWAT and Pandas package are installed', 'ERROR')

# The link columns in the order the log table holds them. Only this program fills them.
LINK_TEXT_COLUMNS = ['model_id', 'caller_model_id', 'caller_project_id', 'caller_kind', 'caller_name', 'experiment_run_id', 'rag_model_id', 'match_method']
LINK_COLUMNS = LINK_TEXT_COLUMNS + ['match_candidates']

# What the matching reads of a log row besides the link columns. A table without them
# was written by a parser that is older than the link columns.
CONTENT_COLUMNS = ['timestamp', 'model', 'system_prompt', 'user_prompt', 'kind', 'embedding_mode', 'input_hash', 'rag_collection', 'rag_query_hash']
PROMPT_COLUMNS = ['system_prompt', 'user_prompt']
# Read when the table has them
OPTIONAL_COLUMNS = ['user_agent', 'runtime', 'project']
# The user agents of requests that no Builder page sent: the score code of a published model, a job or
# a script, and a SAS session. The Builder page runs in a browser.
NOT_A_BROWSER = ('python-requests', 'sas')
# What the RAG runtime sends as the project input of every embedding request
RUNTIME_PROJECT = 'rag'

# The columns no row of the catalog can be read without. The others - projectid, runid,
# target_model, system_template, user_template, resolved_system_prompt, resolved_user_prompt,
# collection, template_source, resolved_system_hash, resolved_user_hash, run_time,
# window_start, window_end and optimization_id - read as missing values when the catalog
# does not have them. The catalog holds three more that nothing here reads: projectname, and
# prompt_length and output_length of a run.
CATALOG_KEY_COLUMNS = ['modelid', 'name', 'role']
# The columns an ingestion run is read from. modelid, the setup that holds the run, is optional.
RUN_COLUMNS = ['run_id', 'collection', 'embed_model', 'started_at', 'finished_at']

# The role of the catalog rows that answer a log row of each kind
ANSWER_ROLE = {'llm': 'llm', 'embedding': 'embedding', 'decision': 'decision_model'}
KIND_OF_ROLE = {role: kind for kind, role in ANSWER_ROLE.items()}
# The roles of the catalog rows that hold a call the Builder made for a prompt, in the order
# the two rules are asked. The role is the match_method of the rows it links.
CALL_ROLES = ('optimization', 'judge')

# The methods a row of each kind can get, in the order the summary names them
METHODS = {
    'llm': ['experiment run', 'template', 'optimization', 'judge'],
    'decision': ['experiment run', 'questions and state', 'questions'],
    'embedding': ['retrieval query', 'ingestion run'],
}

LABELS = {
    'model_id': 'The SAS Model Manager ID of the model that answered the request',
    'caller_model_id': 'The SAS Model Manager ID of the prompt, decision template or RAG setup that made the request',
    'caller_project_id': 'The SAS Model Manager project of the caller',
    'caller_kind': 'The kind of the caller: prompt, decision or rag',
    'caller_name': 'The name of the caller',
    'experiment_run_id': 'The experiment run of the caller the request belongs to',
    'rag_model_id': 'The SAS Model Manager ID of the RAG setup whose retrieval the prompt carried',
    'match_method': 'The rule that linked the request to its caller',
    'match_candidates': 'The number of candidates the rule found',
}

# A variable of a template: {{name}}. It stands for any text, also for none.
VARIABLE = re.compile(r'\{\{[^{}]*\}\}')
# The hash of a text as the catalog and the log table hold it
TEXT_HASH = re.compile(r'[0-9a-f]{64}')
# What the catalog writes into template_source for a RAG setup that the Prompt Builder saved,
# and for a model that the step RAG - Register Setup registered - which it names after the setup
BUILDER_SETUP = 'builder'
STEP_SETUP = 'step'
STEP_MODEL_PREFIX = 'RAG Retrieval - '
INGESTION_RUN = re.compile(r'"ingestion_run_id"\s*:\s*"([^"]*)"')
# A template with a variable in its system prompt is found through the first characters
# of that system prompt or, when it starts with the variable, through its last ones.
# Only a template that has less fixed text than this at both ends is compared with every row.
PREFIX_LENGTH = 12
# The calls of optimizations and judges are compared one by one with a row while a model has
# no more of them than this. Beyond it they are found through a piece of their longest text
# of this many characters, which is looked up at every place of the prompts of the row.
CALLS_COMPARED = 32
CALL_ANCHOR = 24
SAS_EPOCH = datetime(1960, 1, 1)
STAGE_TABLE = '_LNK_STAGE'
LINKS_TABLE = '_LNK_LINKS'
READ_PAGE = 2000
# How many models, names or rows a message names before it counts the rest
NAMED_IN_A_MESSAGE = 5


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def text(value):
    """The value as text. A missing value of CAS or pandas is the empty text and a whole
    number that arrives as a float (a run id of a numeric column) loses its '.0'."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if value != value:
            return ''
        if value.is_integer():
            return str(int(value))
        return str(value)
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value)


def squeeze(value):
    """The text with every run of whitespace as one blank and none at its ends."""
    return ' '.join(text(value).split())


def size(value):
    """The number of characters that are not whitespace."""
    return len(''.join(text(value).split()))


def number(value):
    """A number of a table as a float. None when the value is missing or is no number."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        try:
            value = float(text(value).strip())
        except ValueError:
            return None
    value = float(value)
    return None if value != value or value in (float('inf'), float('-inf')) else value


def same_time(one, other):
    """Whether two run times are the same time. The tracker of a prompt holds the run time
    the container returned and the log holds the one it logged: they are compared with
    three decimals, which is what a table and a file leave of the same number."""
    return round(one, 3) == round(other, 3)


def text_hash(value):
    """The hash the log parser, Get-All-Prompts.sas and this program compare texts by: the
    SHA-256 of the UTF-8 bytes of the text with every run of whitespace as one blank, in
    lower-case hex."""
    return hashlib.sha256(squeeze(value).encode('utf-8', errors='replace')).hexdigest()


def hash_or(stored, value):
    """The hash the catalog holds for a text, or the hash of the text where it holds none.
    The catalog takes its hash of the whole text and cuts the text to the length of a SAS
    column afterwards, so the hash still names a prompt that is longer than that."""
    stored = squeeze(stored).lower()
    return stored if TEXT_HASH.fullmatch(stored) else text_hash(value)


def question_keys(value):
    """The keys the questions of a decision are compared by.

    Questions are JSON, and two texts of the same questions may differ in the order of
    their keys and in their whitespace: the parsed value written with sorted keys is
    the key. A text that is no JSON is compared as text, and so that it meets the same
    text on the other side every questions text has that key as well.
    """
    raw = text(value)
    keys = ['T:' + squeeze(raw)]
    try:
        parsed = json.loads(raw)
    except ValueError:
        return keys
    if isinstance(parsed, (dict, list)):
        keys.append('J:' + json.dumps(parsed, sort_keys=True, separators=(',', ':'), ensure_ascii=False))
    return keys


def template_parts(template):
    """The fixed texts of a template between its {{variables}}, and their size.

    One part more than the template has variables. Whitespace inside a part is
    squeezed, whitespace beside a variable belongs to the variable.
    """
    parts = tuple(squeeze(part) for part in VARIABLE.split(text(template)))
    return parts, sum(size(part) for part in parts)


def fits(parts, squeezed):
    """Whether a squeezed text is the template: its first part at the start, its last
    part at the end and the parts between in their order, with any text - also none,
    also several lines - where a variable stands. No regular expression is involved:
    every part is searched once, from where the part before it ended."""
    if len(parts) == 1:
        return squeezed == parts[0]
    first, last = parts[0], parts[-1]
    if len(squeezed) < len(first) + len(last):
        return False
    if not squeezed.startswith(first) or not squeezed.endswith(last):
        return False
    position = len(first)
    limit = len(squeezed) - len(last)
    for part in parts[1:-1]:
        if not part:
            continue
        found = squeezed.find(part, position, limit)
        if found < 0:
            return False
        position = found + len(part)
    return True


def holds(texts, system, user):
    """Whether a request holds the texts the catalog has of a call that was made for a
    prompt: each of them stands in its system prompt or in its user prompt, and at least
    one of them is a part of that prompt and not all of it. A request whose prompts ARE
    these texts is the prompt itself, not a call that was made about it."""
    inside = False
    for part in texts:
        if part == system or part == user:
            continue
        if part in system or part in user:
            inside = True
        else:
            return False
    return inside


def run_order(run_id):
    """Sorts run ids so that the newest is the largest: numbers by their value, anything else as text behind them."""
    try:
        return (0, float(run_id), '')
    except ValueError:
        return (1, 0.0, run_id)


def sas_seconds(value):
    """A point in time as seconds since 1960, read from a SAS datetime, a datetime or a text
    such as '2026-01-05 09:15:00' or '2026-01-05T09:15:00+00:00'. A text with an offset is
    turned to UTC, one without is taken as it is. None when the value is no time."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)
    if isinstance(value, datetime):
        moment = value
    else:
        raw = text(value).strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            pass
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            try:
                moment = datetime.strptime(raw[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return (moment - SAS_EPOCH).total_seconds()


# ---------------------------------------------------------------------------
# The catalog as an index
# ---------------------------------------------------------------------------

def build_index(catalog, runs=None, min_literal=20):
    """Turns the catalog and the ingestion runs into what a log row is looked up in.

    Nothing here depends on the number of log rows, and no log row is ever compared
    with the whole catalog: a model is found by its name, an experiment run by its
    prompts, a decision template by its questions, a prompt template by the start of
    or the end of its system prompt, a call of an optimization or of a judge by the
    model it was sent to, a RAG setup by its collection and an ingestion run by its
    embedding model.

    A catalog row whose content can not be read - questions that are nested deeper
    than Python reads JSON, say - is left out and named in index['skipped'].

    Args:
        catalog (pandas.DataFrame): MODEL_CATALOG, column names in lower case.
        runs (pandas.DataFrame): RAG_RUNS, column names in lower case, or None.
        min_literal (int): The fixed text a template needs to take part.

    Returns:
        dict: The index.
    """
    index = {
        'models': {},        # container name -> [(role, modelID, projectID)]
        'callers': {},       # modelID -> what the link columns say of the caller
        'runs': {},          # (space, system key, hash of the user prompt) -> [experiment run]
        'run_times': {},     # (caller, run id, model the run was sent to) -> the run time the catalog holds, None where it holds none
        'run_ids': set(),    # (caller, run id) of every experiment run of the catalog
        'exact': {},         # squeezed system prompt without a variable -> [prompt pattern]
        'prefix': {},        # start of a system prompt with a variable -> [prompt pattern]
        'suffix': {},        # end of a system prompt that starts with a variable -> [prompt pattern]
        'open': [],          # prompt patterns whose system prompt starts and ends with a variable
        'questions': {},     # questions key -> [decision pattern]
        'calls': {},         # (role, model) -> the calls an optimization or a judge sent to the model for a prompt
        'collections': {},   # collection -> [modelID of a RAG setup]
        'setups': {},        # modelID of a RAG setup -> its name and who wrote it
        'builder_setups': set(),  # modelIDs of the RAG setups the Prompt Builder saved
        'rag_targets': {},   # modelID of a RAG setup -> the embedding models it is known to call
        'ingestions': {},    # embedding model -> [ingestion run]
        'run_setups': {},    # ingestion run id -> modelIDs of the setups that hold the run
        'run_mates': {},     # modelID of a RAG setup -> the setups that hold an ingestion run it holds
        'decided': {},       # the setups of a collection -> the ones among them that are taken
        'short': set(),      # callers with a template that is too short to take part
        'skipped': [],       # (name, modelID, error) of the catalog rows that could not be read
        'unusable_calls': 0,
        'patterns': 0,
        'runs_without_window': 0,
    }
    patterns = {}
    weak_callers = set()
    calls = {}

    def pattern_of(key, where, first):
        pattern = patterns.get(key)
        if pattern is None:
            pattern = dict(first, targets=set(), any_target=False)
            patterns[key] = pattern
            where.append(pattern)
            index['patterns'] += 1
        return pattern

    def add_target(pattern, target):
        if target:
            pattern['targets'].add(target)
        else:
            pattern['any_target'] = True

    def add_caller(model_id, project_id, kind, name, weak=False):
        """A row of the prompt, the decision template or the setup itself says what kind the caller
        is. The row of a call that was made for it only says so until such a row is read."""
        if model_id not in index['callers'] or (model_id in weak_callers and not weak):
            index['callers'][model_id] = {'caller_model_id': model_id, 'caller_project_id': project_id, 'caller_kind': kind, 'caller_name': name}
            weak_callers.discard(model_id)
            if weak:
                weak_callers.add(model_id)

    def add_record(record):
        # A column the catalog does not have reads as a missing value
        cell = record.get
        model_id = squeeze(cell('modelid'))
        role = squeeze(cell('role')).lower()
        name = squeeze(cell('name'))
        if not model_id or not role:
            return

        if role in KIND_OF_ROLE:
            entry = (role, model_id, squeeze(cell('projectid')))
            known = index['models'].setdefault(name.lower(), [])
            if name and entry[:2] not in [(known_role, known_id) for known_role, known_id, _ in known]:
                known.append(entry)
            return
        target = squeeze(cell('target_model')).lower()
        run_id = squeeze(cell('runid'))
        resolved_system = squeeze(cell('resolved_system_prompt'))
        resolved_user = squeeze(cell('resolved_user_prompt'))
        run_time = number(cell('run_time'))

        if role in CALL_ROLES:
            # A call the Builder made for a prompt: the texts the request must hold, the models it
            # was sent to and, where the catalog knows it, the time it was sent in
            texts = tuple(part for part in (resolved_system, resolved_user) if part)
            targets = {part for part in (squeeze(piece) for piece in target.split(',')) if part}
            start_text, end_text = squeeze(cell('window_start')), squeeze(cell('window_end'))
            start, end = sas_seconds(start_text), sas_seconds(end_text)
            usable = (sum(size(part) for part in texts) >= min_literal and targets
                      and (start is not None or not start_text) and (end is not None or not end_text)
                      and (start is None or end is None or start <= end))
            if not usable:
                index['unusable_calls'] += 1
                return
            add_caller(model_id, squeeze(cell('projectid')), 'prompt', name, weak=True)
            key = (role, model_id, run_id, squeeze(cell('optimization_id')), texts, start, end)
            call = patterns.get(key)
            if call is None:
                # The log has whole seconds, so the window is taken with the seconds it starts and ends in
                call = {'caller': model_id, 'texts': texts, 'literal': sum(size(part) for part in texts), 'run': run_id, 'run_time': run_time,
                        'start': None if start is None else math.floor(start), 'end': None if end is None else math.ceil(end), 'models': set()}
                patterns[key] = call
                index['patterns'] += 1
            for model in targets - call['models']:
                call['models'].add(model)
                calls.setdefault((role, model), []).append(call)
            return
        if role not in ('prompt', 'decision', 'rag'):
            return

        if role == 'rag':
            add_caller(model_id, squeeze(cell('projectid')), role, name)
            collection = squeeze(cell('collection'))
            if collection:
                setups = index['collections'].setdefault(collection, [])
                if model_id not in setups:
                    setups.append(model_id)
            if target:
                index['rag_targets'].setdefault(model_id, set()).add(target)
            source = squeeze(cell('template_source')).lower()
            index['setups'].setdefault(model_id, {'name': name, 'source': source})
            if source == BUILDER_SETUP:
                index['builder_setups'].add(model_id)
            return

        system_template = text(cell('system_template'))
        user_template = text(cell('user_template'))
        system_hash, user_hash = cell('resolved_system_hash'), cell('resolved_user_hash')
        system_parts, system_size = template_parts(system_template)
        user_parts, user_size = template_parts(user_template)
        # Everything that reads the content of the row is done before the index is touched, so a row
        # that can not be read leaves nothing of itself behind
        template_keys, run_keys = [], []
        if role == 'decision':
            # A decision: the system template is the questions JSON, the user template the state
            if size(system_template) >= min_literal:
                template_keys = question_keys(system_template)
            if run_id:
                resolved_questions = text(cell('resolved_system_prompt')) if resolved_system else system_template
                if not resolved_user and len(user_parts) == 1:
                    resolved_user = user_parts[0]
                    user_hash = ''
                if resolved_user and size(resolved_questions) >= min_literal:
                    # The questions by their JSON and their text, and by the hash the catalog took of the whole text
                    stored = squeeze(system_hash).lower()
                    run_keys = [('D', key, hash_or(user_hash, resolved_user)) for key in question_keys(resolved_questions)
                                + ['H:' + (stored if resolved_system and TEXT_HASH.fullmatch(stored) else text_hash(resolved_questions))]]
        elif run_id:
            # A run without resolved prompts is its templates when they hold no variable
            if not resolved_system and not resolved_user and len(system_parts) == 1 and len(user_parts) == 1:
                resolved_system, resolved_user = system_parts[0], user_parts[0]
                system_hash = user_hash = ''
            if size(resolved_system) + size(resolved_user) >= min_literal:
                run_keys = [('P', hash_or(system_hash, resolved_system), hash_or(user_hash, resolved_user))]

        add_caller(model_id, squeeze(cell('projectid')), role, name)
        if run_id:
            index['run_ids'].add((model_id, run_id))
            index['run_times'][(model_id, run_id, target)] = run_time
        for key in run_keys:
            index['runs'].setdefault(key, []).append({'caller': model_id, 'run': run_id, 'target': target, 'run_time': run_time})

        if role == 'prompt':
            if system_size + user_size >= min_literal:
                if len(system_parts) == 1:
                    where = index['exact'].setdefault(system_parts[0], [])
                elif len(system_parts[0]) >= PREFIX_LENGTH:
                    where = index['prefix'].setdefault(system_parts[0][:PREFIX_LENGTH], [])
                elif len(system_parts[-1]) >= PREFIX_LENGTH:
                    where = index['suffix'].setdefault(system_parts[-1][-PREFIX_LENGTH:], [])
                else:
                    where = index['open']
                pattern = pattern_of((model_id, system_parts, user_parts), where,
                                     {'caller': model_id, 'system': system_parts, 'user': user_parts, 'literal': system_size + user_size})
                add_target(pattern, target)
            elif system_size + user_size > 0 or len(system_parts) + len(user_parts) > 2:
                index['short'].add(model_id)
        elif template_keys:
            for key in template_keys:
                where = index['questions'].setdefault(key, [])
                pattern = pattern_of((model_id, key, user_parts), where,
                                     {'caller': model_id, 'state': user_parts, 'literal': user_size})
                add_target(pattern, target)
        elif size(system_template) > 0:
            index['short'].add(model_id)

    for catalog_row in catalog.to_dict('records'):
        try:
            add_record(catalog_row)
        except Exception as error:
            index['skipped'].append((squeeze(catalog_row.get('name')), squeeze(catalog_row.get('modelid')), type(error).__name__))

    for key, found in calls.items():
        entry = {'calls': found, 'anchors': None, 'width': 0, 'at_words': True}
        if len(found) > CALLS_COMPARED:
            width = max(1, min(CALL_ANCHOR, min(max(len(part) for part in call['texts']) for call in found)))
            # The calls for one prompt hold the same system prompt, and their user prompts start alike and end
            # alike. Of five pieces at even distances in each of its texts, a call is found through the one that
            # the fewest calls share. A piece starts where a word of the text starts, so that it is only looked
            # for where a word of a prompt starts - unless a text has no such place, and then at every place
            pieces = []
            for call in found:
                of_call = []
                for part in call['texts']:
                    starts = [place + 1 for place in range(len(part) - width) if part[place] == ' ']
                    of_call += [part[starts[len(starts) * step // 5]:][:width] for step in range(5)] if starts else []
                if not of_call:
                    entry['at_words'] = False
                    of_call = [part[:width] for part in call['texts'] if len(part) >= width]
                pieces.append(of_call)
            shared = {}
            for of_call in pieces:
                for piece in set(of_call):
                    shared[piece] = shared.get(piece, 0) + 1
            entry['width'], entry['anchors'] = width, {}
            for call, of_call in zip(found, pieces):
                entry['anchors'].setdefault(min(of_call, key=lambda piece: shared[piece]), []).append(call)
        index['calls'][key] = entry

    if runs is not None and len(runs) > 0:
        seen = {}
        for record in runs.to_dict('records'):
            run_cell = record.get
            run_id = squeeze(run_cell('run_id'))
            embed_model = squeeze(run_cell('embed_model')).lower()
            start = sas_seconds(run_cell('started_at'))
            end = sas_seconds(run_cell('finished_at'))
            setup = squeeze(run_cell('modelid'))
            collection = squeeze(run_cell('collection'))
            if run_id and setup:
                index['run_setups'].setdefault(run_id, set()).add(setup)
            if not embed_model:
                continue
            # A run that never finished has no end, and its window would take every later request
            if start is None or end is None or end < start:
                index['runs_without_window'] += 1
                continue
            key = (embed_model, run_id or (start, end, collection))
            ingestion = seen.get(key)
            if ingestion is None:
                ingestion = {'run': run_id, 'start': start, 'end': end, 'collection': collection, 'setups': set()}
                seen[key] = ingestion
                index['ingestions'].setdefault(embed_model, []).append(ingestion)
            if setup:
                ingestion['setups'].add(setup)
        for holders in index['run_setups'].values():
            if len(holders) > 1:
                for setup in holders:
                    index['run_mates'].setdefault(setup, set()).update(holders)
        # A run table that names no setup: the setup is the one that holds the collection of the run
        for ingestions in index['ingestions'].values():
            for ingestion in ingestions:
                if not ingestion['setups']:
                    ingestion['setups'] = set(index['collections'].get(ingestion['collection'], []))
                ingestion['setups'] = set(saved_setups(index, ingestion['setups']))
    return index


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------

def choose(entries):
    """Picks the caller among (caller, score) pairs. The smaller score is the better
    one and a caller counts with its best. Returns the caller, or None when two callers
    share the best score, with the number of callers and the best score."""
    best = {}
    for caller, score in entries:
        if caller not in best or score < best[caller]:
            best[caller] = score
    top = min(best.values())
    winners = [caller for caller, score in best.items() if score == top]
    return (winners[0] if len(winners) == 1 else None), len(best), top


def tier(pattern, model):
    """0 when the pattern can have been sent to the model of the row: the catalog names
    that model for it, or names none. 1 when the catalog only names other models - such
    a caller is taken when no caller of tier 0 fits."""
    return 0 if pattern['any_target'] or model in pattern['targets'] else 1


def runs_with_prompts(index, space, system_keys, user, model):
    """The experiment runs whose resolved prompts are the prompts of a row and that were
    sent to the model of the row. The user prompt is compared by its hash, the system
    prompt by its hash or, for the questions of a decision, by the keys of the questions."""
    found = {}
    for key in system_keys:
        for run in index['runs'].get((space, key, user), ()):
            if run['target'] in ('', model):
                found.setdefault((run['caller'], run['run']), run)
    return list(found.values())


def claim_run(found, runtime):
    """What the rule 'experiment run' makes of the runs that hold the prompts of a row.

    A run was one request per model, and the tracker keeps the run time that request
    returned: the row is the request of the run whose run time is its own. Returns

    * ('fits', runs) - the runs whose run time is the one of the row, the closest and then
      the oldest first. Which of them the row gets is decided over all rows, as a run is
      given to one row only;
    * ('link', match) - no run time decides: the runs are of two callers, which is
      ambiguous, or the catalog holds no run time for them, and then the newest of them is
      taken as before the catalog had run times, with their number as the candidates;
    * ('none', None) - the prompts are those of runs, but no run has the run time of the
      row: it is another request with the same prompts, and the rules after this one say
      who sent it.
    """
    timed = [run for run in found if run['run_time'] is not None]
    fitting = [] if runtime is None else [run for run in timed if same_time(run['run_time'], runtime)]
    if fitting:
        if len({run['caller'] for run in fitting}) > 1:
            return 'link', {'match_method': 'ambiguous', 'match_candidates': len(fitting)}
        return 'fits', sorted(fitting, key=lambda run: (abs(run['run_time'] - runtime), run_order(run['run'])))
    untimed = [run for run in found if run['run_time'] is None]
    if not untimed:
        return 'none', None
    if len({run['caller'] for run in untimed}) > 1:
        return 'link', {'match_method': 'ambiguous', 'match_candidates': len(untimed)}
    newest = max((run['run'] for run in untimed), key=run_order)
    return 'link', {'caller': untimed[0]['caller'], 'match_method': 'experiment run', 'match_candidates': len(untimed), 'experiment_run_id': newest, 'untimed': True}


def fitting_patterns(index, system, user, cache):
    """The prompt patterns a squeezed system and user prompt fit. Kept per pair of
    prompts, as a log holds the same prompts many times."""
    key = (system, user)
    fitting = cache.get(key)
    if fitting is None:
        fitting = [pattern for pattern in index['exact'].get(system, ()) if fits(pattern['user'], user)]
        for candidates in (index['prefix'].get(system[:PREFIX_LENGTH], ()), index['suffix'].get(system[-PREFIX_LENGTH:], ()), index['open']):
            fitting += [pattern for pattern in candidates if fits(pattern['system'], system) and fits(pattern['user'], user)]
        cache[key] = fitting
    return fitting


def match_template(index, system, user, model, cache):
    """Rule 'template': the system and the user prompt of the row fit the templates of a prompt."""
    fitting = fitting_patterns(index, system, user, cache)
    if not fitting:
        return None
    caller, callers, _ = choose((pattern['caller'], (tier(pattern, model), -pattern['literal'])) for pattern in fitting)
    if caller is None:
        return {'match_method': 'ambiguous', 'match_candidates': callers}
    return {'caller': caller, 'match_method': 'template', 'match_candidates': callers}


def calls_held(index, role, model, system, user, cache):
    """The calls of a role that were sent to a model and whose texts the prompts of a row
    hold. Kept per pair of prompts."""
    key = (role, model, system, user)
    held = cache.get(key)
    if held is None:
        entry = index['calls'][(role, model)]
        if entry['anchors'] is None:
            candidates = entry['calls']
        else:
            width, anchors, found = entry['width'], entry['anchors'], {}
            for prompt in (system, user):
                if entry['at_words']:
                    start = prompt.find(' ') + 1
                    while start > 0:
                        for call in anchors.get(prompt[start:start + width], ()):
                            found[id(call)] = call
                        start = prompt.find(' ', start) + 1
                else:
                    for start in range(len(prompt) - width + 1):
                        for call in anchors.get(prompt[start:start + width], ()):
                            found[id(call)] = call
            candidates = list(found.values())
        held = [call for call in candidates if holds(call['texts'], system, user)]
        cache[key] = held
    return held


def match_call(index, role, system, user, model, moment, runtime, cache):
    """Rules 'optimization' and 'judge': the request is a call the Builder made for a
    prompt - the calls of a prompt optimization, the call of a judge that ranks the
    answers of a run. The catalog holds for each of them the texts the request must hold,
    the model it was sent to and, where it is known, the time it was sent in. All three
    must fit. A judge call gets the run that was judged when the catalog leaves one."""
    if (role, model) not in index['calls']:
        return None
    fitting = [call for call in calls_held(index, role, model, system, user, cache)
               if (call['start'] is None and call['end'] is None)
               or (moment is not None and (call['start'] is None or moment >= call['start']) and (call['end'] is None or moment <= call['end']))]
    if not fitting:
        return None
    caller, callers, top = choose((call['caller'], -call['literal']) for call in fitting)
    if caller is None:
        return {'match_method': 'ambiguous', 'match_candidates': callers}
    match = {'caller': caller, 'match_method': role, 'match_candidates': callers}
    if role == 'judge':
        best = [call for call in fitting if call['caller'] == caller and -call['literal'] == top]
        judged = {call['run'] for call in best}
        if len(judged) > 1 and runtime is not None:
            timed = {call['run'] for call in best if call['run_time'] is not None and same_time(call['run_time'], runtime)}
            judged = timed if len(timed) == 1 else judged
        if len(judged) == 1:
            match['experiment_run_id'] = judged.pop()
    return match


def match_questions(index, keys, state, model, cache):
    """Rules 'questions and state' and 'questions': the questions of the row are the
    questions of a decision template. A template whose state has fixed text that the
    state of the row fits is ahead of one that has none or does not fit."""
    entries = []
    for key in keys:
        for pattern in index['questions'].get(key, ()):
            state_key = (pattern['state'], state)
            state_fits = cache.get(state_key)
            if state_fits is None:
                state_fits = pattern['literal'] > 0 and fits(pattern['state'], state)
                cache[state_key] = state_fits
            entries.append((pattern['caller'], (tier(pattern, model), 0 if state_fits else 1, -pattern['literal'] if state_fits else 0)))
    if not entries:
        return None
    caller, callers, top = choose(entries)
    if caller is None:
        return {'match_method': 'ambiguous', 'match_candidates': callers}
    return {'caller': caller, 'match_method': 'questions and state' if top[1] == 0 else 'questions', 'match_candidates': callers}


def registered_for(index, step, builder):
    """Whether a model of a collection is the registration of the setup the Prompt Builder
    saved for that collection. What the catalog and RAG_RUNS let be seen of it: who wrote
    the model, the embedding model of both, the name - the step RAG - Register Setup names
    its model 'RAG Retrieval - ' and the setup - and the ingestion runs both hold. A model
    the step did not write, another embedding model, or another name without a common
    ingestion run: it is another setup with the same collection name."""
    described, saved = index['setups'].get(step, {}), index['setups'].get(builder, {})
    if described.get('source') != STEP_SETUP:
        return False
    targets, saved_targets = index['rag_targets'].get(step, set()), index['rag_targets'].get(builder, set())
    if targets and saved_targets and not targets.intersection(saved_targets):
        return False
    if saved.get('name') and described.get('name', '').lower() == (STEP_MODEL_PREFIX + saved['name']).lower():
        return True
    return builder in index['run_mates'].get(step, ())


def saved_setups(index, setups):
    """The setup among several of one collection that the Prompt Builder saved, when it
    saved exactly one of them and every other one is its registration - otherwise the
    setups as they are. A setup that was saved in the Prompt Builder and registered with
    the step RAG - Register Setup is two models with one collection and the same ingestion
    runs: the model the Builder saved is the one that holds the setup."""
    key = tuple(sorted(setups))
    decided = index['decided'].get(key)
    if decided is None:
        decided = list(setups)
        saved = [setup for setup in decided if setup in index['builder_setups']]
        if len(decided) > 1 and len(saved) == 1 and all(registered_for(index, setup, saved[0]) for setup in decided if setup != saved[0]):
            decided = saved
        index['decided'][key] = decided
    return decided


def setup_of_collection(index, collection, user_prompt=''):
    """The RAG setup a collection belongs to: the one setup the catalog has for it. When
    several setups hold the collection, the ingestion run the retrieval names decides,
    if exactly one of them holds that run, and after that the one setup among them that
    the Prompt Builder saved and the others are the registration of. The empty text when
    nothing decides."""
    setups = index['collections'].get(collection, ())
    if len(setups) == 1:
        return setups[0]
    if len(setups) > 1 and user_prompt:
        # Of several retrievals in one prompt the last one counts: the log parser takes the
        # collection and the question of that one, and the run is taken of the same
        named = INGESTION_RUN.findall(user_prompt)
        if named:
            holding = [setup for setup in setups if setup in index['run_setups'].get(named[-1], ())]
            if len(holding) == 1:
                return holding[0]
    setups = saved_setups(index, setups)
    return setups[0] if len(setups) == 1 else ''


def match_query(index, evidence, input_hash, moment, model, window):
    """Rule 'retrieval query': the text an embedding model got in query mode is the query
    of a retrieval, and the prompt that carries that retrieval follows within the window.
    The row gets the setup of the collection the nearest such prompt names. The same
    question is embedded many times and for more than one collection, so a prompt that
    came hours later, or before the embedding, says nothing about this request."""
    seen = evidence.get(input_hash)
    if not input_hash or not seen or moment is None:
        return None
    position = bisect_left(seen['times'], moment)
    if position == len(seen['times']) or seen['times'][position] - moment > window:
        return None
    nearest = seen['times'][position]
    carried = []
    while position < len(seen['times']) and seen['times'][position] == nearest:
        carried.append(seen['prompts'][position])
        position += 1
    collections = {collection for collection, _ in carried}
    if len(collections) > 1:
        return {'match_method': 'ambiguous', 'match_candidates': len(collections)}
    setups = saved_setups(index, index['collections'].get(collections.pop(), ()))
    if not setups:
        return None
    named = {setup for _, setup in carried}
    if len(named) == 1 and '' not in named:
        setup = named.pop()
    elif len(setups) == 1:
        setup = setups[0]
    else:
        return {'match_method': 'ambiguous', 'match_candidates': len(setups)}
    # A setup that is known to embed with another model did not send this request
    targets = index['rag_targets'].get(setup)
    if targets and model not in targets:
        return None
    return {'caller': setup, 'match_method': 'retrieval query', 'match_candidates': 1}


def match_ingestion(index, moment, model, cache):
    """Rule 'ingestion run': the request lies inside exactly one ingestion run of its
    embedding model. Inside two runs it belongs to neither."""
    if moment is None:
        return None
    key = (model, moment)
    if key not in cache:
        inside = [ingestion for ingestion in index['ingestions'].get(model, ()) if ingestion['start'] <= moment <= ingestion['end']]
        if not inside:
            cache[key] = None
        elif len(inside) > 1:
            cache[key] = {'match_method': 'ambiguous', 'match_candidates': len(inside)}
        elif len(inside[0]['setups']) == 1:
            cache[key] = {'caller': next(iter(inside[0]['setups'])), 'match_method': 'ingestion run', 'match_candidates': 1}
        elif inside[0]['setups']:
            cache[key] = {'match_method': 'ambiguous', 'match_candidates': len(inside[0]['setups'])}
        else:
            cache[key] = None
    return cache[key]


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def link_rows(rows, catalog, runs=None, redo_all=False, min_literal=20, utc_offset=0.0, query_window=300.0):
    """Links the rows of a log table to the catalog.

    Args:
        rows (pandas.DataFrame): The log rows with CONTENT_COLUMNS and LINK_COLUMNS, and the
            OPTIONAL_COLUMNS the table has, names in lower case. The prompts of a row that is
            not linked again may be missing.
        catalog (pandas.DataFrame): MODEL_CATALOG, names in lower case.
        runs (pandas.DataFrame): RAG_RUNS, names in lower case, or None.
        redo_all (bool): True links every row, False only the rows without a match_method.
        min_literal (int): The fixed text a template needs to take part.
        utc_offset (float): The hours the clock of the log is ahead of UTC.
        query_window (float): The seconds within which the prompt of a retrieval follows its query.

    Returns:
        tuple: A frame of the LINK_COLUMNS with one row per log row in the order of
        the rows - what was there for a row that was left alone - and the summary.
    """
    index = build_index(catalog, runs, min_literal)
    records = rows.to_dict('records')
    doubles = {}

    def model_of(name, kind):
        """The modelID of the model that answered, and the kind of the row when it has none."""
        known = index['models'].get(name, ())
        if not kind:
            roles = {role for role, _, _ in known}
            kind = KIND_OF_ROLE[roles.pop()] if len(roles) == 1 else ''
        of_kind = [entry for entry in known if entry[0] == ANSWER_ROLE.get(kind)] or list(known)
        if len(of_kind) > 1:
            # Two models with one name: the name of the container does not say which of them answered
            double = doubles.setdefault((name, of_kind[0][0] if len({role for role, _, _ in of_kind}) == 1 else 'model'), {'models': of_kind, 'rows': 0})
            double['rows'] += 1
        return (of_kind[0][1] if len(of_kind) == 1 else ''), kind

    todo = [redo_all or not squeeze(record.get('match_method')) for record in records]
    models = [squeeze(record.get('model')).lower() for record in records]
    moments = [sas_seconds(record.get('timestamp')) for record in records]
    kinds = []
    model_ids = []
    for record, model in zip(records, models):
        model_id, kind = model_of(model, squeeze(record.get('kind')).lower())
        kinds.append(kind)
        model_ids.append(model_id)

    # A row whose content can not be compared - questions that are nested deeper than Python
    # reads JSON, say - is not linked, and the rows after it are
    failed = {}

    # The retrievals the LLM rows of the whole table carry, by the hash of their query and in
    # the order of their time: they are what an embedding row in query mode is linked through
    setups = [''] * len(records)
    carried = {}
    for position, record in enumerate(records):
        collection = squeeze(record.get('rag_collection'))
        if kinds[position] != 'llm' or not collection:
            continue
        try:
            setups[position] = setup_of_collection(index, collection, text(record.get('user_prompt')))
        except Exception as error:
            if todo[position]:
                failed[position] = type(error).__name__
        query_hash = squeeze(record.get('rag_query_hash')).lower()
        if query_hash and moments[position] is not None:
            carried.setdefault(query_hash, []).append((moments[position], collection, setups[position]))
    evidence = {}
    for query_hash, prompts in carried.items():
        prompts.sort(key=lambda prompt: prompt[0])
        evidence[query_hash] = {'times': [moment for moment, _, _ in prompts], 'prompts': [(collection, setup) for _, collection, setup in prompts]}

    # Rule 'experiment run', over all rows: a run was one request for each of its models, so a run
    # is given to one row only. The runs that rows of earlier runs of this program hold are taken.
    given = set()
    stale = {'gone': 0, 'other time': 0}
    for position, record in enumerate(records):
        if todo[position]:
            continue
        method, caller, run_id = squeeze(record.get('match_method')), squeeze(record.get('caller_model_id')), squeeze(record.get('experiment_run_id'))
        if not run_id or method not in ('experiment run', 'judge'):
            continue
        # The id of a run is its place in the tracker, and the Builder numbers the runs again when one
        # is deleted: a row that was linked earlier may name a run the catalog no longer has under that id
        if (caller, run_id) not in index['run_ids']:
            stale['gone'] += 1
        elif method == 'experiment run':
            given.add((caller, run_id, models[position]))
            known = index['run_times'].get((caller, run_id, models[position]), index['run_times'].get((caller, run_id, '')))
            runtime = number(record.get('runtime'))
            if known is not None and runtime is not None and not same_time(known, runtime):
                stale['other time'] += 1

    pattern_cache, call_cache, state_cache, window_cache = {}, {}, {}, {}
    counted = {'untimed runs': 0}

    def by_content(kind, first, second, model, moment, runtime):
        """The rules after 'experiment run': for an LLM row, of its squeezed prompts, the template and then
        the calls made for a prompt - for a decision row, of the keys of its questions and its state, the questions."""
        if kind == 'llm':
            match = match_template(index, first, second, model, pattern_cache)
            for role in CALL_ROLES:
                match = match or match_call(index, role, first, second, model, moment, runtime, call_cache)
            return match
        return match_questions(index, first, second, model, state_cache)

    def close(link, match):
        """Writes what a rule found into the link columns of a row."""
        if match is None:
            match = {'match_method': 'none', 'match_candidates': 0}
        counted['untimed runs'] += 1 if match.get('untimed') else 0
        link['match_method'] = match['match_method']
        link['match_candidates'] = match['match_candidates']
        link['experiment_run_id'] = match.get('experiment_run_id', '')
        if 'caller' in match:
            link.update(index['callers'].get(match['caller'], {'caller_model_id': match['caller']}))

    # The rows whose run time is the one of a run with their prompts. Which of them gets the run is
    # decided once all rows were seen, and only these rows are kept with their prompts until then
    waiting = []
    links = []
    for position, record in enumerate(records):
        if not todo[position]:
            link = {column: text(record.get(column)) for column in LINK_TEXT_COLUMNS}
            link['match_candidates'] = record.get('match_candidates')
            links.append(link)
            continue

        kind, model = kinds[position], models[position]
        link = dict.fromkeys(LINK_TEXT_COLUMNS, '')
        link['model_id'] = model_ids[position]
        if kind == 'llm':
            link['rag_model_id'] = setups[position]
        links.append(link)
        match = None
        try:
            moment = None if moments[position] is None else moments[position] - utc_offset * 3600.0
            if position in failed:
                pass
            elif kind in ('llm', 'decision'):
                runtime = number(record.get('runtime'))
                # An experiment run is a request of the Builder page. A published model that sends the prompts
                # of a run again is the same caller, but its request is not that run
                may_be_run = bool(index['runs']) and not squeeze(record.get('user_agent')).lower().startswith(NOT_A_BROWSER)
                found = []
                if kind == 'llm':
                    first, second = squeeze(record.get('system_prompt')), squeeze(record.get('user_prompt'))
                    if may_be_run:
                        found = runs_with_prompts(index, 'P', [text_hash(first)], text_hash(second), model)
                else:
                    first, second = question_keys(record.get('system_prompt')), squeeze(record.get('user_prompt'))
                    if may_be_run:
                        found = runs_with_prompts(index, 'D', first + ['H:' + text_hash(record.get('system_prompt'))], text_hash(second), model)
                decided, result = claim_run(found, runtime) if found else ('none', None)
                if decided == 'fits':
                    waiting.append((moments[position] if moments[position] is not None else float('inf'), position, result, kind, first, second, moment, runtime))
                    continue
                match = result if decided == 'link' else by_content(kind, first, second, model, moment, runtime)
            elif kind == 'embedding':
                mode = squeeze(record.get('embedding_mode')).lower()
                if mode == 'query':
                    match = match_query(index, evidence, squeeze(record.get('input_hash')).lower(), moments[position], model, query_window)
                elif mode == 'document' and squeeze(record.get('project')).lower() in ('', RUNTIME_PROJECT):
                    # The RAG runtime names itself in the project input of its requests. A document that
                    # another program embedded while a run was under way is no chunk of that run
                    match = match_ingestion(index, moment, model, window_cache)
        except Exception as error:
            failed[position] = type(error).__name__
            match = None
        close(link, match)

    # The earlier request gets the run. A second request with the same prompts and the same run time finds
    # the run given, and the rules after this one say who sent it
    for _, position, fitting, kind, first, second, moment, runtime in sorted(waiting, key=lambda entry: entry[:2]):
        match = None
        for run in fitting:
            key = (run['caller'], run['run'], models[position])
            if key not in given:
                given.add(key)
                match = {'caller': run['caller'], 'match_method': 'experiment run', 'match_candidates': len(fitting), 'experiment_run_id': run['run']}
                break
        if match is None:
            try:
                match = by_content(kind, first, second, models[position], moment, runtime)
            except Exception as error:
                failed[position] = type(error).__name__
        close(links[position], match)

    summary = {'rows': len(records), 'linked': sum(todo), 'left': len(records) - sum(todo), 'kinds': {},
               'patterns': index['patterns'], 'short': len(index['short']), 'runs_without_window': index['runs_without_window'],
               'untimed_runs': counted['untimed runs'], 'stale_runs': stale, 'failed_rows': failed, 'skipped': index['skipped'],
               'unusable_calls': index['unusable_calls'], 'doubles': doubles}
    for kind, link in zip(kinds, links):
        counts = summary['kinds'].setdefault(kind if kind in METHODS else 'unknown', {'rows': 0, 'model': 0, 'rag': 0, 'methods': {}})
        counts['rows'] += 1
        counts['model'] += 1 if link['model_id'] else 0
        counts['rag'] += 1 if link['rag_model_id'] else 0
        method = link['match_method'] or 'not linked yet'
        counts['methods'][method] = counts['methods'].get(method, 0) + 1
    return pd.DataFrame(links, columns=LINK_COLUMNS), summary


def summary_lines(summary):
    """The summary as the lines of the SAS log: one per kind of row. They hold no semicolon:
    a line of the SAS log is a SAS statement on its way there."""
    lines = []
    for kind in ['llm', 'decision', 'embedding', 'unknown']:
        counts = summary['kinds'].get(kind)
        if counts is None:
            continue
        methods = dict(counts['methods'])
        named = [f"{method} {methods.pop(method, 0)}" for method in METHODS.get(kind, [])]
        named += [f"{method} {methods.pop(method, 0)}" for method in ['ambiguous', 'none']]
        named += [f"{method} {count}" for method, count in sorted(methods.items())]
        label = 'rows of no known kind' if kind == 'unknown' else f"{kind} rows"
        line = f"{counts['rows']} {label}: {counts['model']} linked to a model, caller by {', '.join(named)}"
        if kind == 'llm':
            line += f", and {counts['rag']} linked to a RAG setup"
        lines.append(line + '.')
    return lines


def named_first(entries):
    """The first entries of a list for a message, and how many more there are."""
    entries = list(entries)
    more = len(entries) - NAMED_IN_A_MESSAGE
    return ', '.join(entries[:NAMED_IN_A_MESSAGE]) + (f" and {more} more" if more > 0 else '')


def warning_lines(summary):
    """What a run has to say about the catalog and about rows it linked earlier, as the WARNING
    lines of the SAS log. They are written whether or not a row was linked."""
    lines = []
    for name, model_id, error in summary['skipped'][:NAMED_IN_A_MESSAGE]:
        lines.append(f"The catalog row of {name or 'a model without a name'} ({model_id}) could not be read - {error} - and takes no part in the links.")
    if len(summary['skipped']) > NAMED_IN_A_MESSAGE:
        lines.append(f"{len(summary['skipped']) - NAMED_IN_A_MESSAGE} more catalog rows could not be read and take no part in the links.")
    if summary['failed_rows']:
        errors = sorted(set(summary['failed_rows'].values()))
        lines.append(f"{len(summary['failed_rows'])} row(s) could not be compared with the catalog - {named_first(errors)} - and got match_method none.")
    stale = summary['stale_runs']
    if stale['gone'] or stale['other time']:
        lines.append(f"{stale['gone'] + stale['other time']} row(s) that were linked to an experiment run earlier no longer name that run: for {stale['gone']} the catalog has no run "
                     f"with their id, for {stale['other time']} the run with their id has another run time than the request. The id of a run is its place in the tracker "
                     "and changes when a run is deleted. Link all rows again with _lnk_redo_all = 1.")
    return lines


def note_lines(summary, settings):
    """What a run that linked rows has to say besides its counts, as the NOTE lines of the SAS log."""
    lines = []
    if summary['short']:
        lines.append(f"{summary['short']} prompt(s) or decision template(s) of the catalog have a template with less than {settings['min_literal']} characters of fixed text. "
                     "Such a template takes no part, as it would fit the requests of other callers.")
    if summary['unusable_calls']:
        lines.append(f"{summary['unusable_calls']} catalog row(s) of an optimization or a judge name no model, hold less than {settings['min_literal']} characters of text "
                     "or have a window that can not be read. Such a row takes no part.")
    if summary['runs_without_window']:
        lines.append(f"{summary['runs_without_window']} ingestion run(s) have no start or no end and take no part.")
    if summary['untimed_runs']:
        lines.append(f"{summary['untimed_runs']} row(s) were linked to an experiment run without a run time to compare, as the catalog holds none for the runs with their prompts. "
                     "They carry the newest of those runs, and match_candidates is the number of those runs.")
    for (name, role), double in list(summary['doubles'].items())[:NAMED_IN_A_MESSAGE]:
        described = [f"{model_id} in project {project_id or 'unknown'}" for _, model_id, project_id in double['models']]
        lines.append(f"The catalog holds {len(double['models'])} models named {name} with the role {role} - {named_first(described)} - so the name of the container does not say "
                     f"which of them answered, and model_id is empty in {double['rows']} row(s).")
    if len(summary['doubles']) > NAMED_IN_A_MESSAGE:
        lines.append(f"{len(summary['doubles']) - NAMED_IN_A_MESSAGE} more model names are held by more than one model of the catalog, and model_id is empty in their rows.")
    return lines


# ---------------------------------------------------------------------------
# CAS
# ---------------------------------------------------------------------------

class Refused(Exception):
    """A table the program does not link. The message is the ERROR line of the SAS log."""


def named(name):
    """A table name as a name literal of the DATA step."""
    return "'" + name.replace("'", "''") + "'n"


def quoted(value):
    return "'" + value.replace("'", "''") + "'"


def run_action(conn, action, **parameters):
    """Runs a CAS action and raises when it reports an error."""
    result = conn.retrieve(action, **parameters)
    if getattr(result, 'severity', 0) > 1:
        raise RuntimeError(f"The CAS action {action} failed: {getattr(result, 'reason', '')} {getattr(result, 'status', '')}".strip())
    return result


def columns_of(conn, caslib, name):
    """The columns of a CAS table in their order, as (name, label) pairs."""
    info = run_action(conn, 'table.columnInfo', table={'caslib': caslib, 'name': name})['ColumnInfo']
    labels = info['Label'] if 'Label' in info.columns else [''] * len(info)
    return [(str(column), text(label)) for column, label in zip(info['Column'], labels)]


def label_of(conn, caslib, name):
    """The label of a CAS table. The empty text when it has none or it can not be read."""
    try:
        info = conn.retrieve('table.tableInfo', caslib=caslib, name=name)['TableInfo']
        return text(info['Label'].iloc[0]).strip() if 'Label' in info.columns and len(info) > 0 else ''
    except Exception:
        return ''


def read_table(conn, caslib, name, columns=None, where=None):
    """Reads a CAS table page by page, as the fetch action hands out a limited number of
    rows per call. Column names come back in lower case."""
    table = {'caslib': caslib, 'name': name}
    if where:
        table['where'] = where
    frames = []
    first = 1
    while True:
        parameters = {'table': table, 'from': first, 'to': first + READ_PAGE - 1, 'maxRows': READ_PAGE, 'index': False, 'sasTypes': False}
        if columns:
            parameters['fetchVars'] = [{'name': column} for column in columns]
        page = run_action(conn, 'table.fetch', **parameters).get('Fetch')
        if page is None or len(page) == 0:
            break
        frames.append(pd.DataFrame(page))
        if len(page) < READ_PAGE:
            break
        first += READ_PAGE
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns or [])
    frame.columns = [str(column).lower() for column in frame.columns]
    return frame


def row_count(conn, caslib, name):
    return int(run_action(conn, 'simple.numRows', table={'caslib': caslib, 'name': name})['numrows'])


def link_table(conn, settings, log=print):
    """Reads the tables, links the rows and builds the new log table beside the old one.

    The new table is <log table>_NEW in the caslib of the log table: the rows of the log
    table in their order with every column they had and the label of the table, the link
    columns replaced. The SAS program takes it from there the way the parser replaces a
    table. When no row was linked no new table is built.

    Args:
        log: Called with a message and, for a WARNING, with 'WARNING' as the type of the message.

    Returns:
        tuple: The number of rows of the log table, 0 when it has none, and whether the
        new table was built.
    """
    log_caslib, log_table = settings['log_caslib'], settings['log_table']
    new_table = log_table + '_NEW'

    # A name with a line break can stand neither in a message nor in the code of a DATA step
    for name in (log_caslib, log_table, settings['catalog_caslib'], settings['catalog_table'], settings['runs_caslib'], settings['runs_table']):
        if any(not character.isprintable() for character in name):
            raise Refused(f"The settings name a table or a CAS library with a line break or another character that is no text: {name}. Nothing was linked.")

    described = columns_of(conn, log_caslib, log_table)
    present = {column.lower(): label for column, label in described}
    absent = [column for column in CONTENT_COLUMNS + LINK_COLUMNS if column not in present]
    if absent:
        raise Refused(f"The table {log_caslib}.{log_table} has no column {', '.join(absent)}: it was written by a Log-Parser-Code.sas or a step LLM - Log Parser "
                      f"that is older than the link columns. Parse a log into it with the parser of this release first. The table was left as it is.")
    catalog_columns = {column.lower() for column, _ in columns_of(conn, settings['catalog_caslib'], settings['catalog_table'])}
    absent = [column for column in CATALOG_KEY_COLUMNS if column not in catalog_columns]
    if absent:
        raise Refused(f"The table {settings['catalog_caslib']}.{settings['catalog_table']} has no column {', '.join(absent)}, so it is not the catalog "
                      f"that Get-All-Prompts.sas writes. Run Get-All-Prompts.sas of this release first. The table {log_caslib}.{log_table} was left as it is.")

    # The rows get a number in a copy, one thread so that the number is the place of the row
    run_action(conn, 'dataStep.runCode', single='yes',
               code=f"data {named(STAGE_TABLE)}(caslib='CASUSER'); set {named(log_table)}(caslib={quoted(log_caslib)}); _lnk_row = _n_; _lnk_one = 1; run;")
    small = [column for column in CONTENT_COLUMNS + LINK_COLUMNS + OPTIONAL_COLUMNS if column in present and column not in PROMPT_COLUMNS]
    rows = read_table(conn, 'CASUSER', STAGE_TABLE, ['_lnk_row'] + small)
    if len(rows) == 0:
        return 0, False
    # The prompts are only read of the rows that are linked
    prompts = read_table(conn, 'CASUSER', STAGE_TABLE, ['_lnk_row'] + PROMPT_COLUMNS, None if settings['redo_all'] else 'missing(match_method)')
    if len(prompts) > 0:
        rows = rows.merge(prompts, on='_lnk_row', how='left', validate='one_to_one')
    else:
        rows = rows.assign(**dict.fromkeys(PROMPT_COLUMNS))
    rows = rows.reset_index(drop=True)

    catalog = read_table(conn, settings['catalog_caslib'], settings['catalog_table'])
    runs = read_table(conn, settings['runs_caslib'], settings['runs_table']) if settings['runs_exists'] else None
    if runs is not None:
        absent = [column for column in RUN_COLUMNS if column not in runs.columns]
        if absent and len(runs) > 0:
            log(f"The table {settings['runs_caslib']}.{settings['runs_table']} has no column {', '.join(absent)}, so no embedding row is linked to an ingestion run.")
            runs = None

    started = time.perf_counter()
    links, summary = link_rows(rows, catalog, runs, settings['redo_all'], settings['min_literal'], settings['utc_offset'], settings['query_window'])
    seconds = time.perf_counter() - started

    if summary['linked'] == 0:
        # Nothing to write: the table, its promotion and its saved file stay as they are
        log(f"0 of {summary['rows']} rows of {log_caslib}.{log_table} were linked, as every row has a match_method. The table was left as it is. "
            "With _lnk_redo_all = 1 all rows are linked again.")
        for line in warning_lines(summary):
            log(line, 'WARNING')
        return len(rows), False

    links.insert(0, '_lnk_one', 1)
    links.insert(0, '_lnk_row', rows['_lnk_row'].astype('int64'))
    links['match_candidates'] = pd.to_numeric(links['match_candidates'], errors='coerce')
    variables = [{'name': '_lnk_row', 'type': 'double'}, {'name': '_lnk_one', 'type': 'double'}]
    variables += [{'name': column, 'type': 'double' if column == 'match_candidates' else 'varchar', 'label': present[column] or LABELS[column]} for column in LINK_COLUMNS]
    conn.upload_frame(links, casout=dict(name=LINKS_TABLE, caslib='CASUSER', replace=True), importoptions={'vars': variables})

    table_label = label_of(conn, log_caslib, log_table)
    run_action(conn, 'table.dropTable', caslib=log_caslib, name=new_table, quiet=True)
    try:
        # Everything is one BY group, so the rows are written in the order of their number
        run_action(conn, 'dataStep.runCode', single='yes',
                   code=f"data {named(new_table)}(caslib={quoted(log_caslib)} drop=_lnk_row _lnk_one); "
                        f"merge {named(STAGE_TABLE)}(caslib='CASUSER' in=_lnk_in drop={' '.join(LINK_COLUMNS)}) {named(LINKS_TABLE)}(caslib='CASUSER'); "
                        f"by _lnk_one _lnk_row; if _lnk_in; run;")
        written, expected = row_count(conn, log_caslib, new_table), row_count(conn, log_caslib, log_table)
        if written != len(links) or written != expected:
            raise Refused(f"The new table has {written} rows, {len(links)} were linked and {log_caslib}.{log_table} has {expected}: the table changed while it was linked, or the "
                          f"link columns could not be merged. The table {log_caslib}.{log_table} was left as it is.")
        # The link columns are back where they were
        run_action(conn, 'table.alterTable', caslib=log_caslib, name=new_table, columnOrder=[column for column, _ in described])
    except Exception:
        # Without the new table the SAS program leaves the log table as it is
        conn.retrieve('table.dropTable', caslib=log_caslib, name=new_table, quiet=True)
        raise
    if table_label:
        # The label of the table is no part of its rows: the new table is given the one the table had.
        # A label that can not be set is no reason to keep the links back
        labelled = conn.retrieve('table.alterTable', caslib=log_caslib, name=new_table, label=table_label)
        if getattr(labelled, 'severity', 0) > 1:
            log(f"The label of the table {log_caslib}.{log_table} could not be given to the linked table, which has none now.")

    log(f"{summary['linked']} of {summary['rows']} rows of {log_caslib}.{log_table} were linked against {len(catalog)} catalog rows in {seconds:.2f} seconds"
        + (f", {summary['left']} rows that have a match_method were left as they are." if summary['left'] else '.'))
    for line in summary_lines(summary) + note_lines(summary, settings):
        log(line)
    for line in warning_lines(summary):
        log(line, 'WARNING')
    return written, True


if setting('_lnk_ready') == '1':
    #  Add certificate location to operating system list of trusted certs
    os.environ['CAS_CLIENT_SSL_CA_LIST'] = os.environ['SSLCALISTLOC']

    # Connect to CAS
    conn = swat.CAS(hostname=SAS.sasfnc('getoption', 'cashost'), port=SAS.sasfnc('getoption', 'casport'), password=os.environ['SAS_SERVICES_TOKEN'], session=setting('_casSessionUUID'))

    settings = {
        'log_caslib': setting('_lnk_log_caslib'),
        'log_table': setting('_lnk_log_table'),
        'catalog_caslib': setting('_lnk_catalog_caslib'),
        'catalog_table': setting('_lnk_catalog_table'),
        'runs_caslib': setting('_lnk_runs_caslib'),
        'runs_table': setting('_lnk_runs_table'),
        'runs_exists': setting('_lnk_runs_exists') == '1',
        'redo_all': setting('_lnk_redo_all') == '1',
        'min_literal': int(number_setting('_lnk_min_literal', 20)),
        'utc_offset': number_setting('_lnk_utc_offset', 0),
        'query_window': max(0.0, number_setting('_lnk_query_window', 300)),
    }
    try:
        table_rows, table_built = link_table(conn, settings, log=say)
    except Refused as refusal:
        say(str(refusal), 'ERROR')
        put_number('_lnk_refused', 1)
    else:
        put_number('_lnk_unchanged', 0 if table_built or table_rows == 0 else 1)
        put_number('_lnk_row_count', table_rows)
endsubmit;
run; quit;

proc python terminate;
run; quit;

* Replace the log table by the new one, promote and save it;
%macro _lnk_handle_output_table;
    %if &_lnk_row_count. GT 0 and &_lnk_unchanged. NE 1 %then %do;
        proc cas;
            /* The new table was built beside the existing one, which is only dropped once its replacement exists */
            table.tableExists result=staged /
                casLib = "&_lnk_log_caslib.",
                name = "&_lnk_log_table._NEW";

            if staged.exists GE 1 then do;
                /* Dropped twice, as a session table of that name hides a promoted one */
                table.dropTable /
                    casLib = "&_lnk_log_caslib.",
                    name = "&_lnk_log_table.",
                    quiet = True;
                table.dropTable /
                    casLib = "&_lnk_log_caslib.",
                    name = "&_lnk_log_table.",
                    quiet = True;
                table.alterTable /
                    casLib = "&_lnk_log_caslib.",
                    name = "&_lnk_log_table._NEW",
                    rename = "&_lnk_log_table.";

                /* A table that was promoted when it was linked is promoted again, as the parser does it when it appends */
                %if &_lnk_promote_table. or &_lnk_log_scope. EQ 2 %then %do;
                    table.promote /
                        casLib = "&_lnk_log_caslib.",
                        name = "&_lnk_log_table.",
                        targetLib = "&_lnk_log_caslib.",
                        target = "&_lnk_log_table.",
                        quiet = True;
                %end;
                tableWritten = True;
            end;
            else do;
                print (error) "The new table could not be created, the table &_lnk_log_caslib..&_lnk_log_table. was left as it is.";
                tableWritten = False;
                call symputx('_lnk_failed', 1);
            end;

            /* A table that has a saved file is saved again: the file would bring back the table without its links */
            %if &_lnk_save_table. or &_lnk_log_saved. %then %do;
                if tableWritten then do;
                    table.save /
                        table = {casLib = "&_lnk_log_caslib.", name = "&_lnk_log_table."},
                        casLib = "&_lnk_log_caslib.",
                        name = "&_lnk_log_table.",
                        replace = True;
                end;
            %end;
        run; quit;
    %end;
    %else %if &_lnk_row_count. EQ 0 %then %do;
        %put NOTE: The table &_lnk_log_caslib..&_lnk_log_table. has no rows, it was left as it is.;
    %end;
    %else %if &_lnk_row_count. LT 0 and &_lnk_ready. EQ 1 and &_lnk_refused. NE 1 %then %do;
        %put ERROR: The rows could not be linked, the table &_lnk_log_caslib..&_lnk_log_table. was left as it is.;
    %end;

    /* A run that built no new table - every row has a match_method, or the table has no rows - leaves the log table
       as it is. A table that was not in memory and that this program loaded from its saved file is a table of this
       CAS session then, and gone when the session ends: it is put in place the way a linked table is, promoted
       where _lnk_promote_table says so. Nothing was linked, so the saved file holds what the table holds and is
       not written again */
    %if (&_lnk_unchanged. EQ 1 or &_lnk_row_count. EQ 0) and &_lnk_log_scope. EQ 0 and &_lnk_log_exists. EQ 1 %then %do;
        %if &_lnk_promote_table. %then %do;
            proc cas;
                table.promote /
                    casLib = "&_lnk_log_caslib.",
                    name = "&_lnk_log_table.",
                    targetLib = "&_lnk_log_caslib.",
                    target = "&_lnk_log_table.",
                    quiet = True;
                table.tableExists result=found /
                    casLib = "&_lnk_log_caslib.",
                    name = "&_lnk_log_table.";
                call symputx('_lnk_log_scope', found.exists);
            run; quit;
        %end;
        %if &_lnk_log_scope. EQ 2 %then %do;
            %put NOTE: The table &_lnk_log_caslib..&_lnk_log_table. was not in memory. It was loaded from its saved file and promoted as it was loaded, the saved file was left as it is.;
        %end;
        %else %if &_lnk_promote_table. %then %do;
            %put WARNING: The table &_lnk_log_caslib..&_lnk_log_table. was not in memory. It was loaded from its saved file and could not be promoted, so it is a table of this CAS session and gone when the session ends. The saved file was left as it is.;
        %end;
        %else %do;
            %put NOTE: The table &_lnk_log_caslib..&_lnk_log_table. was not in memory. It was loaded from its saved file and not promoted, as _lnk_promote_table is not 1, so it is a table of this CAS session and gone when the session ends. The saved file was left as it is.;
        %end;
    %end;

    proc cas;
        table.dropTable /
                casLib = 'CASUSER',
                table = '_LNK_STAGE',
                quiet = True;
        table.dropTable /
                casLib = 'CASUSER',
                table = '_LNK_LINKS',
                quiet = True;
    run; quit;
%mend _lnk_handle_output_table;

%_lnk_handle_output_table;

* Clean up;
%if &_lnk_clean_cas_session. %then %do;
    cas lnkMySess terminate;
%end;
%sysmacdelete _lnk_handle_output_table;
%sysmacdelete _lnk_ensure_loaded;
* A run that wrote an ERROR line and no table ends with the condition code of a failed job, so that SAS Job Execution and a schedule report it as failed and not as completed: a table that was not found, a table the program refused and rows that could not be linked leave _lnk_row_count below 0, and a new table that could not be put in place sets _lnk_failed. A run with a WARNING and a run that had no row to link end as before. Nothing follows but the macro variables that are deleted;
%if &_lnk_row_count. LT 0 or &_lnk_failed. EQ 1 %then %do;
    %let SYSCC = 1012;
%end;
%symdel _lnk_clean_cas_session _casSessionUUID _lnk_row_count _lnk_unchanged _lnk_refused _lnk_failed _lnk_ready _lnk_log_exists _lnk_catalog_exists _lnk_runs_exists _lnk_log_scope _lnk_log_saved;
%symdel _lnk_log_caslib _lnk_log_table _lnk_catalog_caslib _lnk_catalog_table _lnk_runs_caslib _lnk_runs_table _lnk_redo_all _lnk_min_literal _lnk_utc_offset _lnk_query_window _lnk_promote_table _lnk_save_table;
