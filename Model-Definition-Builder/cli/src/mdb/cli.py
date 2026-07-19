# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The mdb command-line interface.

Every command prints the exact next step; nothing requires reading docs
before first success. Restricted networks are first-class: --offline works
everywhere, proxies and corporate CA bundles are honored from the
environment (HTTPS_PROXY, REQUESTS_CA_BUNDLE) and MDB_VERIFY_SSL=false
mirrors the repo's existing -k convention.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__
from .core import drift, facts
from .core.generator import (
    CoreAssets, GenerationError, effective_score_file, list_custom_options, render_assets,
)
from .core.importer import import_folder
from .core.manifest import MANIFEST_FILENAME, ModelManifest, export_json_schema, load_manifest
from .core.netutil import env_flag, make_session
from .core.paths import RepoNotFoundError, core_dir, definitions_dir, fact_sheet_path, find_repo_root
from .core.validator import validate_all, validate_folder
from .providers import load_adapters
from .providers.base import CatalogModel, ProviderAdapter, slugify

app = typer.Typer(
    name="mdb",
    help="Model Definition Builder for the SAS Agentic AI Accelerator.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


KINDS = ("llm", "embedding")


class Context:
    def __init__(self) -> None:
        try:
            self.repo = find_repo_root()
        except RepoNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)
        self.core = CoreAssets.load(core_dir(self.repo))

    def defs_dir(self, kind: str) -> Path:
        return definitions_dir(self.repo, kind)

    def fact_sheet(self, kind: str) -> Path:
        return fact_sheet_path(self.repo, kind)

    def kind_of(self, folder: Path) -> str:
        return "embedding" if folder.parent.name == "Embedding-Definitions" else "llm"

    def managed_folders(self) -> list[Path]:
        folders = []
        for kind in KINDS:
            folders.extend(
                f for f in sorted(self.defs_dir(kind).iterdir())
                if f.is_dir() and (f / MANIFEST_FILENAME).is_file()
            )
        return folders

    def find_folder(self, model_id: str) -> Path | None:
        for kind in KINDS:
            folder = self.defs_dir(kind) / model_id
            if folder.is_dir():
                return folder
        return None

    def resolve_targets(self, ids: list[str], select_all: bool) -> list[Path]:
        if select_all:
            folders = self.managed_folders()
            if not folders:
                console.print("[yellow]No managed definitions found (no folder has a definition.yaml yet).[/yellow]")
                console.print("Adopt an existing folder with [bold]mdb import <model_id>[/bold] or create one with [bold]mdb add[/bold].")
                raise typer.Exit(0)
            return folders
        if not ids:
            console.print("[red]Name at least one model_id or pass --all.[/red]")
            raise typer.Exit(2)
        folders = []
        for model_id in ids:
            folder = self.find_folder(model_id)
            if folder is None:
                console.print(f"[red]{model_id}: no such folder in LLM-Definitions or Embedding-Definitions[/red]")
                raise typer.Exit(2)
            folders.append(folder)
        return folders


def _env_api_key(adapter: ProviderAdapter) -> Optional[str]:
    if not adapter.env_key_var:
        return None
    return os.environ.get(adapter.env_key_var)


def _print_issues(issues) -> bool:
    """Prints issues, returns True if any error is present."""
    has_error = False
    for issue in issues:
        color = {"error": "red", "warning": "yellow", "info": "dim"}[issue.severity]
        console.print(f"[{color}]{issue.format()}[/{color}]")
        has_error = has_error or issue.severity == "error"
    return has_error


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

def _pick_from_list(title: str, entries: list[str]) -> int:
    console.print(f"\n[bold]{title}[/bold]")
    for index, entry in enumerate(entries, start=1):
        console.print(f"  {index}. {entry}")
    while True:
        raw = Prompt.ask("Number", default="1")
        try:
            choice = int(raw)
            if 1 <= choice <= len(entries):
                return choice - 1
        except ValueError:
            pass
        console.print(f"[yellow]Enter a number between 1 and {len(entries)}.[/yellow]")


def _enrich_from_static(live: list[CatalogModel], static: list[CatalogModel]) -> list[CatalogModel]:
    """Live listings confirm availability; static snapshots fill metadata gaps
    (OpenAI's /v1/models returns ids only - no context length or pricing)."""
    static_by_ref = {m.ref: m for m in static}
    for model in live:
        known = static_by_ref.get(model.ref)
        if known is None:
            continue
        if model.display_name == model.ref:
            model.display_name = known.display_name
        for attr in ("context_length", "max_output_tokens", "input_price_per_m",
                     "output_price_per_m", "knowledge_cutoff", "release_date"):
            if getattr(model, attr) is None:
                setattr(model, attr, getattr(known, attr))
        model.reasoning = model.reasoning or known.reasoning
        model.extended_thinking = model.extended_thinking or known.extended_thinking
        if known.source != "static":
            model.source = f"live, enriched from {known.source}"
    return live


