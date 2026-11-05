# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The installation check of the content folder, and the page Start Here that shows it.

`mdb check` runs it: the checks read SAS Viya with GET requests only (`get`, a function of a path and an
Accept header that answers (status, bytes)), the compute server is probed in a compute session
(viya/startpage.py), and the page is written into the job definition Start Here, which serves it as a
form - nothing runs when somebody opens it. Until the first check the job definition holds the page the
release ships (SAS-Viya-Integrations/Setup/Start-Here.html), which says to run `mdb check`.

This module imports nothing of mdb and no HTTP library: what it reads it is handed.
"""
import html
import json
import re
from urllib.parse import quote

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
# The fact sheets of the folder Data are the ones of the release: where a site keeps definitions of its own,
# mdb load-facts loads its sheets, and the job would replace them
FACTS_TODO = ('Run mdb load-facts, or the job Load-Accelerator-Tables of the folder Data in this content root,'
              ' which loads the fact sheets of the release, not those of definitions of your own.')
# The CAS tables the two reports bind, the report that reads each, and what loads it
REPORT_TABLES = (
    ('ACCELERATOR_RELEASES', 'Prompt Builder', LOADER_JOB + ', or mdb load-releases.'),
    ('LLM_LOGS', 'LLM Usage Report', 'Collect the logs of the containers and run Log-Parser-Code.sas or the step LLM - Log Parser.'),
    ('PROMPT_EXPERIMENTS', 'LLM Usage Report', 'Run Get-All-Prompts.sas or the step LLM - Get All Prompts.'),
    ('LLM_FACT_SHEET', 'LLM Usage Report', FACTS_TODO),
    ('EMBEDDING_FACT_SHEET', 'LLM Usage Report', FACTS_TODO),
    ('DECISION_FACT_SHEET', 'LLM Usage Report', FACTS_TODO),
    ('RAG_SETUPS', 'LLM Usage Report', 'Run Get-All-Prompts.sas or the step LLM - Get All Prompts.'),
    ('RAG_RUNS', 'LLM Usage Report', 'Run Get-All-Prompts.sas or the step LLM - Get All Prompts.'),
)
# The CAS tables of the logging chain that no report binds: two of the tables Get-All-Prompts.sas writes beside
# PROMPT_EXPERIMENTS, which Link-Logs-To-Models.sas reads
GET_ALL_PROMPTS = 'Run Get-All-Prompts.sas or the step LLM - Get All Prompts.'
CHAIN_TABLES = (
    ('PROMPT_OPTIMIZATIONS', 'Link-Logs-To-Models.sas', GET_ALL_PROMPTS),
    ('MODEL_CATALOG', 'Link-Logs-To-Models.sas', GET_ALL_PROMPTS),
)
# The Python packages of each part: (part, name to install, module, names its version is found under, the least version, needed for)
PACKAGES = (
    ('Logging and Monitoring', 'pandas', 'pandas', ('pandas',), '', 'the log parser and the link program'),
    ('Logging and Monitoring', 'swat', 'swat', ('swat',), '', 'the log parser and the link program'),
    ('Logging and Monitoring', 'requests', 'requests', ('requests',), '', 'Get-All-Prompts.sas'),
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
SESSION = 'Requests of mdb check'
# What a file of the Files service is addressed by
FILE_URI = re.compile(r'/files/files/[0-9A-Za-z-]+')
ASK_ADMINISTRATOR = 'Run mdb check as a user who may read it, or ask an administrator for the right to read it.'
# What to do where every request of mdb check was refused
SIGN_IN_REFUSED = 'Run mdb check again. If the line stays, ask an administrator why SAS Viya does not take your sign-in.'
ASK_RIGHTS = 'Ask an administrator for the rights to read what mdb check checks, or run it as an administrator.'
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
# A decision comes with the package as well, with the custom node type it carries (the Call LLM node under its one id)
DECISION_MISSING = ('Import the package of the accelerator again, or run mdb builders-import: a decision comes with the package, with '
                    'the Call LLM node, and mdb content-deploy writes none. Where builders-import names mdb decision-node-migrate, '
                    'run that first.')
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
# alone or beside the object, and for two objects of its name and kind. It writes no report and no decision and fails none over
# what a folder holds. The page judges what a folder holds under the name of an entry the same way. Below a folder
# mdb fails, the two differ: mdb looks at nothing there, and the page reads the one folder of the name - its line
# says that nothing is written below it - and reads nothing where two folders have the name
KIND_URI = {'file': '/files/files/', 'step': '/dataFlows/steps/', 'jobDefinition': '/jobDefinitions/definitions/',
            'report': '/reports/reports/', 'folder': '/folders/folders/', 'decision': '/decisions/flows/'}
# The kinds that come with the package alone: mdb writes none of them and fails none over what its folder holds
PACKAGE_ONLY = ('report', 'decision')
REFERENCE_BESIDE = 'present, and a reference of that name is in its folder, which names an object that lives elsewhere'
REFERENCE_BESIDE_TODO = 'Remove the reference from the folder: while it is there, the import and mdb content-deploy write nothing for this entry.'
# Two objects of the name and kind of an entry in one folder: the entry is there, and nothing is written for it
NOT_ONE_TODO = 'Remove or rename all but one of them: while they are there, the import and mdb content-deploy write nothing for this entry.'
# What the line of a folder adds to either: mdb writes nothing into a folder whose entry it fails, nor below it
BELOW_TOO = ' Nothing is written below the folder either.'

# A line of a part that the options of the Prompt Builder switch off: what it found does not matter there
NOT_USED = 'not used'
# The parts of the accelerator, in the order of the page. Prompt building is what every site needs
CORE = 'Prompt building'
LOGGING = 'Logging and the LLM Usage Report'
RAG_PART = 'RAG'
OPTIMIZATION = 'Prompt optimization'
DECISIONS = 'Decision models'
PARTS = (CORE, LOGGING, RAG_PART, OPTIMIZATION, DECISIONS)
# The option of the Prompt Builder report that switches a part on, and whether it is on where the option is not set -
# as the Prompt Builder reads it (config.ts); prompt building and logging have none
PART_SWITCH = {RAG_PART: ('enableRag', False), OPTIMIZATION: ('enableOptimization', False), DECISIONS: ('enableDecision', True)}
# The RAG backends the Prompt Builder offers, by the option that switches each off (on where not set), and the
# package each needs
BACKEND_SWITCH = {'psycopg2-binary': ('enable_pgvector', 'pgvector'), 'singlestoredb': ('enable_singlestore', 'SingleStore')}
ON_WORDS = ('true', '1', 'yes', 'on')
OFF_WORDS = ('false', '0', 'no', 'off')
# The section and the troubleshooting page of the one line that stands for every request that got no answer
TROUBLESHOOTING_TLS = 'Administration-Guide/Troubleshooting-Guide#program-certificate-verify-failed'


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
    return 'Run mdb check again. If the line stays, look at the state of the service in SAS Environment Manager.'


# ---------------------------------------------------------------------------
# The requests of mdb check, and the compute server
# ---------------------------------------------------------------------------

def person_of(user):
    """A user id as the person it names: a system can write one person with a domain behind @ or
    in front of a backslash, and in upper or lower case."""
    return text_of(user).lower().split(chr(92))[-1].split('@')[0].strip()


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


def one_line_for_a_refused_session(lines, statuses):
    """Where SAS Viya refused every request of mdb check, the lines that would say so one by one - and
    the ones that were not looked for because of them - are taken out, and one line at the top stands
    for them, as the checks it stands for: 403 is a right to ask for, 401 a sign-in SAS Viya did not take."""
    if not statuses or any(status not in REFUSED for status in statuses):
        return lines
    kept = [entry for entry in lines if entry['state'] != NOT_CHECKED]
    codes = ' and '.join(str(status) for status in sorted(set(statuses)))
    found = f'SAS Viya refused every request of mdb check (HTTP {codes})'
    todo = ' '.join(([SIGN_IN_REFUSED] if 401 in statuses else []) + ([ASK_RIGHTS] if 403 in statuses else []))
    entry = line(SESSION, 'Requests to SAS Viya', NOT_CHECKED, found, todo)
    entry['count'] = max(len(lines) - len(kept), 1)
    return [entry] + kept


def no_answer_said(kind, host, guide):
    """(what was found, what to do) where the compute server could not reach SAS Viya, for the kind of
    failure: tls (the certificate of SAS Viya is not trusted there), connect, timeout, or other."""
    # the page names no host: SAS Viya is the address the SAS option SERVICESBASEURL names
    where = 'SAS Viya (the address the SAS option SERVICESBASEURL names)'
    if kind == 'tls':
        return (f'the Python of this compute context does not trust the TLS certificate of {where}',
                'Point SAS_VIYA_CABUNDLE at a file with the CA certificates of your site in the SAS session of this compute context - '
                'on a test system SAS_VIYA_VERIFY_SSL=false switches the check off -, then run mdb check again. See ' + guide.rstrip('/')
                + '/' + TROUBLESHOOTING_TLS)
    if kind == 'connect':
        return (f'this compute context could not connect to {where} - the name does not resolve from inside the cluster, '
                'or the connection was refused',
                'Have the administrator of the cluster check that the pods of the compute server reach the address the SAS option '
                'SERVICESBASEURL names, through a proxy where the cluster needs one.')
    if kind == 'timeout':
        return (f'{where} did not answer this compute context in time',
                'Run mdb check again. If the line stays, have the administrator of the cluster check the way from the compute server '
                'to the address the SAS option SERVICESBASEURL names.')
    return ('the request from this compute context ended without an answer', 'The log of the compute session names the error.')


#: The compute context each part runs its jobs in: the option of the Prompt Builder that names it
PART_CONTEXT = {RAG_PART: 'ragComputeContext', OPTIMIZATION: 'computeContext'}
#: The parts whose jobs read and write as the person who starts them, so their compute context has to run as that person
AS_THE_USER = (RAG_PART, OPTIMIZATION)
COMPUTE = 'Compute server'
PYTHON_TODO = ('Configure Python for this compute context (SAS Viya: Configuring Python Integration), or point the option that '
               'names it at a context that has Python.')


def compute_lines(probes, guide):
    """The lines of the compute server, from one probe per compute context: whether a session could be
    started, whether PROC PYTHON runs, whether the compute server reaches SAS Viya and trusts its
    certificate, and - for the parts whose jobs run as the user - whether its sessions run as you. A
    probe is {'context', 'parts', 'started', 'error', 'python', 'viya': {'status', 'failure', 'host'},
    'sameUser'}; each line is of the first part that uses the context."""
    lines = []
    for probe in probes:
        context, parts = text_of(probe.get('context')), list(probe.get('parts') or [CORE])
        part = parts[0]
        if not probe.get('started'):
            lines.append(dict(line(COMPUTE, f'{context}: a compute session', NOT_CHECKED,
                                   'no session could be started' + (': ' + text_of(probe['error']) if probe.get('error') else ''),
                                   'Look at the compute context in SAS Environment Manager, and whether you may start sessions in it.'),
                              part=part))
            continue
        if not probe.get('python'):
            lines.append(dict(line(COMPUTE, f'{context}: PROC PYTHON', MISSING, 'does not run in this compute context', PYTHON_TODO),
                              part=part))
            continue
        lines.append(dict(line(COMPUTE, f'{context}: PROC PYTHON', OK, 'runs'), part=part))
        viya = probe.get('viya') or {}
        if viya.get('failure'):
            found, todo = no_answer_said(viya['failure'], viya.get('host', ''), guide)
            lines.append(dict(line(COMPUTE, f'{context}: SAS Viya from the compute server', MISSING, found, todo), part=part,
                              failure=viya['failure']))
        elif viya.get('switch') == 'mdb':
            lines.append(dict(line(COMPUTE, f'{context}: SAS Viya from the compute server', OK,
                                   f'answered HTTP {viya.get("status")} with TLS verification off: mdb runs with '
                                   'SAS_VIYA_VERIFY_SSL=false and set it in the session of this check'), part=part))
        else:
            off = ' (its sessions switch TLS verification off with SAS_VIYA_VERIFY_SSL)' if viya.get('switch') == 'context' else ''
            trusts = 'reaches SAS Viya' if off else 'reaches SAS Viya and trusts its certificate'
            lines.append(dict(line(COMPUTE, f'{context}: SAS Viya from the compute server', OK,
                                   f'answered HTTP {viya.get("status")}: the compute server {trusts}{off}'),
                              part=part))
        same = probe.get('sameUser')
        for user_part in parts:
            if user_part not in AS_THE_USER:
                continue
            what = f'{context}: the user of its sessions ({user_part})'
            if same is False:
                lines.append(dict(line(COMPUTE, what, MISSING, 'its sessions run as another user than you',
                                       f'Point the option {PART_CONTEXT[user_part]} of the Prompt Builder at a compute context that runs '
                                       'as the person who starts the job, such as SAS Studio compute context.'), part=user_part))
            elif same is True:
                lines.append(dict(line(COMPUTE, what, OK, 'its sessions run as you'), part=user_part))
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
                           'Import the package of the accelerator, or run mdb check --content-root with the folder it was imported into.')]
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
    deploy = STEP_MISSING if kind == 'step' else REPORT_MISSING if kind == 'report' else DECISION_MISSING if kind == 'decision' else ENTRY_MISSING
    if status == 404:
        return line(section, what, MISSING, 'its folder is not in the content root', deploy)
    if status != 200:
        state, found = not_read(status, 'its folder')
        return line(section, what, state, found, ask_again(status))
    # a reference is no object of the folder: only what is left is looked at for the entry
    there, references, others = named_as(members, name, kind)
    if there and kind in PACKAGE_ONLY:
        # mdb writes no report and no decision and fails none over what its folder holds: one that is there is present
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
    bring = 'Import the package of the accelerator, then run mdb check again.'
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
                          'Set the parameter casServer of the job definition Start Here to the CAS server that holds the tables.'))
        return lines + [line(section, f'{caslib}.{table} ({report})', NOT_CHECKED, 'the CAS server was not found',
                             'Set the parameter casServer of the job definition Start Here.') for tables, section in TABLE_GROUPS for table, report, _ in tables]
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
                          setup + ' With another name, set the parameter repositoryName of the job definition Start Here.'))
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
                              setup + f' With another name, set the parameter {key} of the job definition Start Here.'))
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


#: The part of the accelerator each group of packages is for
PACKAGE_PART = {'Logging and Monitoring': LOGGING, 'RAG': RAG_PART, 'Prompt optimization': OPTIMIZATION}


def probe_of(probes, part):
    """The probe of the compute context a part runs its jobs in, or None."""
    return next((probe for probe in probes if part in (probe.get('parts') or [])), None)


def package_lines(probes):
    """The Python packages each part needs, in the compute context it runs its jobs in: what the probe of
    that context found - {module: version, '' for one that gives none} for the modules that are installed."""
    lines = []
    for group, package, module, names, least, needed in PACKAGES:
        section = 'Python packages: ' + group
        what = package + (' ' + least + ' or later' if least else '')
        probe = probe_of(probes, PACKAGE_PART[group])
        if not probe or not probe.get('started') or not probe.get('python'):
            where = f' in {probe["context"]}' if probe else ''
            lines.append(line(section, what, NOT_CHECKED, f'not looked at: no Python ran{where}', 'See the lines of the compute server.'))
            continue
        context = text_of(probe.get('context'))
        install = f'Have {what} installed into the Python of the compute context {context}. It is needed for {needed}.'
        installed = probe.get('packages') or {}
        if installed.get(module) is None:
            lines.append(line(section, what, MISSING, f'not installed in {context}', install))
            continue
        version = text_of(next((installed.get(name) for name in names if installed.get(name)), '') or installed.get(module))
        if least and version and not at_least(version, least):
            lines.append(line(section, what, MISSING, f'version {version} in {context}', install))
        else:
            lines.append(line(section, what, OK, (f'version {version}' if version else 'installed, its version is not given')
                              + f' in {context}'))
    return lines


def contexts_to_probe(options, settings, given=None):
    """[(compute context, [the parts that run their jobs in it])], one entry per context: prompt building and
    logging in the one given (--compute-context), else the first the parameter computeContexts names; RAG and
    prompt optimization in the one their option names, else that one - where the options do not switch them off."""
    named = [part.strip() for part in text_of(settings.get('computeContexts')).split('|') if part.strip()]
    first = text_of(given) or (named[0] if named else 'SAS Studio compute context')
    plan = {first: [CORE, LOGGING]}
    in_use = parts_in_use(options)
    for part, label in PART_CONTEXT.items():
        if in_use[part] is False:
            continue
        plan.setdefault(text_of((options or {}).get(label)) or first, []).append(part)
    return list(plan.items())


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
    """The compute contexts the options of the Prompt Builder and the parameter computeContexts of Start Here name."""
    section = 'Compute contexts'
    wanted = []
    for label, used_for in (('computeContext', 'the prompt optimization job'), ('ragComputeContext', 'the RAG ingestion job')):
        value = text_of((options or {}).get(label))
        if value:
            wanted.append((value, f'option {label} of the Prompt Builder, for {used_for}'))
    for name in [part.strip() for part in settings['computeContexts'].split('|') if part.strip()]:
        if name not in [known for known, _ in wanted]:
            wanted.append((name, 'named by Start Here'))
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


def read_options(get, settings):
    """The options of the Prompt Builder report of the content root, or None where they cannot be read."""
    listings = read_root(get, settings['contentRoot'].rstrip('/') or '/')
    read_folders(get, listings, folders_of([path for _, path in REPORTS]))
    return builder_options(get, listings)[0]


def check_installation(get, settings, probes=()):
    """Every line of the installation check, and the uri of the two reports where they were found. `probes`
    are what the compute server said, one per compute context (compute_lines)."""
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
    lines += destination_lines(get)
    lines += context_lines(get, settings, options)
    lines += compute_lines(probes, settings['guideUrl'])
    lines += package_lines(probes)
    return with_parts(lines, options), reports


# ---------------------------------------------------------------------------
# The parts of the accelerator
# ---------------------------------------------------------------------------

def part_of(entry):
    """The part of the accelerator a line belongs to, by its section and what it looked at."""
    section, what = entry['section'], entry['what']
    if section.startswith('Python packages: '):
        return {'Logging and Monitoring': LOGGING, 'RAG': RAG_PART, 'Prompt optimization': OPTIMIZATION}.get(
            section[len('Python packages: '):], CORE)
    if section == 'CAS tables of the reports':
        return CORE if 'ACCELERATOR_RELEASES' in what or what.startswith('CAS server') else LOGGING
    if section == 'CAS tables of the logging chain':
        return LOGGING
    if section == 'SAS Model Manager':
        return RAG_PART if '(embedding model definitions)' in what else DECISIONS if '(decision model definitions)' in what else CORE
    if section == 'Options of the Prompt Builder report':
        return RAG_PART if what == 'embeddingProjectID' else CORE
    if section == 'Compute contexts':
        return OPTIMIZATION if 'option computeContext ' in what else RAG_PART if 'option ragComputeContext ' in what else CORE
    return CORE


def flag(value, fallback):
    """An on/off option of the Prompt Builder report as the Prompt Builder reads it: an on word, an off
    word, or the fallback for anything else."""
    word = text_of(value).lower()
    return True if word in ON_WORDS else False if word in OFF_WORDS else fallback


def parts_in_use(options):
    """{part: True / False / None} - None where the options could not be read, so nothing is known."""
    return {part: (True if part not in PART_SWITCH else None if options is None
                   else flag(options.get(PART_SWITCH[part][0]), PART_SWITCH[part][1])) for part in PARTS}


def with_parts(lines, options):
    """Every line with its part, and whether that part is used on this site (used: False where the options
    switch it off). A line of a part the options switch off, or of a RAG backend they do not offer, that is
    not ok is not used: what it found does not matter on this site."""
    in_use = parts_in_use(options)
    for entry in lines:
        entry['part'] = entry.get('part') or part_of(entry)
        entry['used'] = in_use[entry['part']] is not False
        if entry['state'] == OK:
            continue
        if not entry['used']:
            switch = PART_SWITCH[entry['part']][0]
            entry['state'] = NOT_USED
            entry['todo'] = f'Nothing to do: the option {switch} of the Prompt Builder switches {entry["part"]} off on this site.'
            continue
        backend = BACKEND_SWITCH.get(entry['what'])
        if entry['part'] == RAG_PART and backend and options is not None and not flag(options.get(backend[0]), True):
            entry['state'] = NOT_USED
            entry['todo'] = f'Nothing to do: the option {backend[0]} of the Prompt Builder does not offer {backend[1]} on this site.'
    return lines


def part_verdicts(lines):
    """One verdict per part that has lines, in the order of PARTS: (part, verdict, what to do first). A
    line that was made after the parts were given - the one line of a session - is of the part its
    section says."""
    verdicts = []
    for part in PARTS:
        of_part = [entry for entry in lines if entry.get('part', part_of(entry)) == part]
        missing = [entry for entry in of_part if entry['state'] == MISSING]
        unchecked = [entry for entry in of_part if entry['state'] == NOT_CHECKED]
        if of_part and part in PART_SWITCH and not any(entry.get('used', True) for entry in of_part):
            verdicts.append((part, 'not used here', f'The option {PART_SWITCH[part][0]} of the Prompt Builder switches it off.'))
        elif missing:
            count = sum(entry.get('count', 1) for entry in missing)
            verdicts.append((part, f'{count} to do', missing[0]['what'] + ': ' + missing[0]['todo']))
        elif unchecked:
            count = sum(entry.get('count', 1) for entry in unchecked)
            verdicts.append((part, f'{count} not checked', unchecked[0]['what'] + ': ' + unchecked[0]['todo']))
        elif of_part:
            verdicts.append((part, 'ready', ''))
    return verdicts


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
td.unused { color: #5b6068; font-weight: normal; }
td.verdict { white-space: nowrap; font-weight: bold; }
th[scope="row"] { background: transparent; font-size: 1rem; white-space: nowrap; }
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
  td.unused { color: #9aa0a8; }
  .note { color: #b3b8c0; }
}
"""
STATE_CLASS = {OK: 'ok', MISSING: 'missing', NOT_CHECKED: 'unchecked', NOT_USED: 'unused'}
# The class of the cell of a verdict of a part, by how the verdict starts
VERDICT_CLASS = (('ready', 'ok'), ('not used', 'unused'), ('not checked', 'unchecked'))


