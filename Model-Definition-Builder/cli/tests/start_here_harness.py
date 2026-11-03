"""Runs the installation check of mdb check (mdb/core/start_here.py) against a SAS Viya system that is made up.

The module is read from its file and run in a namespace of its own, so a test can replace a function of
it without touching the module mdb imports. SAS Viya is a Site: a content folder, CAS tables, SAS Model
Manager, a credential domain, publishing destinations and compute contexts that a test builds and then
takes things out of. The Site answers GET requests and knows no other. The compute server is a probe -
what viya/startpage.py hands the check for one compute context.
"""
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

MODULE = Path(__file__).resolve().parents[1] / "src" / "mdb" / "core" / "start_here.py"
REPOSITORY = Path(__file__).resolve().parents[3]
VIYA_HOST = "https://viya.example.com"
ROOT = "/SAS Agentic AI Accelerator"
TOKEN = "not-a-token"

# The settings of the check where the job definition Start Here gives none
DEFAULT_SETTINGS = {
    "contentRoot": ROOT, "caslib": "Public", "casServer": "cas-shared-default", "credentialDomain": "agentic-ai-keys",
    "repositoryName": "LLM Repository", "llmProject": "LLM Model Project", "embeddingProject": "Embedding Model Project",
    "decisionProject": "Decision Model Project",
    "computeContexts": "SAS Studio compute context|SAS Job Execution compute context",
    "guideUrl": "https://sassoftware.github.io/sas-agentic-ai-accelerator",
}

# What release.json of the made-up content folder lists: folders first, as mdb writes them, then the objects
ENTRIES = [
    ("Data", "folder"), ("Logging and Monitoring", "folder"), ("Prompt Builder", "folder"), ("RAG", "folder"),
    ("RAG/rag_core", "folder"), ("RAG/steps", "folder"), ("RAG/rag_core/adapters", "folder"),
    ("release.json", "file"), ("Start Here", "jobDefinition"),
    ("Prompt Builder/Prompt Builder", "report"), ("Prompt Builder/SAS-LLM-Prompt-Builder-UI", "jobDefinition"),
    ("RAG/steps/RAG - List Documents.step", "step"), ("RAG/rag_core/__init__.py", "file"), ("RAG/rag_core/adapters/base.py", "file"),
    ("Logging and Monitoring/LLM Usage Report", "report"), ("Logging and Monitoring/LLM - Log Parser.step", "step"),
    ("Logging and Monitoring/Log-Parser-Code.sas", "file"),
    ("Data/llm_fact_sheet.csv", "file"), ("Data/Load-Accelerator-Tables", "jobDefinition"),
    ("Decisions", "folder"), ("Decisions/Call LLM Node", "decision"),
]
MEMBER_TYPE = {"folder": "folder", "file": "file", "step": "flowStep", "jobDefinition": "jobDefinition", "report": "report",
               "decision": "decision"}
URI_OF = {"folder": "/folders/folders/", "file": "/files/files/", "step": "/dataFlows/steps/", "jobDefinition": "/jobDefinitions/definitions/",
          "report": "/reports/reports/", "decision": "/decisions/flows/"}
TABLES = ("ACCELERATOR_RELEASES", "LLM_LOGS", "PROMPT_EXPERIMENTS", "LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET",
          "DECISION_FACT_SHEET", "RAG_SETUPS", "RAG_RUNS")
# The tables of the logging chain that no report binds (round 19; the LLM Usage Report binds the other three since
# its Decision Models and RAG pages)
CHAIN_TABLES = ("PROMPT_OPTIMIZATIONS", "MODEL_CATALOG")


def source():
    """The module of the check, with LF line endings."""
    return MODULE.read_text(encoding="utf-8").replace("\r\n", "\n")


#: The text the tests read the rules of the check from: the module
python_block = source


class FakeResponse:
    def __init__(self, status_code, body=b""):
        self.status_code = status_code
        self.content = body


