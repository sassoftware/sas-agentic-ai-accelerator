# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the guide and the shipped example tell an author to write.

A manifest is written from the examples, so the examples are held to what
the loader reads and to one rule: the group they equip for the people who
open the Prompt Builder is the same one everywhere on the page.
"""
from pathlib import Path

from mdb.viya.credentials import Manifest

REPO = Path(__file__).resolve().parents[3]

EXAMPLE = REPO / "SAS-Viya-Integrations" / "Other" / "credentials.example.yaml"
GUIDE = REPO / "website" / "docs" / "Administration-Guide" / "Managing-Credentials.md"


def test_the_shipped_example_is_a_manifest_and_removes_nothing():
    manifest = Manifest.load(EXAMPLE)
    assert [(who.type, who.id) for who in manifest.identities] == [
        ("group", "LLMConsumers"), ("group", "RAGEngineers"),
        ("user", "sas-be-sa"), ("group", "FraudAnalytics")]
    # the `state: absent` entry is shown as a comment: an example that is
    # copied and applied must not delete a credential
    assert all(who.state == "present" for who in manifest.identities)
    assert "  #   state: absent" in EXAMPLE.read_text(encoding="utf-8")


def test_the_guide_and_the_example_equip_one_group_for_the_prompt_builder():
    """A site wrote its manifest for `PromptEngineers` from the examples
    while its users were members of `LLMConsumers`, the group the script
    example of the same page equips: the apply succeeded and the Prompt
    Builder still had no key. Every example names the one group now, and the
    page says that the credential has to go to a group the users are in."""
    guide = GUIDE.read_text(encoding="utf-8")
    assert "PromptEngineers" not in guide
    assert "PromptEngineers" not in EXAMPLE.read_text(encoding="utf-8")
    for example in ("-IdentityId LLMConsumers", "- {type: group, id: LLMConsumers}",
                    "--identity LLMConsumers", "mdb credentials-add group LLMConsumers"):
        assert example in guide, example
    assert "No <provider> key available in the <domain> credential domain" in guide
    assert "members of" in guide


def test_the_manifest_in_the_guide_loads_and_marks_one_identity_absent(tmp_path):
    guide = GUIDE.read_text(encoding="utf-8")
    block = guide[guide.index("```yaml\ndomain: agentic-ai-keys") + len("```yaml\n"):]
    path = tmp_path / "credentials.yaml"
    path.write_text(block[:block.index("```")], encoding="utf-8")
    manifest = Manifest.load(path)
    assert [(who.id, who.state) for who in manifest.identities] == [
        ("LLMConsumers", "present"), ("RAGEngineers", "present"), ("sas-be-sa", "present"),
        ("FraudAnalytics", "present"), ("FormerTeam", "absent")]