def shown(value, limit=TEXT_LIMIT):
    """A text as it stands on the page: on one line, cut to its limit, and escaped - nothing of
    it is read as HTML."""
    text = ' '.join(str(value).split())
    if len(text) > limit:
        text = text[:limit - 3].rstrip() + '...'
    return go_template_safe(html.escape(text, quote=True))


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
    return {state: sum(entry.get('count', 1) for entry in lines if entry['state'] == state) for state in (OK, MISSING, NOT_CHECKED, NOT_USED)}


def counts_said(counts):
    """The counts as the summary and the log say them: not used only where there is such a line."""
    return (f'{counts[OK]} ok, {counts[MISSING]} missing, {counts[NOT_CHECKED]} not checked'
            + (f', {counts[NOT_USED]} not used' if counts.get(NOT_USED) else ''))


def verdict_rows(verdicts):
    """The table of the verdicts of the parts."""
    rows = ['<table>', '<thead><tr><th scope="col">Part</th><th scope="col">State</th><th scope="col">First thing to do</th></tr></thead>',
            '<tbody>']
    for part, verdict, first in verdicts:
        kind = next((css for word, css in VERDICT_CLASS if word in verdict), 'missing')
        # the part is the header of its row: a row of a check starts with a cell, a row of a part does not
        rows.append(f'<tr><th scope="row">{shown(part)}</th><td class="verdict {kind}">{shown(verdict)}</td><td>{shown(first)}</td></tr>')
    return rows + ['</tbody>', '</table>']


