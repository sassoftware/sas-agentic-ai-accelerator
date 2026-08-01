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
from .core.paths import (
    RepoNotFoundError, archive_dir, core_dir, definitions_dir, fact_sheet_path, find_repo_root,
)
from .core.validator import validate_all, validate_folder
from .providers import load_adapters
from .providers.base import CatalogModel, ProviderAdapter, slugify

app = typer.Typer(
    name="mdb",
    help="Model Definition Builder for the SAS Agentic AI Accelerator.",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
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

    def managed_manifests(self, kind: str) -> list[ModelManifest]:
        return [
            load_manifest(f)
            for f in sorted(self.defs_dir(kind).iterdir())
            if f.is_dir() and (f / MANIFEST_FILENAME).is_file()
        ]

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
                     "output_price_per_m", "knowledge_cutoff", "release_date",
                     "embedding_length"):
            if getattr(model, attr) is None:
                setattr(model, attr, getattr(known, attr))
        # kind never comes from live listings (e.g. OpenAI's /v1/models returns
        # ids only) - without this, a live add of a known embedding model
        # silently built an LLM definition while the offline add got it right.
        if model.kind == "llm" and known.kind == "embedding":
            model.kind = known.kind
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


def _cast_like(current, raw: str):
    """Cast a prompt answer to the type of the value it replaces. Numeric
    options stay int only for integral answers - a catalog default of 1 must
    not truncate an entered 0.5."""
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "y", "yes", "on")
    if isinstance(current, (int, float)):
        value = float(raw)
        return int(value) if isinstance(current, int) and value.is_integer() else value
    return raw


def _review_catalog_values(manifest, skip_review: bool, core=None) -> None:
    """Show the catalog-derived options, metadata and pricing and let the user
    confirm or adjust them before anything is written - these values steer
    scoring behavior and cost monitoring, so they should be accepted
    consciously, not silently. Skipped with --accept-defaults / --yes; unknown
    token pricing is still asked about (or, when skipped, warned about)."""
    metadata = manifest.metadata
    pricing = metadata.pricing
    pricing_unknown = (manifest.kind == "llm" and pricing.cost_type == "Tokens"
                       and pricing.input_token_price is None and pricing.output_token_price is None)
    if skip_review:
        if pricing_unknown:
            console.print(
                "[yellow]No token pricing available for this model - set metadata.pricing in "
                "definition.yaml (0 for a free model); mdb validate reminds you (V008).[/yellow]"
            )
        return

    unknown = "[dim]unknown[/dim]"
    table = Table(title=f"Catalog-derived values for {manifest.model_id}")
    table.add_column("Section")
    table.add_column("Name")
    table.add_column("Value")
    table.add_row("definition", "kind", manifest.kind.upper())
    for name, spec in manifest.options.items():
        bounds = "" if spec.max is None else f"  (max {spec.max:g})"
        table.add_row("options", name, f"{spec.default}{bounds}")
    table.add_row("metadata", "description", metadata.description)
    table.add_row("metadata", "context_length", str(metadata.context_length) if metadata.context_length else unknown)
    table.add_row("metadata", "release_date", metadata.release_date or unknown)
    table.add_row("metadata", "knowledge_cutoff", metadata.knowledge_cutoff or unknown)
    if manifest.kind == "llm" and pricing.cost_type == "Tokens":
        table.add_row("pricing", "input_token_price",
                      unknown if pricing.input_token_price is None else f"{pricing.input_token_price:g}")
        table.add_row("pricing", "output_token_price",
                      unknown if pricing.output_token_price is None else f"{pricing.output_token_price:g}")
    console.print(table)

    adjust = not Confirm.ask("Accept these values?", default=True)
    if adjust:
        console.print("[dim]Press Enter to keep a value.[/dim]")
        for name, spec in manifest.options.items():
            raw = Prompt.ask(f"options.{name}.default", default=str(spec.default))
            try:
                spec.default = _cast_like(spec.default, raw)
            except (TypeError, ValueError):
                console.print(f"[yellow]'{raw}' does not fit {name} - keeping {spec.default}.[/yellow]")
        # Option NAMES are part of the provider contract (e.g. newer OpenAI
        # models take max_completion_tokens instead of max_tokens) - allow
        # fixing them here instead of editing definition.yaml afterwards.
        while True:
            action = Prompt.ask(
                "Rename or drop an option (old=new renames, -name drops, Enter continues)",
                default="",
            ).strip()
            if not action:
                break
            if action.startswith("-"):
                target = action[1:].strip()
                if manifest.options.pop(target, None) is None:
                    console.print(f"[yellow]No option named '{target}'.[/yellow]")
                else:
                    console.print(f"[dim]Dropped {target}.[/dim]")
                continue
            if "=" not in action:
                console.print("[yellow]Use old=new to rename, -name to drop, or Enter to continue.[/yellow]")
                continue
            old_name, new_name = (part.strip() for part in action.split("=", 1))
            if old_name not in manifest.options:
                console.print(f"[yellow]No option named '{old_name}'.[/yellow]")
                continue
            if not new_name or new_name in manifest.options:
                console.print(f"[yellow]'{new_name}' is empty or already exists.[/yellow]")
                continue
            manifest.options = {
                (new_name if key == old_name else key): value
                for key, value in manifest.options.items()
            }
            spec = manifest.options[new_name]
            if core is not None and new_name not in core.vocabulary and spec.type is None:
                console.print(
                    f"[yellow]'{new_name}' is not in the option vocabulary - generation will fail "
                    "unless you pick a vocabulary name or add an inline type in definition.yaml.[/yellow]"
                )
            else:
                console.print(f"[dim]Renamed {old_name} -> {new_name}.[/dim]")
        metadata.description = Prompt.ask("metadata.description", default=metadata.description)
        for attribute in ("release_date", "knowledge_cutoff"):
            raw = Prompt.ask(f"metadata.{attribute}", default=getattr(metadata, attribute) or "").strip()
            setattr(metadata, attribute, raw or None)
        raw = Prompt.ask("metadata.context_length",
                         default=str(metadata.context_length) if metadata.context_length else "").strip()
        try:
            metadata.context_length = int(float(raw)) if raw else None
        except ValueError:
            console.print(f"[yellow]'{raw}' is not a number - keeping {metadata.context_length}.[/yellow]")
    if manifest.kind == "llm" and pricing.cost_type == "Tokens" and (adjust or pricing_unknown):
        if pricing_unknown:
            console.print(
                "No token pricing is available for this model. Enter the per-token prices "
                "(e.g. 2.5e-07; 0 for a free model; leave empty to decide later)."
            )
        for attribute, label in (("input_token_price", "pricing.input_token_price"),
                                 ("output_token_price", "pricing.output_token_price")):
            current = getattr(pricing, attribute)
            raw = Prompt.ask(label, default="" if current is None else f"{current:g}").strip()
            if raw == "":
                continue
            try:
                setattr(pricing, attribute, float(raw))
            except ValueError:
                console.print(f"[yellow]'{raw}' is not a number - keeping {current}.[/yellow]")


