# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deployment-specific VA report options, saved and restored around an import.

THE PROBLEM. Every option an admin sets on the Prompt Builder object -
repository and project ids, the SCR endpoint, the credential domain, the
feature switches, and for its RAG part the content root and which vector
stores the deployment offers - is stored INSIDE the report. A transfer package
carries those values too, so importing a newer version of the report replaces
a site's configuration with whatever the package was built against. The report
keeps working and quietly points at somebody else's environment, which is the
worst shape a failure can take.

THE SHAPE OF THE DATA. Report content is BIRD XML, and each option is one
hidden parameter:

    <PromptDefinition label="contentRoot" hidden="true" isParameter="true"
                      dataType="string" multiValued="false" name="pr26">
      <DefaultValue><String behavior="fixed">/SAS Agentic AI …</String></DefaultValue>
    </PromptDefinition>

`label` is the option name the builder reads; the `name` (pr26) is a positional
id that is NOT stable across report versions - which is exactly why this module
matches on the label and never on `name`.

A CHECKBOX of the Options pane (the three feature switches, TLS verification
in manifested models, the run history, one per vector store) has the same
shape: Visual Analytics stores it as a string parameter holding the word
`true` or `false`, which is also what a report holds that was saved while a
switch was a dropdown with those two words as keys. So a switch reads the
same from both. A parameter typed as a boolean (`<Boolean>` in place of
`<String>`) has not been seen in a report; it is read and written all the
same, and its element is kept as it is.

WHAT IS SAVED. Only labels the file names. Restoring writes values back over
whatever the import brought and leaves every other part of the report alone,
so a new report version keeps its new layout, its new data items and its new
objects, and gets the site's configuration back.

ONE REPORT, AND THE ONE IT REPLACED. The RAG Builder is a part of the Prompt
Builder: one report, one object, one set of options. Up to 2.2.x it was a
report of its own, named "RAG Builder", with its own options. Sites still have
that report (an import never deletes) and options files saved from it, so
`fold_legacy` moves what such a section holds into the Prompt Builder section
- wherever options are saved or restored - instead of dropping it.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Optional

#: The report carrying builder options, and the seed kinds whose Model Manager
#: project it needs: the LLM project for prompts, the embedding project for
#: its RAG part. Keyed by NAME rather than id because an import can mint a new
#: id, and the name is what an admin recognises in the transfer package.
MERGED_REPORT = "Prompt Builder"
BUILDER_REPORTS = {
    MERGED_REPORT: ("llm", "embedding"),
}

#: The option that holds each seed kind's Model Manager project id.
SEED_PROJECT_LABELS = {
    "llm": "llmProjectID",
    "embedding": "embeddingProjectID",
}

#: Reports that carried builder options up to 2.2.x and are retired. They are
#: still READ - a site keeps the report until someone deletes it, and its
#: values are the site's RAG configuration - but never written: `fold_legacy`
#: moves their options into MERGED_REPORT.
LEGACY_REPORTS = frozenset({"RAG Builder"})

#: The content folder of the accelerator unless a site moved it - the
#: contentRoot of SAS-Viya-Integrations/package-manifest.yaml.
DEFAULT_CONTENT_ROOT = "/SAS Agentic AI Accelerator"

#: Where each Builder report sits below the content root: the folder of the
#: Prompt Builder report in the package of 2.3.0 (manifest path
#: `Prompt Builder/Prompt Builder`), and the folder the retired report sat in
#: (the package of 2.2.x: `RAG/RAG Builder`).
REPORT_FOLDERS = {MERGED_REPORT: "Prompt Builder", "RAG Builder": "RAG"}


def report_folder(content_root: str, report: str) -> str:
    """The SAS Content folder a Builder report sits in, below `content_root`."""
    return f"{(content_root or DEFAULT_CONTENT_ROOT).rstrip('/')}/{REPORT_FOLDERS[report]}"

