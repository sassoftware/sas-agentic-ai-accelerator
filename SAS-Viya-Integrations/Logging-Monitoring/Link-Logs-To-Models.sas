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
    content: the prompts, questions and hashes of the log row are compared
    with the templates and experiment runs of the table MODEL_CATALOG that
    Get-All-Prompts.sas writes, and with the ingestion runs of RAG_RUNS.
    A row that fits nothing gets match_method = none, a row that fits two
    callers equally well gets match_method = ambiguous.

    The order of the programs is: collect the log, Log-Parser-Code.sas,
    Get-All-Prompts.sas, this program.

    Every other column of the log table stays in CAS and is not rewritten:
    the program numbers the rows in a copy in CASUSER, reads what it compares
    into Python, uploads the link columns and merges them back by that number.

    lnk is short for:
      Link
*********************************************************************************/
cas mysess;

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
* A template takes part with at least this many characters of fixed text (whitespace is not counted), an experiment run with at least as many characters in its prompts;
%let _lnk_min_literal = 20;
* The hours the clock of the containers is ahead of UTC. The ingestion runs are recorded in UTC;
%let _lnk_utc_offset = 0;
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

* The number of rows of the new table. The Python code sets it as its last statement, so -1 means that the links were not made;
%let _lnk_row_count = -1;
* 1 when the Python code refused the tables and wrote the ERROR line itself;
%let _lnk_refused = 0;

proc python restart;
submit;
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
try:
    import swat
    import pandas as pd
except ImportError:
    SAS.logMessage('Ensure that the SWAT and Pandas package are installed', messageType='ERROR')

# The link columns in the order the log table holds them. Only this program fills them.
LINK_TEXT_COLUMNS = ['model_id', 'caller_model_id', 'caller_project_id', 'caller_kind', 'caller_name', 'experiment_run_id', 'rag_model_id', 'match_method']
LINK_COLUMNS = LINK_TEXT_COLUMNS + ['match_candidates']

# What the matching reads of a log row besides the link columns. A table without them
# was written by a parser that is older than the link columns.
CONTENT_COLUMNS = ['timestamp', 'model', 'system_prompt', 'user_prompt', 'kind', 'embedding_mode', 'input_hash', 'rag_collection', 'rag_query_hash']
PROMPT_COLUMNS = ['system_prompt', 'user_prompt']
# Read when the table has it
OPTIONAL_COLUMNS = ['user_agent']
# The user agents of requests that no Builder page sent: the score code of a published model, a job or
# a script, and a SAS session. The Builder page runs in a browser.
NOT_A_BROWSER = ('python-requests', 'sas')

# The columns no row of the catalog can be read without. The others - projectid, runid,
# target_model, system_template, user_template, resolved_system_prompt, resolved_user_prompt,
# collection, template_source, resolved_system_hash and resolved_user_hash - read as missing
# values when the catalog does not have them.
CATALOG_KEY_COLUMNS = ['modelid', 'name', 'role']
# The columns an ingestion run is read from. modelid, the setup that holds the run, is optional.
RUN_COLUMNS = ['run_id', 'collection', 'embed_model', 'started_at', 'finished_at']

# The role of the catalog rows that answer a log row of each kind
ANSWER_ROLE = {'llm': 'llm', 'embedding': 'embedding', 'decision': 'decision_model'}
KIND_OF_ROLE = {role: kind for kind, role in ANSWER_ROLE.items()}