def _catalog_for(adapter: ProviderAdapter, ctx: Context, offline: bool, verify_ssl: bool) -> list[CatalogModel]:
    static = adapter.static_catalog(ctx.core.core_dir)
    if not offline and not env_flag("MDB_OFFLINE"):
        try:
            session = make_session(verify_ssl)
            models = adapter.live_catalog(session, _env_api_key(adapter))
            if models:
                console.print(f"[dim]Live catalog: {len(models)} models from {adapter.display_name}.[/dim]")
                return _enrich_from_static(models, static)
        except NotImplementedError as exc:
            console.print(f"[dim]{exc}[/dim]")
        except Exception as exc:
            console.print(f"[yellow]Live catalog unavailable ({exc}) - falling back to the bundled snapshot.[/yellow]")
    if static:
        console.print(f"[dim]Using bundled static catalog ({static[0].source}) - confirm pricing before relying on cost monitoring.[/dim]")
    return static


def _select_model(adapter: ProviderAdapter, catalog: list[CatalogModel], ref: Optional[str],
                  yes: bool) -> Optional[CatalogModel]:
    if ref:
        for model in catalog:
            if model.ref == ref:
                return model
        return CatalogModel(ref=ref, display_name=ref, source="manual entry")
    if not catalog:
        return None
    if yes:
        console.print("[red]--yes needs an explicit model reference (mdb add <provider> <model-ref> --yes).[/red]")
        raise typer.Exit(2)
    search = Prompt.ask("Filter the model list (empty for all)", default="")
    filtered = [m for m in catalog if search.lower() in m.ref.lower() or search.lower() in m.display_name.lower()]
    if not filtered:
        console.print("[yellow]No match - showing everything.[/yellow]")
        filtered = catalog
    filtered = filtered[:30]
    labels = []
    for m in filtered:
        price = (f"${m.input_price_per_m:g}/${m.output_price_per_m:g} per 1M"
                 if m.input_price_per_m is not None else "price unknown")
        ctx_len = f"{m.context_length:,} ctx" if m.context_length else "ctx unknown"
        flags = " [reasoning]" if m.reasoning else ""
        if m.kind == "embedding":
            flags += " [embedding]"
        labels.append(f"{m.display_name}  ({m.ref}, {ctx_len}, {price}){flags}")
    return filtered[_pick_from_list(f"Models available from {adapter.display_name}", labels)]