@app.command()
def add(
    provider: Optional[str] = typer.Argument(None, help="Provider adapter id (see 'mdb providers')"),
    ref: Optional[str] = typer.Argument(None, help="Provider model reference / deployment name / HF repo"),
    model_id: Optional[str] = typer.Option(None, "--id", help="Definition folder name (snake_case)"),
    yes: bool = typer.Option(False, "--yes", help="Non-interactive: accept all defaults"),
    accept_defaults: bool = typer.Option(
        False, "--accept-defaults",
        help="Skip the review of catalog-derived options, metadata and pricing "
             "(the wizard confirms them by default; --yes implies this)",
    ),
    offline: bool = typer.Option(False, "--offline", help="No network calls - use bundled catalogs / manual entry"),
    verify_ssl: bool = typer.Option(True, "--verify-ssl/--no-verify-ssl", help="TLS verification for provider calls"),
    resource: Optional[str] = typer.Option(None, help="Azure resource host, any flavor (azure-foundry)"),
    deployment: Optional[str] = typer.Option(None, help="Azure deployment name (azure-foundry)"),
    api_version: Optional[str] = typer.Option(
        None, "--api-version",
        help="Azure API version (azure-foundry): omit for the GA v1 endpoint; set e.g. 2024-10-21 "
             "or 2025-01-01-preview to use the legacy /openai/deployments route",
    ),
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
    kind: Optional[str] = typer.Option(None, help="Model kind: llm or embedding (any provider whose adapter supports embedding definitions)"),
    license_: Optional[str] = typer.Option(None, "--license", help="License class for self-hosted models (Open-Source/Proprietary)"),
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
        "resource": resource, "deployment": deployment, "api_version": api_version, "repo": repo,
        "gated": {True: "y", False: "n"}.get(gated), "runtime": runtime,
        "params_billions": str(params_billions) if params_billions is not None else None,
        "base_url": base_url, "kind": kind, "license": license_,
        "description": description,
    }
    answers: dict[str, str] = {}
    question_params = {question.param for question in adapter.questions()}
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
    # The definition KIND (llm vs embedding) decides the score template, the
    # destination folder (LLM-Definitions vs Embedding-Definitions) and the
    # Model Manager project - so --kind is honored for EVERY adapter that can
    # build embedding definitions, not only the ones that ask a kind question.
    supports_embedding = getattr(adapter, "embedding_template", None) is not None
    kind_is_flaggable = supports_embedding and "kind" not in question_params
    if kind is not None and kind not in ("llm", "embedding"):
        console.print("[red]--kind must be 'llm' or 'embedding'.[/red]")
        raise typer.Exit(2)
    # Surface flags that this provider does not consume, so a misdirected flag
    # (e.g. --kind on an LLM-only provider) is not silently ignored.
    for flag_name, flag_value in flag_answers.items():
        if flag_name == "kind" and kind_is_flaggable:
            continue
        if flag_value is not None and flag_name != "description" and flag_name not in question_params:
            console.print(f"[yellow]--{flag_name.replace('_', '-')} is not used by provider "
                          f"'{provider}' - ignored.[/yellow]")
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
    manual_entry = cm is None
    if cm is None:
        if not ref and yes:
            console.print("[red]No model reference given.[/red]")
            raise typer.Exit(2)
        the_ref = ref or Prompt.ask("Provider model reference (exact model string)")
        display = the_ref if yes else Prompt.ask("Display name", default=the_ref)
        cm = CatalogModel(ref=the_ref, display_name=display, source="manual entry")

    # Resolve the kind explicitly instead of silently defaulting to llm:
    # embedding-only adapters (voyage) force it, --kind overrides it, and a
    # manual entry on a both-kinds adapter is asked interactively - the paths
    # that used to misfile embedding models into LLM-Definitions.
    embedding_only = supports_embedding and adapter.template == adapter.embedding_template
    if embedding_only:
        cm.kind = "embedding"
    elif kind_is_flaggable:
        if kind is not None:
            cm.kind = kind
        elif manual_entry and not yes:
            cm.kind = Prompt.ask("Model kind", choices=["llm", "embedding"], default=cm.kind)

    proposed = model_id or slugify(cm.display_name)
    final_id = proposed if yes else Prompt.ask("Definition folder / model_id", default=proposed)

    modeler = os.environ.get("SAS_RESPONSIBLE_PARTY", "") or os.environ.get("USERNAME", "") or os.environ.get("USER", "")
    try:
        manifest = adapter.build_manifest(cm, final_id, answers, modeler)
        # State the kind up front - it decides the score template, the folder
        # and the Model Manager project, and used to be visible only as an
        # easily-missed folder name near the end.
        kind_folder = "LLM-Definitions" if manifest.kind == "llm" else "Embedding-Definitions"
        console.print(f"\n[bold cyan]Adding an {manifest.kind.upper()} model definition -> "
                      f"{kind_folder}/{final_id}/[/bold cyan]")
        # The catalog-derived values steer scoring behavior and cost monitoring
        # - confirm them consciously by default; --accept-defaults / --yes skip
        # the review (unknown token pricing is still asked/warned about).
        _review_catalog_values(manifest, skip_review=yes or accept_defaults, core=ctx.core)
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

    console.print(f"\n[green]Created {final_id} ({manifest.kind} definition, {len(rendered)} files + fact-sheet row).[/green]")
    console.print("Next steps:")
    console.print(f"  1. mdb validate {final_id} --live     (smoke-test the provider before Viya)")
    console.print(f"  2. mdb test {final_id}                (run the generated scoreModel() locally - what SCR will execute)")
    console.print(f"  3. mdb register {final_id}            (register in SAS Model Manager)")
    console.print(f"  4. mdb publish {final_id}             (publish to SCR - or mdb ship {final_id} for register + publish)")


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
            elif result.inconclusive:
                # Transient upstream state (e.g. rate-limited): says nothing
                # about the definition - warn without failing the validation.
                console.print(f"[yellow]{folder.name}: smoke test inconclusive - {result.detail}[/yellow]")
            else:
                has_error = True
                console.print(f"[red]{folder.name}: smoke test failed - {result.detail}[/red]")
    raise typer.Exit(1 if has_error else 0)