def go_template_safe(text):
    """A page as SAS Job Execution can serve it as a form: the form is read as a Go template, and two braces
    open or close one of its actions. Every brace of a text of the page is written as an entity; the style and
    the markup of the page hold no two braces side by side."""
    return text.replace('{', '&#123;').replace('}', '&#125;')


def render_page(settings, lines, reports, checked=None, viya_host=''):
    """The page as HTML text. Every text of it goes through shown(). `checked` says when and by what the check
    ran: {'when': text, 'by': text}."""
    counts = counts_of(lines)
    checked = checked or {}
    refused = next((entry for entry in lines if entry['section'] == SESSION), None)
    release = next((entry for entry in lines if entry['section'] == 'Release'), None)
    if refused:
        said = refused['found'] + '. ' + refused['todo']
    else:
        said = release['what'] + ': ' + release['found'] + '.' if release else ''
    guide = settings['guideUrl'].rstrip('/')
    page = page_head()
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
    when = text_of(checked.get('when'))
    by = text_of(checked.get('by')) or 'mdb check'
    page += ['</ul>', '<h2>Installation check</h2>',
             '<p class="summary">' + shown(counts_said(counts) + '. ' + said, 2 * TEXT_LIMIT) + '</p>',
             '<p class="note">' + shown(('Last checked on ' + when + ' by ' + by if when else 'Checked by ' + by) + ', for the content root '
                                        + settings['contentRoot'] + '. Run mdb check again to bring this page up to date: it shows what '
                                        'the person who ran the check could read, and changes nothing.', 2 * TEXT_LIMIT) + '</p>']
    verdicts = part_verdicts(lines)
    if len(verdicts) > 1:
        # one line of a session that was refused is all there is: the summary has said it
        page += ['<h3>' + shown('By part') + '</h3>'] + verdict_rows(verdicts)
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
    text = '\n'.join(page) + '\n'
    assert '{{' not in text and '}}' not in text
    return text