@app.command()
def add(
    provider: Optional[str] = typer.Argument(None, help="Provider adapter id (see 'mdb providers')"),
    ref: Optional[str] = typer.Argument(None, help="Provider model reference / deployment name / HF repo"),
    model_id: Optional[str] = typer.Option(None, "--id", help="Definition folder name (snake_case)"),
    yes: bool = typer.Option(False, "--yes", help="Non-interactive: accept all defaults"),
    offline: bool = typer.Option(False, "--offline", help="No network calls - use bundled catalogs / manual entry"),
    verify_ssl: bool = typer.Option(True, "--verify-ssl/--no-verify-ssl", help="TLS verification for provider calls"),
    resource: Optional[str] = typer.Option(None, help="Azure resource host (azure-foundry)"),
    deployment: Optional[str] = typer.Option(None, help="Azure deployment name (azure-foundry)"),
    commit_resource: bool = typer.Option(
        False, "--commit-resource",
        help="Bake the Azure resource host into the definition as its default. Without this, the "
             "definition stays environment-neutral: deployed containers read AZURE_OPENAI_RESOURCE "
             "or a per-call option instead.",
    ),
    repo: Optional[str] = typer.Option(None, help="Hugging Face repo id (hf-selfhosted)"),
    gated: Optional[bool] = typer.Option(None, help="HF repo is gated (hf-selfhosted)"),
    runtime: Optional[str] = typer.Option(None, help="Runtime family: transformers | onnx | sentence-transformers (hf-selfhosted)"),
    params_billions: Optional[float] = typer.Option(None, help="Parameter count in billions (hf-selfhosted)"),
    base_url: Optional[str] = typer.Option(None, help="Server base URL (ollama/vllm self-hosted)"),
    kind: Optional[str] = typer.Option(None, help="Model kind: llm or embedding (ollama/vllm self-hosted)"),
    description: Optional[str] = typer.Option(None, help="Model description for Model Manager and the fact sheet"),
):
    """Add a new model definition: pick a provider and model, answer a few questions,
    and every framework asset is generated."""
    ctx = Context()
    adapters = load_adapters()

    if provider is None:
        ids = list(adapters)
        labels = [f"{adapters[i].display_name}  ({i})" for i in ids]
        provider = ids[_pick_from_list("Providers", labels)]
    if provider not in adapters:
        console.print(f"[red]Unknown provider '{provider}'. Known: {', '.join(adapters)}[/red]")
        raise typer.Exit(2)
    adapter = adapters[provider]

    if adapter.env_key_var:
        if _env_api_key(adapter):
            console.print(f"[green]Using {adapter.env_key_var} from the environment/.env.[/green]")
        else:
            console.print(
                f"[yellow]No {adapter.env_key_var} found.[/yellow] Get one at {adapter.docs_url} "
                "and add it to your .env - catalog browsing may be limited and smoke tests will be skipped."
            )

    flag_answers = {
        "resource": resource, "deployment": deployment, "repo": repo,
        "gated": {True: "y", False: "n"}.get(gated), "runtime": runtime,
        "params_billions": str(params_billions) if params_billions is not None else None,
        "base_url": base_url, "kind": kind,
        "description": description,
    }
    answers: dict[str, str] = {}
    for question in adapter.questions():
        supplied = flag_answers.get(question.param)
        if supplied is not None:
            answers[question.param] = supplied
        elif yes:
            if question.required and not question.default:
                console.print(f"[red]--yes needs --{question.param.replace('_', '-')} for {adapter.display_name}.[/red]")
                raise typer.Exit(2)
            answers[question.param] = question.default
        else:
            answers[question.param] = Prompt.ask(question.prompt, default=question.default or None)
    if description:
        answers["description"] = description
    if answers.get("resource"):
        if commit_resource or yes:
            answers["commit_resource"] = commit_resource
        else:
            answers["commit_resource"] = Confirm.ask(
                f"Bake '{answers['resource']}' into the definition as its default resource? "
                "(No keeps it environment-driven via AZURE_OPENAI_RESOURCE)",
                default=False,
            )

    # Adapters with questions may still have a catalog (Bedrock); Azure and HF
    # simply yield an empty list and fall through to manual entry.
    catalog = _catalog_for(adapter, ctx, offline, verify_ssl)
    manual_ref = answers.get("deployment") or answers.get("repo")
    if manual_ref and not ref:
        ref = manual_ref
    cm = _select_model(adapter, catalog, ref, yes)
    if cm is None:
        if not ref and yes:
            console.print("[red]No model reference given.[/red]")
            raise typer.Exit(2)
        the_ref = ref or Prompt.ask("Provider model reference (exact model string)")
        display = the_ref if yes else Prompt.ask("Display name", default=the_ref)
        cm = CatalogModel(ref=the_ref, display_name=display, source="manual entry")

    proposed = model_id or slugify(cm.display_name)
    final_id = proposed if yes else Prompt.ask("Definition folder / model_id", default=proposed)

    modeler = os.environ.get("SAS_RESPONSIBLE_PARTY", "") or os.environ.get("USERNAME", "") or os.environ.get("USER", "")
    try:
        manifest = adapter.build_manifest(cm, final_id, answers, modeler)
        rendered = render_assets(manifest, ctx.core)
    except (GenerationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    defs_name = "LLM-Definitions" if manifest.kind == "llm" else "Embedding-Definitions"
    folder = ctx.defs_dir(manifest.kind) / final_id
    if folder.exists() and any(folder.iterdir()):
        console.print(f"[red]{folder} already exists and is not empty - pick another id or use mdb import.[/red]")
        raise typer.Exit(2)

    fact_sheet = ctx.fact_sheet(manifest.kind)
    console.print(f"\n[bold]Will create {defs_name}/{final_id}/[/bold] with:")
    console.print(f"  {MANIFEST_FILENAME}  (the only file you edit)")
    for name in sorted(rendered):
        console.print(f"  {name}")
    console.print(f"  + a row in {fact_sheet.name} (pricing source: {manifest.generation.catalog_provenance})")
    if not yes and not Confirm.ask("Write these files?", default=True):
        raise typer.Exit(0)

    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest.save(folder)
    for name, content in rendered.items():
        (folder / name).write_bytes(content)
    drift.write_lock(folder, manifest_path.read_bytes(), rendered)
    facts.upsert_row(fact_sheet, manifest)

    register_script = "register-LLMs.py" if manifest.kind == "llm" else "register-Embedding.py"
    console.print(f"\n[green]Created {final_id} ({len(rendered)} files + fact-sheet row).[/green]")
    console.print("Next steps:")
    console.print(f"  1. mdb validate {final_id} --live     (smoke-test the provider before Viya)")
    console.print(f"  2. cd {defs_name} && python {register_script} -l {final_id}")


# ---------------------------------------------------------------------------
# generate / validate / sync
# ---------------------------------------------------------------------------

@app.command()
def generate(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all", help="Every managed definition"),
    check: bool = typer.Option(False, "--check", help="CI drift gate: report, write nothing, exit 1 on drift"),
    force: bool = typer.Option(False, "--force", help="Overwrite hand-edited generated files"),
):
    """(Re)render all generated assets from definition.yaml."""
    ctx = Context()
    failed = False
    for folder in ctx.resolve_targets(ids or [], all_):
        model_id = folder.name
        try:
            manifest = load_manifest(folder)
            rendered = render_assets(manifest, ctx.core)
        except Exception as exc:
            console.print(f"[red]{model_id}: {exc}[/red]")
            failed = True
            continue
        classifications = drift.classify(folder, rendered)
        pending = [c for c in classifications if c.status != drift.FileStatus.UNCHANGED]
        if check:
            if pending:
                failed = True
                for c in pending:
                    console.print(f"[red]{model_id}/{c.filename}: {c.status.value}[/red]")
            else:
                console.print(f"[green]{model_id}: clean[/green]")
            continue
        blockers = [c for c in pending
                    if c.status in (drift.FileStatus.HAND_EDITED, drift.FileStatus.UNTRACKED)]
        if blockers and not force:
            failed = True
            for c in blockers:
                console.print(
                    f"[red]{model_id}/{c.filename} was edited by hand.[/red] Fold the change into "
                    f"definition.yaml, add it to generation.overrides, or rerun with --force."
                )
            continue
        for c in pending:
            (folder / c.filename).write_bytes(rendered[c.filename])
        drift.write_lock(folder, (folder / MANIFEST_FILENAME).read_bytes(), rendered)
        console.print(f"[green]{model_id}: {len(pending)} file(s) written, {len(classifications) - len(pending)} unchanged.[/green]")
        for option_name in list_custom_options(manifest, ctx.core):
            console.print(
                f"[yellow]{model_id}: option '{option_name}' is not in the standardized vocabulary - "
                "it is sent to the provider as-is and shows up in UIs under its raw name with your "
                "description (no standardized label, no cross-provider translation). "
                "Fine if intended; standardize it in definition-core/static/option-vocabulary.json otherwise.[/yellow]"
            )
    if not check and not failed:
        console.print("Next: mdb sync --all   (keep the fact sheet in step)")
    raise typer.Exit(1 if failed else 0)


@app.command()
def validate(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all", help="Every folder, incl. unmanaged (reported, never failed)"),
    live: bool = typer.Option(False, "--live", help="One real provider call per model (needs API keys)"),
    verify_ssl: bool = typer.Option(True, "--verify-ssl/--no-verify-ssl"),
):
    """Static coherence rules (and optionally a live provider smoke test)."""
    ctx = Context()
    if all_ and not ids:
        issues = []
        for kind in KINDS:
            issues.extend(validate_all(ctx.defs_dir(kind), ctx.core, ctx.fact_sheet(kind)))
    else:
        issues = []
        for folder in ctx.resolve_targets(ids or [], all_):
            issues.extend(validate_folder(folder, ctx.core, ctx.fact_sheet(ctx.kind_of(folder))))
    has_error = _print_issues(issues)
    if not issues:
        console.print("[green]Everything checks out.[/green]")

    if live:
        adapters = load_adapters()
        session = make_session(verify_ssl)
        targets = ctx.resolve_targets(ids or [], all_) if ids or all_ else []
        for folder in targets:
            if not (folder / MANIFEST_FILENAME).is_file():
                continue
            manifest = load_manifest(folder)
            adapter = adapters.get(manifest.provider.adapter)
            if adapter is None:
                console.print(f"[yellow]{folder.name}: unknown adapter '{manifest.provider.adapter}' - skipped.[/yellow]")
                continue
            result = adapter.smoke_test(manifest, _env_api_key(adapter), session)
            if result.skipped:
                console.print(f"[dim]{folder.name}: {result.detail}[/dim]")
            elif result.ok:
                console.print(f"[green]{folder.name}: {result.detail}[/green]")
            else:
                has_error = True
                console.print(f"[red]{folder.name}: smoke test failed - {result.detail}[/red]")
    raise typer.Exit(1 if has_error else 0)


@app.command()
def sync(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all"),
):
    """Upsert the fact-sheet rows of managed definitions (legacy rows stay untouched)."""
    ctx = Context()
    for folder in ctx.resolve_targets(ids or [], all_):
        if not (folder / MANIFEST_FILENAME).is_file():
            continue
        manifest = load_manifest(folder)
        result = facts.upsert_row(ctx.fact_sheet(manifest.kind), manifest)
        console.print(f"{folder.name}: fact-sheet row {result}")


# ---------------------------------------------------------------------------
# import / test / providers / schema
# ---------------------------------------------------------------------------

@app.command("import")
def import_(
    model_id: str = typer.Argument(..., help="Existing hand-written definition folder to adopt"),
    apply: bool = typer.Option(False, "--apply", help="Also regenerate the files and sync the fact sheet"),
):
    """Reverse-engineer definition.yaml from an existing folder (best effort, reviewed by you)."""
    ctx = Context()
    folder = ctx.find_folder(model_id)
    if folder is None:
        console.print(f"[red]{model_id}: no such folder in LLM-Definitions or Embedding-Definitions[/red]")
        raise typer.Exit(2)
    if (folder / MANIFEST_FILENAME).is_file():
        console.print(f"[yellow]{model_id} already has a definition.yaml - nothing imported.[/yellow]")
        raise typer.Exit(0)
    fact_sheet = ctx.fact_sheet(ctx.kind_of(folder))
    result = import_folder(folder, fact_sheet)
    # Render BEFORE writing the manifest: a folder must never be left
    # half-adopted when e.g. an option cannot be resolved
    try:
        rendered = render_assets(result.manifest, ctx.core)
    except GenerationError as exc:
        console.print(f"[red]{model_id}: {exc}[/red]")
        console.print("Nothing was written - the folder stays fully hand-maintained. "
                      "Declare the option inline (type/description) in a definition.yaml "
                      "or extend the vocabulary, then rerun mdb import.")
        raise typer.Exit(1)
    manifest_path = result.manifest.save(folder)
    console.print(f"[green]Wrote {manifest_path}.[/green]")
    for note in result.notes:
        console.print(f"  [yellow]note:[/yellow] {note}")
    changed = [c for c in drift.classify(folder, rendered) if c.status != drift.FileStatus.UNCHANGED]
    if changed:
        console.print("\nRegenerating would change these files (intended normalizations included):")
        for c in changed:
            console.print(f"  {c.filename} ({c.status.value})")
    if apply:
        for c in changed:
            (folder / c.filename).write_bytes(rendered[c.filename])
        drift.write_lock(folder, manifest_path.read_bytes(), rendered)
        facts.upsert_row(fact_sheet, result.manifest)
        console.print(f"[green]Converged {model_id} ({len(changed)} file(s) rewritten).[/green]")
        console.print(f"Re-test before re-publishing: mdb validate {model_id} --live")
    else:
        console.print(f"\nReview definition.yaml, then converge with: mdb import {model_id} --apply")
        console.print("(or delete definition.yaml to leave the folder fully hand-maintained)")


@app.command()
def test(
    model_id: str = typer.Argument(...),
    prompt: str = typer.Option("Reply with the single word OK.", "--prompt"),
    system: str = typer.Option("You are a helpful assistant.", "--system"),
):
    """Invoke the generated scoreModel() locally (makes a real provider call for API models)."""
    ctx = Context()
    folder = ctx.find_folder(model_id)
    if folder is None:
        console.print(f"[red]{model_id}: no such folder in LLM-Definitions or Embedding-Definitions[/red]")
        raise typer.Exit(2)
    manifest = load_manifest(folder)
    adapters = load_adapters()
    adapter = adapters.get(manifest.provider.adapter)
    options: dict = {}
    if manifest.provider.auth.mode == "api_key":
        key = _env_api_key(adapter) if adapter else None
        if not key:
            console.print(f"[red]No API key found ({adapter.env_key_var if adapter else 'unknown env var'}) - "
                          "set it in the environment or .env.[/red]")
            raise typer.Exit(2)
        options["API_KEY"] = key
    score_path = folder / effective_score_file(manifest)
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"mdb_score_{model_id}", score_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    console.print(f"[dim]Calling scoreModel() from {score_path.name}...[/dim]")
    table = Table(show_header=False)
    if manifest.kind == "embedding":
        embedding, run_time, tokens = module.scoreModel([prompt], ["mdb-test"], [json.dumps(options)])
        vector = json.loads(embedding)
        table.add_row("embedding", f"{len(vector)}-dimension vector [{vector[0]:.5f}, {vector[1]:.5f}, ...]")
        table.add_row("run_time", f"{run_time:.2f}s")
        table.add_row("tokens", str(tokens))
    else:
        response, run_time, prompt_length, output_length = module.scoreModel(
            [prompt], [system], [json.dumps(options)]
        )
        table.add_row("response", str(response))
        table.add_row("run_time", f"{run_time:.2f}s")
        table.add_row("prompt_length", str(prompt_length))
        table.add_row("output_length", str(output_length))
    console.print(table)


# ---------------------------------------------------------------------------
# register / publish / ship / endpoints (Viya lifecycle)
# ---------------------------------------------------------------------------

def _scr_endpoint() -> str:
    endpoint = os.environ.get("SAS_SCR_ENDPOINT")
    if not endpoint:
        console.print("[yellow]SAS_SCR_ENDPOINT is not set - the registered endPoint attribute will use "
                      "a placeholder. Set it in .env for correct endpoint metadata.[/yellow]")
        endpoint = "https://viya-host/llm"
    return endpoint.rstrip("/")


def _viya_session():
    from .viya.session import ViyaConfigError, create_session
    try:
        return create_session()
    except ViyaConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)