def option_xml(options):
    """The content of a report with one hidden parameter per option, as the Prompt Builder keeps them."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<SASReport label="Prompt Builder"><PromptDefinitions>']
    for position, (label, value) in enumerate(options.items()):
        escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = f'<String behavior="fixed">{escaped}</String>' if str(value) else '<String behavior="fixed"/>'
        parts.append(f'<PromptDefinition label="{label}" hidden="true" isParameter="true" dataType="string" multiValued="false" '
                     f'name="pr{position}">\n  <DefaultValue>{body}</DefaultValue>\n</PromptDefinition>')
    parts.append("</PromptDefinitions></SASReport>")
    return "\n".join(parts)


class Site:
    """A SAS Viya system with a complete installation of the accelerator, unless a test changes it."""

    def __init__(self, entries=None, page_size=None):
        self._next = 0
        self.page_size = page_size
        self.requests = []          # (path, query) of every GET, in their order
        self.answers = {}           # path -> status, for a path a test wants refused or broken
        self.prefixes = {}          # path prefix -> status
        self.folders = {}           # folder id -> list of members
        self.folder_ids = {}        # path below the root ('' for the root) -> folder id
        self.files = {}             # uri -> bytes
        self.reports = {}           # uri -> content
        self.root = ROOT
        self.entries = list(ENTRIES if entries is None else entries)
        self.release = {"kind": "sas-agentic-ai-accelerator/release", "version": "2.3.0",
                        "commit": "0123456789abcdef0123456789abcdef01234567", "date": "2026-10-12T10:00:00+02:00",
                        "entries": [{"path": path, "kind": kind, "hash": None} for path, kind in self.entries]}
        self.release_text = None    # what the file holds when it is not the JSON of self.release
        self.cas_servers = ["cas-shared-default"]
        self.tables = {name: {"name": name, "state": "loaded", "scope": "global", "rowCount": 100 + position,
                              "lastModified": "2026-10-07T16:12:02.378Z", "createdBy": "uid7781"}
                       for position, name in enumerate(TABLES + CHAIN_TABLES)}
        self.repositories = [{"id": self._id("rrrrrrrr"), "name": "LLM Repository", "createdBy": "uid7781"}]
        self.projects = [{"id": self._id("pppppppp"), "name": name, "repositoryId": self.repositories[0]["id"], "createdBy": "uid7781"}
                         for name in ("LLM Model Project", "Embedding Model Project", "Decision Model Project")]
        self.models = {self.projects[0]["id"]: 31, self.projects[1]["id"]: 17, self.projects[2]["id"]: 5}
        self.domains = ["agentic-ai-keys"]
        self.destinations = [{"name": "maslocal", "destinationType": "microAnalyticService", "createdBy": "uid7781"},
                             {"name": "llm-registry", "destinationType": "privateDocker"}]
        self.contexts = [{"name": "SAS Studio compute context", "createdBy": "uid7781"}, {"name": "SAS Job Execution compute context"},
                         {"name": "Optimize compute context"}]
        self.options = {
            "viyaHost": "", "modelRepositoryID": self.repositories[0]["id"], "llmProjectID": self.projects[0]["id"],
            "credentialDomain": "agentic-ai-keys", "enableOptimization": "true", "computeContext": "Optimize compute context",
            "enableRag": "true", "embeddingProjectID": self.projects[1]["id"], "ragComputeContext": "SAS Studio compute context",
        }
        self.folder_ids[""] = self._id("ffffffff")
        self.folders[self.folder_ids[""]] = []
        for path, kind in self.entries:
            self.add(path, kind)

    def _id(self, prefix):
        self._next += 1
        return f"{prefix}-0000-4000-8000-{self._next:012d}"

    # -- building -----------------------------------------------------------
    def add(self, path, kind, member_type=None):
        """Puts an object into the content folder, its folders included."""
        folder, _, name = path.rpartition("/")
        if folder and folder not in self.folder_ids:
            self.add(folder, "folder")
        if kind == "folder" and path in self.folder_ids:
            return self.folder_ids[path]
        identifier = self._id("oooooooo")
        uri = URI_OF.get(kind, "/files/files/") + identifier
        if kind == "folder":
            self.folder_ids[path] = identifier
            self.folders[identifier] = []
        self.folders[self.folder_ids[folder]].append({
            "id": self._id("mmmmmmmm"), "name": name, "contentType": member_type or MEMBER_TYPE.get(kind, kind), "uri": uri,
            "type": "child", "createdBy": "uid7781", "addedBy": "uid9024"})
        return uri

    def twin(self, path, kind, first=False):
        """Puts one more object of that name and kind into the folder, beside what is there: a child with an id of
        its own. A second folder has members of its own - one file - and is no folder of a path. Two objects of
        one name and kind in one folder were staged in no pass on SAS Viya; mdb content-deploy has a line for
        them. Returns its uri."""
        folder, _, name = path.rpartition("/")
        identifier = self._id("tttttttt")
        if kind == "folder":
            self.folders[identifier] = [{"id": self._id("mmmmmmmm"), "name": "__init__.py", "contentType": "file", "type": "child",
                                         "uri": "/files/files/" + self._id("oooooooo")}]
        entry = {"id": self._id("mmmmmmmm"), "name": name, "contentType": MEMBER_TYPE.get(kind, kind), "uri": URI_OF[kind] + identifier,
                 "type": "child", "createdBy": "uid7781", "addedBy": "uid9024"}
        members = self.folders[self.folder_ids[folder]]
        members.insert(0, entry) if first else members.append(entry)
        return entry["uri"]

    def refer(self, path, kind="file", first=False):
        """Puts a reference into a folder, under the name of the path: a member that names an object of that kind
        which lives elsewhere. The member has the shape that was read on SAS Viya 2026.09 on 2026-10-13 for a
        reference to a file: its type says reference, and contentType and uri are the ones of the object it
        names. A reference to a folder names a folder that has members of its own. Returns the uri it names."""
        folder, _, name = path.rpartition("/")
        identifier = self._id("eeeeeeee")
        uri = URI_OF.get(kind, "/files/files/") + identifier
        if kind == "folder":
            self.folders[identifier] = [{"id": self._id("mmmmmmmm"), "name": "__init__.py", "contentType": "file", "type": "child",
                                         "uri": "/files/files/" + self._id("oooooooo")}]
        entry = {"id": self._id("mmmmmmmm"), "name": name, "contentType": MEMBER_TYPE.get(kind, kind), "uri": uri, "type": "reference",
                 "createdBy": "uid7781", "addedBy": "uid9024"}
        members = self.folders[self.folder_ids[folder]]
        members.insert(0, entry) if first else members.append(entry)
        return uri

    def remove(self, path):
        """Takes an object out of its folder. A folder goes with everything in it."""
        folder, _, name = path.rpartition("/")
        members = self.folders[self.folder_ids[folder]]
        members[:] = [member for member in members if member["name"] != name]
        for known in [known for known in self.folder_ids if known == path or known.startswith(path + "/")]:
            del self.folder_ids[known]

    def member(self, path):
        folder, _, name = path.rpartition("/")
        return next(member for member in self.folders[self.folder_ids[folder]] if member["name"] == name)

    def refuse(self, prefix, status):
        """Answers every path that starts this way with a status: 403 for a right that is lacking, 500 for a service that is down."""
        self.prefixes[prefix] = status

    def paths(self, prefix=""):
        return [path for path, _ in self.requests if path.startswith(prefix)]

    # -- answering ----------------------------------------------------------
    def get(self, url, headers=None, verify=None, timeout=None):
        assert url.startswith(VIYA_HOST + "/"), url
        parts = urlsplit(url)
        path = unquote(parts.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        self.requests.append((path, dict(query, **{"@accept": (headers or {}).get("Accept")})))
        assert (headers or {}).get("Authorization") == "Bearer " + TOKEN
        assert timeout is not None
        if path in self.answers:
            return FakeResponse(self.answers[path], b"{}")
        for prefix, status in self.prefixes.items():
            if path.startswith(prefix):
                return FakeResponse(status, b'{"message": "refused for uid7781@example.com"}')
        segments = path.strip("/").split("/")

        if path == "/folders/folders/@item":
            wanted = query.get("path", "")
            if wanted != self.root:
                return FakeResponse(404)
            return self._json({"id": self.folder_ids[""], "name": self.root.rsplit("/", 1)[-1], "createdBy": "uid7781"})
        if segments[:2] == ["folders", "folders"] and segments[3:] == ["members"]:
            if segments[2] not in self.folders:
                return FakeResponse(404)
            return self._page(self.folders[segments[2]], query)
        if segments[:2] == ["files", "files"] and segments[3:] == ["content"]:
            release_uri = self._release_uri()
            if release_uri and path == release_uri + "/content":
                text = self.release_text if self.release_text is not None else json.dumps(self.release, indent=2)
                return FakeResponse(200, text.encode("utf-8"))
            return FakeResponse(200, self.files.get(path[:-len("/content")], b""))
        if segments[:2] == ["reports", "reports"] and segments[3:] == ["content"]:
            assert (headers or {}).get("Accept") == "application/vnd.sas.report.content+xml"
            return FakeResponse(200, option_xml(self.options).encode("utf-8"))
        if path == "/casManagement/servers":
            return self._page([{"name": name} for name in self.cas_servers], query)
        if segments[:2] == ["casManagement", "servers"] and len(segments) == 7 and segments[3] == "caslibs" and segments[5] == "tables":
            if segments[2] not in self.cas_servers or segments[4] != "Public" or segments[6] not in self.tables:
                return FakeResponse(404)
            return self._json(self.tables[segments[6]])
        if path == "/modelRepository/repositories":
            return self._page(self._filtered(self.repositories, query.get("filter", "")), query)
        if path == "/modelRepository/projects":
            return self._page(self._filtered(self.projects, query.get("filter", "")), query)
        if segments[:2] == ["modelRepository", "projects"] and segments[3:] == ["models"]:
            count = self.models.get(segments[2], 0)
            return self._json({"items": [{"id": "m", "name": "a model", "createdBy": "uid7781"}][:min(count, int(query.get("limit", 20)))],
                               "count": count})
        if segments[:2] == ["credentials", "domains"] and len(segments) == 3:
            return self._json({"id": segments[2], "type": "password", "createdBy": "uid7781"}) if segments[2] in self.domains else FakeResponse(404)
        if path == "/modelPublish/destinations":
            return self._page(self.destinations, query)
        if path == "/compute/contexts":
            return self._page(self.contexts, query)
        return FakeResponse(404)

    def _release_uri(self):
        root = self.folders[self.folder_ids[""]]
        return next((member["uri"] for member in root if member["name"] == "release.json"), None)

    @staticmethod
    def _json(value):
        return FakeResponse(200, json.dumps(value).encode("utf-8"))

    @staticmethod
    def _conditions(expression):
        """The eq(field,'value') conditions of a filter, alone or inside one and(...)."""
        expression = unquote(expression)
        if expression.startswith("and(") and expression.endswith(")"):
            expression = expression[4:-1]
        conditions = []
        for part in expression.split("),"):
            part = part if part.endswith(")") else part + ")"
            assert part.startswith("eq(") and part.endswith(")"), expression
            field, _, literal = part[3:-1].partition(",")
            assert literal[0] == literal[-1] and literal[0] in "'\"", literal
            conditions.append((field, literal[1:-1]))
        return conditions

    def _filtered(self, items, expression):
        if not expression:
            return items
        return [item for item in items if all(item.get(field) == value for field, value in self._conditions(expression))]

    def _page(self, items, query):
        start, limit = int(query.get("start", 0)), int(query.get("limit", 20))
        size = min(limit, self.page_size) if self.page_size else limit
        return self._json({"items": items[start:start + size], "start": start, "limit": limit, "count": len(items)})


class PageRun:
    """What one run of the check left behind: its lines, the uris of the reports and the page."""

    def __init__(self, namespace, work, site, probes):
        self.namespace, self.work, self.site, self.probes = namespace, Path(work), site, probes
        self.lines, self.reports, self.statuses = [], {}, []

    @property
    def page(self):
        return (self.work / "start_here.html").read_text(encoding="utf-8")


# Every package the check looks for, in a version that is new enough
ALL_PACKAGES = {"pandas": "2.2.3", "swat": "1.18.1", "requests": "2.32.3", "psycopg2": "2.9.10", "psycopg2-binary": "2.9.10",
                "singlestoredb": "1.17.0", "pypdfium2": "4.30.0", "markitdown": "0.1.3", "dspy": "3.2.1", "optuna": "4.1.0"}
PARTS = ["Prompt building", "Logging and the LLM Usage Report", "RAG", "Prompt optimization", "Decision models"]


def probe(packages=ALL_PACKAGES, context="SAS Studio compute context", parts=None, **changes):
    """What the compute server of a compute context said: a session, PROC PYTHON, these packages
    ({module or name: version}, None for one that is not installed), SAS Viya answered 401, and sessions that
    run as the user."""
    found = {"context": context, "parts": list(parts or PARTS), "started": True, "python": True,
             "packages": {name: version for name, version in (packages or {}).items() if version is not None},
             "viya": {"status": 401, "failure": "", "host": VIYA_HOST}, "sameUser": True}
    found.update(changes)
    return found


def load(monkeypatch, work, site=None, settings=None, packages=ALL_PACKAGES, probes=None):
    """The functions of the check, in a namespace of their own. `probes` are what the compute server said,
    by default one probe of a context that has `packages`."""
    site = site if site is not None else Site()
    namespace = {"__name__": "start_here"}
    exec(compile(source(), str(MODULE), "exec"), namespace)
    probes = probes if probes is not None else [probe(packages)]
    loaded = PageRun(namespace, work, site, probes)
    loaded.settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    return loaded


def run(monkeypatch, work, site=None, settings=None, packages=ALL_PACKAGES, probes=None, checked=None):
    """The check as mdb check runs it - every line, the one line for a refusal of everything, the page -,
    with the page written to work/start_here.html."""
    loaded = load(monkeypatch, work, site, settings, packages, probes)
    functions = loaded.namespace
    lines, reports = functions["check_installation"](functions["recorded"](get_of(loaded.site), loaded.statuses),
                                                     loaded.settings, loaded.probes)
    loaded.lines = functions["one_line_for_a_refused_session"](lines, loaded.statuses)
    loaded.reports = reports
    page = functions["render_page"](loaded.settings, loaded.lines, reports, checked or {"when": "2026-11-03 10:00 UTC", "by": "mdb check"},
                                    VIYA_HOST)
    (Path(work) / "start_here.html").write_text(page, encoding="utf-8")
    return loaded


def get_of(site):
    """The get(path, accept) the checks of the program are handed, answered by a Site."""
    def get(path, accept="application/json"):
        response = site.get(VIYA_HOST + path, headers={"Authorization": "Bearer " + TOKEN, "Accept": accept}, verify=True, timeout=(5, 30))
        return response.status_code, response.content
    return get