INTRODUCTION = ('The accelerator brings large language models, embedding models and decision models into SAS Viya as models of '
                'SAS Model Manager that are published like any other model. With the Prompt Builder you write prompt templates, '
                'decision templates and RAG setups, test them against those models and register the result. The log of the containers '
                'becomes tables that say which model answered which request, for which prompt, and what it cost.')


def page_head():
    """The lines of the page up to its list of where to go, which is left open."""
    return ['<!DOCTYPE html>', '<html lang="en">', '<head>', '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>SAS Agentic AI Accelerator - Start Here</title>', '<style>' + STYLE + '</style>', '</head>', '<body>', '<main>',
            '<h1>SAS Agentic AI Accelerator</h1>', '<p>' + shown(INTRODUCTION, 2 * TEXT_LIMIT) + '</p>', '<h2>Where to go</h2>', '<ul>']


def placeholder_page(guide_url=None):
    """The page the release ships, before the first mdb check: the guides, and what to run. The job definition
    holds it as its form; SAS-Viya-Integrations/Setup/Start-Here.html is this text."""
    guide = (guide_url or 'https://sassoftware.github.io/sas-agentic-ai-accelerator').rstrip('/')
    page = page_head()
    for label, path in GUIDES:
        page.append('<li>' + guide_link(label, guide + '/' + path, {GUIDE_HOST}) + '</li>')
    page += ['</ul>', '<h2>Installation check</h2>',
             '<p class="summary">' + shown('This installation has not been checked yet.') + '</p>',
             '<p>' + shown('Run mdb check from the checkout of the accelerator, as the administrator who sets it up. It checks the '
                           'content folder, the CAS tables, SAS Model Manager, the credential domain, the publishing destinations and '
                           'the compute server, prints what to do, and writes the result into this page - with the links to the two '
                           'reports of your site.') + '</p>',
             '<pre>mdb check</pre>',
             '<p class="note">' + shown('See ' + guide + '/Administration-Guide/Setup-Additional-UIs#mdb-check') + '</p>',
             '</main>', '</body>', '</html>']
    text = '\n'.join(page) + '\n'
    assert '{{' not in text and '}}' not in text
    return text
