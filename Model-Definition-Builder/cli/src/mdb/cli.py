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
import re
from pathlib import Path, PurePath
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__
from .core import drift, facts
from .core.generator import (
    AZURE_ENV_TEMPLATE, AZURE_TEMPLATES, CoreAssets, GenerationError, effective_score_file,
    list_custom_options, render_assets,
)
from .core.importer import import_folder
from .core.manifest import MANIFEST_FILENAME, ModelManifest, export_json_schema, load_manifest
from .core.netutil import env_flag, make_session
from .core.paths import (
    RepoNotFoundError, archive_dir, core_dir, definitions_dir, fact_sheet_path, find_repo_root,
    kind_folder_name, kind_of_folder,
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


def _print_version(value: bool) -> None:
    if value:
        # The accelerator release this mdb ships with (mdb/__init__.py); the
        # same string is stamped into every generated scorer's header.
        typer.echo(f"mdb {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", "-V", help="Print the mdb version and exit.",
        callback=_print_version, is_eager=True,
    ),
) -> None:
    """Model Definition Builder for the SAS Agentic AI Accelerator."""


KINDS = ("llm", "embedding", "decision")

# What `mdb test` sends a decision model: one support ticket and the three
# question primitives, so the answer map shows every shape at once.
DECISION_TEST_STATE = (
    "Hi, I was charged twice for my March invoice and nobody has answered my two "
    "previous mails. Please refund one of the payments this week."
)
DECISION_TEST_QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Which team should handle this ticket?",
        "criteria": {
            "billing": "Payments, invoices, refunds and subscription changes",
            "technical": "Bugs, errors, outages and login problems",
            "sales": "Pricing, plans, quotes and upgrades",
        },
    },
    "escalate": {"type": "noul", "instructions": "The customer is angry or threatens to leave"},
    "tone": {
        "type": "score",
        "instructions": "How frustrated does the customer sound?",
        "criteria": ["Calm, just stating facts", "Frustrated but civil", "Very angry, strong language"],
    },
}


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
        return kind_of_folder(folder)

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
                console.print(f"[red]{model_id}: no such folder in LLM-Definitions, Embedding-Definitions "
                              "or Decision-Definitions[/red]")
                raise typer.Exit(2)
            folders.append(folder)
        return folders