#: Options both reports held with the same meaning. The Prompt Builder's value
#: is kept; a legacy value that differs is reported, because the two reports
#: of one site were not supposed to disagree and an admin should know they did.
SHARED_LABELS = (
    "viyaHost", "modelRepositoryID", "SCREndpoint", "deploymentType", "credentialDomain",
)

#: Options only the legacy report held. They keep their name in the merged
#: report. The `enable_<backend>` switches are listed for the backends the
#: accelerator ships; any other label of that shape is carried the same way.
RAG_ONLY_LABELS = (
    "embeddingProjectID", "contentRoot", "casServer", "storeSslmode", "deletedPolicy",
    "retainDays", "recordHistory", "embedReplicas", "persistElements", "persistChunks",
    "enable_pgvector", "enable_singlestore",
)

#: Legacy options that changed name on the way into the merged report.
#: `computeContext` meant "the context ingestion runs in" in the legacy report
#: and means "the context the optimization job runs in" in the Prompt Builder,
#: with different values on every site that has both - so the legacy one is
#: carried as `ragComputeContext` and never written to `computeContext`.
LEGACY_RENAMES = {"computeContext": "ragComputeContext"}

#: Legacy options nothing reads any more; they are not carried.
LEGACY_DROPPED = frozenset({"enabledBackends"})

#: The switch that offers the RAG part, and the legacy options that prove a
#: site really had the legacy report. An options file saved WITHOUT a live
#: legacy report still holds a legacy section - discovery alone wrote
#: repository, embedding project, SCR endpoint and deployment type - and
#: switching RAG on for that would switch it on at sites that never had the
#: RAG Builder. Discovery never writes the labels below; only a report holds
#: them. The 2.2.x package shipped both as defaults, so they prove that the
#: site had the report, not that anyone used it: nothing in the options tells
#: those two apart.
RAG_SWITCH = "enableRag"
RAG_PROOF_LABELS = ("contentRoot", "casServer")

#: Every option of the merged report, in the order the Options pane lists
#: them: shared fields, then each switch followed by what it reveals. A
#: shipped report must already hold all of them, because `write_options`
#: reports a label the report lacks and never inserts it. The list is the
#: field names LLM-Prompt-Builder/src/va/ddc.ts posts with every switch on;
#: tests/test_package_guards.py reads that file and fails when the two differ.
#:
#: `sslVerify` (2.3.0) is the checkbox "Verify TLS certificates in manifested
#: models". No 2.2.x report and no 2.2.x options file holds it, and nothing
#: here supplies a value for it: a restore writes it only when the file names
#: it, so a site that never set it keeps the default of the imported report.
MERGED_REPORT_LABELS = (
    "viyaHost", "modelRepositoryID", "llmProjectID", "SCREndpoint", "deploymentType",
    "judgeModel", "modelCardReportURI", "credentialDomain", "sslVerify",
    "enableDecision",
    "enableOptimization", "computeContext", "optimizeJobProgram", "minOptimizeSamples",
    RAG_SWITCH, "embeddingProjectID", "contentRoot", "casServer", "ragComputeContext",
    "storeSslmode", "deletedPolicy", "retainDays", "recordHistory", "embedReplicas",
    "persistElements", "persistChunks", "enable_pgvector", "enable_singlestore",
)

#: Options that are never carried between deployments. A report id or a
#: capture timestamp describes THIS environment's report, not its
#: configuration, and copying it would be meaningless at best.
#:
#: API_KEYS is excluded for a different and stronger reason: this file is a
#: configuration record meant to be kept with deployment paperwork, reviewed
#: in a diff and handed between admins. A provider key does not belong in
#: anything with those properties. The builder reads its keys from the
#: credential domain / key table at run time, so there is nothing here to
#: carry - and the seed's placeholder ("key-value") would only teach someone
#: that this is where a real key goes.
NON_PORTABLE = {"id", "name", "width", "type", "API_KEYS"}

