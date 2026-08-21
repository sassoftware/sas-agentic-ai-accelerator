# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register Setup's lookups: finding a project or model by its name.

A name is free text a person chose. "Bob's policies" is an ordinary thing to
call a RAG setup, and it is exactly the shape that ends a filter's string
literal early.
"""
import pytest

from rag_core.registration import name_filter


def test_an_ordinary_name_uses_the_usual_quote():
    assert name_filter("Travel policies") == "eq(name,'Travel policies')"


def test_a_name_with_an_apostrophe_is_wrapped_in_the_other_quote():
    """Not doubled - delimited.

    Doubling is not part of the documented grammar; guessing it fails the same
    silent way the bug did.
    """
    assert name_filter("Bob's policies") == 'eq(name,"Bob\'s policies")'


def test_a_name_with_a_double_quote_falls_back_to_the_apostrophe():
    assert name_filter('The "final" draft') == """eq(name,'The "final" draft')"""


def test_a_name_carrying_both_quotes_is_refused_by_name():
    """Refused, not guessed.

    The alternative failure is invisible: a filter the service parses
    differently returns nothing, register_model reads that as "no such model",
    and every re-registration adds another duplicate.
    """
    with pytest.raises(ValueError, match="both"):
        name_filter("""Bob's "final" draft""")