def _rebuild_sheets(ctx: Context, prune: bool = False) -> bool:
    """Regenerate each kind's fact sheet from its managed definitions (shared by
    `sync --rebuild` and `load-facts --rebuild`). Returns False when there are no
    managed definitions at all."""
    rebuilt_any = False
    for kind in KINDS:
        manifests = ctx.managed_manifests(kind)
        if not manifests:
            continue
        rebuilt_any = True
        sheet = ctx.fact_sheet(kind)
        summary = facts.rebuild_sheet(sheet, manifests, keep_legacy=not prune)
        verb = "created" if summary["created"] else "rebuilt"
        message = f"{sheet.name}: {verb} from {summary['written']} definition(s)"
        if summary["legacy_kept"]:
            message += f", kept {summary['legacy_kept']} legacy row(s)"
        if summary["legacy_dropped"]:
            message += f", dropped {summary['legacy_dropped']} legacy row(s)"
        console.print(message)
    return rebuilt_any


@app.command()
def sync(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all"),
    rebuild: bool = typer.Option(
        False, "--rebuild",
        help="Regenerate each kind's whole fact sheet from all managed definitions "
             "(sorted by model_id), creating it if absent. Ignores model ids / --all.",
    ),
    prune: bool = typer.Option(
        False, "--prune",
        help="With --rebuild, also drop rows for models that no longer have a "
             "definition folder (default keeps such legacy rows).",
    ),
):
    """Upsert the fact-sheet rows of managed definitions (legacy rows stay untouched).

    With --rebuild the sheet becomes a pure function of the definitions: every
    managed row is regenerated and the file is rewritten from scratch, so you no
    longer maintain the CSV by hand alongside the definitions.
    """
    ctx = Context()
    if prune and not rebuild:
        console.print("[red]--prune only applies together with --rebuild.[/red]")
        raise typer.Exit(2)
    if rebuild:
        if ids or all_:
            console.print("[yellow]--rebuild regenerates the entire sheet for each kind; ignoring model ids / --all.[/yellow]")
        if not _rebuild_sheets(ctx, prune=prune):
            console.print("[yellow]No managed definitions found (no folder has a definition.yaml yet).[/yellow]")
        return
    for folder in ctx.resolve_targets(ids or [], all_):
        if not (folder / MANIFEST_FILENAME).is_file():
            continue
        manifest = load_manifest(folder)
        result = facts.upsert_row(ctx.fact_sheet(manifest.kind), manifest)
        console.print(f"{folder.name}: fact-sheet row {result}")


