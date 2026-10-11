# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register Setup and a SAS Model Manager project that does not exist yet.

`ensure_project` creates the project it does not find. SAS Model Manager
places a project in a folder, and it does not take the folder from the
repository the request names:

    POST /modelRepository/projects returned 400: {"httpStatusCode":400,
    "message":"You must specify a value for the \\"folderId\\" parameter."}

That was found on a live system; the same request with the `folderId` of the
repository added was answered 201. Nobody had met it, because the project of
the default configuration (RAG Engineering) already existed everywhere the
step had run.

The stub answers the way the service was seen to answer: a repository carries
`id`, `name`, `folderId` and `defaultRepository`, and a project without a
`folderId` is refused with that 400.
"""
import pathlib

import pytest

from rag_core.registration import ViyaClient, register_setup

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "retrieve_context.py"
REFUSAL = ('{"version":2,"httpStatusCode":400,"message":"You must specify a '
           'value for the \\"folderId\\" parameter."}')
SETTINGS = {
    "BACKEND": "pgvector", "COLLECTION": "rag_hr_policies_v1",
    "EMBED_MODEL": "all_minilm_l6_v2", "EMBED_ENDPOINT": "https://viya.example/llm",
    "STORE_HOST": "db.example", "STORE_PORT": "5432", "STORE_DB": "postgres",
    "STORE_SSLMODE": "require", "CREDENTIAL_DOMAIN": "agentic-ai-keys",
    "DEFAULT_K": 4, "INGESTION_RUN_ID": "run-1", "rag_project": "RAG_HR",
    "tables_caslib": "casuser",
}
NINE = ([{"id": "repo-%d" % index, "name": "Repository %d" % index,
          "folderId": "folder-%d" % index, "defaultRepository": False}
         for index in range(1, 5)]
        + [{"id": "repo-public", "name": "Public", "folderId": "folder-public",
            "defaultRepository": True}]
        + [{"id": "repo-%d" % index, "name": "Repository %d" % index,
            "folderId": "folder-%d" % index, "defaultRepository": False}
           for index in range(6, 10)])


class _Reply:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = b"x"
        self.text = text
        self.headers = {"ETag": 'W/"stub"'}

    def json(self):
        return self._payload


class _ModelManager:
    """Projects and repositories as SAS Model Manager keeps them, and enough
    of the rest for a whole registration."""

    def __init__(self, projects=(), repositories=NINE):
        self.calls = []
        self.headers = {}
        self.projects = [dict(project) for project in projects]
        self.repositories = [dict(repository) for repository in repositories]
        self.model = None

    def posted_projects(self):
        return [body for method, url, _, body in self.calls
                if method == "POST" and url.endswith("/modelRepository/projects")]

    def request(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        body = kwargs.get("json")
        self.calls.append((method, url, params, body))
        if url.endswith("/modelRepository/repositories"):
            limit = int(params.get("limit", 20))
            return _Reply(200, {"items": self.repositories[:limit]})
        if url.endswith("/modelRepository/projects"):
            if method == "GET":
                # the filter is not evaluated: the caller compares the names
                return _Reply(200, {"items": [dict(p) for p in self.projects]})
            if not body.get("folderId"):
                return _Reply(400, text=REFUSAL)
            created = dict(body, id="proj-%d" % (len(self.projects) + 1))
            self.projects.append(created)
            return _Reply(201, created)
        if "/projectVersions" in url:
            return _Reply(200, {"items": [{"id": "ver-1", "name": "Version 1"}]})
        if "/modelRepository/projects/" in url:
            return _Reply(200, {"id": url.rsplit("/", 1)[-1],
                                "latestVersion": "Version 1"})
        if "/folders/folders/@item" in url:
            return _Reply(200, {"id": "folder-1"})
        if url.endswith("/members"):
            return _Reply(200, {"items": []})
        if url.endswith("/files/files"):
            return _Reply(201, {"id": "file-1"})
        if url.endswith("/contents"):
            return _Reply(201, {})
        if url.endswith("/modelRepository/models/model-1"):
            if method == "GET":
                return _Reply(200, dict(self.model))
            self.model = dict(body)
            return _Reply(200, dict(self.model))
        if url.endswith("/modelRepository/models"):
            if method == "GET":
                return _Reply(200, {"items": [self.model] if self.model else []})
            self.model = dict(body, id="model-1", tags=[])
            return _Reply(201, {"count": 1, "items": [{"id": "model-1"}]})
        return _Reply(200, {})


def _client(service):
    return ViyaClient("https://viya.example", "token", session=service)


# ---------------------------------------------------------------------------
# a project that is not there yet
# ---------------------------------------------------------------------------
def test_a_new_project_names_the_folder_of_its_repository():
    service = _ModelManager()
    project_id = _client(service).ensure_project("RAG Claims")
    assert project_id == "proj-1"
    (body,) = service.posted_projects()
    assert body["name"] == "RAG Claims"
    assert body["folderId"] == "folder-public"
    assert body["repositoryId"] == "repo-public"
    # what the request held before is still in it
    assert body["function"] == "RAG"
    assert body["tags"] == ["LLM", "RAG-Engineering"]
    assert body["description"].startswith("RAG setups registered by")


def test_the_request_without_the_folder_is_the_one_the_service_refuses():
    """The stub is worth what its refusal is worth: the body `ensure_project`
    sent before this change gets the 400 that was seen."""
    service = _ModelManager()
    with pytest.raises(RuntimeError, match="returned 400.*folderId"):
        _client(service).json(
            "POST", "/modelRepository/projects",
            json={"name": "RAG Claims", "function": "RAG",
                  "repositoryId": "repo-public"})


def test_the_default_repository_is_chosen_wherever_it_stands_in_the_list():
    """Not the first one the service lists: the one it marks."""
    service = _ModelManager()
    assert service.repositories[0]["id"] != "repo-public"
    chosen = _client(service).project_repository("RAG Claims")
    assert chosen == {"id": "repo-public", "folderId": "folder-public",
                      "name": "Public"}
    listing = [params for method, url, params, _ in service.calls
               if url.endswith("/modelRepository/repositories")]
    # more than one is asked for, or the mark could not be looked for
    assert listing == [{"limit": 100}]


@pytest.mark.parametrize("mark", [True, "true", "True"])
def test_the_mark_is_read_as_a_boolean_or_as_its_word(mark):
    repositories = [dict(NINE[0]), dict(NINE[1], defaultRepository=mark)]
    service = _ModelManager(repositories=repositories)
    assert _client(service).project_repository("P")["id"] == "repo-2"


@pytest.mark.parametrize("unmarked", [False, None, "false", "", 0])
def test_without_a_marked_repository_the_first_one_is_taken(unmarked):
    repositories = [dict(repository, defaultRepository=unmarked)
                    for repository in NINE]
    service = _ModelManager(repositories=repositories)
    _client(service).ensure_project("RAG Claims")
    (body,) = service.posted_projects()
    assert (body["repositoryId"], body["folderId"]) == ("repo-1", "folder-1")


def test_a_repository_that_does_not_carry_the_mark_at_all_is_not_the_default():
    repositories = [{"id": "repo-a", "name": "A", "folderId": "folder-a"},
                    {"id": "repo-b", "name": "B", "folderId": "folder-b"}]
    service = _ModelManager(repositories=repositories)
    assert _client(service).project_repository("P")["id"] == "repo-a"


# ---------------------------------------------------------------------------
# what cannot be created says why
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("folder", [None, "", "absent"])
def test_a_repository_without_a_folder_is_refused_by_name(folder):
    """A clear text, not a KeyError - and no request that the service would
    refuse with a text about a parameter nobody set."""
    public = {"id": "repo-public", "name": "Public", "defaultRepository": True}
    if folder != "absent":
        public["folderId"] = folder
    service = _ModelManager(repositories=[dict(NINE[0]), public])
    with pytest.raises(RuntimeError) as refused:
        _client(service).ensure_project("RAG Claims")
    text = str(refused.value)
    assert "'RAG Claims'" in text and "'Public'" in text
    assert "folder (folderId)" in text
    assert "Create the project in SAS Model Manager" in text
    assert not service.posted_projects()


def test_a_repository_without_an_id_is_refused_too():
    service = _ModelManager(repositories=[{"name": "Public", "folderId": "f",
                                           "defaultRepository": True}])
    with pytest.raises(RuntimeError, match="does not name its id"):
        _client(service).ensure_project("RAG Claims")
    assert not service.posted_projects()


def test_no_repository_at_all_is_still_said_as_before():
    service = _ModelManager(repositories=[])
    with pytest.raises(RuntimeError, match="no model repository exists to create "
                                           "the project 'RAG Claims' in"):
        _client(service).ensure_project("RAG Claims")
    assert not service.posted_projects()


# ---------------------------------------------------------------------------
# a project that is there
# ---------------------------------------------------------------------------
def test_an_existing_project_is_found_without_any_post():
    service = _ModelManager(projects=[{"id": "proj-7", "name": "RAG Engineering"}])
    assert _client(service).ensure_project("RAG Engineering") == "proj-7"
    assert [method for method, _, _, _ in service.calls] == ["GET"]
    assert not service.posted_projects()
    # and the repositories are not even read
    assert not [url for _, url, _, _ in service.calls if "repositories" in url]


def test_a_project_of_another_name_on_the_page_is_not_the_project():
    service = _ModelManager(projects=[{"id": "proj-7", "name": "RAG Engineering 2"}])
    assert _client(service).ensure_project("RAG Engineering") == "proj-2"
    (body,) = service.posted_projects()
    assert body["name"] == "RAG Engineering" and body["folderId"] == "folder-public"


# ---------------------------------------------------------------------------
# the whole registration
# ---------------------------------------------------------------------------
def test_a_setup_is_registered_into_a_project_that_did_not_exist():
    service = _ModelManager()
    result = register_setup(
        _client(service), dict(SETTINGS), TEMPLATE.read_text(encoding="utf-8"),
        [], "/SAS Agentic AI Accelerator/RAG", "HR", mm_project="RAG Claims",
        log=lambda *_: None)
    assert result["project_id"] == "proj-1"
    (body,) = service.posted_projects()
    assert body["name"] == "RAG Claims" and body["folderId"] == "folder-public"
    created = next(body for method, url, _, body in service.calls
                   if method == "POST" and url.endswith("/modelRepository/models"))
    assert created["projectId"] == "proj-1"


def test_registering_again_creates_no_second_project():
    service = _ModelManager()
    for _ in range(2):
        register_setup(
            _client(service), dict(SETTINGS), TEMPLATE.read_text(encoding="utf-8"),
            [], "/SAS Agentic AI Accelerator/RAG", "HR", mm_project="RAG Claims",
            log=lambda *_: None)
    assert len(service.posted_projects()) == 1
