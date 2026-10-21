# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""mdb deploy --ingress contour and the placement options (2026-10-19, from
the two Contour deployments on AKS).

- without the new options the output is today's, byte for byte
- --ingress contour: the per-model files carry no Ingress, llm-router.yaml
  has one route per rendered model, the resolver and the Contour Ingress
- a subset render warns that the router would lose the other routes
- --node, --node-class, --requests, --limit-memory, --pull-policy land where
  they belong and nowhere else; --dns is the router's resolver
- the refusals of values Kubernetes does not take
"""
import copy
import io
import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mdb import cli
from mdb.core.manifest import load_manifest

REGISTRY = "registry.example.com/llm"
HOST = "viya.example.com"
#: (definitions folder, model_id, template it renders with)
MODELS = [
    ("LLM-Definitions", "claude_sonnet_4_5", "deploy-modelName-template.yaml"),
    ("LLM-Definitions", "qwen_25_05b", "deploy-modelName-PV-template.yaml"),
    ("LLM-Definitions", "gpt_4o_mini_2025_01_01", "deploy-modelName-env-template.yaml"),
    ("Decision-Definitions", "jev_1_13", "deploy-modelName-template.yaml"),
]
PLACEMENT = ["--node", "aks-stateless-1", "--node-class", "stateless", "--requests", "100m,384Mi",
             "--limit-memory", "1Gi", "--pull-policy", "IfNotPresent"]


@pytest.fixture
def defs(tmp_path, monkeypatch, repo_root) -> Path:
    """A definitions root holding a plain, a PV, an Azure and a decision definition."""
    root = tmp_path / "defs"
    for folder, model_id, _ in MODELS:
        shutil.copytree(repo_root / folder / model_id, root / folder / model_id)
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(root))
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "res.openai.azure.com")
    monkeypatch.delenv("SAS_CONTAINER_REGISTRY", raising=False)
    return root


def _deploy(monkeypatch, out_dir: Path, *args) -> tuple:
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))
    result = CliRunner().invoke(cli.app, ["deploy", *args, "--registry", REGISTRY, "--host", HOST,
                                          "--out", str(out_dir)])
    return result, out.getvalue()


def _documents(path: Path) -> list:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def _kind(documents: list, kind: str) -> dict:
    return next(doc for doc in documents if doc["kind"] == kind)


def _reference(repo_root: Path, defs: Path, folder: str, model_id: str, template: str) -> bytes:
    """Today's rendering of a template, written down independently of cli.py:
    the placeholders the templates have always carried, nothing else."""
    manifest = load_manifest(defs / folder / model_id)
    lines = []
    for line in (repo_root / "SCR-LLM-Deployment-YAML" / template).read_text(encoding="utf-8").splitlines():
        line = (line.replace("containerRegistry", REGISTRY).replace("llm_name", model_id)
                .replace("llmname", model_id.replace("_", "-")).replace("model_name", model_id)
                .replace("{{ AZURE-RESOURCE-HOST }}", "res.openai.azure.com")
                .replace("{{ AZURE-DEPLOYMENT-NAME }}", manifest.provider.model_version))
        lines.append(re.sub(r"host$", HOST, line))
    return ("\n".join(lines) + "\n").encode("utf-8")


# -- today's output ----------------------------------------------------------------

@pytest.mark.parametrize("extra", [[], ["--ingress", "nginx"]])
def test_without_the_new_options_the_output_is_todays_byte_for_byte(extra, defs, tmp_path, monkeypatch, repo_root):
    result, printed = _deploy(monkeypatch, tmp_path / "yaml", "--all", *extra)
    assert result.exit_code == 0, printed
    for folder, model_id, template in MODELS:
        written = (tmp_path / "yaml" / f"deploy-{model_id}.yaml").read_bytes()
        assert written == _reference(repo_root, defs, folder, model_id, template), model_id
    assert sorted(path.name for path in (tmp_path / "yaml").iterdir()) == sorted(
        f"deploy-{model_id}.yaml" for _, model_id, _ in MODELS)
    for word in ("router", "--node", "--dns", "workload.sas.com"):
        assert word not in printed


# -- --ingress contour ---------------------------------------------------------------

def test_contour_renders_one_router_with_a_route_per_model(defs, tmp_path, monkeypatch):
    result, printed = _deploy(monkeypatch, tmp_path / "yaml", "--all", "--ingress", "contour", "--namespace", "team-a",
                              "--tls-secret", "sas-ingress-certificate")
    assert result.exit_code == 0, printed
    for _, model_id, _ in MODELS:
        documents = _documents(tmp_path / "yaml" / f"deploy-{model_id}.yaml")
        assert [doc["kind"] for doc in documents] == ["Deployment", "Service"]
        assert {doc["metadata"]["namespace"] for doc in documents} == {"team-a"}
    router = _documents(tmp_path / "yaml" / "llm-router.yaml")
    assert [doc["kind"] for doc in router] == ["ConfigMap", "Deployment", "Service", "Ingress"]
    assert {doc["metadata"]["name"] for doc in router} == {"llm-router"}
    assert {doc["metadata"]["namespace"] for doc in router} == {"team-a"}

    conf = _kind(router, "ConfigMap")["data"]["default.conf"]
    lines = [line.strip() for line in conf.splitlines()]
    routes = [line for line in lines if line.startswith("location = /llm/") and line != (
        'location = /llm/healthz { return 200 "ok\\n"; }')]
    # in the order --all renders them: the LLM definitions, then the decision one
    assert routes == [
        f"location = /llm/{model_id}/{model_id} {{ set $u http://{model_id.replace('_', '-')}.team-a.svc.cluster.local"
        f":443/{model_id}; proxy_pass $u; }}"
        for model_id in ("claude_sonnet_4_5", "gpt_4o_mini_2025_01_01", "qwen_25_05b", "jev_1_13")]
    for line in ("resolver 10.0.0.10 valid=30s ipv6=off;", "client_max_body_size 64m;", "proxy_read_timeout 300s;",
                 "proxy_send_timeout 300s;", 'location = /llm/healthz { return 200 "ok\\n"; }',
                 "location / { return 404; }"):
        assert line in lines

    deployment = _kind(router, "Deployment")
    pod = deployment["spec"]["template"]
    container = pod["spec"]["containers"][0]
    assert container["image"] == "nginxinc/nginx-unprivileged:1.27-alpine"
    assert container["ports"] == [{"containerPort": 8080, "protocol": "TCP"}]
    assert container["readinessProbe"] == {"httpGet": {"path": "/llm/healthz", "port": 8080}}
    assert pod["spec"]["volumes"] == [{"name": "conf", "configMap": {"name": "llm-router"}}]
    assert pod["metadata"]["annotations"]["llm-router/conf-models"] == ",".join(
        re.findall(r"location = /llm/(\w+)/", conf))
    service = _kind(router, "Service")
    assert service["spec"]["ports"] == [{"port": 80, "targetPort": 8080, "protocol": "TCP"}]
    assert service["spec"]["selector"] == {"app": "llm-router"}

    ingress = _kind(router, "Ingress")
    assert ingress["metadata"]["annotations"] == {"projectcontour.io/response-timeout": "300s"}
    assert ingress["spec"] == {
        "ingressClassName": "contour",
        "tls": [{"hosts": [HOST], "secretName": "sas-ingress-certificate"}],
        "rules": [{"host": HOST, "http": {"paths": [{"path": "/llm", "pathType": "Prefix", "backend": {
            "service": {"name": "llm-router", "port": {"number": 80}}}}]}}],
    }
    assert "routes only" not in printed
    assert "kubectl apply -f" in printed and "llm-router.yaml" in printed and f"https://{HOST}/llm/healthz" in printed


def test_contour_keeps_the_model_documents_of_today(defs, tmp_path, monkeypatch):
    """Contour drops the Ingress and nothing else of a model's file."""
    assert _deploy(monkeypatch, tmp_path / "nginx", "--all")[0].exit_code == 0
    assert _deploy(monkeypatch, tmp_path / "contour", "--all", "--ingress", "contour")[0].exit_code == 0
    for _, model_id, _ in MODELS:
        nginx = (tmp_path / "nginx" / f"deploy-{model_id}.yaml").read_text(encoding="utf-8")
        contour = (tmp_path / "contour" / f"deploy-{model_id}.yaml").read_text(encoding="utf-8")
        assert nginx.startswith(contour) and nginx[len(contour):].startswith("---\nkind: Ingress\n")