@app.command()
def setup():
    """Create the SAS Model Manager repository and the LLM/Embedding Model Projects if missing.

    Idempotent: existing objects are left untouched. `mdb register` runs this
    automatically for the kind it registers, so a separate call is optional -
    use it to bootstrap a fresh environment up front."""
    from .viya.registry import ensure_repository_and_project
    ctx = Context()
    responsible_party = os.environ.get("SAS_RESPONSIBLE_PARTY", "")
    created_any = False
    with _viya_session() as session:
        for kind in KINDS:
            for created in ensure_repository_and_project(session, kind, ctx.core, responsible_party):
                console.print(f"[green]created {created}.[/green]")
                created_any = True
    if not created_any:
        console.print("[green]Repository and projects already exist - nothing to do.[/green]")


@app.command()
def register(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all"),
    update: bool = typer.Option(False, "--update", help="Update an already-registered model in place "
                                                        "(new minor version + content replacement)"),
):
    """Register managed definitions in SAS Model Manager (both kinds, one path)."""
    from .viya.registry import ensure_repository_and_project, register_model
    ctx = Context()
    targets = [f for f in ctx.resolve_targets(ids or [], all_) if (f / MANIFEST_FILENAME).is_file()]
    scr = _scr_endpoint()
    responsible_party = os.environ.get("SAS_RESPONSIBLE_PARTY", "")
    ensured_kinds: set[str] = set()
    failed = False
    with _viya_session() as session:
        for folder in targets:
            manifest = load_manifest(folder)
            if manifest.kind not in ensured_kinds:
                for created in ensure_repository_and_project(session, manifest.kind, ctx.core, responsible_party):
                    console.print(f"[green]setup: created {created}.[/green]")
                ensured_kinds.add(manifest.kind)
            row = facts.read_row(ctx.fact_sheet(manifest.kind), manifest.model_id)
            if row is None:
                console.print(f"[yellow]{manifest.model_id}: no fact-sheet row - run mdb sync first "
                              "(cost monitoring metadata will be incomplete).[/yellow]")
            try:
                result = register_model(session, manifest, folder, row, scr, update=update)
            except Exception as exc:
                console.print(f"[red]{manifest.model_id}: {exc}[/red]")
                failed = True
                continue
            color = {"created": "green", "updated": "green", "skipped": "yellow"}[result.action]
            hint = "" if result.action != "skipped" else "  (already registered - use --update to replace)"
            console.print(f"[{color}]{result.model_id}: {result.action}{hint}[/{color}] "
                          f"{os.environ.get('SAS_VIYA_URL', '')}{result.url}")
    if not failed:
        console.print("Next: mdb publish <id> --wait")
    raise typer.Exit(1 if failed else 0)


