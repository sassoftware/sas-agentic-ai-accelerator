# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""builder-26: the default of an option that is no enum, no bool and not
API_KEY is a text, a number, true or false - the Prompt Builder draws it in a
number field or a text field as it is written, and cannot draw null, a list or
an object so. mdb wrote such a default for base_url from provider.params of a
self-hosted model."""
import json

import pytest

from mdb.core.generator import GenerationError, check_options_json


@pytest.mark.parametrize("default", ["", "http://localhost:11434/v1", 0, 0.5, True, False])
def test_a_text_a_number_or_a_boolean_passes(default):
    check_options_json({"base_url": {"default": default, "type": "string"}, "my_option": {"default": default}})


@pytest.mark.parametrize("default", [None, [], ["http://a"], {"url": "http://a"}])
@pytest.mark.parametrize("type_", [None, "string", "number"])
def test_null_a_list_or_an_object_is_refused(default, type_):
    option = {"default": default} if type_ is None else {"default": default, "type": type_}
    with pytest.raises(GenerationError) as raised:
        check_options_json({"base_url": option})
    assert str(raised.value) == (
        f"Option 'base_url' has the default {json.dumps(default)} - the default of an option is a text, "
        "a number, true or false.")