# One hidden parameter, captured whole so the value can be swapped without
# disturbing the attributes around it. DOTALL: the elements are pretty-printed
# across several lines.
_PROMPT = re.compile(
    r'(<PromptDefinition\b[^>]*\blabel="(?P<label>[^"]+)"[^>]*>)'
    r'(?P<body>.*?)'
    r'(</PromptDefinition>)',
    re.DOTALL,
)
# The value element of a parameter's default, whichever of the two it is. The
# tag is captured so that a write puts the same element back.
_DEFAULT_VALUE = re.compile(
    r'(<(?P<tag>String|Boolean)\b[^>]*>)(?P<value>.*?)(</(?P=tag)>)', re.DOTALL
)
_BOOLEAN_TYPE = re.compile(r'\bdataType="boolean"', re.IGNORECASE)

#: What a boolean parameter can be told, by the word it becomes. The same
#: words the Prompt Builder reads for a switch in a URL.
_BOOLEAN_WORDS = {
    "true": "true", "1": "true", "yes": "true", "on": "true",
    "false": "false", "0": "false", "no": "false", "off": "false",
}


def _unescape(text: str) -> str:
    return (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'")
                .replace("&amp;", "&"))


def _escape(text: str) -> str:
    # & first, or the entities introduced below are escaped a second time.
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def option_text(value) -> str:
    """An option value as the report stores it: always a string.

    A switch is stored as `true` / `false` - the words a checkbox of the
    Options pane stores - while a hand-edited options file may well say JSON
    `true`. `str(True)` is "True", which is not the stored word and differs
    from it on every comparison, so a boolean gets its stored spelling.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _is_set(value) -> bool:
    """Whether an option holds a value: an empty one means nobody set it."""
    return option_text(value).strip() != ""


def _boolean_word(text: str) -> Optional[str]:
    """`true` / `false` for a text that plainly says on or off, else None."""
    return _BOOLEAN_WORDS.get(text.strip().lower())


def _same_setting(one: str, other: str) -> bool:
    """Whether two stored values say the same: equal text, or the same on /
    off in two spellings - a checkbox the retired report stored as `1` is
    `true` once it was ticked in the merged one."""
    if one == other:
        return True
    word = _boolean_word(one)
    return word is not None and word == _boolean_word(other)


def _typed_boolean(opening: str, value: re.Match) -> bool:
    """Whether a parameter is typed as a boolean: by its value element, or by
    the dataType of its definition."""
    return value.group("tag") == "Boolean" or bool(_BOOLEAN_TYPE.search(opening))


def read_options(content: str) -> dict:
    """Every hidden parameter of a report, as {label: value}.

    Every value is text. A parameter typed as a boolean gives the word `true`
    or `false`, the same a string parameter holds for a checkbox, so a saved
    options file does not depend on which of the two a report uses.
    """
    found: dict = {}
    for match in _PROMPT.finditer(content):
        label = match.group("label")
        value = _DEFAULT_VALUE.search(match.group("body"))
        text = _unescape(value.group("value")) if value else ""
        if value and _typed_boolean(match.group(1), value):
            text = _boolean_word(text) or text.strip()
        found[label] = text
    return found


@dataclass
class RestoreResult:
    applied: dict = field(default_factory=dict)
    unchanged: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    #: {label: value} not written because the report types the parameter as a
    #: boolean and the value says neither on nor off.
    rejected: dict = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def write_options(content: str, values: dict) -> tuple:
    """Return (new_content, RestoreResult) with `values` written over `content`.

    A label the report does not have is REPORTED, not inserted: the parameter
    list belongs to the report, and inventing an entry for an option this
    version of the builder no longer reads would leave a value nothing
    consumes. That is usually the interesting signal - the option was renamed
    or dropped between versions.

    Values are compared and written in their stored spelling (`option_text`),
    so a switch given as a JSON boolean is `true` / `false` in the report and
    counts as unchanged when the report already says so.

    Only the text of the value element changes; the element stays what the
    report made it, so a string parameter is never turned into a boolean one
    or the reverse. A parameter the report types as a boolean gets the word
    `true` or `false` for any value that plainly says on or off (`1`, `yes`,
    `True`); a value that says neither is not written and listed in
    `rejected`. A string parameter gets the value as it is given.
    """
    result = RestoreResult()
    present = read_options(content)
    result.missing = sorted(set(values) - set(present) - NON_PORTABLE)

    def replace(match: re.Match) -> str:
        label = match.group("label")
        if label in NON_PORTABLE or label not in values:
            return match.group(0)
        wanted = option_text(values[label])
        value = _DEFAULT_VALUE.search(match.group("body"))
        if value is not None and _typed_boolean(match.group(1), value):
            word = _boolean_word(wanted)
            if word is None:
                result.rejected[label] = wanted
                return match.group(0)
            wanted = word
        current = present.get(label, "")
        if current == wanted:
            result.unchanged[label] = wanted
            return match.group(0)
        if value is None:
            # A parameter with no DefaultValue element: leave it be rather
            # than synthesise one, and say so.
            result.missing.append(label)
            return match.group(0)
        body = match.group("body")
        body = (body[:value.start()] + value.group(1) + _escape(wanted) + value.group(4)
                + body[value.end():])
        result.applied[label] = wanted
        return match.group(1) + body + match.group(4)

    return _PROMPT.sub(replace, content), result


def rehost_content(content: str, host: str, placeholder: str = "your-sas-viya-host") -> tuple:
    """Point a report at this deployment: every occurrence of the shipped
    placeholder host - the Data-Driven Content URL, the `viyaHost` and
    `SCREndpoint` defaults - becomes `host`. Returns (content, occurrences).

    A shipped package names the placeholder on purpose (core/packages.py
    keeps it that way), so a restore or an import that leaves it in place
    leaves a report loading its app from a host that does not exist. Nothing
    else is touched: a host an admin has set is not the placeholder.
    """
    host = host.strip().rstrip("/")
    if host.startswith("https://") or host.startswith("http://"):
        host = host.split("://", 1)[1]
    if not host or placeholder not in content:
        return content, 0
    return content.replace(placeholder, host), content.count(placeholder)


def options_file(deployment: str, reports: dict) -> dict:
    """The saved-options document.

    `reports` maps report name -> {option: value}. The file is deliberately
    plain and hand-editable: an admin who has no live report to capture from
    should be able to write one from the documentation, and an admin
    reviewing a change should be able to read the diff.
    """
    return {
        "version": 1,
        "kind": "sas-agentic-ai-accelerator/builder-options",
        "deployment": deployment,
        "savedAt": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "reports": {
            name: {key: value for key, value in options.items()
                   if key not in NON_PORTABLE}
            for name, options in reports.items()
        },
    }


def merge_seed(seed: dict, captured: Optional[dict]) -> dict:
    """Seed values, overlaid with what the live report already holds.

    The seed knows what THIS run discovered (repository id, project id, SCR
    endpoint); the live report knows what an admin has tuned since. The admin
    wins on every key they have set, because a value someone chose beats a
    value that was derived - except where the live value is empty, which means
    nobody has set it.
    """
    merged = {key: value for key, value in (seed or {}).items()
              if key not in NON_PORTABLE}
    for key, value in (captured or {}).items():
        if key in NON_PORTABLE:
            continue
        if _is_set(value):
            merged[key] = value
    return merged


def fold_legacy(reports: dict) -> tuple:
    """Move the options of a retired report into the merged one.

    `reports` maps report name -> {option: value}, as an options file holds
    them or as they were just read from live reports. Returns (reports,
    messages): a new mapping with no LEGACY_REPORTS key, and what an admin
    should be told about the move. Nothing is read from anywhere else, and
    the input is left alone.

    The rules, in the order they matter:

    * What the merged report holds wins. A legacy value only fills a label
      the merged section lacks or holds empty - otherwise a save taken while
      the retired report still exists would put its stale values over ones
      tuned since.
    * SHARED_LABELS keep the merged value; a legacy value that differs is
      reported, one message per label.
    * LEGACY_RENAMES are carried under their new name, LEGACY_DROPPED not at
      all, everything else under its own name. Where the merged section
      already holds another value, the legacy one is dropped and named in ONE
      message per legacy report - a site tuned after the upgrade gets a line,
      not a line per option.
    * RAG_SWITCH becomes "true" only when the legacy section holds one of
      RAG_PROOF_LABELS, and an existing RAG_SWITCH is never overwritten: an
      admin who switched RAG off meant it.

    A mapping without a legacy section comes back equal with no messages, so
    folding what was already folded changes nothing.
    """
    legacy_names = [name for name in reports if name in LEGACY_REPORTS]
    if not legacy_names:
        return dict(reports), []

    messages: list = []
    merged = dict(reports.get(MERGED_REPORT) or {})
    for legacy_name in legacy_names:
        section = reports.get(legacy_name) or {}
        carried: list = []
        dropped: list = []
        for label, value in section.items():
            if label in NON_PORTABLE or label in LEGACY_DROPPED or not _is_set(value):
                continue
            if label in SHARED_LABELS and _is_set(merged.get(label)):
                kept = option_text(merged[label]).strip()
                other = option_text(value).strip()
                if kept != other:
                    note = (f"{label}: the {MERGED_REPORT} value '{kept}' is kept; "
                            f"'{legacy_name}' held '{other}'.")
                    if label == "credentialDomain" and kept.lower() == "none":
                        # `none` is no domain but "no credential lookup", and
                        # the value stays: changing it would switch key
                        # lookups on for prompts and decisions.
                        note += (f" RAG cannot be used with 'none' (no credential lookup): set the "
                                 f"credential domain to '{other}' in the Options pane, or RAG setups "
                                 "stay disabled.")
                    elif label == "credentialDomain":
                        note += (" The RAG part reads its secrets from the kept domain - "
                                 "add the vector store credentials to it.")
                    messages.append(note)
                continue
            target = LEGACY_RENAMES.get(label, label)
            if _is_set(merged.get(target)):
                kept = option_text(merged[target]).strip()
                other = option_text(value).strip()
                if not _same_setting(kept, other):
                    dropped.append(f"{target} '{kept}' (was '{other}')")
                continue
            merged[target] = value
            carried.append(target if target == label else f"{label} -> {target}")
        if carried:
            messages.append(f"'{legacy_name}': {len(carried)} option(s) carried into "
                            f"{MERGED_REPORT}: {', '.join(carried)}.")
        if dropped:
            messages.append(f"'{legacy_name}': {len(dropped)} value(s) differ and the {MERGED_REPORT} "
                            f"ones are kept: {', '.join(dropped)}.")
        had_report = any(_is_set(section.get(label)) for label in RAG_PROOF_LABELS)
        if RAG_SWITCH in merged:
            current = option_text(merged[RAG_SWITCH]).strip()
            if had_report and current.lower() != "true":
                messages.append(
                    f"{RAG_SWITCH} is '{current}' in {MERGED_REPORT} and is kept: "
                    + ("enable RAG in the Options pane to use the carried settings." if carried else
                       f"nothing was carried from '{legacy_name}' - when RAG is enabled in the Options "
                       "pane, its RAG options are the ones the report holds now."))
            continue
        if had_report:
            merged[RAG_SWITCH] = "true"
            messages.append(f"{RAG_SWITCH} set to true: the site had the report '{legacy_name}' "
                            f"({' / '.join(RAG_PROOF_LABELS)}).")
        else:
            messages.append(f"{RAG_SWITCH} not set: '{legacy_name}' holds discovered values only, so "
                            "RAG stays off until it is enabled in the Options pane.")

    folded = {name: values for name, values in reports.items() if name not in LEGACY_REPORTS}
    folded[MERGED_REPORT] = merged
    return folded, messages