@app.command("load-facts")
def load_facts(
    caslib: Optional[str] = typer.Option(
        None, "--caslib", "-l",
        help="Target CAS library (env: SAS_CAS_LIBRARY; default: Public).",
    ),
    server: Optional[str] = typer.Option(
        None, "--server",
        help="CAS server name (env: SAS_CAS_SERVER; default: auto-detect cas-shared-default).",
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild",
        help="Regenerate the sheets from the definitions before loading.",
    ),
    prune: bool = typer.Option(
        False, "--prune",
        help="With --rebuild, also drop rows for models that no longer have a "
             "definition folder (default keeps such legacy rows).",
    ),
):
    """Upload, promote and save the fact-sheet CSVs to CAS (drops any existing table first).

    The Python equivalent of Load-Fact-Sheets.sas: each sheet is loaded with
    global scope (promoted) and saved to the caslib's data source on disk, so the
    SAS Visual Analytics monitoring report can bind to LLM_FACT_SHEET /
    EMBEDDING_FACT_SHEET across sessions and restarts.
    """
    from .viya.cas import load_fact_sheet, resolve_server
    ctx = Context()
    if prune and not rebuild:
        console.print("[red]--prune only applies together with --rebuild.[/red]")
        raise typer.Exit(2)
    if rebuild:
        _rebuild_sheets(ctx, prune=prune)
    caslib = caslib or os.environ.get("SAS_CAS_LIBRARY") or "Public"
    server_choice = server or os.environ.get("SAS_CAS_SERVER")
    with _viya_session() as session:
        server_name = resolve_server(session, server_choice)
        console.print(
            f"Loading fact sheets into CAS library [bold]{caslib}[/bold] on [bold]{server_name}[/bold]:"
        )
        loaded = 0
        for kind in KINDS:
            sheet = ctx.fact_sheet(kind)
            if not sheet.is_file():
                console.print(f"  [yellow]{sheet.name} not found - run 'mdb sync --rebuild' first, skipping.[/yellow]")
                continue
            try:
                result = load_fact_sheet(session, sheet, kind, caslib, server_name)
            except RuntimeError as exc:
                console.print(f"  [red]{exc}[/red]")
                raise typer.Exit(1)
            loaded += 1
            # "dropped" reflects the loaded copy; the save step replaces any saved one
            suffix = " (replaced a loaded copy)" if result["dropped"] else ""
            console.print(f"  {result['table']}: uploaded, promoted (global) and saved to disk{suffix}")
    if not loaded:
        console.print("[red]No fact sheet was loaded - generate them first with 'mdb sync --rebuild'.[/red]")
        raise typer.Exit(1)
    console.print("[green]Done. The tables are promoted and persisted; the monitoring report can bind to them.[/green]")


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
def setup(
    out: str = typer.Option(".", "--out", help="Directory for the authorization-rules and builder seed files"),
    no_files: bool = typer.Option(False, "--no-files", help="Only create the repository/projects; skip the seed files"),
):
    """Create the SAS Model Manager repository and the LLM/Embedding Model Projects if missing,
    and write the authorization-group rules and Prompt/RAG Builder seed files.

    Idempotent: existing objects are left untouched. `mdb register` runs the
    repository/project check automatically for the kind it registers, so calling
    setup is optional - use it to bootstrap a fresh environment up front. It also
    writes sas-viya-cli-commands.txt (the LLM Consumers / Prompt Engineers groups
    and folder/repository authorization rules) and the llm-prompt-builder.json /
    rag-builder.json builder seeds."""
    from .viya.registry import (
        authorization_rules_text, builder_seed, ensure_repository_and_project,
    )
    ctx = Context()
    responsible_party = os.environ.get("SAS_RESPONSIBLE_PARTY", "")
    deployment_type = os.environ.get("SAS_DEPLOYMENT_TYPE", "k8s")
    scr_endpoint = _scr_endpoint()
    created_any = False
    repo_id = repo_folder = None
    project_ids: dict[str, Optional[str]] = {}
    with _viya_session() as session:
        for kind in KINDS:
            try:
                ensured = ensure_repository_and_project(session, kind, ctx.core, responsible_party)
            except Exception as exc:
                console.print(f"[red]{kind}: {exc}[/red]")
                raise typer.Exit(1)
            for created in ensured.created:
                console.print(f"[green]created {created}.[/green]")
                created_any = True
            repo_id = repo_id or ensured.repository_id
            repo_folder = repo_folder or ensured.repository_folder_id
            project_ids[kind] = ensured.project_id
    if not created_any:
        console.print("[green]Repository and projects already exist.[/green]")
    if no_files:
        raise typer.Exit(0)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sas-viya-cli-commands.txt").write_text(
        authorization_rules_text(repo_id, repo_folder), encoding="utf-8")
    (out_dir / "llm-prompt-builder.json").write_text(
        json.dumps(builder_seed("llm", repo_id, project_ids.get("llm"), scr_endpoint, deployment_type), indent=4),
        encoding="utf-8")
    (out_dir / "rag-builder.json").write_text(
        json.dumps(builder_seed("embedding", repo_id, project_ids.get("embedding"), scr_endpoint, deployment_type), indent=4),
        encoding="utf-8")
    console.print(f"[green]Wrote sas-viya-cli-commands.txt (authorization rules), llm-prompt-builder.json "
                  f"and rag-builder.json to {out_dir}/.[/green]")
    console.print("Review sas-viya-cli-commands.txt before running it - it creates access groups and rules.")