def _env_api_key(adapter: ProviderAdapter) -> Optional[str]:
    if not adapter.env_key_var:
        return None
    for name in (adapter.env_key_var, *getattr(adapter, "env_key_fallbacks", ())):
        value = os.environ.get(name)
        if value:
            return value
    return None


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
        if model.kind == "llm" and known.kind in ("embedding", "decision"):
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
                enriched = _enrich_from_static(models, static)
                # A live listing does not carry everything a provider serves
                # (OpenRouter's /models omits its decision models); the
                # snapshot's extra entries stay selectable, marked as such.
                live_refs = {m.ref for m in enriched}
                enriched.extend(m for m in static if m.ref not in live_refs)
                return enriched
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
        if m.kind in ("embedding", "decision"):
            flags += f" [{m.kind}]"
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
    pricing_unknown = (manifest.kind in ("llm", "decision") and pricing.cost_type == "Tokens"
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
    if manifest.kind in ("llm", "decision") and pricing.cost_type == "Tokens":
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
    if manifest.kind in ("llm", "decision") and pricing.cost_type == "Tokens" and (adjust or pricing_unknown):
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
             "definition stays environment-neutral: deployed containers read AZURE_OPENAI_RESOURCE.",
    ),
    repo: Optional[str] = typer.Option(None, help="Hugging Face repo id (hf-selfhosted)"),
    gated: Optional[bool] = typer.Option(None, help="HF repo is gated (hf-selfhosted)"),
    runtime: Optional[str] = typer.Option(None, help="Runtime family: transformers | onnx | sentence-transformers (hf-selfhosted)"),
    params_billions: Optional[float] = typer.Option(None, help="Parameter count in billions (hf-selfhosted)"),
    base_url: Optional[str] = typer.Option(None, help="Server base URL (ollama/vllm self-hosted)"),
    kind: Optional[str] = typer.Option(None, help="Model kind: llm, embedding or decision (any provider whose adapter has a template for that kind)"),
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
    supports_decision = getattr(adapter, "decision_template", None) is not None
    kind_is_flaggable = (supports_embedding or supports_decision) and "kind" not in question_params
    if kind is not None and kind not in KINDS:
        console.print(f"[red]--kind must be one of {', '.join(KINDS)}.[/red]")
        raise typer.Exit(2)
    if (kind == "embedding" and not supports_embedding) or (kind == "decision" and not supports_decision):
        console.print(f"[red]Provider '{provider}' has no {kind} template - see mdb providers.[/red]")
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
    decision_only = supports_decision and adapter.template == adapter.decision_template
    if embedding_only:
        cm.kind = "embedding"
    elif decision_only:
        cm.kind = "decision"
    elif kind_is_flaggable:
        if kind is not None:
            cm.kind = kind
        elif manual_entry and not yes:
            offered = ["llm"] + (["embedding"] if supports_embedding else []) + (["decision"] if supports_decision else [])
            cm.kind = Prompt.ask("Model kind", choices=offered, default=cm.kind)

    proposed = model_id or slugify(cm.display_name)
    final_id = proposed if yes else Prompt.ask("Definition folder / model_id", default=proposed)

    modeler = os.environ.get("SAS_RESPONSIBLE_PARTY", "") or os.environ.get("USERNAME", "") or os.environ.get("USER", "")
    try:
        manifest = adapter.build_manifest(cm, final_id, answers, modeler)
        # State the kind up front - it decides the score template, the folder
        # and the Model Manager project, and used to be visible only as an
        # easily-missed folder name near the end.
        kind_folder = kind_folder_name(manifest.kind)
        console.print(f"\n[bold cyan]Adding a {manifest.kind.upper()} model definition -> "
                      f"{kind_folder}/{final_id}/[/bold cyan]")
        # The catalog-derived values steer scoring behavior and cost monitoring
        # - confirm them consciously by default; --accept-defaults / --yes skip
        # the review (unknown token pricing is still asked/warned about).
        _review_catalog_values(manifest, skip_review=yes or accept_defaults, core=ctx.core)
        rendered = render_assets(manifest, ctx.core)
    except (GenerationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    defs_name = kind_folder_name(manifest.kind)
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


def _load_releases(session, ctx: "Context", caslib: str, server_name: str, table: str,
                   changelog: Optional[Path] = None, csv_data: Optional[bytes] = None) -> dict:
    """Parse CHANGELOG.md into the release table and load it (drop, upload,
    promote, save). Shared by `mdb load-releases`, `mdb setup` and `mdb
    builders-import`. With `csv_data` - the Data/ACCELERATOR_RELEASES.csv a
    package holds - that table is loaded as it is and no changelog is read."""
    import tempfile
    from .core.releases import build_release_table, rows_of_csv, summarise
    from .viya.cas import load_csv_table
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / f"{table}.csv"
        if csv_data is not None:
            summary = summarise(rows_of_csv(csv_data))
            csv_path.write_bytes(csv_data)
        else:
            source = changelog or (ctx.repo / "CHANGELOG.md")
            if not source.is_file():
                raise RuntimeError(f"no changelog at {source} - pass --changelog or set MDB_REPO to the accelerator "
                                   "clone")
            rows, summary = build_release_table(source, csv_path)
        result = load_csv_table(session, csv_path, table, caslib, server_name)
    summary["dropped"] = result["dropped"]
    return summary


def _releases_table() -> str:
    from .core.releases import DEFAULT_TABLE, TABLE_ENV
    return os.environ.get(TABLE_ENV) or DEFAULT_TABLE


def _print_release_counts(summary: dict) -> None:
    counts = ", ".join(f"{name} {n}" for name, n in summary["by_component"].items())
    console.print(f"  rows per component: {counts}")
    # a Builder entry is one row per part it concerns, so the rows outnumber
    # the entries and the parts add up to more than the Builder's entries
    parts = ", ".join(f"{name} {n}" for name, n in summary["by_part"].items())
    console.print(f"  rows per Builder part (builder_part): {parts} - {summary['rows']} rows "
                  f"for {summary['entries']} changelog entries")


@app.command("load-releases")
def load_releases(
    caslib: Optional[str] = typer.Option(
        None, "--caslib", "-l",
        help="Target CAS library (env: SAS_CAS_LIBRARY; default: Public).",
    ),
    table: Optional[str] = typer.Option(
        None, "--table", "-t",
        help="Table name (env: SAS_RELEASES_TABLE; default: ACCELERATOR_RELEASES).",
    ),
    server: Optional[str] = typer.Option(
        None, "--server",
        help="CAS server name (env: SAS_CAS_SERVER; default: auto-detect cas-shared-default).",
    ),
    changelog: Optional[Path] = typer.Option(
        None, "--changelog",
        help="The CHANGELOG.md to read (default: the accelerator clone's).",
    ),
    csv_out: Optional[Path] = typer.Option(
        None, "--csv",
        help="Only write the table as CSV to this path; do not contact SAS Viya.",
    ),
):
    """Load the accelerator's release history from CHANGELOG.md into CAS as the
    ACCELERATOR_RELEASES table (drops any existing table first).

    One row per release and one per change, with the release, the section
    (Added / Changed / Fixed / Removed), the component it concerns (Prompt
    Builder, RAG runtime, Model Definition Builder, ...) and the change as a
    headline plus detail. A change to the Prompt Builder is one row per part
    of it that it concerns - builder_part is Prompt, Decision or RAG - so a
    button bar on that column shows it under each. It is the table a report
    author assigns to the Prompt Builder object in SAS Visual Analytics - a
    Data-Driven Content object needs a data assignment before it renders -
    and it says which release an environment runs. `mdb setup` loads it too.
    """
    from .core.releases import build_release_table, current_words
    from .viya.cas import resolve_server
    ctx = Context()
    table_name = table or _releases_table()
    source = changelog or (ctx.repo / "CHANGELOG.md")
    if csv_out is not None:
        try:
            _, summary = build_release_table(source, csv_out)
        except (OSError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        console.print(f"Wrote {summary['rows']} rows for {summary['releases']} releases to {csv_out} "
                      f"({current_words(summary)}).")
        _print_release_counts(summary)
        return
    caslib = caslib or os.environ.get("SAS_CAS_LIBRARY") or "Public"
    server_choice = server or os.environ.get("SAS_CAS_SERVER")
    with _viya_session() as session:
        server_name = resolve_server(session, server_choice)
        console.print(f"Loading the release table into CAS library [bold]{caslib}[/bold] on [bold]{server_name}[/bold]:")
        try:
            summary = _load_releases(session, ctx, caslib, server_name, table_name, changelog)
        except (RuntimeError, ValueError) as exc:
            console.print(f"  [red]{exc}[/red]")
            raise typer.Exit(1)
    suffix = " (replaced a loaded copy)" if summary["dropped"] else ""
    console.print(f"  {table_name}: {summary['rows']} rows for {summary['releases']} releases, uploaded, "
                  f"promoted (global) and saved to disk{suffix}")
    _print_release_counts(summary)
    console.print(f"[green]Done. The table names the {current_words(summary)}. Assign "
                  f"{caslib}.{table_name} to the Prompt Builder object in SAS Visual Analytics.[/green]")


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
    mas: bool = typer.Option(
        False, "--mas",
        help="Call scoreModel() the way the MAS REST API does - plain strings - instead of the "
             "one-element lists CAS and the SCR container pass (the default)",
    ),
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
    # Both calling conventions reach the same scorer: SCR / CAS wrap each input
    # in a one-element list, the MAS REST API passes the plain string (#26).
    wrap = (lambda value: value) if mas else (lambda value: [value])
    convention = "MAS REST (plain strings)" if mas else "SCR (one-element lists)"
    console.print(f"[dim]Calling scoreModel() from {score_path.name} - {convention}...[/dim]")
    table = Table(show_header=False)
    if manifest.kind == "decision":
        state = DECISION_TEST_STATE if prompt == "Reply with the single word OK." else prompt
        answers, answer, confidence, run_time, prompt_length, output_length = module.scoreModel(
            wrap(state), wrap(json.dumps(DECISION_TEST_QUESTIONS)), wrap(json.dumps(options))
        )
        table.add_row("state", state)
        table.add_row("answers", json.dumps(json.loads(answers), indent=1))
        table.add_row("answer", f"{answer} (confidence {float(confidence):.3f})")
        table.add_row("run_time", f"{run_time:.2f}s")
        table.add_row("prompt_length", str(prompt_length))
        table.add_row("output_length", str(output_length))
    elif manifest.kind == "embedding":
        embedding, run_time, tokens = module.scoreModel(
            wrap(prompt), wrap("mdb-test"), wrap(json.dumps(options))
        )
        vector = json.loads(embedding)
        table.add_row("embedding", f"{len(vector)}-dimension vector [{vector[0]:.5f}, {vector[1]:.5f}, ...]")
        table.add_row("run_time", f"{run_time:.2f}s")
        table.add_row("tokens", str(tokens))
    else:
        response, run_time, prompt_length, output_length = module.scoreModel(
            wrap(prompt), wrap(system), wrap(json.dumps(options))
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
        session = create_session()
        console.print(f"[dim]SAS Viya: {session.auth_summary}[/dim]")
        return session
    except ViyaConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)


@app.command()
def setup(
    out: str = typer.Option(".", "--out", help="Directory for the authorization-rules file and the builder seed file"),
    no_files: bool = typer.Option(False, "--no-files", help="Only create the repository/projects; skip the two files"),
    no_releases: bool = typer.Option(
        False, "--no-releases",
        help="Skip loading the ACCELERATOR_RELEASES table into CAS (see 'mdb load-releases')"),
):
    """Create the SAS Model Manager repository and the LLM/Embedding Model Projects if missing,
    load the release table into CAS, and write the authorization-group rules and
    the Prompt Builder seed file.

    Idempotent: existing objects are left untouched. `mdb register` runs the
    repository/project check automatically for the kind it registers, so calling
    setup is optional - use it to bootstrap a fresh environment up front. It also
    writes sas-viya-cli-commands.txt (the LLM Consumers / Prompt Engineers groups
    and folder/repository authorization rules) and llm-prompt-builder.json, the
    Prompt Builder seed: the repository id, the LLM and embedding project ids,
    the SCR endpoint and the deployment type, under their option names."""
    from .core.options import BUILDER_REPORTS
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
        if not no_releases:
            # The table the Prompt Builder object is assigned in SAS Visual Analytics;
            # loading it here means a report import never lacks a data source.
            from .viya.cas import resolve_server
            caslib = os.environ.get("SAS_CAS_LIBRARY") or "Public"
            table_name = _releases_table()
            try:
                server_name = resolve_server(session, os.environ.get("SAS_CAS_SERVER"))
                from .core.releases import current_words
                summary = _load_releases(session, ctx, caslib, server_name, table_name)
                console.print(f"[green]loaded {caslib}.{table_name} ({summary['rows']} rows, "
                              f"{current_words(summary)}) - assign it to the Prompt Builder object in "
                              f"SAS Visual Analytics.[/green]")
            except (RuntimeError, ValueError) as exc:
                console.print(f"[yellow]release table not loaded: {exc} - run 'mdb load-releases' later.[/yellow]")
    if not created_any:
        console.print("[green]Repository and projects already exist.[/green]")
    if no_files:
        raise typer.Exit(0)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sas-viya-cli-commands.txt").write_text(
        authorization_rules_text(repo_id, repo_folder), encoding="utf-8")
    # One seed for the one report: both of its seed kinds' project ids.
    seed_kinds = [kind for kinds in BUILDER_REPORTS.values() for kind in kinds]
    (out_dir / "llm-prompt-builder.json").write_text(
        json.dumps(builder_seed(repo_id, {kind: project_ids.get(kind) for kind in seed_kinds},
                                scr_endpoint, deployment_type), indent=4),
        encoding="utf-8")
    console.print(f"[green]Wrote sas-viya-cli-commands.txt (authorization rules) and llm-prompt-builder.json "
                  f"(the Prompt Builder seed, with the LLM and the embedding project id) to {out_dir}/.[/green]")
    console.print("Review sas-viya-cli-commands.txt before running it - it creates access groups and rules.")


@app.command("options-save")
def options_save(
    file: str = typer.Option("builder-options.json", "--file", "-f",
                             help="Where to write the options file"),
    reports_only: bool = typer.Option(
        False, "--reports-only",
        help="Capture only what the live reports hold; skip repository/project discovery"),
    content_root: Optional[str] = typer.Option(
        None, "--content-root",
        help="The content folder the report sits in (default /SAS Agentic AI Accelerator): the report read is "
             "<content root>/Prompt Builder/Prompt Builder, and the retired <content root>/RAG/RAG Builder."),
):
    """Save this deployment's Prompt Builder option values to a file.

    Every option an admin sets on the Prompt Builder object - repository and
    project ids, the SCR endpoint, the credential domain, the feature
    switches, and for its RAG part the content root and which vector stores
    are offered - lives INSIDE the VA report. Importing a newer report from a
    transfer package therefore replaces a site's configuration with whatever
    the package was built against, and the report keeps working while
    pointing at somebody else's environment.

    Run this BEFORE importing a report package, and `mdb options-restore`
    after. `mdb builders-import` does both by itself when it is given no
    options file: it reads the report before the import, keeps what it read
    in a file of its own and writes it back. Saving first is for a file to
    keep, to edit before the import, or to carry to another deployment.

    A site upgrading from 2.2.x still has the retired report named "RAG
    Builder". Its options are read too and folded into the Prompt Builder
    section: the RAG settings keep their names, its `computeContext` becomes
    `ragComputeContext`, and `enableRag` is set. Whatever the Prompt Builder
    report already holds wins. Save BEFORE deleting that report.

    The file supersedes the llm-prompt-builder.json seed that `mdb setup`
    writes: it starts from the same discovered values (repository, projects,
    SCR endpoint) and overlays whatever the live reports already hold, so a
    tuned deployment is captured as tuned.
    """
    import json as _json
    from .core.options import options_file

    ctx = Context()
    with _viya_session() as session:
        seeds = {} if reports_only else _discovered_seeds(session, ctx, "capturing the report only")
        captured = _capture_options(session, seeds, content_root=content_root or "")
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


class _ReportUnreadable(RuntimeError):
    """A Builder report is on the site (or may be) and its options cannot be
    read."""

    def __init__(self, report: str, reason):
        self.report = report
        super().__init__(f"{report}: {reason}")


def _capture_options(session, seeds: dict, strict: bool = False, content_root: str = "") -> dict:
    """{report name: {option: value}} for the Builder report, as options-save
    writes it: what `seeds` discovered, overlaid with what the live reports
    hold.

    A report is the one at its place below `content_root` (the content root
    of the release unless named): `<root>/Prompt Builder/Prompt Builder`, and
    the retired `<root>/RAG/RAG Builder` - not a report of that name anywhere
    on the system.

    A retired report that still exists is read as well and folded into the
    merged one BEFORE the seed is applied, so the order of precedence is: the
    merged report's own values, then the retired report's, then discovery. A
    project id an admin once chose in the retired report beats the one this
    run would derive.

    A report whose content cannot be read, or that is read and holds no
    option at all, is passed over with a line - unless `strict`: then it
    raises _ReportUnreadable, and so does a lookup the service does not
    answer. That is for the caller about to replace the report: options it
    could not read are options it would lose.
    """
    from .core.options import (BUILDER_REPORTS, LEGACY_REPORTS, fold_legacy, merge_seed,
                               read_options, report_folder)
    from .viya.reports import find_report_at, get_content
    live: dict = {}
    for report_name in (*BUILDER_REPORTS, *sorted(LEGACY_REPORTS)):
        folder = report_folder(content_root, report_name)
        if strict:
            try:
                report = find_report_at(session, folder, report_name, strict=True)
            except Exception as exc:
                raise _ReportUnreadable(report_name, f"whether it is in {folder} could not be asked ({exc})") \
                    from exc
        else:
            report = find_report_at(session, folder, report_name)
        if not report:
            continue
        try:
            content, _ = get_content(session, report["id"])
        except Exception as exc:
            if strict:
                raise _ReportUnreadable(report_name, f"it is in {folder} and its content cannot be read ({exc})") \
                    from exc
            console.print(f"[yellow]{report_name}: content unreadable ({exc}).[/yellow]")
            continue
        options = read_options(content)
        if not options:
            why = (f"it is in {folder} and holds no option at all - it is not the report of the Builder, or its "
                   "content is not what mdb reads")
            if strict:
                raise _ReportUnreadable(report_name, why)
            console.print(f"[yellow]{report_name}: {why} - passed over.[/yellow]")
            continue
        live[report_name] = options
        if report_name in LEGACY_REPORTS:
            console.print(f"[yellow]'{report_name}': read from the retired report of that name "
                          f"and folded into the {', '.join(BUILDER_REPORTS)} section.[/yellow]")
    folded, messages = fold_legacy(live)
    for message in messages:
        console.print(f"[yellow]  {message}[/yellow]")
    captured: dict = {}
    for report_name in BUILDER_REPORTS:
        if report_name not in folded and report_name not in seeds:
            console.print(f"[yellow]{report_name}: no live report and nothing "
                          "discovered - skipped.[/yellow]")
            continue
        merged = merge_seed(seeds.get(report_name, {}), folded.get(report_name))
        captured[report_name] = merged
        source = ("report + discovery" if (report_name in folded and report_name in seeds)
                  else ("report" if report_name in folded else "discovery"))
        console.print(f"[green]{report_name}: {len(merged)} options ({source}).[/green]")
    return captured


@app.command("options-restore")
def options_restore(
    file: str = typer.Option("builder-options.json", "--file", "-f",
                             help="The options file written by `mdb options-save`"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Report what would change without writing"),
    content_root: Optional[str] = typer.Option(
        None, "--content-root",
        help="The content folder the report sits in (default /SAS Agentic AI Accelerator): the report written "
             "is <content root>/Prompt Builder/Prompt Builder - a report of that name elsewhere is not touched."),
):
    """Write saved option values back into the Prompt Builder report after an import.

    Only the options named in the file are touched: a newly imported report
    keeps its new layout, data items and objects, and gets this deployment's
    configuration back. An option the report does not have is reported rather
    than inserted - that usually means it was renamed or dropped between
    versions, which is worth knowing. When the report lacks an option this
    release's report has, the report is an older one: everything else is
    still written, and the command ends with exit code 1.

    A file saved with 2.2.x holds a second section, "RAG Builder", for the
    report of that name that is retired. It is folded into the Prompt Builder
    section first, exactly as `mdb options-save` does it now, so its values
    reach the report that replaced it. The retired report is not written to
    and not deleted; when it still exists the command says so.
    """
    import json as _json

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
    with _viya_session() as session:
        failures = _restore_options(session, saved, dry_run, _site_host(), content_root=content_root or "")
    if failures:
        console.print(f"[red]Not every option {'would be' if dry_run else 'was'} restored.[/red] {file} "
                      "still holds all of them: fix what is reported above and run the command again.")
        raise typer.Exit(1)


def _site_host() -> str:
    """The host reports should point at: SAS_VIYA_URL, else the SAS Viya CLI profile's endpoint."""
    from .viya.session import resolve_auth
    url = os.environ.get("SAS_VIYA_URL", "")
    if not url:
        try:
            url = resolve_auth().url
        except Exception:
            url = ""
    return url.split("://", 1)[-1].strip().rstrip("/")


def _fold_saved(saved: dict) -> dict:
    """Saved options with any retired report's section folded into the merged
    one, saying what the fold did. Every path that restores goes through
    this, so a retired report's name never becomes a restore target."""
    from .core.options import fold_legacy
    folded, messages = fold_legacy(saved)
    for message in messages:
        console.print(f"[yellow]  {message}[/yellow]")
    return folded


def _warn_retired_reports(session, content_root: str = "") -> list[str]:
    """Say so when a retired report still exists on the site, at its place
    below the content root; returns the names found. Nothing is deleted: the
    report may be the only place a setting was ever written down, and
    removing content is an admin's call."""
    from .core.options import LEGACY_REPORTS, MERGED_REPORT, report_folder
    from .viya.reports import find_report_at
    found = [name for name in sorted(LEGACY_REPORTS)
             if find_report_at(session, report_folder(content_root, name), name)]
    for name in found:
        console.print(
            f"[yellow]A report named '{name}' still exists on this site. It is retired: the RAG Builder "
            f"is part of the {MERGED_REPORT} report since 2.3.0, and the old report keeps serving the old "
            f"standalone UI with its own options. Once the {MERGED_REPORT} report shows your RAG setups, "
            f"delete the report '{name}' and the job definition 'RAG-Builder-UI' next to it "
            "(SAS Environment Manager > Content, or SAS Drive). Nothing was deleted.[/yellow]")
    return found


def _restore_options(session, saved: dict, dry_run: bool, host: str = "",
                     arriving: Optional[dict] = None, content_root: str = "") -> int:
    """Write the saved option values back into the named reports and point
    every remaining placeholder host at `host`; returns the number of reports
    that could not be written, or not in full. Shared by options-restore and
    builders-import.

    A label the report lacks is named and not written. When it is an option
    of the merged report (MERGED_REPORT_LABELS) the report is an older one
    than the values were saved for, and it counts as not written in full:
    the command must not end with exit code 0 while a setting did not reach
    the site. What can be written is still written. A label no version of
    the report holds any more keeps its line and is no failure.

    `arriving` maps a report name to the option labels of the report a
    pending import brings ({name: set of labels}); builders-import passes it
    in a dry run, where the live report is still the one from before the
    import and lacks every option the new version adds. Those are summed up
    in one line instead of being reported as unknown one by one.

    A report is the one at its place below `content_root` - a copy of that
    name somewhere else is not written to.
    """
    from .core.options import MERGED_REPORT_LABELS, REPORT_FOLDERS, rehost_content, report_folder, write_options
    from .viya.reports import find_report_at, get_content, put_content
    failures = 0
    arriving = arriving or {}
    for report_name, values in _fold_saved(saved).items():
        values = values or {}
        folder = report_folder(content_root, report_name) if report_name in REPORT_FOLDERS else ""
        report = find_report_at(session, folder, report_name) if folder else None
        if not report:
            if report_name in arriving:
                then = f", and the {len(values)} saved option(s) are written then" if values else ""
                console.print(f"[cyan]{report_name}: not on this site yet - it arrives with the import"
                              f"{then}.[/cyan]")
            else:
                console.print(f"[yellow]{report_name}: no such report in {folder or 'the content folder'} - "
                              "skipped.[/yellow]")
            continue
        try:
            content, etag = get_content(session, report["id"])
        except Exception as exc:
            console.print(f"[red]{report_name}: reading the report failed ({exc}) - its options were not "
                          "written.[/red]", highlight=False)
            failures += 1
            continue
        updated, result = write_options(content, values)
        updated, rehosted = rehost_content(updated, host) if host else (updated, 0)
        with_import = [label for label in result.missing if label in arriving.get(report_name, ())]
        if with_import:
            console.print(f"[cyan]  {report_name}: {len(with_import)} option(s) arrive with the imported "
                          f"report and are written then: {', '.join(with_import)}[/cyan]")
        for label in result.missing:
            if label in with_import:
                continue
            console.print(f"[yellow]  {report_name}: '{label}' is not an option of "
                          "this report version - not written.[/yellow]")
        unwritten = [label for label in result.missing
                     if label not in with_import and label in MERGED_REPORT_LABELS]
        if unwritten:
            console.print(f"[red]{report_name}: {len(unwritten)} option(s) the Prompt Builder reads cannot "
                          f"be written ({', '.join(unwritten)}) - the report "
                          + ("the import brings" if report_name in arriving else "on this site")
                          + " is from before 2.3.0, or was not saved with the merged UI. Import the "
                          "package of this release (`mdb builders-import`), then restore again.[/red]")
        for label, value in result.rejected.items():
            console.print(f"[red]  {report_name}: '{label}' is a checkbox in this report and '{value}' says "
                          "neither on nor off - not written. Use true or false.[/red]")
        if unwritten or result.rejected:
            failures += 1
        if not result.changed and not rehosted:
            # "already matches" is only true of a report that took every value.
            if values and not unwritten and not result.rejected:
                console.print(f"[green]{report_name}: already matches "
                              f"({len(result.unchanged)} options, no placeholder host).[/green]")
            continue
        what = []
        if result.applied:
            what.append(f"{len(result.applied)} option(s): {', '.join(sorted(result.applied))}")
        if rehosted:
            what.append(f"placeholder host -> {host} ({rehosted} occurrence(s): the DDC URL and option defaults)")
        if dry_run:
            console.print(f"[cyan]{report_name}: would restore " + "; ".join(what) + "[/cyan]")
            continue
        try:
            put_content(session, report["id"], updated, etag)
        except Exception as exc:
            console.print(f"[red]{report_name}: writing the report failed ({exc}).[/red]")
            failures += 1
            continue
        console.print(f"[green]{report_name}: restored " + "; ".join(what) + "[/green]")
    _warn_retired_reports(session, content_root)
    return failures


def _discovered_seeds(session, ctx: "Context", otherwise: str = "", kinds=None) -> dict:
    """{report name: seed} from this environment: the repository id, the
    project id of every seed kind the report needs, the SCR endpoint and the
    deployment type.

    The kinds are discovered one by one, and one that fails is left out of
    the seed and named - with `otherwise` saying what happens instead: a
    missing embedding project must not cost the report its LLM project.
    A report for which no kind could be discovered gets no seed.
    """
    from .core.options import BUILDER_REPORTS
    from .viya.registry import builder_seed, ensure_repository_and_project
    responsible_party = os.environ.get("SAS_RESPONSIBLE_PARTY", "")
    deployment_type = os.environ.get("SAS_DEPLOYMENT_TYPE", "k8s")
    scr_endpoint = _scr_endpoint()
    seeds: dict = {}
    only = kinds
    for report_name, kinds in BUILDER_REPORTS.items():
        repository_id = None
        project_ids: dict = {}
        for kind in kinds:
            if only is not None and kind not in only:
                continue
            try:
                ensured = ensure_repository_and_project(session, kind, ctx.core, responsible_party)
            except Exception as exc:
                console.print(f"[yellow]{report_name}: could not discover the {kind} project ({exc})"
                              + (f"; {otherwise}" if otherwise else "") + ".[/yellow]")
                continue
            repository_id = repository_id or ensured.repository_id
            project_ids[kind] = ensured.project_id
        if project_ids:
            seeds[report_name] = builder_seed(repository_id, project_ids, scr_endpoint, deployment_type)
    return seeds


def _discovered_options(session, ctx: "Context") -> dict:
    """What `mdb options-save` would record on an environment with no tuned
    report: repository and project ids, the SCR endpoint and deployment type
    for the Builder report. The first-install values."""
    from .core.options import merge_seed
    return {name: merge_seed(seed, None) for name, seed in _discovered_seeds(session, ctx).items()}


def _content_manifest(ctx: "Context", check_sources: bool = True):
    """The content manifest of the checkout. A manifest that cannot be used
    ends the command with exit code 2 and one line per defect: nothing is
    deployed, exported or checked against a list that is wrong."""
    from .core.content import ManifestError, load_manifest
    try:
        return load_manifest(ctx.repo, check_sources=check_sources)
    except ManifestError as exc:
        console.print(f"[red]The content manifest cannot be used[/red] ({exc.where}):", highlight=False)
        for problem in exc.problems:
            console.print(f"  {problem}", markup=False, highlight=False, soft_wrap=True)
        raise typer.Exit(2)


def _package_objects(package: dict) -> list[tuple[str, str]]:
    """(type, name) of every object a package carries."""
    from .core.packages import package_objects
    return package_objects(package)


def _emit_outcome(outcome, indent: str = "  ") -> None:
    """One line for one entry of a deploy or of an import: what happened,
    the kind, the path and what there is to say about it."""
    note = f"  ({outcome.note})" if outcome.note else ""
    console.print(f"{indent}{outcome.status:<12} {outcome.entry.kind:<13} {outcome.entry.path}{note}",
                  markup=False, highlight=False, soft_wrap=True)


def _deploy_settings(content_root_given: Optional[str], compute_context: Optional[str]) -> dict:
    """The values a deploy or an import writes into every job definition that
    has the parameter, by its name: the compute context where one was given,
    and the content root the content goes to."""
    from .core.content import CONTENT_ROOT_PARAMETER, CONTEXT_PARAMETER
    settings: dict = {}
    if compute_context and compute_context.strip():
        settings[CONTEXT_PARAMETER] = compute_context.strip()
    if content_root_given:
        settings[CONTENT_ROOT_PARAMETER] = content_root_given.rstrip("/")
    return settings


@app.command("content-deploy")
def content_deploy(
    content_root: Optional[str] = typer.Option(
        None, "--content-root",
        help="SAS Content folder to bring to the state of the checkout (default: the content root the "
             "manifest names, /SAS Agentic AI Accelerator). Given, it is also written into the parameter "
             "contentRoot of the job definitions that have one."),
    only: Optional[list[str]] = typer.Option(
        None, "--only",
        help="Deploy only the entries at or below this path of the manifest (repeatable), e.g. --only RAG "
             "for the RAG runtime. Default: every entry."),
    to: Optional[str] = typer.Option(
        None, "--to",
        help="With one --only: where that part of the manifest goes instead of its place in the content root. "
             "For a custom step, a file or a job definition: the object a site has somewhere else - its SAS "
             "Content path, its id or its URI - which is updated in place and never created. For a folder of "
             "the manifest: the SAS Content folder its entries are deployed into."),
    compute_context: Optional[str] = typer.Option(
        None, "--compute-context",
        help="Write this compute context into the parameter _contextName of the job definitions that have "
             "one. Without it a job definition on the site keeps the context it has."),
    create: bool = typer.Option(
        False, "--create",
        help="Create the folder the content goes to (--content-root, or the folder form of --to) with every "
             "folder above it, where it is not there. Without it only the content root the manifest names is "
             "created; any other folder that is not there ends the command and nothing is written."),
    force: bool = typer.Option(
        False, "--force",
        help="With --to for one object: update it although its name on the site is not the name of the entry."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would change; write nothing."),
):
    """Bring a SAS Content folder to the state of this checkout, from the
    content manifest (SAS-Viya-Integrations/package-manifest.yaml).

    Entry by entry, and in place: a folder is created when it is missing; a
    file is uploaded when it is missing, and one whose bytes differ gets its
    content replaced - the file stays the object it was; a custom step is
    registered, and an existing one is updated so that it keeps its id and
    the flows that use it keep working; a job definition is created, or
    updated with its program, its form and its parameters. Two files are
    written by mdb itself: the release table as CSV, exactly as `mdb
    load-releases --csv` writes it, and release.json - the release, the commit
    of the checkout and its date, and every entry with its kind and, for a
    file, a custom step and a job definition, a hash of its content.

    One line per entry says what happened: equal, updated, created - or
    FAILED with the reason. Nothing is deleted, and nothing that is not in
    the manifest is touched; what else sits in the folders of the manifest is
    listed at the end. A report is not deployed: it reaches a site through
    `mdb builders-import`, and its line says whether it is on the site.

    THE RUN STOPS at the first write that fails - a refusal, or no answer,
    also for a folder that cannot be created - and says what was written
    before it; every entry behind it is listed as not attempted. Where the
    update of a file is refused, the file is left as it is. An entry that
    fails before anything was sent (two objects of its name in the folder, a
    reference to an object elsewhere, an object that cannot be read) is
    reported and the run goes on. Either way the exit code is 1, and a second
    run leaves what is equal by then as it is. release.json is written last
    and only when every entry in front of it was written: after a run that
    did not get there, the folder's release.json is the one an earlier run
    wrote, or there is none.

    SITE SETTINGS. A parameter the manifest marks `siteSetting` - the compute
    context `_contextName`, the CAS library, the credential domain and the
    other values of a site - gets its default in a new job definition and
    keeps the site's value in an existing one. --compute-context writes the
    context it is given. `contentRoot` is always written with the folder the
    content goes to: a job reads the content root it sits in.

    A FOLDER THAT IS NOT THERE. The content root the manifest names is
    created on a first install. Any other folder - a --content-root, the
    folder of `--only <folder> --to` - is created only with --create;
    without it the command says so and writes nothing.

    PARTS. With --only a part of the folder is deployed and the rest is not
    looked at: `--only RAG` is the RAG runtime. With --to that part goes
    somewhere else: `--only RAG --to "/Team/Retrieval"` deploys the RAG
    runtime into a RAG content root of any name (what the deploy-rag-content
    scripts run), and `--only "Logging and Monitoring/LLM - Log Parser.step"
    --to "/Custom Steps/LLM - Log Parser.step"` updates, in place, the step a
    site registered in a folder of its own - the step its flows reference.
    The line says which object that is: its name on the site and its folder.
    An object whose name is not the entry's is not written without --force.
    """
    from .core.content import CheckoutSource, part_of
    from .viya.content import (CREATED, EQUAL, FAILED, NOT_ATTEMPTED, NOT_DEPLOYED, UPDATED, DeployError, deploy,
                               deploy_to, selected)
    ctx = Context()
    manifest = _content_manifest(ctx)
    root = (content_root or manifest.content_root).rstrip("/")
    if not root.startswith("/"):
        console.print("[red]--content-root must be a SAS Content path, starting with a slash.[/red]")
        raise typer.Exit(2)
    only = [path.strip("/") for path in only or [] if path and path.strip("/")]
    for path in only:
        if not selected(manifest, [path])[1] and manifest.find(path, "folder") is None:
            console.print(f"--only {path}: the manifest lists nothing at or below that path.", style="red",
                          markup=False, highlight=False)
            raise typer.Exit(2)
    # the content root a job reads is the one it is deployed into - for the
    # folder of the manifest, not for a part of it somewhere else
    settings = _deploy_settings(root if to is None else None, compute_context)
    source = CheckoutSource(manifest)

    # --to: one part of the manifest, somewhere else
    target_entry = None
    if to is not None:
        to = to.strip()
        if len(only) != 1:
            console.print("[red]--to goes with exactly one --only: the entry, or the folder of the manifest, that "
                          "is deployed there.[/red]")
            raise typer.Exit(2)
        if content_root:
            console.print("[red]--to names the place itself; --content-root does not go with it.[/red]")
            raise typer.Exit(2)
        listed = [entry for entry in manifest.objects() if entry.path == only[0]]
        if listed and listed[0].kind == "report":
            console.print("[red]A report is not deployed: it reaches a site through `mdb builders-import`.[/red]")
            raise typer.Exit(2)
        if listed:
            target_entry = listed[0]
        elif not to.startswith("/"):
            console.print(f"--only {only[0]} is a folder of the manifest, so --to has to be the SAS Content folder "
                          "its entries go into, starting with a slash.", style="red", markup=False, highlight=False)
            raise typer.Exit(2)

    with _viya_session() as session:
        if target_entry is not None:
            console.print(f"{'Dry run - nothing is written. What a run would do to' if dry_run else 'Updating'} "
                          f"{to} from {target_entry.path}:", markup=False, highlight=False)
            outcome = deploy_to(session, manifest, target_entry, to, dry_run, source, settings, force)
            _emit_outcome(outcome)
            if outcome.status == FAILED:
                console.print("[red]Nothing was updated.[/red]")
                raise typer.Exit(1)
            console.print("Nothing was written." if dry_run else "[green]Done.[/green]")
            return
        if to is not None:
            deployed, root, only_shown = part_of(manifest, only[0], to), to.rstrip("/"), []
            console.print(f"{'Dry run - nothing is written. What a run would do to' if dry_run else 'Deploying to'} "
                          f"{root} (the entries below {only[0]} of the manifest):", markup=False, highlight=False)
        else:
            deployed, only_shown = manifest, only
            console.print(f"{'Dry run - nothing is written. What a run would do to' if dry_run else 'Deploying to'} "
                          f"{root}" + (f" (only {', '.join(only)})" if only else "") + ":",
                          markup=False, highlight=False)
        try:
            result = deploy(session, deployed, root, dry_run, _emit_outcome, only=only_shown, source=source,
                            settings=settings,
                            create_root=create or (to is None and root == manifest.content_root))
        except DeployError as exc:
            console.print(str(exc), style="red", markup=False, highlight=False)
            raise typer.Exit(1)
    if result.unlisted:
        console.print(f"\nNot in the manifest and left alone ({len(result.unlisted)}) - not deployed, and "
                      "`mdb package-export` leaves them out:", highlight=False)
        for line in result.unlisted:
            console.print(f"  {line}", markup=False, highlight=False, soft_wrap=True)
    counts = ", ".join(f"{result.count(status)} {status}"
                       for status in (EQUAL, UPDATED, CREATED, NOT_DEPLOYED, NOT_ATTEMPTED) if result.count(status))
    console.print(f"\n{len(result.outcomes)} entries: {counts or 'nothing done'}"
                  + (f", {len(result.failed)} FAILED" if result.failed else "") + ".", highlight=False)
    if result.stopped_at is not None:
        console.print(result.stop_line(), style="red", markup=False, highlight=False, soft_wrap=True)
    if result.failed:
        console.print("[red]Not every entry reached the folder.[/red] Fix what is reported above and run the "
                      "command again: what is equal by then is left as it is.")
        raise typer.Exit(1)
    if dry_run:
        console.print("Nothing was written.")
    else:
        console.print("[green]Done.[/green]")


@app.command("package-export")
def package_export(
    content_root: Optional[str] = typer.Option(
        None, "--content-root",
        help="SAS Content folder to export (default: the content root the manifest names, "
             "/SAS Agentic AI Accelerator). Any other folder needs --out."),
    name: Optional[str] = typer.Option(None, "--name", help="Package name (default: the folder's name)."),
    out: Optional[Path] = typer.Option(
        None, "--out", "-o",
        help="Where to write the package (default: the file the manifest names, "
             "SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json)."),
    keep: bool = typer.Option(False, "--keep", help="Leave the exported package and the record of the export job "
                                                    "on the server too."),
    allow_differences: bool = typer.Option(
        False, "--allow-differences",
        help="Write the package although it differs from the checkout or lacks an entry of the manifest - "
             "for a test folder that holds no report. Needs --out; the release file is never written that way."),
    timeout: int = typer.Option(600, "--timeout", help="Seconds to wait for the export job."),
):
    """Export the content folder to the one transfer package of the release.

    The folder is exported with its dependencies, and then reduced to what the
    content manifest lists: every other object is dropped with its entries in
    the package - a generated artifact, a report somebody parked in the
    folder, a dependency from outside it - and so is every authorization rule
    and share. In the Prompt Builder report the option defaults are reset to
    the neutral values in every copy of the report, the per-user states are
    removed, and the exporting environment's hostname becomes the placeholder,
    also inside the compressed report content.

    What would be written is then compared with the checkout
    (`mdb package-check --release` asks the same): every entry of the manifest
    is there and equals its source, and nothing else is. The command writes nothing and
    ends with exit code 1 - the file from before stays as it is - when a
    difference is left, when the package still names a host, or when an object
    sits on a path that never ships (Showcases, Example, a folder named
    generated, a name starting with ZZ_VERIFY), listed or not. Bring the
    folder to the checkout with `mdb content-deploy` first; a report that lacks
    an option of the current UI has to be saved once with that UI in SAS Visual
    Analytics.

    Of the files a report brings along, those stay that are children of the
    report and that its manifest entry lists; a picture the report shows from
    a folder of its own is left out, and the report is then a difference.

    What the export leaves on the server - the package and the record of the
    export job - is deleted unless --keep. A delete that is refused or gets
    no answer does not cost the file: it is still written and checked, a line
    names what is left with its id and how to delete it, and the command
    ends with exit code 3 (1 stays "nothing was written").

    The export names people: `createdBy` / `modifiedBy` of the package, of
    every object and inside the bodies, and `contentSourceLocation`, the path
    of the exported folder. They are left as the export wrote them - every
    package up to 2.2.x carried them, and that the transfer service imports a
    package without them was not shown - and the command says which user ids
    the file holds.
    """
    from .core.content import content_root_problem
    from .core.packages import (ALLOWED_HOSTS, check_text, describe_findings, filter_package, named_users,
                                package_differences, reset_report_defaults, sanitise_text,
                                strip_report_states)
    from .viya.transfer import (delete_job, delete_package, download_package, folder_by_path, job_messages,
                                leftover_line, start_export, wait_for_job)
    ctx = Context()
    manifest = _content_manifest(ctx)
    root = (content_root or manifest.content_root).rstrip("/")
    target = out or manifest.package_path
    if out is None and root != manifest.content_root:
        console.print(f"[red]{root} is not the content root of the release ({manifest.content_root}): an export "
                      f"of it is not written to {manifest.package}. Pass --out to write it somewhere else. "
                      "Nothing was exported.[/red]", highlight=False)
        raise typer.Exit(2)
    if allow_differences and (out is None or target.resolve() == manifest.package_path.resolve()):
        console.print(f"[red]--allow-differences needs --out, and not {manifest.package}: the release file is "
                      "only written when it equals the checkout. Nothing was exported.[/red]", highlight=False)
        raise typer.Exit(2)
    if content_root_problem(root):
        console.print(f"[red]{root}: {content_root_problem(root)}. Nothing was exported.[/red]", highlight=False)
        raise typer.Exit(2)
    package_name = name or root.rsplit("/", 1)[-1]
    with _viya_session() as session:
        try:
            folder_obj = folder_by_path(session, root)
            console.print(f"Exporting [bold]{root}[/bold] as package [bold]{package_name}[/bold] ...",
                          highlight=False)
            job = start_export(session, package_name, [f"/folders/folders/{folder_obj['id']}"])
            job = wait_for_job(session, next(l["href"] for l in job["links"] if l["rel"] == "self"), timeout=timeout)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        if str(job.get("state", "")).lower() != "completed":
            console.print(f"[red]The export job ended {job.get('state')} ({job.get('errorCount', 0)} error(s)).[/red]")
            for line in job_messages(session, job):
                console.print(f"  {line}")
            raise typer.Exit(1)
        package_uri = job["packageUri"]
        try:
            text = download_package(session, package_uri)
        except RuntimeError as exc:
            text = None
            console.print(f"{exc}", style="red", markup=False, highlight=False, soft_wrap=True)
        # What the export left on the server is deleted - the package and the
        # record of the export job. A delete that fails does not cost the
        # file: it is still written and checked, and the line names what is
        # left and how to delete it.
        leftovers = []
        if not keep:
            export_job = next((link.get("href") for link in job.get("links") or [] if link.get("rel") == "self"),
                              None) or f"/transfer/exportJobs/{job.get('id')}"
            for uri, what, delete in ((package_uri, "package", delete_package),
                                      (export_job, "export job record", delete_job)):
                try:
                    delete(session, uri)
                except RuntimeError as exc:
                    leftovers.append(f"{leftover_line(uri, what)} ({exc})")
    for line in leftovers:
        console.print(f"  {line}.", style="yellow", markup=False, highlight=False, soft_wrap=True)
    if text is None:
        console.print("[red]Nothing was written: the package could not be downloaded.[/red]")
        raise typer.Exit(1)
    parsed = json.loads(text)
    filtered = filter_package(parsed, manifest, f"/folders/folders/{folder_obj['id']}")
    for line in filtered.dropped:
        console.print(f"  left out: {line}", markup=False, highlight=False, soft_wrap=True)
    text = json.dumps(parsed, indent=2, ensure_ascii=False) + chr(10)
    try:
        text, reset = reset_report_defaults(text)
        text, states = strip_report_states(text)
        text, fixed = sanitise_text(text, target.name, ALLOWED_HOSTS)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    for copy_name, labels in reset.items():
        console.print(f"  reset {len(labels)} option default(s) of {copy_name} to the neutral values: "
                      f"{', '.join(labels)}", highlight=False)
    for report_name, n in states.items():
        console.print(f"  removed {n} per-user report state(s) from report '{report_name}'")
    for host, n in fixed.items():
        console.print(f"  replaced {host} with the placeholder ({n} occurrence(s))")
    # Everything below is asked of the text, before there is a file: the
    # target is the package `mdb builders-import` reads when none is named,
    # and a package that is written and then found unfit is imported by the
    # next run from this checkout, whatever the exit code said. What is
    # judged is what would be written.
    package = json.loads(text)
    unfit = False
    for line in filtered.refused:
        console.print(f"  never ships, although the manifest lists it: {line}", style="red", markup=False,
                      highlight=False, soft_wrap=True)
        unfit = True
    result = check_text(text, target.name, ALLOWED_HOSTS)
    if not result.ok:
        console.print(f"[red]The export still names a host after the rewrite: {describe_findings(result)}[/red]")
        unfit = True
    differences = [] if not result.ok else package_differences(package, manifest, target.name)
    for line in differences:
        console.print(f"  {line}", style="yellow" if allow_differences else "red", markup=False, highlight=False,
                      soft_wrap=True)
    if differences and allow_differences:
        console.print(f"[yellow]{len(differences)} difference(s) to the checkout - written all the same "
                      "(--allow-differences): this is not a package of the release.[/yellow]")
    elif differences:
        console.print(f"[red]{len(differences)} difference(s) between the export and the checkout.[/red] Run "
                      "`mdb content-deploy` to bring the folder to the checkout, and for a report open it in "
                      "SAS Visual Analytics with the current UI and save it; then export again.")
        unfit = True
    if unfit:
        console.print("[red]Nothing was written: " + (
            f"{target.name} is as it was before the export." if target.exists() else
            f"there is no {target.name}.") + "[/red]", highlight=False)
        raise typer.Exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    counts: dict = {}
    for kind, _name in _package_objects(package):
        counts[kind] = counts.get(kind, 0) + 1
    console.print(f"[green]Wrote {target}[/green] ({package.get('transferObjectCount', '?')} objects: "
                  + ", ".join(f"{n} {kind}" for kind, n in counts.items()) + f"; {len(filtered.dropped)} left out).",
                  highlight=False)
    users = named_users(package)
    if users:
        console.print(f"  The file names the user id(s) {', '.join(sorted(users))} in createdBy / modifiedBy "
                      f"({sum(users.values())} place(s): the package, its objects and their bodies), and the "
                      "exported folder in contentSourceLocation. They are left as the export wrote them.",
                      markup=False, highlight=False, soft_wrap=True)
    # What to do with the file depends on what it is: only the release file,
    # written without a difference, is something to commit.
    if target.resolve() == manifest.package_path.resolve():
        # The changelog is finished before the deploy: release.json and the
        # release table were made from this checkout, and a changelog edit
        # after the export makes `mdb package-check --release` fail.
        console.print("Run `mdb package-check --release`, then commit this file - and nothing else with it: the "
                      "changelog and every source the manifest names stay as they were at the deploy (release.json "
                      "names that commit, and the release table was made from that changelog). Tag the commit that "
                      "adds the file.", markup=False, highlight=False, soft_wrap=True)
    elif differences:
        console.print("Keep this file out of the repository. `mdb builders-import "
                      f"{_shell_word(target)} --content-root <folder> --no-reports` brings what it holds into a "
                      "test folder.", markup=False, highlight=False, soft_wrap=True)
    else:
        console.print(f"The file equals the checkout and is not the release file ({manifest.package}): there is "
                      "nothing to commit.", markup=False, highlight=False, soft_wrap=True)
    if leftovers:
        console.print(f"[yellow]{target.name} was written and checked; {len(leftovers)} object(s) of the export are "
                      "left on the server (named above). Exit code 3.[/yellow]", highlight=False)
        raise typer.Exit(3)


#: Seconds `mdb builders-import` waits for one import job unless told otherwise.
IMPORT_TIMEOUT = 600


#: An argument no shell reads anything into, and what double quotes do not
#: keep as it is: PowerShell and bash expand `$` and read a backtick, bash
#: reads `\` and (at a prompt) `!`, and neither spells a `"` the other's way.
_BARE_WORD = re.compile(r"[A-Za-z0-9_./:-]+")
_NOT_KEPT_IN_DOUBLE_QUOTES = frozenset('"$`!\\')


def _shell_word(value, windows: bool = os.name == "nt") -> str:
    """A command-line argument as it can be pasted into PowerShell or bash,
    whichever the admin is in: bare when it holds nothing a shell reads, else
    in double quotes (cmd reads those too), in single quotes when it holds a
    character double quotes do not keep. A path is spelled with forward
    slashes, which Windows reads as well and bash does not eat.

    One case has no spelling both shells read: a value with a single quote
    and one of those characters. It is quoted for the platform's own shell
    (PowerShell on Windows), so Git Bash on Windows does not read it back. A
    single-quoted word is not a quoted word in cmd at all.
    """
    text = value.as_posix() if isinstance(value, PurePath) else str(value)
    if _BARE_WORD.fullmatch(text):
        return text
    if not _NOT_KEPT_IN_DOUBLE_QUOTES.intersection(text):
        return f'"{text}"'
    return "'" + text.replace("'", "''" if windows else "'\\''") + "'"


def _import_command(packages, options, caslib, table, server, no_releases: bool, timeout: int,
                    report_caslib=None, content_root=None, compute_context=None, no_reports: bool = False,
                    no_capture: bool = False, create: bool = False) -> str:
    """`mdb builders-import` with the arguments a run was given, for the line
    that tells an admin to run it again. An argument that was not given is
    left out, so the repeat reads the same environment the first run did -
    and so is an empty --caslib, --report-caslib, --table or --server, which
    the run reads as not given."""
    words = ["mdb", "builders-import", *(_shell_word(path) for path in packages or [])]
    if options is not None:
        words += ["--options", _shell_word(options)]
    for flag, value in (("--caslib", caslib), ("--report-caslib", report_caslib), ("--table", table),
                        ("--server", server), ("--content-root", content_root),
                        ("--compute-context", compute_context)):
        if value:
            words += [flag, _shell_word(value)]
    if no_releases:
        words.append("--no-releases")
    if no_reports:
        words.append("--no-reports")
    if no_capture and options is None:
        words.append("--no-options-capture")
    if create:
        words.append("--create")
    if timeout != IMPORT_TIMEOUT:
        words += ["--timeout", str(timeout)]
    return " ".join(words)


def _live_options(session, content_root: str = "") -> dict:
    """{report name: {option: value}} as the Builder report at its place
    below `content_root` holds them now - a retired report folded in, as
    `mdb options-save --reports-only` records them. Empty when the site has
    neither report there: then there is nothing an import could overwrite.

    Raises _ReportUnreadable when a report is there and cannot be read or
    holds no option, or when the service does not say whether it is there:
    an import behind that would replace a report whose options nobody
    holds."""
    from .core.options import BUILDER_REPORTS, LEGACY_REPORTS, report_folder
    from .viya.reports import find_report_at
    on_site = False
    for name in (*BUILDER_REPORTS, *sorted(LEGACY_REPORTS)):
        folder = report_folder(content_root, name)
        try:
            on_site = bool(find_report_at(session, folder, name, strict=True)) or on_site
        except Exception as exc:
            raise _ReportUnreadable(name, f"whether it is in {folder} could not be asked ({exc})") from exc
    return _capture_options(session, {}, strict=True, content_root=content_root) if on_site else {}


def _keep_captured(captured: dict) -> Path:
    """Write options captured before an import to a file of their own in the
    current directory and return its path.

    The import replaces the report, so from that moment the captured values
    exist only in this process. A run that stops between the import and the
    write-back would leave a site on the package's defaults with nothing to
    restore from - and a second run would capture those defaults. The file is
    what `mdb options-restore --file` and `mdb builders-import --options`
    read. Its name carries the time, so no run overwrites another's.
    """
    import datetime as _datetime
    from .core.options import options_file
    stamp = _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = Path(f"builder-options-captured-{stamp}.json")
    counter = 1
    while path.exists():
        counter += 1
        path = Path(f"builder-options-captured-{stamp}-{counter}.json")
    path.write_text(json.dumps(options_file(os.environ.get("SAS_VIYA_URL", ""), captured), indent=2) + "\n",
                    encoding="utf-8")
    return path


def _transfer_refusal(package_root: Optional[str], target_root: str) -> Optional[str]:
    """Why the reports of a package are NOT handed to the transfer service
    for this content root, or None when they are.

    The transfer service places an object by the folders the package holds,
    and takes no target folder. Measured on 2026-10-12: a package whose root
    folder was not at the root of SAS Content landed in a NEW folder of that
    name at the root, and the folder it was meant for stayed empty. The one
    mapping known to work is the one every release up to 2.2.x used: a root
    folder at the root of SAS Content, imported into the folder of the same
    path. Anything else is not guessed."""
    if package_root is None:
        return "the package does not say which folder it was exported from"
    if package_root.count("/") != 1:
        return (f"the package was exported from {package_root}, a folder that is not at the root of SAS Content: "
                "the transfer service puts such a package into a new folder at the root of SAS Content "
                f"(measured), not into {target_root}")
    if package_root != target_root:
        return (f"the package was exported from {package_root} and its content goes to {target_root}: the "
                "transfer service imports a report into the folder it was exported from, and no mapping to "
                "another folder is known to work")
    return None


def _missing_ids(captured: dict) -> list:
    """The repository and project id options the captured Prompt Builder
    section holds no value for, in the order of the report."""
    from .core.options import MERGED_REPORT, SEED_PROJECT_LABELS
    section = captured.get(MERGED_REPORT) or {}
    labels = ["modelRepositoryID", *SEED_PROJECT_LABELS.values()]
    return [label for label in labels if not str(section.get(label) or "").strip()]


def _discover_missing(session, ctx: "Context", captured: dict, missing: list) -> dict:
    """Fill the ids `missing` names into the captured section from discovery -
    the kinds whose project id is missing, and the repository with them. A
    value the report holds is never replaced. Returns {label: value} of what
    was filled."""
    from .core.options import BUILDER_REPORTS, MERGED_REPORT, SEED_PROJECT_LABELS
    kinds = [kind for kind, label in SEED_PROJECT_LABELS.items() if label in missing] \
        or list(BUILDER_REPORTS[MERGED_REPORT][:1])
    seed = _discovered_seeds(session, ctx, "the option stays as the imported report has it",
                             kinds=kinds).get(MERGED_REPORT, {})
    section = captured.setdefault(MERGED_REPORT, {})
    filled = {label: seed[label] for label in missing if str(seed.get(label) or "").strip()}
    section.update(filled)
    return filled


def _known_context(saved: Optional[dict]) -> str:
    """The compute context a site's options name for jobs that run as the
    user who starts them: the option `ragComputeContext` of the Prompt
    Builder report (with what a retired report held folded in). Empty when
    the options name none.

    Not `computeContext`: that is the context of the optimization job, which
    a site commonly points at the SAS Job Execution compute context - a
    context that runs under a service account, in which Start Here was
    measured to check nothing."""
    from .core.options import MERGED_REPORT, fold_legacy
    if not saved:
        return ""
    folded, _messages = fold_legacy(json.loads(json.dumps(saved)))
    return str((folded.get(MERGED_REPORT) or {}).get("ragComputeContext") or "").strip()


@app.command("builders-import")
def builders_import(
    packages: Optional[list[Path]] = typer.Argument(
        None, help="Transfer package files (default: the one package this repository ships, "
                   "SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json)."),
    options: Optional[Path] = typer.Option(
        None, "--options",
        help="A builder-options.json from `mdb options-save` to write back after the import. Without it the "
             "options of the Prompt Builder report on the site are captured before the import and written back."),
    caslib: Optional[str] = typer.Option(None, "--caslib", "-l", help="CAS library of the release table (env: SAS_CAS_LIBRARY)."),
    report_caslib: Optional[str] = typer.Option(
        None, "--report-caslib",
        help="CAS library of the other tables the reports read (LLM_LOGS, the fact sheets, PROMPT_EXPERIMENTS), "
             "where a site does not keep them in the library the package names. Default: left as exported."),
    table: Optional[str] = typer.Option(None, "--table", "-t", help="Release table name (env: SAS_RELEASES_TABLE)."),
    server: Optional[str] = typer.Option(None, "--server", help="CAS server (env: SAS_CAS_SERVER)."),
    content_root: Optional[str] = typer.Option(
        None, "--content-root",
        help="SAS Content folder the files, custom steps and job definitions of the package are brought into "
             "(default: the content root the manifest names). The reports are imported only into the folder "
             "the package was exported from, at the root of SAS Content."),
    compute_context: Optional[str] = typer.Option(
        None, "--compute-context",
        help="Compute context written into the parameter _contextName of every job definition that has one. "
             "Without it a job definition the site has keeps its context, and one the import creates gets the "
             "ingestion compute context of the options that are written back (else the package's)."),
    create: bool = typer.Option(
        False, "--create",
        help="Create the --content-root folder (with every folder above it) where it is not there. Without it "
             "only the content root the manifest names is created."),
    no_releases: bool = typer.Option(False, "--no-releases", help="Do not (re)load the release table first."),
    no_reports: bool = typer.Option(
        False, "--no-reports",
        help="Leave the reports of the package out: the transfer service is not called at all."),
    no_options_capture: bool = typer.Option(
        False, "--no-options-capture",
        help="Do not read the options of the Prompt Builder report on the site before the import. The report "
             "the package brings is then configured from this deployment, and what an administrator set is gone."),
    out: str = typer.Option(".", "--out", help="Directory for the authorization-rules file of the content folder."),
    no_files: bool = typer.Option(False, "--no-files", help="Do not write the authorization-rules file."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Say what a run would do; every request but the sign-in is a GET."),
    timeout: int = typer.Option(IMPORT_TIMEOUT, "--timeout", help="Seconds to wait for each import job."),
):
    """Bring the accelerator's content folder into this deployment from the
    one transfer package: load the release table, import the reports, bring
    the files, custom steps and job definitions in place, write the Prompt
    Builder options back, and write the authorization commands for the folder.

    THE RELEASE TABLE is the package's Data/ACCELERATOR_RELEASES.csv where
    the package holds it - the table then says what was imported - else it
    is made from the changelog of this checkout. What the command still needs
    the accelerator clone for: mdb finds its repository first (every command
    does), the manifest gives the default package file and content root and
    says which parameters of a listed job definition are site settings, a
    site without the Prompt Builder report is configured by discovery from
    the model definitions, and a package without the release table takes it
    from the changelog.

    TWO WAYS IN, by kind of object. The REPORTS, with the files that are
    children of a report (its pictures) and the folders they sit in, go
    through the transfer service: the Prompt Builder report's data source is
    retargeted at SAS_CAS_LIBRARY.SAS_RELEASES_TABLE (the package binds
    Public.ACCELERATOR_RELEASES), with --report-caslib the other tables the
    reports read are pointed at the tables of the same name in that library,
    and the placeholder host in the DDC URL becomes SAS_VIYA_URL - all through
    the import mapping. EVERY FILE, CUSTOM STEP AND JOB DEFINITION of the
    package is brought IN PLACE, by the requests `mdb content-deploy` sends,
    with the package as the source and no checkout needed for the content: an
    object that exists at its path is updated under the id it has on this
    site, whatever id the package carries, and a missing one is created. One
    line per entry says equal, updated, created or FAILED. That is not left
    to the transfer service because it was measured to leave a file alone
    that exists in the folder under the same name with another id, to write
    the package's file into no folder, and to report its job as completed.

    WHERE. --content-root names the folder for everything but the reports. A
    report is imported only into the folder the package was exported from,
    and only when that folder is at the root of SAS Content - the one mapping
    known to work. Otherwise the reports are not imported and the command
    says so (and ends with exit code 1 unless --no-reports says that is
    meant): the transfer service takes no target folder, and a package from a
    folder below others was measured to land in a new folder at the root.

    WHAT A SITE SEES whose objects were registered under other ids - a step
    its flows use, a file, a job definition: `updated ... (id <its own id>
    kept)` where the content differs, `equal ... (id <its own id>)` where it
    does not, and no second object of the name. A file keeps its id as well:
    its content is replaced, the file is not deleted. Two objects of one name
    in a folder are both named and neither is written.

    THE OPTIONS of the Prompt Builder report are kept across the import. With
    --options the values of that file are written back. Without it, and when
    the report (or the retired report "RAG Builder") is at its place in the
    content root - <root>/Prompt Builder/Prompt Builder, <root>/RAG/RAG
    Builder; a report of that name elsewhere is not read and not written -
    its options are read before the import, kept in a file
    builder-options-captured-<time>.json in the current directory, and
    written back afterwards - so an upgrade is this one command. When such a
    report is there and cannot be read, or is read and holds no option at
    all, the command stops BEFORE anything is imported: name the options with
    --options <file>, or pass --no-options-capture to import without them. On a site without the report
    the values `mdb setup` discovers are written, and either way every
    remaining placeholder host in the report becomes this deployment's host.
    A package that brings no Prompt Builder report leaves the report on the
    site as it is.

    THE COMPUTE CONTEXT. Start Here and Load-Accelerator-Tables have to run
    as the user who opens them, so their job definitions carry _contextName.
    A context a site set in a job definition - in these two or in
    SAS-LLM-Prompt-Builder-UI - survives the import: an existing value is
    left alone. Only a job definition the import CREATES gets the ingestion
    compute context (ragComputeContext) of the options it writes back, else
    the package's. --compute-context writes the context it names into every
    job definition that has the parameter. contentRoot is written with the
    folder the content goes to.

    A RETIRED REPORT. The import removes nothing: the report "RAG Builder"
    and the job definition RAG-Builder-UI of a 2.2.x site stay where they
    are, the options of the report are folded into the Prompt Builder's, and
    the command says that the report is still there until someone deletes it.

    A DRY RUN writes nothing and uploads nothing. The requests it can make,
    all of them GET (the sign-in aside): /casManagement/servers;
    /folders/folders/@item and /folders/folders/{id}/members (also to find
    a report as a member of its folder); /reports/reports/{id}/content;
    /files/files/{id}/content; /dataFlows/steps/{id};
    /jobDefinitions/definitions/{id}. The transfer
    service is not called, so the mapping lines of a dry run are what the
    package itself says about its tables and substitutions. Without options
    it discovers nothing, because discovery creates a missing repository or
    project.

    The package carries no authorization rule. Like `mdb setup` for the model
    repository, the command writes the rules for the content folder as
    commands to review - sas-viya-cli-content-commands.txt in --out - and runs
    none of them.

    A run that did not do everything ends with exit code 1 and the command to
    repeat. The tasks of the import job are read to the end: one that ended
    with a warning or an error is printed and counts. The in-place part stops
    at the first write that fails and lists what it did not attempt.
    """
    from .core.content import CONTEXT_PARAMETER
    from .core.options import BUILDER_REPORTS
    from .core.packages import (PackageSource, legacy_objects, mapping_preview, report_labels,
                                reports_package)
    from .core.packages import RELEASE_TABLE_PATH
    from .core.releases import DEFAULT_TABLE, current_words
    from .viya.cas import resolve_server
    from .viya.content import CREATED, EQUAL, NOT_ATTEMPTED, UPDATED, DeployError, deploy
    from .viya.transfer import (delete_package, get_mapping, host_of, job_messages, job_tasks, job_uri,
                                leftover_line, rehost_mapping, start_import, table_spec, task_problems,
                                upload_package_bytes, wait_for_job)
    ctx = Context()
    manifest = _content_manifest(ctx, check_sources=False)
    files = [Path(p) for p in packages] if packages else [manifest.package_path]
    for path in files:
        if not path.is_file():
            if packages:
                console.print(f"[red]{path} not found.[/red]", highlight=False)
            else:
                console.print(f"[red]This checkout holds no {manifest.package} - the transfer package of the "
                              "release. Name a package file, or use a checkout that has it.[/red]", highlight=False)
            raise typer.Exit(2)
    root = (content_root or manifest.content_root).rstrip("/")
    if not root.startswith("/"):
        console.print("[red]--content-root must be a SAS Content path, starting with a slash.[/red]")
        raise typer.Exit(2)
    saved = None
    if options is not None:
        if not options.is_file():
            console.print(f"[red]{options} not found - run `mdb options-save` first.[/red]")
            raise typer.Exit(2)
        saved = json.loads(options.read_text(encoding="utf-8")).get("reports") or {}

    def command(options_file) -> str:
        """The command line as it was given, for the line that says how to
        go on - with the options file a run kept, once there is one."""
        return _import_command(packages, options_file, caslib_given, table, server, no_releases, timeout,
                               report_caslib, content_root, compute_context, no_reports, no_options_capture,
                               create)

    caslib_given = caslib
    given = command(options)
    caslib = caslib or os.environ.get("SAS_CAS_LIBRARY") or "Public"
    table_name = table or _releases_table()
    failures = 0
    # What each package's reports hold, and which reports sat in a package
    # that did not import: the restore below is decided per report, not per run.
    arriving: dict = {}
    failed_reports: set = set()
    not_imported: list = []
    imported_any = False
    captured_file: Optional[Path] = None
    # Per package: what it holds, and whether its reports go to the transfer
    # service (`refusal` says why not).
    plans = []
    for path in files:
        package = json.loads(path.read_text(encoding="utf-8"))
        source = PackageSource(package, manifest)
        refusal = None
        if source.reports:
            refusal = "left out (--no-reports)" if no_reports else _transfer_refusal(source.root_path, root)
        plans.append((path, package, source, refusal))
    with _viya_session() as session:
        host = host_of(os.environ.get("SAS_VIYA_URL") or getattr(session, "hostname", "") or "")
        server_name = resolve_server(session, server or os.environ.get("SAS_CAS_SERVER"))
        releases_to = table_spec(server_name, caslib, table_name)
        # The site's options, read while the report that holds them is still
        # the site's own: the import replaces it. Only when a package brings a
        # Builder report through the transfer service - anything else
        # overwrites no report.
        brings_report = any(name in BUILDER_REPORTS for _path, package, source, refusal in plans
                            if source.reports and refusal is None for name in report_labels(package))
        if saved is None and brings_report and no_options_capture:
            console.print("Options: not read from the report on this site (--no-options-capture). The report the "
                          "import brings is configured from this deployment.")
        elif saved is None and brings_report:
            try:
                live = _live_options(session, root)
            except _ReportUnreadable as exc:
                console.print(f"[red]{exc}.[/red]", highlight=False)
                console.print("[red]Nothing was imported.[/red] The import replaces this report, and options that "
                              "cannot be read now are lost with it. Run the command again when the report can be "
                              "read - or name the options yourself with --options <file> (`mdb options-save` "
                              "writes one), or pass --no-options-capture to import without keeping them.")
                raise typer.Exit(1)
            if live:
                saved = live
                # a project id the report holds no value for (a 2.2.x site
                # without the retired report has no embedding project in its
                # Prompt Builder report): written back empty, it would empty
                # the imported report's value. It is discovered instead.
                missing = _missing_ids(live)
                if missing and dry_run:
                    console.print(f"Options: the report holds no value for {', '.join(missing)} - a run discovers "
                                  "it from this deployment (which creates a missing project).", highlight=False)
                elif missing:
                    found = _discover_missing(session, ctx, live, missing)
                    console.print(f"Options: the report holds no value for {', '.join(missing)} - discovered from "
                                  "this deployment: " + (", ".join(f"{k} {v}" for k, v in found.items()) or "nothing")
                                  + ".", highlight=False)
                if dry_run:
                    console.print("Options: read from the report on this site; a run keeps them in a file and "
                                  "writes them back after the import.")
                else:
                    captured_file = _keep_captured(live)
                    given = command(captured_file)
                    console.print(f"Options: read from the report on this site and kept in {captured_file}; "
                                  "they are written back after the import.", highlight=False)
        # The compute context for the job definitions: what was given goes
        # into every one that has the parameter; what the options that are
        # written back name goes only into one this run creates - a context a
        # site set survives the import. The content root is the folder the
        # content goes to.
        given_context = (compute_context or "").strip()
        known = "" if given_context else _known_context(saved)
        settings = _deploy_settings(root, given_context)
        new_values = {CONTEXT_PARAMETER: known} if known else {}
        if given_context:
            console.print(f"Compute context: {given_context} (--compute-context) - written into _contextName of "
                          "every job definition that has it.", markup=False, highlight=False)
        elif known:
            console.print(f"Compute context: {known} (the ingestion compute context of the options) - given to a "
                          "job definition this run creates; one the site has keeps its _contextName.",
                          markup=False, highlight=False)
        # The release table is the one the package holds, where it holds one:
        # the table then says what was imported, not what this checkout's
        # changelog says now.
        table_csv, table_from = None, "the changelog of this checkout"
        for path, _package, source, _refusal in plans:
            table_csv = source.release_table()
            if table_csv is not None:
                table_from = f"{RELEASE_TABLE_PATH} of {path.name}"
                break
        if no_releases:
            console.print(f"Release table: not reloaded (--no-releases); reports bind to {releases_to}.")
        elif dry_run:
            console.print(f"Release table: would load {caslib}.{table_name} on {server_name} from {table_from}.",
                          highlight=False)
        else:
            try:
                summary = _load_releases(session, ctx, caslib, server_name, table_name, csv_data=table_csv)
                console.print(f"Release table: {caslib}.{table_name} loaded from {table_from} ({summary['rows']} "
                              f"rows, {current_words(summary)}).", highlight=False)
            except (RuntimeError, ValueError, UnicodeDecodeError) as exc:
                console.print(f"[red]Release table not loaded: {exc}[/red]")
                raise typer.Exit(1)
        for path, package, source, refusal in plans:
            objects = _package_objects(package)
            counts: dict = {}
            for kind, _name in objects:
                counts[kind] = counts.get(kind, 0) + 1
            console.print(f"\n[bold]{path.name}[/bold] - {package.get('name')}: "
                          + ", ".join(f"{n} {kind}" for kind, n in counts.items()), highlight=False)
            retired = legacy_objects(package)
            if retired:
                console.print(f"  [yellow]This package predates 2.3.0: it carries {', '.join(retired)} - the "
                              "standalone RAG UI, which is part of the Prompt Builder now. Importing it brings "
                              "that UI back, and mdb does not configure its report. Use the package of a "
                              "2.3.0 checkout.[/yellow]")
            for line in source.left_out:
                console.print(f"  not imported: {line}", markup=False, highlight=False, soft_wrap=True)
            for line in source.notes:
                console.print(f"  note: {line}", style="yellow", markup=False, highlight=False, soft_wrap=True)
            package_failed = False
            if not source.reports and not source.has_content:
                console.print("  Nothing of this package can be imported: no report, file, custom step or job "
                              "definition of it sits below a root folder.", style="red", markup=False,
                              highlight=False)
                package_failed = True

            # ---- the reports: through the transfer service, or not at all ----
            names = ", ".join(item.path for item in source.reports)
            carried = report_labels(package) if source.reports and refusal is None else {}
            arriving.update(carried)
            if not source.reports:
                pass
            elif refusal is not None:
                console.print(f"  Reports ({names}): NOT imported - {refusal}.",
                              style=None if no_reports else "red", markup=False, highlight=False, soft_wrap=True)
                if not no_reports:
                    console.print("    Import the package into the folder it was exported from, or pass "
                                  "--no-reports to bring everything but the reports.", markup=False,
                                  highlight=False)
                    package_failed = True
            else:
                reduced, held = reports_package(package, source.root_uri)
                kinds: dict = {}
                for line in held:
                    kinds[line.split(" ", 1)[0]] = kinds.get(line.split(" ", 1)[0], 0) + 1
                holds = ", ".join(f"{n} {kind}" for kind, n in kinds.items())
                console.print(f"  Reports, through the transfer service ({holds}): {names}", markup=False,
                              highlight=False, soft_wrap=True)
                if dry_run:
                    _mapping, changes = rehost_mapping(mapping_preview(reduced), host, DEFAULT_TABLE, releases_to,
                                                       library=report_caslib or None, server=server_name)
                    for change in changes:
                        console.print(f"    mapping: {change}")
                else:
                    uploaded = None
                    try:
                        uploaded = upload_package_bytes(
                            session, path.name, (json.dumps(reduced, ensure_ascii=False) + "\n").encode("utf-8"))
                        mapping = get_mapping(session, uploaded["id"])
                        mapping, changes = rehost_mapping(mapping, host, DEFAULT_TABLE, releases_to,
                                                          library=report_caslib or None, server=server_name)
                        for change in changes:
                            console.print(f"    mapping: {change}")
                        job = start_import(session, package.get("name") or path.stem,
                                           f"/transfer/packages/{uploaded['id']}", mapping)
                        job = wait_for_job(session, job_uri(job), timeout=timeout)
                        state = str(job.get("state", "")).lower()
                        if state != "completed" or job.get("errorCount"):
                            console.print(f"    [red]import ended {job.get('state')} with "
                                          f"{job.get('errorCount', 0)} error(s):[/red]")
                            for line in job_messages(session, job):
                                console.print(f"      {line}", markup=False, highlight=False, soft_wrap=True)
                            package_failed = True
                        else:
                            # the job says it ran; its tasks say what became of each object
                            unfinished = task_problems(job_tasks(session, job))
                            if unfinished:
                                console.print(f"    [red]the import job ended completed, and {len(unfinished)} of "
                                              "its tasks did not:[/red]")
                                for line in unfinished:
                                    console.print(f"      {line}", markup=False, highlight=False, soft_wrap=True)
                                package_failed = True
                            else:
                                console.print(f"    [green]imported {len(held)} object(s) through the transfer "
                                              "service.[/green]")
                    except RuntimeError as exc:
                        console.print(f"    [red]{exc}[/red]", highlight=False)
                        package_failed = True
                    if uploaded and uploaded.get("id"):
                        # the uploaded part of the package is not left on the
                        # server; the import job keeps its own record
                        package_uri = f"/transfer/packages/{uploaded['id']}"
                        try:
                            delete_package(session, package_uri)
                        except RuntimeError as exc:
                            console.print(f"    {leftover_line(package_uri)} ({exc}).", style="yellow",
                                          markup=False, highlight=False, soft_wrap=True)
                    if package_failed:
                        failed_reports.update(carried)

            # ---- everything else: in place, with the package as the source ----
            if source.has_content:
                console.print(f"  Files, custom steps and job definitions, in place in {root}:", markup=False,
                              highlight=False)
                try:
                    result = deploy(session, source.manifest(root), root, dry_run,
                                    lambda outcome: _emit_outcome(outcome, "    "), source=source,
                                    settings=settings, reports=False, new_values=new_values,
                                    create_root=create or root == manifest.content_root)
                except DeployError as exc:
                    console.print(f"    {exc}", style="red", markup=False, highlight=False, soft_wrap=True)
                    package_failed = True
                else:
                    tally = ", ".join(f"{result.count(status)} {status}"
                                      for status in (EQUAL, UPDATED, CREATED, NOT_ATTEMPTED) if result.count(status))
                    console.print(f"    {len(result.outcomes)} entries: {tally or 'nothing done'}"
                                  + (f", {len(result.failed)} FAILED" if result.failed else "") + ".",
                                  highlight=False)
                    if result.stopped_at is not None:
                        console.print(f"    {result.stop_line()}", style="red", markup=False, highlight=False,
                                      soft_wrap=True)
                    if result.unlisted:
                        console.print(f"    Not in the package and left alone ({len(result.unlisted)}): "
                                      + "; ".join(result.unlisted), markup=False, highlight=False, soft_wrap=True)
                    if result.failed:
                        package_failed = True
            if dry_run:
                console.print("  [cyan]dry run - nothing imported.[/cyan]")
            if package_failed:
                failures += 1
                not_imported.append(path.name)
            elif not dry_run:
                imported_any = True
        # The Builder reports this run brings (in a dry run: would bring).
        # Discovery configures those and no other: a report that was not
        # imported is the site's own, with values an admin may have tuned,
        # and a package without the report must not rewrite it.
        targets = [name for name in BUILDER_REPORTS if name in arriving and name not in failed_reports]
        if saved is not None and options is None and dry_run:
            # Read from the report itself a moment ago: comparing the report
            # with its own values would say "already matches" about nothing.
            values = {}
        elif saved is not None:
            if options is not None:
                console.print(f"\nRestoring builder options from {options}:", highlight=False)
            else:
                console.print("\nWriting back the options the report held before the import"
                              + (f" ({captured_file}):" if captured_file else ":"), highlight=False)
            values = _fold_saved(saved)
        elif not targets:
            # Nothing arrived that discovery could configure, so nothing is
            # discovered either: discovery creates a missing project.
            values = {}
            console.print(f"\nNo Builder report {'would be' if dry_run else 'was'} imported - the options of "
                          "a report on this site are left as they are.")
        elif dry_run:
            # Discovery writes: it creates a missing repository or project.
            # A dry run says what would be configured and looks nothing up.
            console.print("\nWould configure the imported report from this deployment (repository, projects, "
                          "SCR endpoint, deployment type) and the site host.")
            values = {name: {} for name in targets}
        else:
            console.print("\nConfiguring the imported report from this deployment (repository, projects, "
                          "SCR endpoint, deployment type) and the site host:")
            # A report nothing was discovered for still gets the site host.
            discovered = _discovered_options(session, ctx)
            values = {name: discovered.get(name, {}) for name in targets}
        if dry_run:
            if arriving:
                console.print("  [cyan](dry run: nothing is imported, so this compares with the reports as they "
                              "are on the site now)[/cyan]")
            failures += _restore_options(session, values, dry_run, host, arriving, content_root=root)
        else:
            # A report whose package did not import is left alone: what is on
            # the site is the report from before, and it has its values.
            for report_name in values:
                if report_name in failed_reports:
                    console.print(f"[yellow]{report_name}: its package did not import - options not "
                                  "restored.[/yellow]")
            failures += _restore_options(
                session, {name: v for name, v in values.items() if name not in failed_reports}, dry_run, host,
                content_root=root)
        if imported_any and not no_files:
            _write_content_rules(session, root, Path(out))
    kept = options if options is not None else captured_file
    if failures:
        if not dry_run:
            # The command that finishes the job. `mdb options-restore` writes
            # options and imports nothing, so it is only the answer when every
            # package imported and the options were not all written.
            refused = any(source.reports and refusal is not None for _p, _pk, source, refusal in plans)
            if saved is None and not arriving:
                # no Builder report was imported, so the repeat imports and configures none either
                again = given
                does = ("it brings again what did not arrive in place; "
                        + ("with --no-reports no report is imported or configured" if no_reports else
                           "the reports are imported only where the line above allows it" if refused else
                           "no report is imported or configured by it"))
            elif saved is None:
                again, does = given, "it imports again and configures the report from this deployment"
            elif not_imported:
                again = given
                does = (f"{', '.join(not_imported)} did not import in full - this imports again and writes the "
                        "options back; what is equal by then is left as it is, and `mdb options-restore` "
                        "imports nothing")
            else:
                again = f"mdb options-restore --file {_shell_word(kept)}"
                does = "every package imported - only the options are left to write"
            console.print("\n[red]Not everything was imported and configured.[/red] Fix what is reported "
                          "above, then run:")
            # The command is selected and pasted, so it is one line whatever
            # the width of the terminal, and printed as it is: wrapped, its
            # first line is `mdb builders-import` without the options file,
            # and read as markup a directory named `[old]` is gone from it.
            console.print(again, markup=False, highlight=False, soft_wrap=True)
            console.print(f"({does})", markup=False, highlight=False)
        raise typer.Exit(1)
    if dry_run:
        return
    if captured_file is not None and captured_file.is_file():
        # every option it holds is back in the report; a run that did not get
        # there keeps it, and its repeat line names it
        captured_file.unlink()
        console.print(f"Removed {captured_file}: every option it held is back in the report.", highlight=False)
    console.print("\n[green]Done.[/green] Next: the Content Security Policy settings are described in the "
                  "administration guide, 'Deploying the Builder UIs', step 4."
                  + ("" if saved is not None or not targets else
                     " Optional settings (credential domain, judge model, the decision, optimization and RAG "
                     "switches and what they reveal) keep the package's defaults, so RAG is off until it is "
                     "enabled - set them in the Options pane, or run `mdb options-restore` with a file saved "
                     "from a tuned deployment."))


#: The file `mdb builders-import` writes the content folder's rules to.
CONTENT_RULES_FILE = "sas-viya-cli-content-commands.txt"


def _write_content_rules(session, content_root: str, out_dir: Path) -> None:
    """Write the authorization commands for the content folder, with the
    folder's id filled in when the folder is found. Nothing is run."""
    from .viya.content import DeployError, find_folder
    from .viya.registry import content_rules_text
    try:
        folder = find_folder(session, content_root)
    except DeployError:
        folder = None
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CONTENT_RULES_FILE).write_text(
        content_rules_text(content_root, (folder or {}).get("id")), encoding="utf-8")
    console.print(f"Wrote {CONTENT_RULES_FILE} to {out_dir}/ - the package carries no authorization rule, "
                  f"and these commands give the LLM Consumers group Read on {content_root}. Review the file "
                  "before running it.", highlight=False)


@app.command("package-check")
def package_check(
    files: Optional[list[str]] = typer.Argument(
        None, help="Transfer packages to check (default: the one this repository ships)"),
    fix: bool = typer.Option(
        False, "--fix",
        help="Before the check, rewrite what can be rewritten in the file: hosts to the placeholder, "
             "per-user report states removed, the Prompt Builder option defaults to the neutral values"),
    allow_missing: bool = typer.Option(
        False, "--allow-missing",
        help="End with exit code 0 when the package of the release is not in the checkout (not with --release)"),
    release: bool = typer.Option(
        False, "--release",
        help="The gate of a release: every difference is an error, also a difference in content against the "
             "checkout. What the release procedure and the workflow of a tag run."),
):
    """Check the transfer package against this checkout - the gate a package
    passes before it ships.

    Against the content manifest (SAS-Viya-Integrations/package-manifest.yaml):
    every entry is in the package at its path, once, and equals its source - a
    file its repository file, a generated file what mdb generates now
    (release.json: its kind, the release, its commit and its entries), a
    custom step the .step file in `templates`, `ui` and `flowMetadata`, a job
    definition its program, its form and its parameters; no object is in the
    package that the manifest does not list; and there is no authorization
    rule and no share.

    A report has no source in the repository, so it is checked by what its
    entry names - the option labels of the current UI with their neutral
    defaults in every copy, the CAS tables it binds, the files that are its
    children with their hashes - and, every report, for two things an export
    brings along: a host other than the documented placeholder or a known
    public address, also inside the compressed report content where no search
    finds it, and per-user report states, each a full copy of the report with
    a user id.

    STRICT FOR A RELEASE, LENIENT BETWEEN RELEASES. The package is exported
    from a system and so is always the content of some earlier commit: a
    changelog entry, a fact sheet row or a fix to a program makes it differ
    from the checkout until someone exports again. Without --release such a
    difference in CONTENT - a file, a custom step, a job definition, one of
    the two generated files - is a line that starts with `drift:` and does
    not fail. What the package must never be still fails: an object outside
    the manifest or twice at one path, a rule, a share, a host, a per-user
    state, a report default that is not neutral, a missing entry, a path that
    never ships, a property or a field nothing in the checkout gives it. With
    --release every difference is an error: that is what the release
    procedure runs after the export, and the workflow on a tag.

    One line per difference, and exit code 1 when one of them fails. `--fix`
    rewrites the hosts, the states and the option defaults in the file; a
    difference in content is fixed in the folder (`mdb content-deploy`) and by
    exporting again. While the checkout holds no package of the release the
    command says so and ends with exit code 1, or 0 with --allow-missing.
    """
    from .core.packages import (PLACEHOLDER_HOST, package_findings, reset_report_defaults,
                                sanitise_text, strip_report_states)
    ctx = Context()
    manifest = _content_manifest(ctx)
    if files:
        targets = [Path(f) for f in files]
    else:
        targets = [manifest.package_path]
        if not targets[0].is_file():
            allowed = allow_missing and not release
            console.print(f"[{'yellow' if allowed else 'red'}]There is no {manifest.package} in this "
                          f"checkout: nothing was checked.[/{'yellow' if allowed else 'red'}]",
                          highlight=False)
            raise typer.Exit(0 if allowed else 1)

    total = drifted = 0
    for path in targets:
        if not path.is_file():
            console.print(f"[red]{path}: not a file[/red]", highlight=False)
            raise typer.Exit(2)
        text = path.read_text(encoding="utf-8")
        if fix:
            before = text
            text, reset = reset_report_defaults(text)
            text, removed = strip_report_states(text)
            text, fixed = sanitise_text(text, path.name)
            for copy_name, labels in reset.items():
                console.print(f"  {path.name}: reset {len(labels)} option default(s) of {copy_name}: "
                              f"{', '.join(labels)}", highlight=False)
            for report_name, n in sorted(removed.items()):
                console.print(f"  {path.name}: removed {n} per-user report state(s) from report '{report_name}'",
                              highlight=False)
            for host, n in sorted(fixed.items()):
                console.print(f"  {path.name}: {host} -> {PLACEHOLDER_HOST} ({n})", highlight=False)
            if text != before:
                path.write_text(text, encoding="utf-8", newline="")
        for difference in package_findings(json.loads(text), manifest, path.name):
            if difference.drift and not release:
                drifted += 1
                console.print(f"drift: {path.name}: {difference.text}", markup=False, highlight=False,
                              soft_wrap=True)
            else:
                total += 1
                console.print(f"{path.name}: {difference.text}", markup=False, highlight=False, soft_wrap=True)
    if total:
        console.print(f"[red]{total} difference(s) between the package and the checkout.[/red] A host, a "
                      "per-user state or an option default is rewritten by --fix; anything else is put right "
                      "in the content folder (`mdb content-deploy`) and by `mdb package-export`."
                      + (f" ({drifted} more line(s) are drift and do not fail.)" if drifted else ""))
        raise typer.Exit(1)
    if drifted:
        console.print(f"[yellow]{len(targets)} package(s): {drifted} difference(s) in content to the checkout - "
                      "drift, which does not fail between releases.[/yellow] The package holds the content of an "
                      "earlier commit; everything else about it is as it has to be. For a release the folder "
                      "is deployed and exported again, and `mdb package-check --release` has to pass.")
        return
    console.print(f"[green]{len(targets)} package(s): every entry of the manifest is in the package and equals "
                  "the checkout, and nothing else is in it.[/green]")


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
    kind: Optional[str] = typer.Option(None, "--kind", help="Only 'llm', 'embedding' or 'decision' (default: all)"),
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
        console.print("[yellow]No registered models found in the LLM/Embedding/Decision model projects.[/yellow]")
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
        # Mounted weights need the volume whatever the template; the hf- profiles
        # always get it, since their baked variant is the historical default.
        use_pv = (pv or manifest.runtime.weights_source == "mounted"
                  or manifest.runtime.requirements_profile.startswith("hf-"))
        # Every Azure definition reads its connection from the container -
        # AZURE_OPENAI_RESOURCE (unless committed), AZURE_OPENAI_API_VERSION -
        # and an environment-configured one also its key and deployment, so
        # they all need the env block the plain template lacks. Rendering the
        # plain template for one produces a pod that starts and then fails
        # every call naming the missing variable.
        is_azure = manifest.runtime.template in AZURE_TEMPLATES
        is_env_configured = manifest.runtime.template == AZURE_ENV_TEMPLATE
        if is_azure:
            template = template_dir / "deploy-modelName-env-template.yaml"
        else:
            template = template_dir / ("deploy-modelName-PV-template.yaml" if use_pv
                                       else "deploy-modelName-template.yaml")
        params = manifest.provider.params
        committed = (params.get("resource", "") if params.get("commit_resource") else "").strip()
        azure_resource = os.environ.get("AZURE_OPENAI_RESOURCE", "").strip() or committed
        if is_azure and not azure_resource:
            console.print(f"[yellow]{manifest.model_id}: AZURE_OPENAI_RESOURCE is not set - the "
                          "rendered YAML keeps the placeholder; fill it in before applying.[/yellow]")
        lines = []
        for line in template.read_text(encoding="utf-8").splitlines():
            line = line.replace("containerRegistry", registry)
            line = line.replace("llm_name", manifest.model_id)
            line = line.replace("llmname", manifest.model_id.replace("_", "-"))
            line = line.replace("model_name", manifest.model_id)
            if is_azure:
                if azure_resource:
                    line = line.replace("{{ AZURE-RESOURCE-HOST }}", azure_resource)
                # The definition's own deployment is the right default here: it
                # is what the score code would fall back to anyway, so the
                # rendered manifest states the model the pod will actually serve.
                line = line.replace("{{ AZURE-DEPLOYMENT-NAME }}", manifest.provider.model_version)
            line = re.sub(r"host$", host, line)  # 'host' value slots only, never the 'hosts:' key
            lines.append(line)
        target = out_dir / f"deploy-{manifest.model_id}.yaml"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
        variant = " (PV variant)" if use_pv and not is_azure else ""
        if is_env_configured:
            variant = " (environment-configured variant)"
        elif is_azure:
            variant = " (Azure variant - connection from the container environment)"
        console.print(f"[green]{manifest.model_id}: {target}[/green]" + variant)
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
        decision_template = getattr(adapter, "decision_template", None)
        served = []
        if adapter.template not in (embedding_template, decision_template):
            served.append("llm")
        if embedding_template is not None:
            served.append("embedding")
        if decision_template is not None:
            served.append("decision")
        kinds = " + ".join(served)
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


def _credential_manifest(manifest: Optional[str], domain: str,
                         identity_type: str, identities: Optional[list[str]],
                         env_file: Optional[str], keys_file: Optional[str]):
    """A manifest from a file, or one assembled from the flags."""
    from .viya.credentials import (
        DEFAULT_MANIFEST, CredentialError, Identity, Manifest,
    )

    # Naming identities on the command line is the single-identity mode and
    # wins; otherwise this is a manifest run, and an unnamed manifest means
    # the conventional file in the working directory.
    if not identities:
        path = Path(manifest or DEFAULT_MANIFEST)
        if not path.is_file():
            console.print(f"[red]No credentials manifest at {path}.[/red]")
            console.print(
                "Write one with [bold]mdb credentials-init[/bold], or equip a "
                "single identity with [bold]--identity[/bold].")
            raise typer.Exit(2)
        return Manifest.load(path)
    if manifest:
        console.print("[yellow]--identity was given, so --manifest is "
                      "ignored.[/yellow]")
    source = Path(keys_file or env_file or ".env").resolve()
    try:
        return Manifest(
            domain=domain,
            source=source,
            identities=[Identity(type=identity_type, id=who,
                                 verbatim=bool(keys_file))
                        for who in identities],
        )
    except CredentialError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)


@app.command("credentials-init")
def credentials_init(
    file: str = typer.Option(
        "credentials.yaml", "--file", "-f",
        help="Where to write the manifest — anywhere you keep deployment records"),
    env_file: str = typer.Option(
        ".env", "--env-file", help="The .env whose entries it should point at"),
    domain: str = typer.Option(
        "agentic-ai-keys", "--domain", help="Credential domain the manifest targets"),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing manifest"),
):
    """Write a starter credentials manifest, listing what your .env carries.

    Keep it wherever your deployment records live: paths inside a manifest
    resolve against the manifest's own directory, so it and the `.env` files
    it names travel together.

    The manifest holds no secrets — only identities and the paths to the files
    that do — which is what makes it the piece you commit and review, while
    the `.env` stays git-ignored.
    """
    from .viya.credentials import (
        CredentialError, map_entries, read_name_value_file, scaffold,
    )

    target = Path(file)
    if target.exists() and not force:
        console.print(f"[red]{target} already exists.[/red] Pass --force to overwrite it.")
        raise typer.Exit(2)
    source = Path(env_file)
    if not source.is_file():
        console.print(f"[red]No .env at {source}.[/red] Pass --env-file to point at one.")
        raise typer.Exit(2)
    try:
        entries = map_entries(read_name_value_file(source))
    except CredentialError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)
    if not entries:
        console.print(
            f"[yellow]{source} carries no recognised entries — expected provider "
            "keys (OPENAI_API_KEY, …), <BACKEND>_RAG_USER/_PW pairs and/or "
            "<BACKEND>_HOST/_PORT/_DB/_SSLMODE settings. Writing the manifest "
            "anyway.[/yellow]")

    if target.parent != Path(""):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        scaffold(source.resolve(), target.resolve(), domain, tuple(sorted(entries))),
        encoding="utf-8")
    console.print(f"[green]Wrote {target} listing {len(entries)} available "
                  "entries (names only — no values).[/green]")
    console.print("Name the groups and users to equip, then run "
                  f"[bold]mdb credentials-apply --manifest {target} --dry-run[/bold].")