# The methods a row of each kind can get, in the order the summary names them
METHODS = {
    'llm': ['experiment run', 'template'],
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
# What the catalog writes into template_source for a RAG setup that the Prompt Builder saved
BUILDER_SETUP = 'builder'
INGESTION_RUN = re.compile(r'"ingestion_run_id"\s*:\s*"([^"]*)"')
# A template with a variable in its system prompt is found through the first characters
# of that system prompt or, when it starts with the variable, through its last ones.
# Only a template that has less fixed text than this at both ends is compared with every row.
PREFIX_LENGTH = 12
SAS_EPOCH = datetime(1960, 1, 1)
STAGE_TABLE = '_LNK_STAGE'
LINKS_TABLE = '_LNK_LINKS'
READ_PAGE = 2000


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
    or the end of its system prompt, a RAG setup by its collection and an ingestion
    run by its embedding model.

    Args:
        catalog (pandas.DataFrame): MODEL_CATALOG, column names in lower case.
        runs (pandas.DataFrame): RAG_RUNS, column names in lower case, or None.
        min_literal (int): The fixed text a template needs to take part.

    Returns:
        dict: The index.
    """
    index = {
        'models': {},        # container name -> [(role, modelID)]
        'callers': {},       # modelID -> what the link columns say of the caller
        'runs': {},          # (space, system key, hash of the user prompt) -> [experiment run]
        'exact': {},         # squeezed system prompt without a variable -> [prompt pattern]
        'prefix': {},        # start of a system prompt with a variable -> [prompt pattern]
        'suffix': {},        # end of a system prompt that starts with a variable -> [prompt pattern]
        'open': [],          # prompt patterns whose system prompt starts and ends with a variable
        'questions': {},     # questions key -> [decision pattern]
        'collections': {},   # collection -> [modelID of a RAG setup]
        'builder_setups': set(),  # modelIDs of the RAG setups the Prompt Builder saved
        'rag_targets': {},   # modelID of a RAG setup -> the embedding models it is known to call
        'ingestions': {},    # embedding model -> [ingestion run]
        'run_setups': {},    # ingestion run id -> modelIDs of the setups that hold the run
        'short': set(),      # callers with a template that is too short to take part
        'patterns': 0,
        'runs_without_window': 0,
    }
    patterns = {}

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

    for record in catalog.to_dict('records'):
        # A column the catalog does not have reads as a missing value
        cell = record.get
        model_id = squeeze(cell('modelid'))
        role = squeeze(cell('role')).lower()
        name = squeeze(cell('name'))
        if not model_id or not role:
            continue

        if role in KIND_OF_ROLE:
            entry = (role, model_id)
            known = index['models'].setdefault(name.lower(), [])
            if name and entry not in known:
                known.append(entry)
            continue
        if role not in ('prompt', 'decision', 'rag'):
            continue

        index['callers'].setdefault(model_id, {'caller_model_id': model_id, 'caller_project_id': squeeze(cell('projectid')), 'caller_kind': role, 'caller_name': name})
        target = squeeze(cell('target_model')).lower()

        if role == 'rag':
            collection = squeeze(cell('collection'))
            if collection:
                setups = index['collections'].setdefault(collection, [])
                if model_id not in setups:
                    setups.append(model_id)
            if target:
                index['rag_targets'].setdefault(model_id, set()).add(target)
            if squeeze(cell('template_source')).lower() == BUILDER_SETUP:
                index['builder_setups'].add(model_id)
            continue

        system_template = text(cell('system_template'))
        user_template = text(cell('user_template'))
        run_id = squeeze(cell('runid'))
        resolved_system = squeeze(cell('resolved_system_prompt'))
        resolved_user = squeeze(cell('resolved_user_prompt'))
        system_hash, user_hash = cell('resolved_system_hash'), cell('resolved_user_hash')
        system_parts, system_size = template_parts(system_template)
        user_parts, user_size = template_parts(user_template)

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

            if run_id:
                # A run without resolved prompts is its templates when they hold no variable
                if not resolved_system and not resolved_user and len(system_parts) == 1 and len(user_parts) == 1:
                    resolved_system, resolved_user = system_parts[0], user_parts[0]
                    system_hash = user_hash = ''
                if size(resolved_system) + size(resolved_user) >= min_literal:
                    key = ('P', hash_or(system_hash, resolved_system), hash_or(user_hash, resolved_user))
                    index['runs'].setdefault(key, []).append({'caller': model_id, 'run': run_id, 'target': target})
        else:
            # A decision: the system template is the questions JSON, the user template the state
            if size(system_template) >= min_literal:
                for key in question_keys(system_template):
                    where = index['questions'].setdefault(key, [])
                    pattern = pattern_of((model_id, key, user_parts), where,
                                         {'caller': model_id, 'state': user_parts, 'literal': user_size})
                    add_target(pattern, target)
            elif size(system_template) > 0:
                index['short'].add(model_id)

            if run_id:
                resolved_questions = text(cell('resolved_system_prompt')) if resolved_system else system_template
                if not resolved_user and len(user_parts) == 1:
                    resolved_user = user_parts[0]
                    user_hash = ''
                if resolved_user and size(resolved_questions) >= min_literal:
                    # The questions by their JSON and their text, and by the hash the catalog took of the whole text
                    keys = question_keys(resolved_questions)
                    stored = squeeze(system_hash).lower()
                    keys.append('H:' + (stored if resolved_system and TEXT_HASH.fullmatch(stored) else text_hash(resolved_questions)))
                    for key in keys:
                        index['runs'].setdefault(('D', key, hash_or(user_hash, resolved_user)), []).append({'caller': model_id, 'run': run_id, 'target': target})

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


def match_run(index, space, system_keys, user, model):
    """Rule 'experiment run': the prompts of the row are the resolved prompts of a run
    that was sent to the model of the row. Asked for the rows a browser can have sent.
    The user prompt is compared by its hash, the system prompt by its hash or, for the
    questions of a decision, by the keys of the questions."""
    found = set()
    for key in system_keys:
        for run in index['runs'].get((space, key, user), ()):
            if run['target'] in ('', model):
                found.add((run['caller'], run['run']))
    if not found:
        return None
    callers = {caller for caller, _ in found}
    if len(callers) > 1:
        return {'match_method': 'ambiguous', 'match_candidates': len(found)}
    newest = max((run for _, run in found), key=run_order)
    return {'caller': callers.pop(), 'match_method': 'experiment run', 'match_candidates': len(found), 'experiment_run_id': newest}


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


def saved_setups(index, setups):
    """The setups among several that the Prompt Builder saved, when it saved exactly one of
    them - otherwise the setups as they are. A setup that was saved in the Prompt Builder and
    registered with the step RAG - Register Setup is two models with one collection and the
    same ingestion runs: the model the Builder saved is the one that holds the setup."""
    setups = list(setups)
    saved = [setup for setup in setups if setup in index['builder_setups']]
    return saved if len(setups) > 1 and len(saved) == 1 else setups


def setup_of_collection(index, collection, user_prompt=''):
    """The RAG setup a collection belongs to: the one setup the catalog has for it. When
    several setups hold the collection, the ingestion run the retrieval names decides,
    if exactly one of them holds that run, and after that the one setup among them that
    the Prompt Builder saved. The empty text when nothing decides."""
    setups = index['collections'].get(collection, ())
    if len(setups) == 1:
        return setups[0]
    if len(setups) > 1 and user_prompt:
        named = INGESTION_RUN.search(user_prompt)
        if named:
            holding = [setup for setup in setups if setup in index['run_setups'].get(named.group(1), ())]
            if len(holding) == 1:
                return holding[0]
    setups = saved_setups(index, setups)
    return setups[0] if len(setups) == 1 else ''


def match_query(index, evidence, input_hash, model):
    """Rule 'retrieval query': the text an embedding model got in query mode is the query
    of a retrieval that an LLM row of the table carries. The same question is embedded
    many times, so there is no time window: the row gets the setup of the collection
    when every such LLM row names the same collection."""
    seen = evidence.get(input_hash)
    if not input_hash or not seen:
        return None
    if len(seen['collections']) > 1:
        return {'match_method': 'ambiguous', 'match_candidates': len(seen['collections'])}
    collection = next(iter(seen['collections']))
    setups = saved_setups(index, index['collections'].get(collection, ()))
    if not setups:
        return None
    if len(seen['setups']) == 1 and not seen['open']:
        setup = next(iter(seen['setups']))
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

def link_rows(rows, catalog, runs=None, redo_all=False, min_literal=20, utc_offset=0.0):
    """Links the rows of a log table to the catalog.

    Args:
        rows (pandas.DataFrame): The log rows with CONTENT_COLUMNS and LINK_COLUMNS, and user_agent
            when the table has it, names in lower case. The prompts of a row that is not linked
            again may be missing.
        catalog (pandas.DataFrame): MODEL_CATALOG, names in lower case.
        runs (pandas.DataFrame): RAG_RUNS, names in lower case, or None.
        redo_all (bool): True links every row, False only the rows without a match_method.
        min_literal (int): The fixed text a template needs to take part.
        utc_offset (float): The hours the clock of the log is ahead of UTC.

    Returns:
        tuple: A frame of the LINK_COLUMNS with one row per log row in the order of
        the rows - what was there for a row that was left alone - and the summary.
    """
    index = build_index(catalog, runs, min_literal)
    records = rows.to_dict('records')

    def model_of(name, kind):
        """The modelID of the model that answered, and the kind of the row when it has none."""
        known = index['models'].get(name, ())
        if not kind:
            roles = {role for role, _ in known}
            kind = KIND_OF_ROLE[roles.pop()] if len(roles) == 1 else ''
        ids = {model_id for role, model_id in known if role == ANSWER_ROLE.get(kind)} or {model_id for _, model_id in known}
        return (ids.pop() if len(ids) == 1 else ''), kind

    todo = [redo_all or not squeeze(record.get('match_method')) for record in records]
    models = [squeeze(record.get('model')).lower() for record in records]
    kinds = []
    model_ids = []
    for record, model in zip(records, models):
        model_id, kind = model_of(model, squeeze(record.get('kind')).lower())
        kinds.append(kind)
        model_ids.append(model_id)

    # The retrievals the LLM rows of the whole table carry, by the hash of their query:
    # they are what an embedding row in query mode is linked through
    setups = [''] * len(records)
    evidence = {}
    for position, record in enumerate(records):
        collection = squeeze(record.get('rag_collection'))
        if kinds[position] != 'llm' or not collection:
            continue
        setups[position] = setup_of_collection(index, collection, text(record.get('user_prompt')))
        query_hash = squeeze(record.get('rag_query_hash')).lower()
        if query_hash:
            seen = evidence.setdefault(query_hash, {'collections': set(), 'setups': set(), 'open': False})
            seen['collections'].add(collection)
            if setups[position]:
                seen['setups'].add(setups[position])
            else:
                seen['open'] = True

    pattern_cache, state_cache, window_cache = {}, {}, {}
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
        # An experiment run is a request of the Builder page. A published model that sends the prompts
        # of a run again is the same caller, but its request is not that run
        may_be_run = bool(index['runs']) and not squeeze(record.get('user_agent')).lower().startswith(NOT_A_BROWSER)
        match = None
        if kind == 'llm':
            link['rag_model_id'] = setups[position]
            system, user = squeeze(record.get('system_prompt')), squeeze(record.get('user_prompt'))
            match = (may_be_run and match_run(index, 'P', [text_hash(system)], text_hash(user), model)) or match_template(index, system, user, model, pattern_cache)
        elif kind == 'decision':
            keys, state = question_keys(record.get('system_prompt')), squeeze(record.get('user_prompt'))
            match = (may_be_run and match_run(index, 'D', keys + ['H:' + text_hash(record.get('system_prompt'))], text_hash(state), model)) or match_questions(index, keys, state, model, state_cache)
        elif kind == 'embedding':
            mode = squeeze(record.get('embedding_mode')).lower()
            if mode == 'query':
                match = match_query(index, evidence, squeeze(record.get('input_hash')).lower(), model)
            elif mode == 'document':
                moment = sas_seconds(record.get('timestamp'))
                match = match_ingestion(index, None if moment is None else moment - utc_offset * 3600.0, model, window_cache)

        if match is None:
            match = {'match_method': 'none', 'match_candidates': 0}
        link['match_method'] = match['match_method']
        link['match_candidates'] = match['match_candidates']
        link['experiment_run_id'] = match.get('experiment_run_id', '')
        if 'caller' in match:
            link.update(index['callers'].get(match['caller'], {'caller_model_id': match['caller']}))
        links.append(link)

    summary = {'rows': len(records), 'linked': sum(todo), 'left': len(records) - sum(todo), 'kinds': {},
               'patterns': index['patterns'], 'short': len(index['short']), 'runs_without_window': index['runs_without_window']}
    for kind, link in zip(kinds, links):
        counts = summary['kinds'].setdefault(kind if kind in METHODS else 'unknown', {'rows': 0, 'model': 0, 'rag': 0, 'methods': {}})
        counts['rows'] += 1
        counts['model'] += 1 if link['model_id'] else 0
        counts['rag'] += 1 if link['rag_model_id'] else 0
        method = link['match_method'] or 'not linked yet'
        counts['methods'][method] = counts['methods'].get(method, 0) + 1
    return pd.DataFrame(links, columns=LINK_COLUMNS), summary


def summary_lines(summary):
    """The summary as the lines of the SAS log: one per kind of row."""
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
        line = f"{counts['rows']} {label}: {counts['model']} linked to a model; caller by {', '.join(named)}"
        if kind == 'llm':
            line += f"; {counts['rag']} linked to a RAG setup"
        lines.append(line + '.')
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
    table in their order with every column they had, the link columns replaced. The SAS
    program takes it from there the way the parser replaces a table.

    Returns:
        int: The number of rows of the new table, 0 when the log table has none.
    """
    log_caslib, log_table = settings['log_caslib'], settings['log_table']
    new_table = log_table + '_NEW'

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
        return 0
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
    links, summary = link_rows(rows, catalog, runs, settings['redo_all'], settings['min_literal'], settings['utc_offset'])
    seconds = time.perf_counter() - started

    links.insert(0, '_lnk_one', 1)
    links.insert(0, '_lnk_row', rows['_lnk_row'].astype('int64'))
    links['match_candidates'] = pd.to_numeric(links['match_candidates'], errors='coerce')
    variables = [{'name': '_lnk_row', 'type': 'double'}, {'name': '_lnk_one', 'type': 'double'}]
    variables += [{'name': column, 'type': 'double' if column == 'match_candidates' else 'varchar', 'label': present[column] or LABELS[column]} for column in LINK_COLUMNS]
    conn.upload_frame(links, casout=dict(name=LINKS_TABLE, caslib='CASUSER', replace=True), importoptions={'vars': variables})

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

    log(f"{summary['linked']} of {summary['rows']} rows of {log_caslib}.{log_table} were linked against {len(catalog)} catalog rows in {seconds:.2f} seconds"
        + (f", {summary['left']} rows that have a match_method were left as they are." if summary['left'] else '.'))
    for line in summary_lines(summary):
        log(line)
    if summary['short']:
        log(f"{summary['short']} prompt(s) or decision template(s) of the catalog have a template with less than {settings['min_literal']} characters of fixed text. Such a template takes no part, as it would fit the requests of other callers.")
    if summary['runs_without_window']:
        log(f"{summary['runs_without_window']} ingestion run(s) have no start or no end and take no part.")
    return written


if SAS.symget('_lnk_ready') == '1':
    #  Add certificate location to operating system list of trusted certs
    os.environ['CAS_CLIENT_SSL_CA_LIST'] = os.environ['SSLCALISTLOC']

    # Connect to CAS
    conn = swat.CAS(hostname=SAS.sasfnc('getoption', 'cashost'), port=SAS.sasfnc('getoption', 'casport'), password=os.environ['SAS_SERVICES_TOKEN'], session=SAS.symget('_casSessionUUID'))

    settings = {
        'log_caslib': SAS.symget('_lnk_log_caslib'),
        'log_table': SAS.symget('_lnk_log_table'),
        'catalog_caslib': SAS.symget('_lnk_catalog_caslib'),
        'catalog_table': SAS.symget('_lnk_catalog_table'),
        'runs_caslib': SAS.symget('_lnk_runs_caslib'),
        'runs_table': SAS.symget('_lnk_runs_table'),
        'runs_exists': SAS.symget('_lnk_runs_exists') == '1',
        'redo_all': SAS.symget('_lnk_redo_all') == '1',
        'min_literal': int(float(SAS.symget('_lnk_min_literal'))),
        'utc_offset': float(SAS.symget('_lnk_utc_offset')),
    }
    try:
        linked_rows = link_table(conn, settings, log=SAS.logMessage)
    except Refused as refusal:
        SAS.logMessage(str(refusal), messageType='ERROR')
        SAS.symput('_lnk_refused', '1')
    else:
        SAS.symput('_lnk_row_count', str(linked_rows))
endsubmit;
run; quit;

proc python terminate;
run; quit;

* Replace the log table by the new one, promote and save it;
%macro _lnk_handle_output_table;
    %if &_lnk_row_count. GT 0 %then %do;
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

                %if &_lnk_promote_table. %then %do;
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
            end;

            %if &_lnk_save_table. %then %do;
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
    %else %if &_lnk_ready. EQ 1 and &_lnk_refused. NE 1 %then %do;
        %put ERROR: The rows could not be linked, the table &_lnk_log_caslib..&_lnk_log_table. was left as it is.;
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
%symdel _lnk_clean_cas_session _casSessionUUID _lnk_row_count _lnk_refused _lnk_ready _lnk_log_exists _lnk_catalog_exists _lnk_runs_exists;
%symdel _lnk_log_caslib _lnk_log_table _lnk_catalog_caslib _lnk_catalog_table _lnk_runs_caslib _lnk_runs_table _lnk_redo_all _lnk_min_literal _lnk_utc_offset _lnk_promote_table _lnk_save_table;