@app.command("options-save")
def options_save(
    file: str = typer.Option("builder-options.json", "--file", "-f",
                             help="Where to write the options file"),
    reports_only: bool = typer.Option(
        False, "--reports-only",
        help="Capture only what the live reports hold; skip repository/project discovery"),
):
    """Save this deployment's Prompt Builder / RAG Builder option values to a file.

    Every option an admin sets on a builder object - repository and project
    ids, the SCR endpoint, the credential domain, the content root, which
    vector stores are offered - lives INSIDE the VA report. Importing a newer
    report from a transfer package therefore replaces a site's configuration
    with whatever the package was built against, and the report keeps working
    while pointing at somebody else's environment.

    Run this BEFORE importing a report package, and `mdb options-restore`
    after.

    The file supersedes the llm-prompt-builder.json / rag-builder.json seeds
    that `mdb setup` writes: it starts from the same discovered values
    (repository, projects, SCR endpoint) and overlays whatever the live
    reports already hold, so a tuned deployment is captured as tuned.
    """
    import json as _json
    from .core.options import BUILDER_REPORTS, merge_seed, options_file, read_options
    from .viya.registry import builder_seed, ensure_repository_and_project
    from .viya.reports import find_report, get_content

    ctx = Context()
    responsible_party = os.environ.get("SAS_RESPONSIBLE_PARTY", "")
    deployment_type = os.environ.get("SAS_DEPLOYMENT_TYPE", "k8s")
    scr_endpoint = _scr_endpoint()
    captured: dict = {}
    with _viya_session() as session:
        seeds: dict = {}
        if not reports_only:
            for report_name, kind in BUILDER_REPORTS.items():
                try:
                    ensured = ensure_repository_and_project(
                        session, kind, ctx.core, responsible_party)
                    seeds[report_name] = builder_seed(
                        kind, ensured.repository_id, ensured.project_id,
                        scr_endpoint, deployment_type)
                except Exception as exc:
                    console.print(f"[yellow]{report_name}: could not discover "
                                  f"repository/project ({exc}); capturing the report only.[/yellow]")
        for report_name in BUILDER_REPORTS:
            live = None
            report = find_report(session, report_name)
            if report:
                try:
                    content, _ = get_content(session, report["id"])
                    live = read_options(content)
                except Exception as exc:
                    console.print(f"[yellow]{report_name}: content unreadable ({exc}).[/yellow]")
            if live is None and report_name not in seeds:
                console.print(f"[yellow]{report_name}: no live report and nothing "
                              "discovered - skipped.[/yellow]")
                continue
            merged = merge_seed(seeds.get(report_name, {}), live)
            captured[report_name] = merged
            source = ("report + discovery" if (live and report_name in seeds)
                      else ("report" if live else "discovery"))
            console.print(f"[green]{report_name}: {len(merged)} options ({source}).[/green]")
    if not captured:
        console.print("[red]Nothing captured.[/red]")
        raise typer.Exit(1)
    deployment = os.environ.get("SAS_VIYA_URL", "")
    out = Path(file)
    if out.parent != Path(""):
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(options_file(deployment, captured), indent=2) + "\n",
                   encoding="utf-8")
    console.print(f"[green]Wrote {file}. Keep it with your deployment records and run "
                  "`mdb options-restore` after importing a report package.[/green]")