@app.command("credentials-apply")
def credentials_apply(
    manifest: Optional[str] = typer.Option(
        None, "--manifest", "-m",
        help="YAML naming every identity to equip (bulk mode)"),
    identity: Optional[list[str]] = typer.Option(
        None, "--identity", "-i",
        help="Identity id to equip; repeat for several (without --manifest)"),
    identity_type: str = typer.Option(
        "group", "--identity-type",
        help="user or group. A user credential overrides a group one"),
    domain: str = typer.Option(
        "agentic-ai-keys", "--domain", help="Credential domain to write into"),
    env_file: Optional[str] = typer.Option(
        None, "--env-file", help="Source .env (default: ./.env)"),
    keys_file: Optional[str] = typer.Option(
        None, "--keys-file",
        help="Raw NAME=VALUE file stored verbatim, no provider mapping"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change; write nothing"),
    skip_domain: bool = typer.Option(
        False, "--skip-domain",
        help="Do not create/update the domain itself (needs admin rights)"),
):
    """Equip identities with the accelerator's keys — many at once.

    One domain holds every key the accelerator needs: the LLM provider keys
    under their provider names, the vector-store user/password pairs under
    their backend prefix, and where each store lives. A USER credential
    overrides a GROUP one, so a department can share a key while one person
    keeps their own.

    The shell scripts under SAS-Viya-Integrations/Other equip one identity per
    run; this does a whole rollout from a manifest, and reports it.

    Three things worth knowing before you run it:

    * A PUT REPLACES an identity's whole credential — the source file must
      list everything that identity should end up with.
    * The service returns NO secrets on a read, not even the entry names, so
      --dry-run can tell you an identity will be created or replaced but never
      what it currently holds. That is a privacy property, not a gap.
    * Creating the domain, or any GROUP credential, needs SAS administrator
      rights. A user may always (re)write their own.

    Secret values are never printed, logged or written anywhere by this
    command — only entry names and counts.
    """
    from .viya.credentials import (
        CredentialError, build_steps, ensure_domain, entries_for,
        fetch_credential, put_credential,
    )

    if identity_type not in ("user", "group"):
        console.print("[red]--identity-type must be user or group.[/red]")
        raise typer.Exit(2)
    try:
        plan = _credential_manifest(manifest, domain, identity_type, identity,
                                    env_file, keys_file)
    except CredentialError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)

    with _viya_session() as session:
        try:
            steps = build_steps(plan, lambda who: fetch_credential(session, plan.domain, who))
        except CredentialError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)

        table = Table(title=f"Credential domain '{plan.domain}'")
        table.add_column("Identity")
        table.add_column("Type")
        table.add_column("Action")
        table.add_column("Entries", justify="right")
        table.add_column("Currently")
        for step in steps:
            if not step.ok:
                table.add_row(step.identity.id, step.identity.type,
                              "[red]skip[/red]", "-", step.problem)
                continue
            held = (f"written by {step.existing_by} {step.existing_at[:10]}"
                    if step.action == "replace" else "no credential")
            table.add_row(step.identity.id, step.identity.type,
                          "replace" if step.action == "replace" else "create",
                          str(len(step.entry_names)), held)
        console.print(table)
        usable = [step for step in steps if step.ok]
        if usable:
            console.print("Entries: " + ", ".join(usable[0].entry_names))
            if any(set(s.entry_names) != set(usable[0].entry_names) for s in usable):
                console.print("[yellow]…and different sets for some identities; "
                              "see the manifest.[/yellow]")
        broken = [step for step in steps if not step.ok]
        if broken:
            console.print(f"[yellow]{len(broken)} identity(ies) skipped.[/yellow]")
        if not usable:
            console.print("[red]Nothing to do.[/red]")
            raise typer.Exit(1)
        if dry_run:
            console.print("[yellow]Dry run — nothing was written. The current "
                          "contents of an existing credential cannot be read "
                          "back, so a replace is reported without a diff.[/yellow]")
            raise typer.Exit(0)

        if not skip_domain:
            try:
                ensure_domain(session, plan.domain)
                console.print(f"[green]Domain '{plan.domain}' is in place.[/green]")
            except CredentialError as exc:
                console.print(f"[yellow]{exc}[/yellow]")
                console.print("[yellow]Continuing: the domain may already exist "
                              "and only an administrator can (re)create it. Pass "
                              "--skip-domain to stop trying.[/yellow]")

        failed = 0
        for step in usable:
            try:
                put_credential(session, plan.domain, step.identity,
                               entries_for(plan, step.identity))
                console.print(
                    f"[green]{step.identity.type} {step.identity.id}: "
                    f"{len(step.entry_names)} entries stored.[/green]")
            except CredentialError as exc:
                failed += 1
                console.print(f"[red]{exc}[/red]")
        if failed:
            raise typer.Exit(1)
        console.print("[green]Done. Check it with `mdb credentials-report`.[/green]")