def test_a_subset_warns_that_the_router_loses_the_other_routes(defs, tmp_path, monkeypatch):
    result, printed = _deploy(monkeypatch, tmp_path / "yaml", "claude_sonnet_4_5", "--ingress", "contour")
    assert result.exit_code == 0, printed
    assert ("llm-router.yaml routes only the 1 model(s) rendered here (claude_sonnet_4_5): applying it removes the "
            "route of every other model the router serves. Render all models with --all.") in printed
    conf = _kind(_documents(tmp_path / "yaml" / "llm-router.yaml"), "ConfigMap")["data"]["default.conf"]
    assert re.findall(r"location = /llm/(\w+)/", conf) == ["claude_sonnet_4_5"]


def test_dns_is_the_routers_resolver(defs, tmp_path, monkeypatch):
    result, printed = _deploy(monkeypatch, tmp_path / "yaml", "--all", "--ingress", "contour", "--dns", "10.96.0.10")
    assert result.exit_code == 0, printed
    conf = _kind(_documents(tmp_path / "yaml" / "llm-router.yaml"), "ConfigMap")["data"]["default.conf"]
    assert "resolver 10.96.0.10 valid=30s ipv6=off;" in conf and "10.0.0.10" not in conf
    assert "kube-dns" not in printed
    result, printed = _deploy(monkeypatch, tmp_path / "nginx", "--all", "--dns", "10.96.0.10")
    assert result.exit_code == 0 and "--dns is the resolver of the contour router" in printed
    assert not (tmp_path / "nginx" / "llm-router.yaml").exists()