@app.command()
def unregister(
    ids: list[str] = typer.Argument(..., help="Registered model_id(s) to delete from SAS Model Manager"),
    yes: bool = typer.Option(False, "--yes", help="Delete without the confirmation prompt"),
):
    """Delete registered model(s) from SAS Model Manager. The local definition
    folder is left untouched - re-register any time with mdb register."""
    from .viya.registry import unregister_model
    failed = False
    with _viya_session() as session:
        for model_id in ids:
            if not yes and not Confirm.ask(
                f"Delete registered model '{model_id}' from SAS Model Manager?", default=False
            ):
                console.print(f"[yellow]{model_id}: skipped.[/yellow]")
                continue
            try:
                result = unregister_model(session, model_id)
            except Exception as exc:
                console.print(f"[red]{model_id}: {exc}[/red]")
                failed = True
                continue
            if result == "deleted":
                console.print(f"[green]{model_id}: deleted from SAS Model Manager.[/green]")
            else:
                console.print(f"[yellow]{model_id}: not registered - nothing to delete.[/yellow]")
    raise typer.Exit(1 if failed else 0)


@app.command()
def publish(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all"),
    destination: Optional[str] = typer.Option(None, "-d", "--destination",
                                              help="SCR publishing destination (env: SAS_PUBLISH_DESTINATION)"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the image build completes"),
):
    """Publish registered models to an SCR container destination."""
    from .viya.registry import publish_model
    ctx = Context()
    dest = destination or os.environ.get("SAS_PUBLISH_DESTINATION")
    if not dest:
        console.print("[red]No destination - pass -d or set SAS_PUBLISH_DESTINATION.[/red]")
        raise typer.Exit(2)
    failed = False
    with _viya_session() as session:
        for folder in ctx.resolve_targets(ids or [], all_):
            model_id = folder.name
            try:
                state = publish_model(session, model_id, dest, wait=wait)
            except Exception as exc:
                console.print(f"[red]{model_id}: {exc}[/red]")
                failed = True
                continue
            if state in ("requested", "completed"):
                console.print(f"[green]{model_id}: publish {state} on {dest}[/green]")
            else:
                console.print(f"[red]{model_id}: publish {state} on {dest}[/red]")
                failed = True
    raise typer.Exit(1 if failed else 0)


@app.command()
def ship(
    model_id: str = typer.Argument(...),
    destination: Optional[str] = typer.Option(None, "-d", "--destination"),
    verify_ssl: bool = typer.Option(True, "--verify-ssl/--no-verify-ssl"),
):
    """validate --live, register --update and publish --wait in one resumable go."""
    ctx = Context()
    folder = ctx.find_folder(model_id)
    if folder is None or not (folder / MANIFEST_FILENAME).is_file():
        console.print(f"[red]{model_id}: no managed definition found.[/red]")
        raise typer.Exit(2)
    manifest = load_manifest(folder)
    issues = validate_folder(folder, ctx.core, ctx.fact_sheet(manifest.kind))
    if _print_issues(issues):
        console.print("[red]Fix the validation errors above, then rerun mdb ship.[/red]")
        raise typer.Exit(1)
    adapters = load_adapters()
    adapter = adapters.get(manifest.provider.adapter)
    if adapter is not None:
        result = adapter.smoke_test(manifest, _env_api_key(adapter), make_session(verify_ssl))
        if result.skipped:
            console.print(f"[dim]smoke test: {result.detail}[/dim]")
        elif result.ok:
            console.print(f"[green]smoke test: {result.detail}[/green]")
        else:
            console.print(f"[red]smoke test failed: {result.detail}[/red]")
            raise typer.Exit(1)
    for stage in (
        lambda: register(ids=[model_id], all_=False, update=True),
        lambda: publish(ids=[model_id], all_=False, destination=destination, wait=True),
    ):
        try:
            stage()
        except typer.Exit as exit_info:
            if exit_info.exit_code:
                raise
    console.print(f"[green]{model_id}: shipped.[/green]")


@app.command()
def endpoints(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """The SCR endpoint manifest for every managed definition."""
    ctx = Context()
    scr = _scr_endpoint()
    entries = []
    for folder in ctx.managed_folders():
        manifest = load_manifest(folder)
        entries.append({
            "model_id": manifest.model_id,
            "kind": manifest.kind,
            "endpoint": f"{scr}/{manifest.model_id}/{manifest.model_id}",
        })
    if as_json:
        console.print_json(json.dumps(entries))
        return
    table = Table()
    table.add_column("model_id")
    table.add_column("kind")
    table.add_column("SCR endpoint")
    for entry in entries:
        table.add_row(entry["model_id"], entry["kind"], entry["endpoint"])
    console.print(table)


@app.command()
def deploy(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all"),
    registry: Optional[str] = typer.Option(None, "--registry", help="Container registry (env: SAS_CONTAINER_REGISTRY)"),
    host: Optional[str] = typer.Option(None, "--host", help="Ingress host (default: host of SAS_SCR_ENDPOINT)"),
    pv: bool = typer.Option(False, "--pv", help="Force the persistent-volume template variant"),
    out: str = typer.Option("deploy-yaml", "--out", help="Output directory for the rendered YAML"),
):
    """Render ready-to-apply SCR deployment YAML (fills all template placeholders)."""
    import re
    from urllib.parse import urlparse
    ctx = Context()
    registry = registry or os.environ.get("SAS_CONTAINER_REGISTRY")
    if not registry:
        console.print("[red]No registry - pass --registry or set SAS_CONTAINER_REGISTRY "
                      "(e.g. myregistry.azurecr.io).[/red]")
        raise typer.Exit(2)
    if not host:
        scr = os.environ.get("SAS_SCR_ENDPOINT", "")
        host = urlparse(scr).hostname or ""
    if not host:
        console.print("[red]No ingress host - pass --host or set SAS_SCR_ENDPOINT.[/red]")
        raise typer.Exit(2)
    template_dir = ctx.repo / "SCR-LLM-Deployment-YAML"
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for folder in ctx.resolve_targets(ids or [], all_):
        if not (folder / MANIFEST_FILENAME).is_file():
            continue
        manifest = load_manifest(folder)
        use_pv = pv or manifest.runtime.requirements_profile.startswith("hf-")
        template = template_dir / ("deploy-modelName-PV-template.yaml" if use_pv
                                   else "deploy-modelName-template.yaml")
        lines = []
        for line in template.read_text(encoding="utf-8").splitlines():
            line = line.replace("containerRegistry", registry)
            line = line.replace("llm_name", manifest.model_id)
            line = line.replace("llmname", manifest.model_id.replace("_", "-"))
            line = line.replace("model_name", manifest.model_id)
            line = re.sub(r"host$", host, line)  # 'host' value slots only, never the 'hosts:' key
            lines.append(line)
        target = out_dir / f"deploy-{manifest.model_id}.yaml"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
        console.print(f"[green]{manifest.model_id}: {target}[/green]"
                      + (" (PV variant)" if use_pv else ""))
    console.print(f"Apply with: kubectl apply -f {out_dir}/deploy-<model_id>.yaml -n <namespace>")


@app.command()
def radar(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all"),
    probe: bool = typer.Option(False, "--probe", help="One real 1-token call per model (definitive, uses credits)"),
    as_json: bool = typer.Option(False, "--json"),
    verify_ssl: bool = typer.Option(True, "--verify-ssl/--no-verify-ssl"),
):
    """Deprecation radar: check every managed model against its provider's live surface."""
    from .core.radar import check_model, env_key_for
    ctx = Context()
    adapters = load_adapters()
    session = make_session(verify_ssl)
    results = []
    for folder in ctx.resolve_targets(ids or [], all_):
        if not (folder / MANIFEST_FILENAME).is_file():
            continue
        manifest = load_manifest(folder)
        if effective_score_file(manifest) in manifest.generation.overrides:
            from .core.radar import RadarResult
            results.append(RadarResult(manifest.model_id, manifest.provider.adapter,
                                       manifest.provider.model_version, "skipped",
                                       "hand-maintained scorer - no provider surface to check"))
            continue
        adapter = adapters.get(manifest.provider.adapter)
        results.append(check_model(manifest, adapter, session, env_key_for(adapter) if adapter else None, probe))
    if as_json:
        console.print_json(json.dumps([r.__dict__ for r in results]))
    else:
        table = Table()
        for col in ("model_id", "provider", "status", "detail"):
            table.add_column(col)
        for r in results:
            color = {"ok": "green", "missing": "red", "not-serving": "red", "skipped": "dim"}[r.status]
            table.add_row(r.model_id, r.provider, f"[{color}]{r.status}[/{color}]", r.detail)
        console.print(table)
    dead = [r for r in results if r.status in ("missing", "not-serving")]
    if dead:
        console.print(f"[red]{len(dead)} model(s) look retired at the provider.[/red] "
                      "Mark them with: mdb retire <model_id>")
    raise typer.Exit(1 if dead else 0)


@app.command()
def retire(
    model_id: str = typer.Argument(...),
):
    """Tag a definition as deprecated (hides it from the Prompt Builder) and regenerate."""
    import yaml as _yaml
    ctx = Context()
    folder = ctx.find_folder(model_id)
    if folder is None or not (folder / MANIFEST_FILENAME).is_file():
        console.print(f"[red]{model_id}: no managed definition found.[/red]")
        raise typer.Exit(2)
    manifest = load_manifest(folder)
    if "deprecated" in manifest.tags.extra:
        console.print(f"{model_id}: already deprecated.")
        raise typer.Exit(0)
    manifest.tags.extra.append("deprecated")
    manifest.save(folder)
    rendered = render_assets(manifest, ctx.core)
    blockers = [c for c in drift.classify(folder, rendered)
                if c.status in (drift.FileStatus.HAND_EDITED, drift.FileStatus.UNTRACKED)]
    if blockers:
        for c in blockers:
            console.print(f"[red]{model_id}/{c.filename} was edited by hand - retire refuses to overwrite it.[/red]")
        console.print("Fold the edits into definition.yaml (or generation.overrides), run mdb generate --force, "
                      "then rerun mdb retire.")
        raise typer.Exit(1)
    for name, content in rendered.items():
        (folder / name).write_bytes(content)
    drift.write_lock(folder, (folder / MANIFEST_FILENAME).read_bytes(), rendered)
    facts.upsert_row(ctx.fact_sheet(manifest.kind), manifest)
    console.print(f"[green]{model_id}: tagged deprecated and regenerated.[/green]")
    console.print(f"Push it to the registered model with: mdb register {model_id} --update")


@app.command()
def providers():
    """List the available provider adapters."""
    table = Table()
    table.add_column("id")
    table.add_column("name")
    table.add_column("API key env var")
    table.add_column("score template")
    for adapter in load_adapters().values():
        table.add_row(adapter.id, adapter.display_name, adapter.env_key_var or "-", adapter.template)
    console.print(table)
    console.print("Third-party adapters: pip packages exposing the 'mdb.providers' entry-point group.")


provider_app = typer.Typer(help="Third-party provider adapter tooling.")
app.add_typer(provider_app, name="provider")

SCAFFOLD_ADAPTER = '''"""Adapter for {name} - fill in the TODOs, then pip install this package."""
from mdb.providers.openai_compat import OpenAICompatAdapter


class {cls}(OpenAICompatAdapter):
    def __init__(self):
        super().__init__(
            id="{name}",
            display_name="{title}",  # TODO
            provider_tag="{title}",  # TODO: the tag shown in SAS Model Manager
            key_name="{title}",      # TODO: must match your LLM_API_KEYS KeyName
            env_key_var="{env}_API_KEY",
            base_url="https://api.example.com/v1",  # TODO: OpenAI-compatible base URL
            docs_url="https://example.com/keys",    # TODO
        )
'''

SCAFFOLD_PYPROJECT = '''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "mdb-provider-{name}"
version = "0.1.0"
dependencies = ["sas-mdb"]

[project.entry-points."mdb.providers"]
{name} = "mdb_provider_{snake}.adapter:{cls}"

[tool.setuptools.packages.find]
where = ["src"]
'''


@provider_app.command("scaffold")
def provider_scaffold(name: str = typer.Argument(..., help="Adapter id (kebab-case)")):
    """Generate a third-party adapter package skeleton (entry-point wired)."""
    snake = name.replace("-", "_")
    cls = "".join(part.capitalize() for part in snake.split("_")) + "Adapter"
    root = Path(f"mdb-provider-{name}")
    (root / "src" / f"mdb_provider_{snake}").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        SCAFFOLD_PYPROJECT.format(name=name, snake=snake, cls=cls), encoding="utf-8")
    pkg = root / "src" / f"mdb_provider_{snake}"
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "adapter.py").write_text(
        SCAFFOLD_ADAPTER.format(name=name, cls=cls, title=snake.title().replace("_", " "),
                                env=snake.upper()), encoding="utf-8")
    console.print(f"[green]Scaffolded {root}/[/green] - fill in the TODOs, then:")
    console.print(f"  pip install -e {root} && mdb provider check {name}")


