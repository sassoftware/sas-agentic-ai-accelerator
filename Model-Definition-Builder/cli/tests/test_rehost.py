# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A restored or imported report must not keep the shipped placeholder host.

The Data-Driven Content URL and the viyaHost / SCREndpoint defaults of a
shipped package all name `your-sas-viya-host`; `mdb options-restore` and
`mdb builders-import` point them at the deployment. Only the placeholder is
touched - a host an administrator set stays.
"""
from mdb.core.options import rehost_content, write_options

REPORT = (
    '<SASReport><VisualElements>'
    '<WebContent url="https://your-sas-viya-host/SASJobExecution/?_program=%2FX&amp;_action=form"/>'
    '</VisualElements><Prompts>'
    '<PromptDefinition label="viyaHost" name="pr1"><DefaultValue><String behavior="fixed">'
    'https://your-sas-viya-host</String></DefaultValue></PromptDefinition>'
    '<PromptDefinition label="SCREndpoint" name="pr2"><DefaultValue><String behavior="fixed">'
    'https://your-sas-viya-host/llm</String></DefaultValue></PromptDefinition>'
    '<PromptDefinition label="credentialDomain" name="pr3"><DefaultValue><String behavior="fixed">'
    'agentic-ai-keys</String></DefaultValue></PromptDefinition>'
    '</Prompts><Footer href="https://sassoftware.github.io"/></SASReport>'
)


def test_every_placeholder_becomes_the_site_host_and_nothing_else_moves():
    out, n = rehost_content(REPORT, "https://viya.example.com/")
    assert n == 3
    assert 'url="https://viya.example.com/SASJobExecution/' in out
    assert '>https://viya.example.com</String>' in out and '>https://viya.example.com/llm</String>' in out
    assert "your-sas-viya-host" not in out
    assert 'https://sassoftware.github.io' in out and 'agentic-ai-keys' in out


def test_a_bare_host_and_an_already_pointed_report_are_handled():
    out, n = rehost_content(REPORT, "viya.example.com")
    assert n == 3 and "viya.example.com/llm" in out
    again, m = rehost_content(out, "other.example.com")
    assert m == 0 and again == out            # nothing left to replace, host kept
    same, k = rehost_content(REPORT, "")
    assert k == 0 and same == REPORT          # no host known: leave the report alone


def test_restored_values_win_over_the_rehosted_defaults():
    # what builders-import does: saved/discovered values first, then the host
    written, result = write_options(REPORT, {"SCREndpoint": "https://viya.example.com/llm-prod"})
    out, n = rehost_content(written, "viya.example.com")
    assert result.applied == {"SCREndpoint": "https://viya.example.com/llm-prod"}
    assert n == 2 and ">https://viya.example.com/llm-prod</String>" in out