def test_without_dns_the_default_resolver_is_named_with_the_way_to_check_it(defs, tmp_path, monkeypatch):
    # A wrong resolver fails no apply - every request through the router answers 502 - so the default is said.
    result, printed = _deploy(monkeypatch, tmp_path / "yaml", "--all", "--ingress", "contour")
    assert result.exit_code == 0, printed
    assert ("The router resolves the model Services with 10.0.0.10, the kube-dns address of AKS. On another cluster "
            "pass --dns with its cluster DNS address: kubectl get service kube-dns -n kube-system -o "
            "jsonpath='{.spec.clusterIP}'") in printed
    result, printed = _deploy(monkeypatch, tmp_path / "nginx", "--all")
    assert result.exit_code == 0 and "kube-dns" not in printed


# -- placement -------------------------------------------------------------------------

def _placed(deployment: dict, node_class: bool = True, sized: bool = True) -> dict:
    """`deployment` as PLACEMENT changes it - the expectation, built from today's."""
    expected = copy.deepcopy(deployment)
    pod = expected["spec"]["template"]["spec"]
    pod["nodeSelector"] = {"kubernetes.io/hostname": "aks-stateless-1"}
    if node_class:
        pod.pop("affinity")
        pod["tolerations"] = [{"key": "workload.sas.com/class", "operator": "Equal", "value": "stateless",
                               "effect": "NoSchedule"}]
    if sized:
        container = pod["containers"][0]
        container["imagePullPolicy"] = "IfNotPresent"
        container["resources"] = {"requests": {"memory": "384Mi", "cpu": "100m"}, "limits": {"memory": "1Gi"}}
    return expected


@pytest.mark.parametrize("ingress", ["nginx", "contour"])
def test_placement_lands_exactly_where_expected(ingress, defs, tmp_path, monkeypatch):
    assert _deploy(monkeypatch, tmp_path / "plain", "--all", "--ingress", ingress)[0].exit_code == 0
    result, printed = _deploy(monkeypatch, tmp_path / "placed", "--all", "--ingress", ingress, *PLACEMENT)
    assert result.exit_code == 0, printed
    for _, model_id, _ in MODELS:
        plain = _documents(tmp_path / "plain" / f"deploy-{model_id}.yaml")
        placed = _documents(tmp_path / "placed" / f"deploy-{model_id}.yaml")
        assert _kind(placed, "Deployment") == _placed(_kind(plain, "Deployment")), model_id
        assert [doc for doc in placed if doc["kind"] != "Deployment"] == [
            doc for doc in plain if doc["kind"] != "Deployment"]
        text = (tmp_path / "placed" / f"deploy-{model_id}.yaml").read_text(encoding="utf-8")
        assert "app: llm\n" not in text.replace("app: llm-", "") and "scalesetpriority" not in text
    if ingress == "contour":
        plain = _documents(tmp_path / "plain" / "llm-router.yaml")
        placed = _documents(tmp_path / "placed" / "llm-router.yaml")
        # the router is placed with the models, but keeps its own size and pull policy
        assert _kind(placed, "Deployment") == _placed(_kind(plain, "Deployment"), sized=False)
        assert [doc for doc in placed if doc["kind"] != "Deployment"] == [
            doc for doc in plain if doc["kind"] != "Deployment"]
    assert "--node pins" not in printed