@app.command("credentials-report")
def credentials_report(
    manifest: Optional[str] = typer.Option(
        None, "--manifest", "-m", help="YAML naming the identities to ask about"),
    identity: Optional[list[str]] = typer.Option(
        None, "--identity", "-i", help="Identity id to ask about; repeat for several"),
    identity_type: str = typer.Option(
        "group", "--identity-type", help="user or group"),
    domain: str = typer.Option(
        "agentic-ai-keys", "--domain", help="Credential domain to inspect"),
):
    """Who holds a credential in the domain, and who wrote it.

    It reports on the identities you NAME, because the credentials service has
    no collection endpoint — /credentials/domains/{domain}/users and /groups
    both answer 404, so there is no way to enumerate holders. Pass the same
    manifest you applied and this becomes the after-picture of that rollout.

    It cannot show what a credential contains: a read returns metadata only,
    never the secrets and not even their names.
    """
    from .viya.credentials import CredentialError, fetch_credential

    plan = _credential_manifest(manifest, domain, identity_type, identity,
                                None, None)
    table = Table(title=f"Credential domain '{plan.domain}'")
    table.add_column("Identity")
    table.add_column("Type")
    table.add_column("Credential")
    table.add_column("Last written by")
    table.add_column("When")
    held = 0
    with _viya_session() as session:
        for who in plan.identities:
            try:
                body = fetch_credential(session, plan.domain, who)
            except CredentialError as exc:
                table.add_row(who.id, who.type, "[red]unreadable[/red]", str(exc)[:40], "")
                continue
            if body is None:
                table.add_row(who.id, who.type, "[yellow]none[/yellow]", "", "")
                continue
            held += 1
            table.add_row(
                who.id, who.type, "[green]yes[/green]",
                str(body.get("modifiedBy") or body.get("createdBy") or ""),
                str(body.get("modifiedTimeStamp") or body.get("creationTimeStamp") or "")[:19],
            )
    console.print(table)
    console.print(f"{held} of {len(plan.identities)} named identities hold a credential.")
    console.print("[dim]Contents are never shown: a credential read returns "
                  "metadata only, so no identity can see another's keys.[/dim]")


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
