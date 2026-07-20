# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Template subset lint: the phase-2 web app renders these templates with a
~100-line TypeScript renderer, so only plain variables, includes and simple
conditionals are allowed - no filters, loops, macros or set blocks."""
import re
from pathlib import Path

FORBIDDEN = [
    (re.compile(r"\{\{[^}]*\|"), "Jinja filter (|) - not portable to the TS renderer"),
    (re.compile(r"\{%-?\s*(for|set|macro|call|filter|block|extends|import|from)\b"),
     "construct outside the include/if subset"),
]
ALLOWED_TAGS = {"if", "elif", "else", "endif", "include"}


def test_templates_stay_in_portable_subset(repo_root):
    templates = list((repo_root / "Model-Definition-Builder" / "definition-core" / "templates").rglob("*.j2"))
    assert templates, "no templates found"
    for template in templates:
        text = template.read_text(encoding="utf-8")
        for pattern, why in FORBIDDEN:
            match = pattern.search(text)
            assert not match, f"{template.name}: {why} -> {match.group(0)!r}"
        for tag in re.findall(r"\{%-?\s*(\w+)", text):
            assert tag in ALLOWED_TAGS, f"{template.name}: tag '{tag}' outside the portable subset"
