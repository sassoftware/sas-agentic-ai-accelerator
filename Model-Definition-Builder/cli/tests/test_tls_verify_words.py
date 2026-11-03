# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""TLS verification is switched off by an explicit false word only - in mdb
(SAS_VIYA_VERIFY_SSL, MDB_VERIFY_SSL) and in the programs and custom steps
that run in SAS (SAS_VIYA_VERIFY_SSL): one setting, one meaning. A typo
keeps the check."""
import re
from pathlib import Path

import pytest

from mdb.core import netutil

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("value, verified", [
    (None, True), ("true", True), ("", True),
    ("false", False), ("NO", False), (" 0 ", False),
    ("ture", True), ("yes", True), ("1", True), ("off", True),
])
def test_mdb_verify_ssl_is_off_only_for_a_false_word(monkeypatch, value, verified):
    if value is None:
        monkeypatch.delenv("MDB_VERIFY_SSL", raising=False)
    else:
        monkeypatch.setenv("MDB_VERIFY_SSL", value)
    assert netutil.make_session().verify is verified


def test_the_option_still_switches_it_off(monkeypatch):
    monkeypatch.delenv("MDB_VERIFY_SSL", raising=False)
    assert netutil.make_session(verify_ssl=False).verify is False


def test_the_programs_that_run_in_sas_take_the_same_words():
    program = REPO / "SAS-Viya-Integrations" / "Logging-Monitoring" / "Get-All-Prompts.sas"
    words = re.search(r"SAS_VIYA_VERIFY_SSL', ''\)\.strip\(\)\.lower\(\) in \(([^)]*)\)",
                      program.read_text(encoding="utf-8"))
    assert words, "the TLS helper of Get-All-Prompts.sas has changed shape - compare it with TLS_OFF_WORDS"
    assert tuple(re.findall(r"'([^']*)'", words.group(1))) == netutil.TLS_OFF_WORDS
