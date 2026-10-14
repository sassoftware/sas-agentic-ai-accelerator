/******************************************************************************
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Start Here: one page that says what the SAS Agentic AI Accelerator is, links
 * to its two reports and to the guides, and checks the installation.
 *
 * The program of the job definition "Start Here" at the root of the content
 * folder. Run from SAS Job Execution it writes ONE HTML page to the browser:
 * no script, no file from another host, its style inline.
 *
 * THE PAGE SHOWS WHAT ITS SAS SESSION MAY READ, so it is opened in a compute
 * context that runs under the identity of the person who opens it: the job
 * definition names that context in its parameter _contextName, and a request
 * can name another one - .../SASJobExecution/?_program=<content root>/Start
 * Here, followed by the parameter _contextName with the name of the context.
 * What the session of a compute context may read depends on how the system
 * configures that context. Measured on the test system on 2026-10-12, in its
 * SAS Job Execution compute context: on one run SAS Viya answered every
 * request with 401 and the page had 15 lines that were not checked; on a
 * later run the same day the page was built completely (97 lines ok), by a
 * session whose user (SYSUSERID) was not the user it was started for
 * (SYS_COMPUTE_SESSION_OWNER, which SAS Job Execution sets).
 * Where every request is refused the page says so in one line at its top,
 * with what to do, in place of those lines. The line says that the session
 * runs as another user than the person who opened the page where the two
 * users differ - a domain written behind @ or in front of a backslash is no
 * difference -, and whose rights to ask for where they are the same.
 *
 * WHAT IT CHECKS, each as one line with ok / missing / not checked and one
 * sentence what to do:
 *   - the release: release.json in the content root (version, commit, date)
 *   - the content: every entry release.json lists, present in this content
 *     root yes or no, so the page needs no checkout. What a folder holds
 *     under the name of an entry is told the way mdb content-deploy tells
 *     it: a member has the name of the entry to the letter, and is of its
 *     kind by what its uri starts with. A member of a folder
 *     that is a reference to an object elsewhere is no entry: it is not
 *     followed. A reference that has the name of an entry and names an
 *     object of the kind of the entry stands in the way of that entry - mdb
 *     content-deploy and the import write nothing for it -, and the line
 *     of the entry says so, where the reference is all the folder holds
 *     under that name and where it lies beside the object. Two objects of
 *     the name and kind of an entry stand in its way as well - nothing says
 *     which one is meant -, and what lies below a name two folders have is
 *     not checked. A report that
 *     is there is present whatever lies beside it: mdb writes no report
 *     and fails none over a reference. A reference to an object of
 *     another kind is no part of the check
 *   - the CAS tables the two reports bind: there, rows, loaded when
 *   - the CAS tables of the logging chain that no report binds: the fact sheet
 *     of the decision models and the four further tables of Get-All-Prompts.sas
 *   - SAS Model Manager: the repository and the projects of the LLM, the
 *     embedding model and the decision model definitions, with their models;
 *     a project without a model is missing
 *   - the options of the Prompt Builder report that name the repository and
 *     the projects
 *   - the credential domain of the provider keys, by its name only
 *   - the Python packages of this compute context, for each part
 *   - the publishing destinations
 *   - the compute contexts the Prompt Builder options and this job name
 *
 * IT CHANGES NOTHING. Every request is a GET, nothing is written but the
 * page: a file in WORK, copied to _webout. No secret is asked for - the
 * credential domain is asked for by name, never for an entry - and the page
 * holds no token and no user id.
 *
 * IT DOES NOT FAIL. A service that does not answer, a right the user lacks,
 * a folder that is not there: each is a line of the page. Where PROC PYTHON
 * does not run in the compute context the page says that and nothing else.
 * The condition code of the session is set back to 0 at the end for that
 * reason: SAS Job Execution shows the page of a job that completed, and its
 * own error page for one that failed.
 *
 * Parameters of the job definition:
 *   _action          execute
 *   _output_type     html - the job writes the page itself, to _webout
 *   _contextName     the compute context the job runs in: one that runs under
 *                    the identity of the person who opens the page
 *   contentRoot      the content folder (default /SAS Agentic AI Accelerator)
 *   caslib           CAS library of the tables of the reports (Public)
 *   casServer        CAS server (cas-shared-default)
 *   credentialDomain credential domain of the provider keys (agentic-ai-keys);
 *                    the option of the Prompt Builder report wins when it is set
 *   repositoryName   SAS Model Manager repository (LLM Repository)
 *   llmProject / embeddingProject / decisionProject
 *                    the projects of the definitions, by name
 *   computeContexts  compute contexts to look for, separated by |
 *   guideUrl         where the guides are (the website of the accelerator); an
 *                    address of another host than that and this SAS Viya is text
 *
 * sh is short for Start Here
 ******************************************************************************/

/* A parameter is taken from the lines below only where the job definition or
   the request did not set it - the pattern of Ingest-Documents.sas */
%macro _sh_default(name, value);
    %if not %symexist(&name.) %then %do;
        %global &name.;
        %let &name. = &value.;
    %end;
%mend _sh_default;

