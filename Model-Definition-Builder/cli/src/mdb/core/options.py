# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deployment-specific VA report options, saved and restored around an import.

THE PROBLEM. Every option an admin sets on a Prompt Builder or RAG Builder
object - repository and project ids, the SCR endpoint, the credential domain,
the content root, which vector stores the deployment offers - is stored INSIDE
the report. A transfer package carries those values too, so importing a newer
version of the report replaces a site's configuration with whatever the
package was built against. The report keeps working and quietly points at
somebody else's environment, which is the worst shape a failure can take.

THE SHAPE OF THE DATA. Report content is BIRD XML, and each option is one
hidden parameter:

    <PromptDefinition label="contentRoot" hidden="true" isParameter="true"
                      dataType="string" multiValued="false" name="pr26">
      <DefaultValue><String behavior="fixed">/SAS Agentic AI …</String></DefaultValue>
    </PromptDefinition>

`label` is the option name the builder reads; the `name` (pr26) is a positional
id that is NOT stable across report versions - which is exactly why this module
matches on the label and never on `name`.

WHAT IS SAVED. Only labels the file names. Restoring writes values back over
whatever the import brought and leaves every other part of the report alone,
so a new report version keeps its new layout, its new data items and its new
objects, and gets the site's configuration back.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Optional

#: Report names carrying builder options, and the seed kind each corresponds
#: to. Keyed by NAME rather than id because an import can mint a new id, and
#: the name is what an admin recognises in the transfer package.
BUILDER_REPORTS = {
    "Prompt Builder": "llm",
    "RAG Builder": "embedding",
}

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
_DEFAULT_STRING = re.compile(
    r'(<String\b[^>]*>)(?P<value>.*?)(</String>)', re.DOTALL
)


def _unescape(text: str) -> str:
    return (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'")
                .replace("&amp;", "&"))


def _escape(text: str) -> str:
    # & first, or the entities introduced below are escaped a second time.
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def read_options(content: str) -> dict:
    """Every hidden parameter of a report, as {label: value}."""
    found: dict = {}
    for match in _PROMPT.finditer(content):
        label = match.group("label")
        value = _DEFAULT_STRING.search(match.group("body"))
        found[label] = _unescape(value.group("value")) if value else ""
    return found


@dataclass
class RestoreResult:
    applied: dict = field(default_factory=dict)
    unchanged: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)

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
    """
    result = RestoreResult()
    present = read_options(content)
    result.missing = sorted(set(values) - set(present) - NON_PORTABLE)

    def replace(match: re.Match) -> str:
        label = match.group("label")
        if label in NON_PORTABLE or label not in values:
            return match.group(0)
        wanted = str(values[label])
        current = present.get(label, "")
        if current == wanted:
            result.unchanged[label] = wanted
            return match.group(0)
        body, count = _DEFAULT_STRING.subn(
            lambda m: m.group(1) + _escape(wanted) + m.group(3),
            match.group("body"), count=1)
        if count == 0:
            # A parameter with no DefaultValue element: leave it be rather
            # than synthesise one, and say so.
            result.missing.append(label)
            return match.group(0)
        result.applied[label] = wanted
        return match.group(1) + body + match.group(4)

    return _PROMPT.sub(replace, content), result


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
        if str(value).strip() != "":
            merged[key] = value
    return merged