def test_placement_keeps_the_pv_mount(defs, tmp_path, monkeypatch):
    result, printed = _deploy(monkeypatch, tmp_path / "yaml", "qwen_25_05b", *PLACEMENT)
    assert result.exit_code == 0, printed
    pod = _kind(_documents(tmp_path / "yaml" / "deploy-qwen_25_05b.yaml"), "Deployment")["spec"]["template"]["spec"]
    assert pod["volumes"][0]["persistentVolumeClaim"] == {"claimName": "llm-weights", "readOnly": True}
    assert pod["containers"][0]["volumeMounts"][0]["mountPath"] == "/pybox/model/mount"


def test_each_placement_option_alone(defs, tmp_path, monkeypatch):
    assert _deploy(monkeypatch, tmp_path / "plain", "claude_sonnet_4_5")[0].exit_code == 0
    plain = _kind(_documents(tmp_path / "plain" / "deploy-claude_sonnet_4_5.yaml"), "Deployment")
    cases = {
        ("--node", "n1"): lambda pod, c: pod.update(nodeSelector={"kubernetes.io/hostname": "n1"}),
        ("--node-class", "stateful"): lambda pod, c: (pod.pop("affinity"), pod.update(tolerations=[
            {"key": "workload.sas.com/class", "operator": "Equal", "value": "stateful", "effect": "NoSchedule"}])),
        ("--requests", "250m,512Mi"): lambda pod, c: c["resources"].update(requests={"memory": "512Mi", "cpu": "250m"}),
        ("--limit-memory", "2Gi"): lambda pod, c: c["resources"].update(limits={"memory": "2Gi"}),
        ("--pull-policy", "Always"): lambda pod, c: c.update(imagePullPolicy="Always"),
    }
    for (option, value), change in cases.items():
        out = tmp_path / option.strip("-")
        result, printed = _deploy(monkeypatch, out, "claude_sonnet_4_5", option, value)
        assert result.exit_code == 0, printed
        expected = copy.deepcopy(plain)
        change(expected["spec"]["template"]["spec"], expected["spec"]["template"]["spec"]["containers"][0])
        assert _kind(_documents(out / "deploy-claude_sonnet_4_5.yaml"), "Deployment") == expected, option
        assert ("--node pins the pods to n1, and the app=llm node affinity of the template still applies"
                in printed) == (option == "--node")


@pytest.mark.parametrize("option, value, message", [
    ("--ingress", "traefik", "--ingress 'traefik' is not one of nginx, contour."),
    ("--node", "Node_1", "--node 'Node_1' is not a Kubernetes node name"),
    ("--node-class", "-x", "--node-class '-x' is not a Kubernetes label value"),
    ("--requests", "100m", "--requests '100m' is not CPU,MEMORY as Kubernetes quantities"),
    ("--requests", "100m,lots", "--requests '100m,lots' is not CPU,MEMORY"),
    ("--limit-memory", "1GB", "--limit-memory '1GB' is not a Kubernetes quantity"),
    ("--pull-policy", "always", "--pull-policy 'always' is not one of Always, IfNotPresent, Never."),
    ("--dns", "10.0.0.10;", "--dns '10.0.0.10;' is neither an IP address nor a host name."),
])
def test_values_kubernetes_does_not_take_are_refused(option, value, message, defs, tmp_path, monkeypatch):
    result, printed = _deploy(monkeypatch, tmp_path / "yaml", "--all", option, value)
    assert result.exit_code == 2
    assert message in printed
    assert not (tmp_path / "yaml").exists()


def test_the_router_template_is_where_deploy_reads_it(repo_root):
    text = (repo_root / "SCR-LLM-Deployment-YAML" / cli.ROUTER_TEMPLATE).read_text(encoding="utf-8")
    # exactly one route line - deploy repeats it per model - and every slot it fills
    assert text.count("location = /llm/model_name/model_name") == 1
    for slot in ("kubeDnsIp", "routedModelIds", "secretName: llm-tls-certs", "- host", "host: host"):
        assert slot in text