%_sh_default(contentRoot, /SAS Agentic AI Accelerator);
%_sh_default(caslib, Public);
%_sh_default(casServer, cas-shared-default);
%_sh_default(credentialDomain, agentic-ai-keys);
%_sh_default(repositoryName, LLM Repository);
%_sh_default(llmProject, LLM Model Project);
%_sh_default(embeddingProject, Embedding Model Project);
%_sh_default(decisionProject, Decision Model Project);
%_sh_default(computeContexts, SAS Studio compute context|SAS Job Execution compute context);
%_sh_default(guideUrl, https://sassoftware.github.io/sas-agentic-ai-accelerator);
%sysmacdelete _sh_default;

/* Whose session this is. SAS Job Execution hands a job the user its session
   was started for (SYS_COMPUTE_SESSION_OWNER) and the compute context it
   runs in (_contextName); SYSUSERID is the user the session runs as. The
   Python program reads the three as text - no statement here resolves one -
   and asks only for what the session has: a program run in SAS Studio may
   have neither of the first two. It compares the two users and writes
   neither to the page. */
%let _sh_hasOwner = %symexist(SYS_COMPUTE_SESSION_OWNER);
%let _sh_hasContext = %symexist(_contextName);

/* An error must not leave a batch session checking syntax only: the page is
   written by a DATA step behind PROC PYTHON, and behind an error such a step
   does not run. The option that decides it is switched off while the program
   runs and put back to what it was. */
%let _sh_syntaxcheck = %sysfunc(getoption(syntaxcheck));
options nosyntaxcheck;

%let _sh_viyaHost = %sysfunc(getoption(SERVICESBASEURL));
%let _sh_work = %sysfunc(pathname(work));
/* The Python program sets this to 0 as its last statement, behind the file of
   the page: -1 means that there is no page to send */
%let _sh_rc = -1;

proc python restart;
submit;
import html
import importlib.util
import json
import os
import re
from urllib.parse import quote
try:
    import requests
except ImportError:
    requests = None
try:
    from importlib import metadata as package_metadata
except ImportError:
    package_metadata = None

PAGE_FILE = 'start_here.html'
OK = 'ok'
MISSING = 'missing'
NOT_CHECKED = 'not checked'
# What read_json gives as the status of an answer that came and is no JSON
NO_JSON = -1
# What read_folders gives as the status of a folder whose name two or more folders have, and of every folder below it
NOT_ONE = -2

RELEASE_FILE = 'release.json'
# The two reports of the content folder, by their path below the content root
REPORTS = (('Prompt Builder', 'Prompt Builder/Prompt Builder'),
           ('LLM Usage Report', 'Logging and Monitoring/LLM Usage Report'))
BUILDER_REPORT = 'Prompt Builder/Prompt Builder'
LOADER_JOB = 'Run the job Load-Accelerator-Tables of the folder Data in this content root'
# The CAS tables the two reports bind, the report that reads each, and what loads it
REPORT_TABLES = (
    ('ACCELERATOR_RELEASES', 'Prompt Builder', LOADER_JOB + ', or mdb load-releases.'),
    ('LLM_LOGS', 'LLM Usage Report', 'Collect the logs of the containers and run Log-Parser-Code.sas or the step LLM - Log Parser.'),
    ('PROMPT_EXPERIMENTS', 'LLM Usage Report', 'Run Get-All-Prompts.sas or the step LLM - Get All Prompts.'),
    ('LLM_FACT_SHEET', 'LLM Usage Report', LOADER_JOB + ', or mdb load-facts.'),
    ('EMBEDDING_FACT_SHEET', 'LLM Usage Report', LOADER_JOB + ', or mdb load-facts.'),
)
# The CAS tables of the logging chain that no report binds: the fact sheet of the decision models, and the four
# tables Get-All-Prompts.sas writes beside PROMPT_EXPERIMENTS, which Link-Logs-To-Models.sas reads
GET_ALL_PROMPTS = 'Run Get-All-Prompts.sas or the step LLM - Get All Prompts.'
CHAIN_TABLES = (
    ('DECISION_FACT_SHEET', 'the costs of the decision models', LOADER_JOB + ', or mdb load-facts.'),
    ('PROMPT_OPTIMIZATIONS', 'Link-Logs-To-Models.sas', GET_ALL_PROMPTS),
    ('RAG_SETUPS', 'Link-Logs-To-Models.sas', GET_ALL_PROMPTS),
    ('RAG_RUNS', 'Link-Logs-To-Models.sas', GET_ALL_PROMPTS),
    ('MODEL_CATALOG', 'Link-Logs-To-Models.sas', GET_ALL_PROMPTS),
)
# The Python packages of each part: (part, name to install, module, names its version is found under, the least version, needed for)
PACKAGES = (
    ('Logging and Monitoring', 'pandas', 'pandas', ('pandas',), '', 'the log parser and the link program'),
    ('Logging and Monitoring', 'swat', 'swat', ('swat',), '', 'the log parser and the link program'),
    ('Logging and Monitoring', 'requests', 'requests', ('requests',), '', 'Get-All-Prompts.sas and this page'),
    ('RAG', 'requests', 'requests', ('requests',), '', 'every step and job of the RAG runtime'),
    ('RAG', 'pandas', 'pandas', ('pandas',), '', 'every step of the RAG runtime'),
    ('RAG', 'swat', 'swat', ('swat',), '', 'every step of the RAG runtime'),
    ('RAG', 'psycopg2-binary', 'psycopg2', ('psycopg2-binary', 'psycopg2'), '', 'the pgvector backend'),
    ('RAG', 'singlestoredb', 'singlestoredb', ('singlestoredb',), '', 'the SingleStore backend'),
    ('RAG', 'pypdfium2', 'pypdfium2', ('pypdfium2',), '', 'PDF text extraction'),
    ('RAG', 'markitdown', 'markitdown', ('markitdown',), '', 'documents of the kinds .docx, .xlsx, .pptx, .epub, .rtf and .msg'),
    ('Prompt optimization', 'dspy', 'dspy', ('dspy', 'dspy-ai'), '3.2.1', 'every optimization'),
    ('Prompt optimization', 'requests', 'requests', ('requests',), '2.31', 'every optimization'),
    ('Prompt optimization', 'optuna', 'optuna', ('optuna',), '3.0', 'the MIPROv2 optimizer'),
)
GUIDES = (
    ('User Guide: Prompt Builder', 'User-Guide/Prompt-Builder'),
    ('User Guide: RAG', 'User-Guide/RAG'),
    ('Administration Guide', 'Administration-Guide/Introduction'),
    ('Administration Guide: Logging and Monitoring', 'Administration-Guide/Logging-' + chr(38) + '-Monitoring'),
    ('Administration Guide: Troubleshooting', 'Administration-Guide/Troubleshooting-Guide'),
)
REPORT_CONTENT = 'application/vnd.sas.report.content+xml'
# One hidden parameter of a report, which is how the Prompt Builder keeps an option, and the value it holds
OPTION = re.compile(r'<PromptDefinition\b[^>]*\blabel="([^"]+)"[^>]*>(.*?)</PromptDefinition>', re.DOTALL)
OPTION_VALUE = re.compile(r'<(String|Boolean)\b[^>]*>(.*?)</\1>', re.DOTALL)
TEXT_LIMIT = 600
# The two answers of a service that refuses the session: no sign-in it takes, and no right
REFUSED = (401, 403)
# The section of the one line that stands for every request, where SAS Viya refused them all
SESSION = 'SAS session of this page'
# What a file of the Files service is addressed by
FILE_URI = re.compile(r'/files/files/[0-9A-Za-z-]+')
ASK_ADMINISTRATOR = 'Ask an administrator to open this page, or for the right to read it.'
# What to do where every request of a session that runs as the person who opened the page was refused
SIGN_IN_REFUSED = 'Open this page again. If the line stays, ask an administrator why SAS Viya does not take the sign-in of your session.'
ASK_RIGHTS = 'Ask an administrator for the rights to read what this page checks.'
# How the page is opened under the identity of the person who opens it. The parameter is written
# with the character it starts with in an address, which this block holds as a number only
AS_YOURSELF = ('Open this page in a compute context that runs as you: add ' + chr(38) + '_contextName=<the name of that compute context> '
               'to the address of this page, or set the context in the parameter _contextName of the job definition.')
# What to do about a step that is not registered. A step is registered by the import and by mdb
# content-deploy; its file, uploaded to the folder, is no step and stays beside the step
STEP_MISSING = ('Import the package of the accelerator again, or run mdb content-deploy: both register the step. '
                'Uploading its .step file to the folder does not.')
STEP_UPLOADED = ('Import the package of the accelerator again, or run mdb content-deploy: both register the step. Then delete the file of '
                 'that name, which neither of them removes: a step file that was uploaded opens empty in SAS Studio.')
STEP_BESIDE = 'Delete the file of that name: it is a step file that was uploaded, not the step.'
# What to do about an entry that is not in its folder: a file, a job definition and a folder are written by the import
# and by mdb content-deploy. A report is not: mdb content-deploy writes none, it comes with the package
ENTRY_MISSING = 'Import the package of the accelerator again, or run mdb content-deploy.'
REPORT_MISSING = 'Import the package of the accelerator again, or run mdb builders-import: a report comes with the package, and mdb content-deploy writes none.'
# A member of a folder that is a reference names an object that lives in another folder. It is told by the type
# of the member, is never followed, and is no object of the folder - whatever kind of object it names. Measured on
# 2026-10-13 for a file: mdb content-deploy and the import in place reported the entry whose name a reference had as
# FAILED and wrote nothing for it, until the reference was removed
REFERENCE = 'reference'
REFERENCE_FOUND = 'a reference of that name is in its folder, and the object it names lives elsewhere'
REFERENCE_TODO = 'Remove the reference from the folder. Then: '
# What the uri of an object of each kind of entry starts with: the uri of a reference is the one of the object it
# names. mdb tells what a folder holds under the name of an entry by the name, to the letter, and by this table
# (viya/content.py, _named). It fails an entry for a reference that has its name and names an object of its kind,
# alone or beside the object, and for two objects of its name and kind. It writes no report and fails none over
# what a folder holds. The page judges what a folder holds under the name of an entry the same way. Below a folder
# mdb fails, the two differ: mdb looks at nothing there, and the page reads the one folder of the name - its line
# says that nothing is written below it - and reads nothing where two folders have the name
KIND_URI = {'file': '/files/files/', 'step': '/dataFlows/steps/', 'jobDefinition': '/jobDefinitions/definitions/',
            'report': '/reports/reports/', 'folder': '/folders/folders/'}
REFERENCE_BESIDE = 'present, and a reference of that name is in its folder, which names an object that lives elsewhere'
REFERENCE_BESIDE_TODO = 'Remove the reference from the folder: while it is there, the import and mdb content-deploy write nothing for this entry.'
# Two objects of the name and kind of an entry in one folder: the entry is there, and nothing is written for it
NOT_ONE_TODO = 'Remove or rename all but one of them: while they are there, the import and mdb content-deploy write nothing for this entry.'
# What the line of a folder adds to either: mdb writes nothing into a folder whose entry it fails, nor below it
BELOW_TOO = ' Nothing is written below the folder either.'


# ---------------------------------------------------------------------------
# One line of the page
# ---------------------------------------------------------------------------

def line(section, what, state, found, todo=''):
    """One check: where it stands on the page, what was looked at, ok / missing / not checked,
    what was found and one sentence what to do."""
    return {'section': section, 'what': str(what), 'state': state, 'found': str(found), 'todo': str(todo)}


def not_read(status, what):
    """State and wording for a request that was not answered with 200. 404 is missing; a right
    that is lacking and a service that does not answer are not checked, and neither is a failure."""
    if status == 404:
        return MISSING, 'not found'
    if status in REFUSED:
        return NOT_CHECKED, f'{what} may not be read by this session (HTTP {status})'
    if status == 0:
        return NOT_CHECKED, f'{what}: the service did not answer'
    if status == NO_JSON:
        return NOT_CHECKED, f'{what}: the answer could not be read'
    if status == NOT_ONE:
        return NOT_CHECKED, f'{what} was not read: it is, or lies below, one of two or more folders of one name, and nothing says which one is meant'
    return NOT_CHECKED, f'{what}: the service answered HTTP {status}'


def ask_again(status):
    """What to do about a request that was not answered with 200 or 404, and about a folder that was not read."""
    if status in REFUSED:
        return ASK_ADMINISTRATOR
    if status == NOT_ONE:
        return 'Remove or rename all but one of the folders of that name.'
    return 'Open this page again. If the line stays, look at the state of the service in SAS Environment Manager.'


# ---------------------------------------------------------------------------
# The SAS session that builds the page
# ---------------------------------------------------------------------------

def person_of(user):
    """A user id as the person it names: a system can write one person with a domain behind @ or
    in front of a backslash, and in upper or lower case."""
    return text_of(user).lower().split(chr(92))[-1].split('@')[0].strip()


def session_of(session_user, opened_by, context):
    """What is known of the SAS session: whether it runs as another user than the one it was
    started for (other), whether it is known to run as that user (own) - neither is known where
    one of the two ids is missing -, and its compute context. The two user ids are compared here
    and go no further - neither is a text of the page."""
    runs_as, started_for = person_of(session_user), person_of(opened_by)
    known = bool(runs_as and started_for)
    return {'other': known and runs_as != started_for, 'own': known and runs_as == started_for, 'context': text_of(context)}


def recorded(get, statuses):
    """get, with the status of every answer kept: 0 for a request that got no answer."""
    def asked(path, accept):
        try:
            status, body = get(path, accept)
        except Exception:
            statuses.append(0)
            raise
        statuses.append(status)
        return status, body
    return asked


def one_line_for_a_refused_session(lines, statuses, session):
    """Where SAS Viya refused every request of the session, the lines that would say so one by
    one - and the ones that were not looked for because of them - are taken out, and one line
    at the top stands for them: what it is and what to do. Measured on 2026-10-12 for a job in
    the SAS Job Execution compute context of the test system: 15 lines that were not checked, 12
    of them with the advice to ask an administrator - and on a later run that day in the same
    context no request was refused and the line was not there. The line counts as the checks it
    stands for. For a session that runs as the person who opened the page the refusal is about
    that person: 403 is a right to ask for, 401 a sign-in that SAS Viya did not take."""
    if not statuses or any(status not in REFUSED for status in statuses):
        return lines
    kept = [entry for entry in lines if entry['state'] != NOT_CHECKED]
    codes = ' and '.join(str(status) for status in sorted(set(statuses)))
    where = ', in the compute context ' + session['context'] if session['context'] else ''
    if session['other']:
        found = (f'SAS Viya refused every request of this page (HTTP {codes}): its SAS session runs as another user than you{where}, '
                 'and what such a session may read depends on how the system configures its compute context')
        todo = AS_YOURSELF
    elif session.get('own'):
        found = f'SAS Viya refused every request of this page (HTTP {codes}), which ran as you' + where
        todo = ' '.join(([SIGN_IN_REFUSED] if 401 in statuses else []) + ([ASK_RIGHTS] if 403 in statuses else []))
    else:
        found = f'SAS Viya refused every request of this page (HTTP {codes})' + (', which ran in the compute context ' + session['context']
                                                                                 if session['context'] else '')
        todo = 'Open this page again. If the line stays, ' + AS_YOURSELF[0].lower() + AS_YOURSELF[1:]
    entry = line(SESSION, 'Requests to SAS Viya', NOT_CHECKED, found, todo)
    entry['count'] = max(len(lines) - len(kept), 1)
    return [entry] + kept


def as_the_session_may_read(lines, session):
    """In a session that runs as another user than the one who opened the page, the advice of a
    line that was refused is to open the page as oneself: asking an administrator is no advice
    for an administrator whose page a service account built."""
    if not session['other']:
        return lines
    for entry in lines:
        if entry['todo'] == ASK_ADMINISTRATOR:
            entry['todo'] = AS_YOURSELF[:-1] + ' - or ask for the right the session lacks.'
    return lines


# ---------------------------------------------------------------------------
# Reading from SAS Viya: GET, and nothing else
# ---------------------------------------------------------------------------

def read_bytes(get, path, accept='application/json'):
    """(status, body) of one GET. 0 stands for a request that got no answer. Never raises."""
    try:
        status, body = get(path, accept)
    except Exception:
        return 0, b''
    return status, body if isinstance(body, bytes) else str(body or '').encode('utf-8')


def read_json(get, path, accept='application/json'):
    """(status, value) of one GET whose answer is JSON; the value is None for any status but 200."""
    status, body = read_bytes(get, path, accept)
    if status != 200:
        return status, None
    try:
        return 200, json.loads(body.decode('utf-8-sig', errors='replace'))
    except ValueError:
        return NO_JSON, None


def list_all(get, path, limit=100, most=5000):
    """(status, items) of a listing, read page by page to its last item."""
    items = []
    start = 0
    while True:
        status, page = read_json(get, path + ('?' if '?' not in path else chr(38)) + chr(38).join([f'start={start}', f'limit={limit}']))
        if status != 200:
            return status, items
        if not isinstance(page, dict):
            return NO_JSON, items
        batch = [item for item in page.get('items') or [] if isinstance(item, dict)] if isinstance(page.get('items'), list) else []
        items.extend(batch)
        start += len(batch)
        count = page.get('count')
        if not batch or start >= most or (isinstance(count, int) and not isinstance(count, bool) and start >= count):
            return 200, items


def filter_literal(text):
    """A text as a literal of the filter language: in single quotes, or in double quotes when it holds one."""
    return "'" + text + "'" if "'" not in text else '"' + text + '"'


def filtered(path, expression):
    return path + '?filter=' + quote(expression, safe="(),'")


def segment(text):
    """A text as one part of a path."""
    return quote(str(text), safe='')


# ---------------------------------------------------------------------------
# The content root and release.json
# ---------------------------------------------------------------------------

def members_of(get, folder_uri):
    """(status, {name: [(kind of member, uri), ...]}) of one folder. A folder can hold two
    members of one name when they are of two kinds: a registered step, and a file that was
    uploaded under the name of the step. The kind of a member that is a reference is
    'reference', whatever it names: such a member is taken for no file, step, report, job
    definition or folder of this folder."""
    status, items = list_all(get, folder_uri + '/members')
    found = {}
    for item in items:
        name = item.get('name')
        if isinstance(name, str):
            kind = REFERENCE if str(item.get('type') or 'child').lower() == REFERENCE else str(item.get('contentType') or item.get('type') or '')
            found.setdefault(name, []).append((kind, str(item.get('uri') or '')))
    return status, found


def member(members, name, kinds):
    """(kind, uri) of the member of that name: the first of one of these kinds, else the first
    of that name, else two empty texts."""
    named = members.get(name) or []
    return ([found for found in named if found[0] in kinds] or named or [('', '')])[0]


def named_as(members, name, kind):
    """What a folder holds under the name of an entry of a kind, told as mdb tells it: (its objects of that kind,
    the references that stand in the way of the entry, its objects of another kind). The name is the one of the
    entry, to the letter. An object or a reference is of the kind where its uri starts as the uri of an object of
    that kind does. A reference is no object of the folder, and one to an object of another kind is in no list."""
    named = members.get(name, [])
    of_kind = KIND_URI.get(kind, '')
    return ([found for found in named if found[0] != REFERENCE and found[1].startswith(of_kind)],
            [found for found in named if found[0] == REFERENCE and found[1].startswith(of_kind)],
            [found for found in named if found[0] != REFERENCE and not found[1].startswith(of_kind)])


def not_one(count):
    """What the line of an entry says of two or more objects of its name and kind."""
    return f'{count} objects of that name and kind are in its folder, and nothing says which one is meant'


def read_root(get, root):
    """The members of the content root, as {'': (status, {name: [(kind, uri), ...]})}: the folders
    are kept by their path below the root, and the root itself is the empty path."""
    status, folder = read_json(get, '/folders/folders/@item?path=' + segment(root))
    root_id = folder.get('id') if isinstance(folder, dict) else None
    if status == 200 and not root_id:
        status = NO_JSON
    return {'': members_of(get, '/folders/folders/' + segment(root_id)) if status == 200 else (status, {})}


def read_folders(get, listings, wanted):
    """Adds the members of the folders below the content root that are asked for and not read
    yet. A folder is told as the entry of one is: by its name and by what its uri starts with.
    A folder that is not there is 404, one whose parent could not be listed has the status of
    the parent. Where two or more folders have the name, none of them is read: nothing says
    which one is meant, whichever the listing names first."""
    for path in sorted({part for part in wanted if part and part not in listings}, key=lambda part: (part.count('/'), part)):
        parent, _, name = path.rpartition('/')
        parent_status, parent_members = listings.get(parent, (404, {}))
        if parent_status != 200:
            listings[path] = (parent_status, {})
            continue
        folders = named_as(parent_members, name, 'folder')[0]
        if len(folders) == 1:
            listings[path] = members_of(get, folders[0][1])
        else:
            listings[path] = (NOT_ONE if folders else 404, {})
    return listings


def folders_of(paths):
    """Every folder a list of paths lies in, the folders above them included."""
    wanted = set()
    for path in paths:
        folder = path.rpartition('/')[0]
        while folder:
            wanted.add(folder)
            folder = folder.rpartition('/')[0]
    return wanted


def release_of(get, listings):
    """(release.json as a dict or None, the lines about it). The file is looked for among the
    members of the content root and read from the files service."""
    section = 'Release'
    status, members = listings['']
    if status == 404:
        return None, [line(section, 'Content root', MISSING, 'the folder was not found',
                           'Import the package of the accelerator, or set the parameter contentRoot of this job to the folder it was imported into.')]
    if status != 200:
        state, found = not_read(status, 'the content root')
        return None, [line(section, 'Content root', state, found, ask_again(status))]
    kind, uri = member(members, RELEASE_FILE, ('file',))
    files, references, _ = named_as(members, RELEASE_FILE, 'file')
    deploy = 'Import the package of the accelerator again, or run mdb content-deploy, which writes the file.'
    if len(files) > 1:
        # none of them is read: the entries the page checks would be the ones of whichever the listing names first
        return None, [line(section, RELEASE_FILE, MISSING, not_one(len(files)), NOT_ONE_TODO)]
    if references and kind != 'file':
        return None, [line(section, RELEASE_FILE, MISSING, REFERENCE_FOUND, REFERENCE_TODO + deploy)]
    if not uri or kind != 'file':
        return None, [line(section, RELEASE_FILE, MISSING, 'not in the content root', deploy)]
    # the member names where its file is: only a file of the Files service is asked for, as only
    # a folder of the Folders service and a report of the Reports service are
    if not FILE_URI.fullmatch(uri):
        return None, [line(section, RELEASE_FILE, MISSING, 'the member of that name is no file of the Files service', deploy)]
    status, body = read_bytes(get, uri + '/content', '/'.join(['*', '*']))
    if status != 200:
        state, found = not_read(status, RELEASE_FILE)
        return None, [line(section, RELEASE_FILE, state, found, deploy if status == 404 else ask_again(status))]
    try:
        release = json.loads(body.decode('utf-8-sig', errors='replace'))
    except ValueError:
        release = None
    if not isinstance(release, dict):
        return None, [line(section, RELEASE_FILE, MISSING, 'the file is empty or holds no JSON object' if body.strip() else 'the file is empty', deploy)]
    version = text_of(release.get('version') or release.get('release'))
    commit = text_of(release.get('commit'))
    date = text_of(release.get('date'))
    found = ', '.join(part for part in ('release ' + version if version else '', 'commit ' + commit[:12] if commit else '',
                                        'of ' + date[:10] if date else '') if part)
    if not version:
        return release, [line(section, RELEASE_FILE, MISSING, 'the file names no version' + (', ' + found if found else ''), deploy)]
    if references:
        # the file is read, and the reference beside it is what the next import or deploy fails for
        return release, [line(section, RELEASE_FILE, MISSING, found + '; ' + REFERENCE_BESIDE, REFERENCE_BESIDE_TODO)]
    return release, [line(section, RELEASE_FILE, OK, found)]


def entries_of(release):
    """The entries release.json lists, as (path, kind), each path once."""
    entries = release.get('entries') if isinstance(release, dict) else None
    listed = []
    for entry in entries if isinstance(entries, list) else []:
        path = entry.get('path') if isinstance(entry, dict) else None
        if isinstance(path, str) and path.strip('/') and (path.strip('/'), text_of(entry.get('kind'))) not in listed:
            listed.append((path.strip('/'), text_of(entry.get('kind'))))
    return listed


def entry_line(path, kind, listings):
    """One entry of release.json: present in this content root, or not."""
    section = 'Content'
    folder, _, name = path.rpartition('/')
    status, members = listings.get(folder, (404, {}))
    what = f'{path} ({kind})' if kind else path
    deploy = STEP_MISSING if kind == 'step' else REPORT_MISSING if kind == 'report' else ENTRY_MISSING
    if status == 404:
        return line(section, what, MISSING, 'its folder is not in the content root', deploy)
    if status != 200:
        state, found = not_read(status, 'its folder')
        return line(section, what, state, found, ask_again(status))
    # a reference is no object of the folder: only what is left is looked at for the entry
    there, references, others = named_as(members, name, kind)
    if there and kind == 'report':
        # mdb writes no report and fails none over what its folder holds: a report that is there is present
        return line(section, what, OK, 'present')
    if references and not there:
        return line(section, what, MISSING, REFERENCE_FOUND, REFERENCE_TODO + deploy)
    below = BELOW_TOO if kind == 'folder' else ''
    if len(there) > 1 and kind in KIND_URI:
        # the entry is there, so it is counted as present, and its line is shown with the ones that are not ok. Two
        # objects come before a reference beside them: below two folders nothing is read, and their line says why.
        # An entry without a kind mdb knows has no objects of its kind to count: any object of its name is the entry
        return dict(line(section, what, MISSING, not_one(len(there)), NOT_ONE_TODO + below), present=True)
    if references:
        # the same for a reference beside the object
        return dict(line(section, what, MISSING, REFERENCE_BESIDE, REFERENCE_BESIDE_TODO + below), present=True)
    uploaded = kind == 'step' and any(found[1].startswith(KIND_URI['file']) for found in others)
    if there:
        if uploaded:
            return line(section, what, OK, 'present, and a file of that name lies beside it', STEP_BESIDE)
        return line(section, what, OK, 'present')
    if uploaded:
        return line(section, what, MISSING, 'a file of that name is there, and no registered step', STEP_UPLOADED)
    if not others:
        return line(section, what, MISSING, 'not in its folder', deploy)
    told = next((known for known, starts in KIND_URI.items() if others[0][1].startswith(starts)), 'member of another kind')
    return line(section, what, MISSING, f'the object of that name is a {told}', deploy)


def content_lines(release, listings):
    entries = entries_of(release)
    if not entries:
        return [line('Content', 'Entries of ' + RELEASE_FILE, MISSING, 'the file lists no entry',
                     'Import the package of the accelerator again, or run mdb content-deploy, which writes the file.')]
    return [entry_line(path, kind, listings) for path, kind in entries]


def text_of(value):
    return '' if value is None else str(value).strip()


# ---------------------------------------------------------------------------
# The options of the Prompt Builder report
# ---------------------------------------------------------------------------

def unescape(text):
    return (text.replace(chr(38) + 'lt;', '<').replace(chr(38) + 'gt;', '>').replace(chr(38) + 'quot;', '"')
            .replace(chr(38) + 'apos;', "'").replace(chr(38) + 'amp;', chr(38)))


def report_options(content):
    """Every hidden parameter of a report as {label: value}: the options of the Prompt Builder."""
    options = {}
    for label, body in OPTION.findall(content or ''):
        value = OPTION_VALUE.search(body)
        options[unescape(label)] = unescape(value.group(2)).strip() if value else ''
    return options


def builder_options(get, listings):
    """(the options of the Prompt Builder report or None, why they could not be read, what to do about it)."""
    folder, _, name = BUILDER_REPORT.rpartition('/')
    status, members = listings.get(folder, (404, {}))
    kind, uri = member(members, name, ('report',)) if status == 200 else ('', '')
    bring = 'Import the package of the accelerator, then open this page again.'
    if status not in (200, 404):
        return None, not_read(status, 'the folder of the report')[1], ask_again(status)
    if kind != 'report' or not uri.startswith('/reports/reports/'):
        return None, 'the report Prompt Builder is not in the content root', bring
    status, body = read_bytes(get, uri + '/content', REPORT_CONTENT)
    if status != 200:
        return None, not_read(status, 'the report Prompt Builder')[1], bring if status == 404 else ask_again(status)
    options = report_options(body.decode('utf-8-sig', errors='replace'))
    if not options:
        return None, 'the report Prompt Builder holds no options', bring
    return options, '', ''


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

TABLE_GROUPS = ((REPORT_TABLES, 'CAS tables of the reports'), (CHAIN_TABLES, 'CAS tables of the logging chain'))


def table_lines(get, server, caslib):
    """The CAS tables the two reports bind, and the ones of the logging chain that no report binds: there,
    their rows, and when they were loaded."""
    status, servers = list_all(get, '/casManagement/servers')
    names = [text_of(item.get('name')) for item in servers if item.get('name')]
    if status != 200:
        state, found = not_read(status, 'the CAS servers')
        return [line(section, f'{caslib}.{table} ({report})', state, found, ask_again(status))
                for tables, section in TABLE_GROUPS for table, report, _ in tables]
    lines = []
    if server not in names:
        lines.append(line(TABLE_GROUPS[0][1], 'CAS server ' + server, MISSING, 'not among the CAS servers: ' + (', '.join(names[:10]) or 'none is listed'),
                          'Set the parameter casServer of this job to the CAS server that holds the tables.'))
        return lines + [line(section, f'{caslib}.{table} ({report})', NOT_CHECKED, 'the CAS server was not found',
                             'Set the parameter casServer of this job.') for tables, section in TABLE_GROUPS for table, report, _ in tables]
    for table, report, load, section in [(table, report, load, section) for tables, section in TABLE_GROUPS for table, report, load in tables]:
        what = f'{caslib}.{table} ({report})'
        status, info = read_json(get, f'/casManagement/servers/{segment(server)}/caslibs/{segment(caslib)}/tables/{segment(table)}')
        if status == 404:
            lines.append(line(section, what, MISSING, 'not in the CAS library', load))
        elif status != 200 or not isinstance(info, dict):
            state, found = not_read(status if status != 200 else NO_JSON, 'the table')
            lines.append(line(section, what, state, found, ask_again(status)))
        elif text_of(info.get('state')).lower() == 'loaded' or info.get('loaded') is True:
            rows = info.get('rowCount')
            when = text_of(info.get('lastModified') or info.get('modifiedTimeStamp') or info.get('created') or info.get('creationTimeStamp'))
            found = (f'{rows} rows' if isinstance(rows, int) and not isinstance(rows, bool) else 'rows not given') + \
                (', loaded ' + when[:16].replace('T', ' ') + ' UTC' if when else ', time of loading not given')
            lines.append(line(section, what, OK, found))
        else:
            lines.append(line(section, what, MISSING, 'saved in the CAS library and not in memory',
                              'Load the table in SAS Environment Manager, or load it again: ' + load[0].lower() + load[1:]))
    return lines


def repository_lines(get, settings):
    """The SAS Model Manager repository and the three projects of the definitions, found the way
    Get-All-Prompts.sas and the Prompt Builder find them: by name, a project inside the repository.
    Returns (the lines, {what: the id that was found})."""
    section = 'SAS Model Manager'
    setup = 'Run mdb setup, which creates the repository and the three projects.'
    found_ids = {}
    name = settings['repositoryName']
    status, items = list_all(get, filtered('/modelRepository/repositories', f'eq(name,{filter_literal(name)})'))
    repositories = [item for item in items if item.get('name') == name and item.get('id')]
    lines = []
    if status != 200:
        state, found = not_read(status, 'the repositories')
        lines.append(line(section, 'Repository ' + name, state, found, ask_again(status)))
    elif not repositories:
        lines.append(line(section, 'Repository ' + name, MISSING, 'no repository of that name',
                          setup + ' With another name, set the parameter repositoryName of this job.'))
    else:
        found_ids['repository'] = text_of(repositories[0].get('id'))
        lines.append(line(section, 'Repository ' + name, OK, 'found'))
    for key, kind in (('llmProject', 'LLM'), ('embeddingProject', 'embedding model'), ('decisionProject', 'decision model')):
        project = settings[key]
        what = f'Project {project} ({kind} definitions)'
        if 'repository' not in found_ids:
            lines.append(line(section, what, NOT_CHECKED, 'not looked for, as the repository is not known', 'See the line of the repository.'))
            continue
        expression = f"and(eq(name,{filter_literal(project)}),eq(repositoryId,'{found_ids['repository']}'))"
        status, items = list_all(get, filtered('/modelRepository/projects', expression))
        projects = [item for item in items if item.get('name') == project and item.get('id')]
        if status != 200:
            state, found = not_read(status, 'the projects')
            lines.append(line(section, what, state, found, ask_again(status)))
            continue
        if not projects:
            lines.append(line(section, what, MISSING, 'no project of that name in the repository',
                              setup + f' With another name, set the parameter {key} of this job.'))
            continue
        found_ids[key] = text_of(projects[0].get('id'))
        status, page = read_json(get, f"/modelRepository/projects/{segment(found_ids[key])}/models?" + chr(38).join(['start=0', 'limit=1']))
        count = page.get('count') if isinstance(page, dict) else None
        if status != 200 or not isinstance(count, int) or isinstance(count, bool):
            lines.append(line(section, what, OK, 'found, its models could not be counted'))
        elif count == 0:
            lines.append(line(section, what, MISSING, 'found, no model yet',
                              f'Register the {kind} definitions you need with mdb register.'
                              + ('' if key == 'llmProject' else f' Without {kind}s this part of the accelerator has nothing to call.')))
        else:
            lines.append(line(section, what, OK, f'found, {count} model' + ('s' if count != 1 else '')))
    return lines, found_ids


def option_lines(options, why, todo, found_ids):
    """The options of the Prompt Builder report that name the repository and the projects: the
    Builder reads the repository and the two projects by the ids these options hold."""
    section = 'Options of the Prompt Builder report'
    fix = 'Set it in the Options pane of the report Prompt Builder, or run mdb builders-import, which writes it.'
    if options is None:
        return [line(section, 'Options', NOT_CHECKED, why, todo)]
    checks = [('modelRepositoryID', 'repository', 'the repository'), ('llmProjectID', 'llmProject', 'the project of the LLM definitions')]
    if text_of(options.get('enableRag')).lower() in ('true', '1', 'yes', 'on') or text_of(options.get('embeddingProjectID')):
        checks.append(('embeddingProjectID', 'embeddingProject', 'the project of the embedding model definitions'))
    lines = []
    for label, key, what in checks:
        value = text_of(options.get(label))
        if not value:
            lines.append(line(section, label, MISSING, 'the option is empty', fix))
        elif key not in found_ids:
            lines.append(line(section, label, NOT_CHECKED, f'the option is set, and {what} was not found by its name',
                              'See the lines of SAS Model Manager.'))
        elif value == found_ids[key]:
            lines.append(line(section, label, OK, f'names {what} that was found'))
        else:
            lines.append(line(section, label, MISSING, f'names another object than {what} that was found by its name', fix))
    return lines


def credential_lines(get, settings, options):
    """The credential domain of the provider keys, by its name. No entry of it is asked for."""
    section = 'Credentials'
    named = text_of((options or {}).get('credentialDomain'))
    domain = named if named and named.lower() != 'none' else settings['credentialDomain']
    what = 'Credential domain ' + domain
    status, _ = read_json(get, '/credentials/domains/' + segment(domain))
    note = ' The option credentialDomain of the Prompt Builder says none, so the Builder looks up no keys.' if named.lower() == 'none' else ''
    if status == 200:
        return [line(section, what, OK, 'found' + ('; the option credentialDomain of the Prompt Builder says none' if note else ''))]
    if status == 404:
        return [line(section, what, MISSING, 'no credential domain of that name',
                     'Create it and its entries with mdb credentials-apply or the script create-credential-domain.' + note)]
    state, found = not_read(status, 'the credential domain')
    return [line(section, what, state, found, ask_again(status))]


def version_numbers(text):
    return [int(part) for part in re.findall(r'\d+', str(text).split('+')[0])[:4]]


def at_least(version, least):
    """Whether a version is the least version or a later one, by its numbers."""
    have, want = version_numbers(version), version_numbers(least)
    width = max(len(have), len(want))
    return have + [0] * (width - len(have)) >= want + [0] * (width - len(want))


def package_lines(is_installed, version_of):
    """The Python packages of the compute context, for each part of the accelerator.
    is_installed(module) says whether Python finds the module, version_of(names) gives the
    version of the first of these names that is installed, '' when none says."""
    lines = []
    for part, package, module, names, least, needed in PACKAGES:
        section = 'Python packages: ' + part
        what = package + (' ' + least + ' or later' if least else '')
        install = f'Have {what} installed into the Python of this compute context. It is needed for {needed}.'
        try:
            present = bool(is_installed(module))
        except Exception:
            present = False
        if not present:
            lines.append(line(section, what, MISSING, 'not installed', install))
            continue
        try:
            version = text_of(version_of(names))
        except Exception:
            version = ''
        if least and version and not at_least(version, least):
            lines.append(line(section, what, MISSING, 'version ' + version, install))
        else:
            lines.append(line(section, what, OK, 'version ' + version if version else 'installed, its version is not given'))
    return lines


def is_installed(module):
    return importlib.util.find_spec(module) is not None


def version_of(names):
    for name in names:
        if package_metadata is None:
            return ''
        try:
            return package_metadata.version(name)
        except Exception:
            continue
    return ''


def destination_lines(get):
    section = 'Publishing destinations'
    status, items = list_all(get, '/modelPublish/destinations')
    if status != 200:
        state, found = not_read(status, 'the publishing destinations')
        return [line(section, 'Publishing destinations', state, found, ask_again(status))]
    named = [text_of(item.get('name')) + (' (' + text_of(item.get('destinationType')) + ')' if item.get('destinationType') else '')
             for item in items if item.get('name')]
    if not named:
        return [line(section, 'Publishing destinations', MISSING, 'none is defined',
                     'Create the destination the models are published to - see Configuration for Publishing in the Administration Guide.')]
    return [line(section, 'Publishing destinations', OK, f'{len(named)}: ' + ', '.join(named[:20]) + (' and more' if len(named) > 20 else ''))]


def context_lines(get, settings, options):
    """The compute contexts the options of the Prompt Builder and this job name."""
    section = 'Compute contexts'
    wanted = []
    for label, used_for in (('computeContext', 'the prompt optimization job'), ('ragComputeContext', 'the RAG ingestion job')):
        value = text_of((options or {}).get(label))
        if value:
            wanted.append((value, f'option {label} of the Prompt Builder, for {used_for}'))
    for name in [part.strip() for part in settings['computeContexts'].split('|') if part.strip()]:
        if name not in [known for known, _ in wanted]:
            wanted.append((name, 'named by this job'))
    status, items = list_all(get, '/compute/contexts')
    if status != 200:
        state, found = not_read(status, 'the compute contexts')
        return [line(section, f'{name} ({why})', state, found, ask_again(status)) for name, why in wanted] or \
            [line(section, 'Compute contexts', state, found, ask_again(status))]
    names = {text_of(item.get('name')) for item in items if item.get('name')}
    lines = []
    for name, why in wanted:
        if name in names:
            found = line(section, f'{name} ({why})', OK, 'found')
            found['context'] = name
            lines.append(found)
        else:
            lines.append(line(section, f'{name} ({why})', MISSING, 'no compute context of that name',
                              'Create the context in SAS Environment Manager, or correct the name where it is set.'))
    return lines


def check_installation(get, settings, installed=is_installed, version=version_of):
    """Every line of the installation check, and the uri of the two reports where they were found."""
    root = settings['contentRoot'].rstrip('/') or '/'
    listings = read_root(get, root)
    release, lines = release_of(get, listings)
    read_folders(get, listings, folders_of([path for _, path in REPORTS] + [path for path, _ in entries_of(release)]))
    if release is not None:
        lines += content_lines(release, listings)
    reports = {}
    for label, path in REPORTS:
        folder, _, name = path.rpartition('/')
        status, members = listings.get(folder, (404, {}))
        kind, uri = member(members, name, ('report',)) if status == 200 else ('', '')
        reports[label] = uri if kind == 'report' and re.fullmatch(r'/reports/reports/[0-9A-Za-z-]+', uri) else ''
    options, why, todo = builder_options(get, listings)
    lines += table_lines(get, settings['casServer'], settings['caslib'])
    repository, found_ids = repository_lines(get, settings)
    lines += repository
    lines += option_lines(options, why, todo, found_ids)
    lines += credential_lines(get, settings, options)
    lines += package_lines(installed, version)
    lines += destination_lines(get)
    lines += context_lines(get, settings, options)
    return lines, reports


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

STYLE = """
body { font-family: Arial, Helvetica, sans-serif; margin: 0; background: #f5f6f7; color: #1b1d21; line-height: 1.45; }
main { max-width: 68rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
h1 { font-size: 1.6rem; margin: 0 0 0.5rem; }
h2 { font-size: 1.25rem; margin: 2rem 0 0.5rem; }
h3 { font-size: 1.05rem; margin: 1.5rem 0 0.4rem; }
p { margin: 0.4rem 0; }
a { color: #0b5cab; }
table { border-collapse: collapse; width: 100%; background: #ffffff; }
th, td { text-align: left; vertical-align: top; padding: 0.35rem 0.6rem; border-bottom: 1px solid #d9dce1; overflow-wrap: anywhere; }
th { font-size: 0.85rem; background: #e9ebee; }
td.state { white-space: nowrap; font-weight: bold; }
td.ok { color: #1c6b2a; }
td.missing { color: #a1260d; }
td.unchecked { color: #6a5300; }
.summary { background: #ffffff; border-left: 0.3rem solid #0b5cab; padding: 0.6rem 0.8rem; }
.note { font-size: 0.9rem; color: #4a4f57; }
summary { cursor: pointer; margin: 0.5rem 0; }
@media (prefers-color-scheme: dark) {
  body { background: #17191c; color: #e8eaed; }
  a { color: #7db7ff; }
  table, .summary { background: #22252a; }
  th { background: #2c3036; }
  th, td { border-bottom-color: #3a3f47; }
  td.ok { color: #7fd18b; }
  td.missing { color: #ff9c8a; }
  td.unchecked { color: #e6c865; }
  .note { color: #b3b8c0; }
}
"""
STATE_CLASS = {OK: 'ok', MISSING: 'missing', NOT_CHECKED: 'unchecked'}


def shown(value, limit=TEXT_LIMIT):
    """A text as it stands on the page: on one line, cut to its limit, and escaped - nothing of
    it is read as HTML."""
    text = ' '.join(str(value).split())
    if len(text) > limit:
        text = text[:limit - 3].rstrip() + '...'
    return html.escape(text, quote=True)


def link(label, address, here=False):
    """A link of the page: to a path of this host (here), or to an http address. Any other
    address - a script, another scheme, a path where an address was asked for - is text."""
    if not re.match(r'/[^/]' if here else r'https?://[^/]', address or ''):
        return shown(label)
    return f'<a href="{shown(address, 2000)}" rel="noopener noreferrer">{shown(label)}</a>'


# The hosts a guide link of the page may point to: the website of the accelerator and this SAS Viya. The parameter
# guideUrl can come with the request, so an address of another host is shown as text and is no link
GUIDE_HOST = 'sassoftware.github.io'


def guide_link(label, address, hosts):
    """A link to a guide: clickable for an https address of one of the hosts, the label with the address as
    text otherwise."""
    host = re.match(r'https://([^/:?#@]+)(?:[/:?#]|$)', address or '')
    if host and host.group(1).lower() in hosts:
        return link(label, address)
    return shown(label + ': ' + (address or ''))


def rows_of(lines):
    rows = ['<table>', '<thead><tr><th scope="col">Check</th><th scope="col">State</th><th scope="col">Found</th>'
            '<th scope="col">What to do</th></tr></thead>', '<tbody>']
    for entry in lines:
        rows.append(f'<tr><td>{shown(entry["what"])}</td><td class="state {STATE_CLASS.get(entry["state"], "unchecked")}">{shown(entry["state"])}</td>'
                    f'<td>{shown(entry["found"])}</td><td>{shown(entry["todo"])}</td></tr>')
    return rows + ['</tbody>', '</table>']


def counts_of(lines):
    """The checks of each state. The one line of a session that was refused counts as the
    checks it stands for."""
    return {state: sum(entry.get('count', 1) for entry in lines if entry['state'] == state) for state in (OK, MISSING, NOT_CHECKED)}


def render_page(settings, lines, reports, session=None, viya_host=''):
    """The page as HTML text. Every text of it goes through shown()."""
    counts = counts_of(lines)
    session = session or {'other': False, 'context': ''}
    refused = next((entry for entry in lines if entry['section'] == SESSION), None)
    release = next((entry for entry in lines if entry['section'] == 'Release'), None)
    if refused:
        said = refused['found'] + '. ' + refused['todo']
    else:
        said = release['what'] + ': ' + release['found'] + '.' if release else ''
    if refused:
        # the line at the top has said whose session it is and what to do
        whose = ''
    elif session['other']:
        whose = ('This page was built by a SAS session that does not run as you'
                 + (', in the compute context ' + session['context'] if session['context'] else '')
                 + ': it shows what that session may read, which depends on how the system configures its compute context. '
                 + AS_YOURSELF + ' ')
    else:
        whose = ('This page lists what the identity of the SAS session that built it can read. In a compute context that runs as a '
                 'service account, that identity is the service account and not the person who opened the page. ')
    guide = settings['guideUrl'].rstrip('/')
    page = ['<!DOCTYPE html>', '<html lang="en">', '<head>', '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>SAS Agentic AI Accelerator - Start Here</title>', '<style>' + STYLE + '</style>', '</head>', '<body>', '<main>',
            '<h1>SAS Agentic AI Accelerator</h1>',
            '<p>The accelerator brings large language models, embedding models and decision models into SAS Viya as models of '
            'SAS Model Manager that are published like any other model. With the Prompt Builder you write prompt templates, '
            'decision templates and RAG setups, test them against those models and register the result. The log of the containers '
            'becomes tables that say which model answered which request, for which prompt, and what it cost.</p>',
            '<h2>Where to go</h2>', '<ul>']
    for label, _ in REPORTS:
        uri = reports.get(label, '')
        if uri:
            page.append('<li>' + link('Open the report ' + label, '/SASVisualAnalytics/?reportUri=' + quote(uri, safe=''), here=True) + '</li>')
        elif refused:
            page.append('<li>' + shown('The report ' + label + ' could not be looked for.') + '</li>')
        else:
            page.append('<li>' + shown('The report ' + label + ' was not found in ' + settings['contentRoot'] + '.') + '</li>')
    own = re.match(r'https?://([^/:?#@]+)', viya_host or '')
    hosts = {GUIDE_HOST} | ({own.group(1).lower()} if own else set())
    for label, path in GUIDES:
        page.append('<li>' + guide_link(label, guide + '/' + path, hosts) + '</li>')
    if not re.match(r'https://([^/:?#@]+)', guide) or re.match(r'https://([^/:?#@]+)', guide).group(1).lower() not in hosts:
        page.append('<li>' + shown('The parameter guideUrl names an address that is neither the website of the accelerator nor this SAS Viya: '
                                   'the addresses of the guides are text, not links.') + '</li>')
    page += ['</ul>', '<h2>Installation check</h2>',
             '<p class="summary">' + shown(f'{counts[OK]} ok, {counts[MISSING]} missing, {counts[NOT_CHECKED]} not checked. ' + said,
                                           2 * TEXT_LIMIT) + '</p>',
             '<p class="note">' + shown('Content root ' + settings['contentRoot'] + '. ' + whose + 'It changed nothing: it only read.',
                                        2 * TEXT_LIMIT) + '</p>']
    # Where the page says to open it in another compute context, it links itself in each one it found
    if session['other'] or (refused and not session.get('own')):
        # the contexts the page found, or - where they could not be listed - the ones the parameter computeContexts names
        listed = [entry for entry in lines if entry['section'] == 'Compute contexts' and entry['state'] != NOT_CHECKED]
        named = ([entry.get('context', '') for entry in listed if entry['state'] == OK] if listed
                 else [part.strip() for part in settings['computeContexts'].split('|')])
        contexts = []
        for name in named:
            if name and name != session['context'] and name not in contexts:
                contexts.append(name)
        address = '/SASJobExecution/?_program=' + quote(settings['contentRoot'].rstrip('/') + '/Start Here', safe='/')
        targets = [link(name, address + chr(38) + '_contextName=' + quote(name, safe=''), here=True) for name in contexts]
        if targets:
            page.append('<p class="note">' + shown('Open this page in the compute context: ') + shown(', ').join(targets)
                        + shown('. Whether a context runs as you depends on how the system configures it.') + '</p>')
    sections = []
    for entry in lines:
        if entry['section'] not in sections:
            sections.append(entry['section'])
    for section in sections:
        of_section = [entry for entry in lines if entry['section'] == section]
        page.append('<h3>' + shown(section) + '</h3>')
        if section != 'Content':
            page += rows_of(of_section)
            continue
        there = sum(1 for entry in of_section if entry['state'] == OK or entry.get('present'))
        page.append('<p>' + shown(f'{there} of {len(of_section)} entries of {RELEASE_FILE} are present in this content root.') + '</p>')
        absent = [entry for entry in of_section if entry['state'] != OK]
        if absent:
            page += rows_of(absent)
        page += ['<details>', '<summary>' + shown(f'Every entry ({len(of_section)})') + '</summary>'] + rows_of(of_section) + ['</details>']
    page += ['</main>', '</body>', '</html>']
    return '\n'.join(page) + '\n'


# ---------------------------------------------------------------------------
# Handing over to SAS
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


def say(message_type, message):
    """Writes one line to the SAS log. Every message of this program takes this way."""
    SAS.logMessage(sas_safe(message), messageType=message_type)


def hand_over(name, value):
    """Sets a macro variable. Everything the SAS code is handed takes this way."""
    SAS.symput(sas_safe(name), sas_safe(value))


def setting(name):
    """A parameter of the job as text, the empty text when it is not set."""
    try:
        value = SAS.symget(name)
    except Exception:
        value = None
    return '' if value is None else str(value).strip()


def start_here():
    """Reads SAS Viya, writes the page into the WORK folder and tells SAS that it is there."""
    settings = {name: setting(name) for name in ('contentRoot', 'caslib', 'casServer', 'credentialDomain', 'repositoryName', 'llmProject',
                                                 'embeddingProject', 'decisionProject', 'computeContexts', 'guideUrl')}
    base = setting('_sh_viyaHost').rstrip('/')
    token = os.environ.get('SAS_SERVICES_TOKEN', '')
    verify = os.environ.get('SSLCALISTLOC') or os.environ.get('CAS_CLIENT_SSL_CA_LIST') or True
    # the user the session was started for and its compute context are asked for only where the session has them
    session = session_of(setting('SYSUSERID'), setting('SYS_COMPUTE_SESSION_OWNER') if setting('_sh_hasOwner') == '1' else '',
                         setting('_contextName') if setting('_sh_hasContext') == '1' else '')
    statuses = []

    def get(path, accept):
        if requests is None:
            return 0, b''
        response = requests.get(base + path, headers={'Authorization': 'Bearer ' + token, 'Accept': accept}, verify=verify, timeout=(5, 30))
        return response.status_code, response.content

    try:
        lines, reports = check_installation(recorded(get, statuses), settings)
        lines = as_the_session_may_read(one_line_for_a_refused_session(lines, statuses, session), session)
    except Exception as error:
        # A check that this program did not foresee is a line of the page as well
        lines, reports = [line('Installation check', 'The checks', NOT_CHECKED, 'they ended early with ' + type(error).__name__,
                               'Open this page again. If the line stays, hand the log of the job to an administrator.')], {}
    if requests is None:
        lines.insert(0, line('Installation check', 'Python package requests', MISSING, 'not installed, so SAS Viya could not be asked',
                             'Have requests installed into the Python of this compute context.'))
    with open(os.path.join(setting('_sh_work'), PAGE_FILE), 'w', encoding='utf-8', newline='\n') as file:
        file.write(render_page(settings, lines, reports, session, setting('_sh_viyaHost')))
    counts = counts_of(lines)
    say('NOTE', f'Start Here: the page holds {sum(counts.values())} checks - {counts[OK]} ok, {counts[MISSING]} missing, '
                f'{counts[NOT_CHECKED]} not checked.')
    refused = [entry for entry in lines if entry['section'] == SESSION]
    if refused:
        # the log takes no ampersand, so the parameter is named and not written as in an address
        todo = refused[0]['todo'] if chr(38) not in refused[0]['todo'] else (
            'Open the page in a compute context that runs as you: the parameter _contextName, in the address of the page or in the '
            'job definition, names the context.')
        say('NOTE', 'Start Here: ' + refused[0]['found'] + '. ' + todo)
    hand_over('_sh_rc', '0')


start_here()
endsubmit;
run; quit;
proc python terminate;
run; quit;

/* ---- The page, sent to the browser -----------------------------------------
   SAS Job Execution hands a job the fileref _webout, and with the parameter
   _output_type = html it shows what the job writes there as a page. The page
   is copied line by line; no line of it is longer than a record.
   Where the Python program left no page - PROC PYTHON did not run in this
   compute context - the lines below are the page.
   A session without _webout, a program run in SAS Studio for one, has
   nowhere to send the page to: the log names the file in WORK. */
%macro _sh_send;
    %if %sysfunc(fileref(_webout)) GT 0 %then %do;
        %if &_sh_rc. EQ 0 %then
            %put NOTE: Start Here: this SAS session has no fileref _webout, so the page was not sent to a browser. It is the file &_sh_work./start_here.html.;
        %else
            %put NOTE: Start Here: this SAS session has no fileref _webout, and the Python program left no page.;
        %return;
    %end;
    %if &_sh_rc. EQ 0 %then %do;
        data _null_;
            infile "&_sh_work./start_here.html" lrecl=32767 truncover;
            file _webout lrecl=32767;
            input;
            put _infile_;
        run;
    %end;
    %else %do;
        data _null_;
            file _webout lrecl=32767;
            put '<!DOCTYPE html>';
            put '<html lang="en"><head><meta charset="utf-8"><title>SAS Agentic AI Accelerator - Start Here</title></head><body>';
            put '<h1>SAS Agentic AI Accelerator</h1>';
            put '<p>The installation check could not run: the Python program of this page did not finish in this compute context.</p>';
            put '<p>The page needs PROC PYTHON. Open it in a compute context whose Python is configured and that runs as you: the parameter _contextName, in the address of this page or in the job definition, names the context. The log of this job has the reason.</p>';
            put '<p>The guides: <a href="https://sassoftware.github.io/sas-agentic-ai-accelerator" rel="noopener noreferrer">https://sassoftware.github.io/sas-agentic-ai-accelerator</a></p>';
            put '</body></html>';
        run;
        %put NOTE: Start Here: the Python program left no page, the page says so.;
    %end;
%mend _sh_send;
%_sh_send;
%sysmacdelete _sh_send;

/* ---- A page, never a failed job ---------------------------------------------
   What the session could not do is a line of the page, so the job ends as
   completed: SAS Job Execution shows the page of such a job, and its own
   error page for a job that failed. */
options &_sh_syntaxcheck.;
%let syscc = 0;
%symdel _sh_viyaHost _sh_work _sh_rc _sh_syntaxcheck _sh_hasOwner _sh_hasContext / nowarn;