@provider_app.command("check")
def provider_check(adapter_id: str = typer.Argument(...)):
    """Conformance-check an adapter: catalog shape, manifest build, asset rendering."""
    from .providers.base import CatalogModel
    ctx = Context()
    adapters = load_adapters()
    adapter = adapters.get(adapter_id)
    if adapter is None:
        console.print(f"[red]Adapter '{adapter_id}' not found (is its package installed?).[/red]")
        raise typer.Exit(2)
    problems = []
    for attr in ("id", "display_name", "provider_tag", "template"):
        if not getattr(adapter, attr, None):
            problems.append(f"missing attribute: {attr}")
    try:
        static = adapter.static_catalog(ctx.core.core_dir)
        console.print(f"static catalog: {len(static)} models")
    except Exception as exc:
        problems.append(f"static_catalog raised: {exc}")
        static = []
    dummy = static[0] if static else CatalogModel(ref="dummy-model", display_name="Dummy Model")
    try:
        manifest = adapter.build_manifest(dummy, "conformance_check_model", {
            "resource": "dummyres", "deployment": "dummy", "repo": "org/dummy",
            "region": "us-east-1",
        }, "tester")
        rendered = render_assets(manifest, ctx.core)
        console.print(f"manifest builds and renders {len(rendered)} assets")
    except Exception as exc:
        problems.append(f"build/render failed: {exc}")
    if problems:
        for problem in problems:
            console.print(f"[red]FAIL: {problem}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{adapter_id}: conformance checks passed.[/green]")


@app.command("schema-export")
def schema_export():
    """Regenerate definition-core/schema/manifest.schema.json from the pydantic models."""
    ctx = Context()
    target = ctx.core.core_dir / "schema" / "manifest.schema.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((json.dumps(export_json_schema(), indent=4, ensure_ascii=False) + "\n").encode("utf-8"))
    console.print(f"[green]Wrote {target}[/green]")


def main() -> None:
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    app()


if __name__ == "__main__":
    main()