@app.command("options-restore")
def options_restore(
    file: str = typer.Option("builder-options.json", "--file", "-f",
                             help="The options file written by `mdb options-save`"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Report what would change without writing"),
):
    """Write saved option values back into the builder reports after an import.

    Only the options named in the file are touched: a newly imported report
    keeps its new layout, data items and objects, and gets this deployment's
    configuration back. An option the report does not have is reported rather
    than inserted - that usually means it was renamed or dropped between
    versions, which is worth knowing.
    """
    import json as _json
    from .core.options import write_options
    from .viya.reports import find_report, get_content, put_content

    path = Path(file)
    if not path.exists():
        console.print(f"[red]{file} not found - run `mdb options-save` first.[/red]")
        raise typer.Exit(1)
    document = _json.loads(path.read_text(encoding="utf-8"))
    saved = document.get("reports", {})
    if not saved:
        console.print(f"[red]{file} names no reports.[/red]")
        raise typer.Exit(1)
    here = os.environ.get("SAS_VIYA_URL", "")
    if document.get("deployment") and document["deployment"] != here:
        # Not an error: moving options between environments is a legitimate
        # deliberate act. It is worth saying out loud because doing it by
        # ACCIDENT is the failure this command exists to prevent.
        console.print(f"[yellow]Note: these options were saved from "
                      f"{document['deployment']}, not {here}.[/yellow]")
    failures = 0
    with _viya_session() as session:
        for report_name, values in saved.items():
            report = find_report(session, report_name)
            if not report:
                console.print(f"[yellow]{report_name}: no such report here - skipped.[/yellow]")
                continue
            content, etag = get_content(session, report["id"])
            updated, result = write_options(content, values)
            for label in result.missing:
                console.print(f"[yellow]  {report_name}: '{label}' is not an option of "
                              "this report version - not written.[/yellow]")
            if not result.changed:
                console.print(f"[green]{report_name}: already matches "
                              f"({len(result.unchanged)} options).[/green]")
                continue
            listing = ", ".join(sorted(result.applied))
            if dry_run:
                console.print(f"[cyan]{report_name}: would restore "
                              f"{len(result.applied)} option(s): {listing}[/cyan]")
                continue
            try:
                put_content(session, report["id"], updated, etag)
            except Exception as exc:
                console.print(f"[red]{report_name}: writing the report failed ({exc}).[/red]")
                failures += 1
                continue
            console.print(f"[green]{report_name}: restored {len(result.applied)} "
                          f"option(s): {listing}[/green]")
    if failures:
        raise typer.Exit(1)


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
    all_targets = ctx.resolve_targets(ids or [], all_)
    targets = [f for f in all_targets if (f / MANIFEST_FILENAME).is_file()]
    scr = _scr_endpoint()
    responsible_party = os.environ.get("SAS_RESPONSIBLE_PARTY", "")
    ensured_kinds: set[str] = set()
    failed = False
    # An explicitly named folder without a manifest is a hand-maintained folder;
    # make the skip visible instead of silently succeeding.
    if not all_ and ids:
        for folder in all_targets:
            if not (folder / MANIFEST_FILENAME).is_file():
                console.print(f"[yellow]{folder.name}: no {MANIFEST_FILENAME} - adopt it with "
                              "'mdb import' first, or it stays hand-maintained. Skipped.[/yellow]")
                failed = True
    with _viya_session() as session:
        for folder in targets:
            try:
                manifest = load_manifest(folder)
                if manifest.kind not in ensured_kinds:
                    ensured = ensure_repository_and_project(session, manifest.kind, ctx.core, responsible_party)
                    for created in ensured.created:
                        console.print(f"[green]setup: created {created}.[/green]")
                    ensured_kinds.add(manifest.kind)
                row = facts.read_row(ctx.fact_sheet(manifest.kind), manifest.model_id)
                if row is None:
                    console.print(f"[yellow]{manifest.model_id}: no fact-sheet row - run mdb sync first "
                                  "(cost monitoring metadata will be incomplete).[/yellow]")
                result = register_model(session, manifest, folder, row, scr, update=update)
            except Exception as exc:
                console.print(f"[red]{folder.name}: {exc}[/red]")
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
                console.print("[dim]  Note: a container already published to an SCR destination is not "
                              "removed by this - delete it at the publishing destination if needed.[/dim]")
            else:
                console.print(f"[yellow]{model_id}: not registered - nothing to delete.[/yellow]")
    raise typer.Exit(1 if failed else 0)


@app.command()
def pull(
    ids: list[str] = typer.Argument(..., help="Registered model_id(s) in SAS Model Manager to recreate locally"),
    adopt: bool = typer.Option(False, "--import", help="Also reverse-engineer definition.yaml so the folder is mdb-managed"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing local folder"),
):
    """Recreate a local definition folder from a model registered in SAS Model
    Manager (the reverse of register). Downloads the model's content and, for
    models registered before mdb (no stored definition.yaml), rebuilds
    modelConfiguration.json from its attributes. With --import it also
    reverse-engineers a definition.yaml (equivalent to running mdb import)."""
    from .viya.registry import pull_model
    ctx = Context()
    failed = False
    with _viya_session() as session:
        for model_id in ids:
            try:
                result = pull_model(session, model_id, ctx.defs_dir, force=force)
            except Exception as exc:
                console.print(f"[red]{model_id}: {exc}[/red]")
                failed = True
                continue
            defs_name = "LLM-Definitions" if result.kind == "llm" else "Embedding-Definitions"
            source = ("its stored definition.yaml" if result.had_definition
                      else "rebuilt modelConfiguration.json from the model's attributes"
                           if result.reconstructed_config else "its stored files")
            console.print(f"[green]{model_id}: pulled to {defs_name}/{model_id}/[/green] "
                          f"({len(result.files)} file(s); {source})")
            if adopt and not result.had_definition:
                folder = ctx.defs_dir(result.kind) / model_id
                fact_sheet = ctx.fact_sheet(result.kind)
                try:
                    imported = import_folder(folder, fact_sheet)
                    imported.manifest.save(folder)
                    for note in imported.notes:
                        console.print(f"  [yellow]note:[/yellow] {note}")
                    console.print(f"[green]{model_id}: wrote definition.yaml - review it, then converge with "
                                  f"'mdb import {model_id} --apply'.[/green]")
                except Exception as exc:
                    console.print(f"[yellow]{model_id}: pulled, but could not reverse-engineer "
                                  f"definition.yaml ({exc}) - the folder stays hand-maintained.[/yellow]")
            elif result.had_definition:
                console.print(f"  Already mdb-managed (definition.yaml pulled). Regenerate with "
                              f"'mdb generate {model_id}'.")
            else:
                console.print(f"  Legacy folder. Adopt it with 'mdb import {model_id}' (or re-run with --import).")
    raise typer.Exit(1 if failed else 0)


@app.command("list")
def list_(
    kind: Optional[str] = typer.Option(None, "--kind", help="Only 'llm' or 'embedding' (default: both)"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """List the models registered in SAS Model Manager with their lifecycle
    status (provider, family, version, modelStatus, approvalState, endpoint)."""
    from .viya.registry import list_registered_models
    kinds = (kind,) if kind in KINDS else KINDS
    if kind is not None and kind not in KINDS:
        console.print(f"[red]--kind must be one of {', '.join(KINDS)}.[/red]")
        raise typer.Exit(2)
    rows: list[dict] = []
    with _viya_session() as session:
        for k in kinds:
            rows.extend(list_registered_models(session, k))
    if as_json:
        console.print_json(json.dumps(rows))
        return
    if not rows:
        console.print("[yellow]No registered models found in the LLM/Embedding model projects.[/yellow]")
        console.print("Register one with [bold]mdb register <model_id>[/bold].")
        return
    table = Table()
    for col in ("model_id", "kind", "provider", "type", "version", "status", "approval", "endpoint"):
        table.add_column(col)
    status_color = {"deployed": "green", "ready for validation": "yellow", "retired": "red"}
    for r in rows:
        status = r["modelStatus"] or "-"
        color = status_color.get(status, "dim")
        table.add_row(
            r["model_id"], r["kind"], r["provider"] or "-", r["llmModelType"] or "-",
            r["deploymentId"] or "-", f"[{color}]{status}[/{color}]",
            r["approvalState"] or "-", r["endPoint"] or "-",
        )
    console.print(table)


@app.command()
def publish(
    ids: Optional[list[str]] = typer.Argument(None),
    all_: bool = typer.Option(False, "--all"),
    destination: Optional[str] = typer.Option(None, "-d", "--destination",
                                              help="SCR publishing destination (env: SAS_PUBLISH_DESTINATION)"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the image build completes"),
):
    """Publish registered models to an SCR container destination."""
    from .viya.registry import ensure_model_lifecycle, publish_model
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
                # A published model is deployed and approved; advance the lifecycle
                # attributes if they are not already there (non-fatal).
                try:
                    if ensure_model_lifecycle(session, model_id, "deployed", "approved"):
                        console.print(f"[dim]{model_id}: modelStatus=deployed, approvalState=approved[/dim]")
                except Exception as exc:
                    console.print(f"[yellow]{model_id}: could not update lifecycle attributes ({exc}).[/yellow]")
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
    archive: bool = typer.Option(
        False, "--archive",
        help="Also move the definition to a git-ignored _archive/ folder (and drop its fact-sheet "
             "row), taking it out of the tracked active set. Recoverable with 'mdb pull'.",
    ),
):
    """Tag a definition as deprecated (hides it from the Prompt Builder) and regenerate.

    By default the deprecated definition stays in place and tracked. Pass
    --archive to move it out of the active set into a git-ignored _archive/
    folder instead (a retired model then no longer ships in the transfer
    package; recover it any time with 'mdb pull')."""
    import yaml as _yaml
    ctx = Context()
    folder = ctx.find_folder(model_id)
    if folder is None or not (folder / MANIFEST_FILENAME).is_file():
        console.print(f"[red]{model_id}: no managed definition found.[/red]")
        raise typer.Exit(2)
    manifest = load_manifest(folder)
    already_deprecated = "deprecated" in manifest.tags.extra
    if already_deprecated and not archive:
        console.print(f"{model_id}: already deprecated.")
        raise typer.Exit(0)
    # Tag + regenerate only when not already deprecated; an already-deprecated
    # definition passed with --archive proceeds straight to archiving.
    if already_deprecated:
        console.print(f"{model_id}: already deprecated - archiving.")
    else:
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
        # In-place retire keeps the model in the fact sheet (tagged deprecated);
        # --archive drops the row below.
        if not archive:
            facts.upsert_row(ctx.fact_sheet(manifest.kind), manifest)
        console.print(f"[green]{model_id}: tagged deprecated and regenerated.[/green]")

    # Also retire the registered model in SAS Model Manager, if reachable and
    # registered. Best-effort: retire stays a local tagging op when Viya is not
    # configured.
    from .viya.session import ViyaConfigError, create_session
    try:
        with create_session() as session:
            from .viya.registry import ensure_model_lifecycle
            if ensure_model_lifecycle(session, model_id, "retired", "retired"):
                console.print(f"[green]{model_id}: modelStatus=retired, approvalState=retired in SAS Model Manager.[/green]")
            else:
                console.print(f"[dim]{model_id}: not registered (or already retired) in SAS Model Manager.[/dim]")
    except ViyaConfigError:
        console.print("[yellow]SAS Viya is not configured - the registered model's status was not updated.[/yellow]")
    except Exception as exc:
        console.print(f"[yellow]Could not update the registered model's status: {exc}[/yellow]")

    if not archive:
        console.print(f"Push the deprecated flag to the registered model with: mdb register {model_id} --update")
        console.print(f"[dim]  Move it out of the active set entirely with: mdb retire {model_id} --archive[/dim]")
        return

    # Archive the definition out of the active, tracked set. _archive/ is
    # git-ignored, so a retired model stops cluttering the definitions directory
    # and the transfer package while a local copy is kept for reference - it is
    # always recoverable with 'mdb pull' or from git history.
    import shutil
    removed = facts.remove_row(ctx.fact_sheet(manifest.kind), model_id)
    destination = archive_dir(ctx.repo) / manifest.kind / model_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.move(str(folder), str(destination))
    fact_note = ", fact-sheet row removed" if removed == "removed" else ""
    console.print(f"[green]{model_id}: archived to _archive/{manifest.kind}/{model_id}/ (git-ignored){fact_note}.[/green]")
    console.print(f"[dim]  No longer in the tracked repo. Recover it with 'mdb pull {model_id}' or from git history.[/dim]")


@app.command()
def providers():
    """List the available provider adapters."""
    table = Table()
    table.add_column("id")
    table.add_column("name")
    table.add_column("kinds")
    table.add_column("API key env var")
    table.add_column("score template")
    for adapter in load_adapters().values():
        embedding_template = getattr(adapter, "embedding_template", None)
        if embedding_template is None:
            kinds = "llm"
        elif adapter.template == embedding_template:
            kinds = "embedding"
        else:
            kinds = "llm + embedding"
        table.add_row(adapter.id, adapter.display_name, kinds, adapter.env_key_var or "-", adapter.template)
    console.print(table)
    console.print("Third-party adapters: pip packages exposing the 'mdb.providers' entry-point group.")


provider_app = typer.Typer(
    help="Third-party provider adapter tooling.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
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
