"""Every option shape mdb's generator can write into an options.json, written
by mdb itself - for verify-round-trip.js.

    python options-writer.py <repository> <out.json> [<names.json>]

mdb is loaded from <repository>/Model-Definition-Builder/cli/src. The shapes
come from the code: every field of OptionSpec (mdb/core/manifest.py) that
reaches options.json as the page reads it with a value of each kind its
annotation allows - a field this script does not know stops it; min, max,
range, informational and value_map, which shape only the range text the page
does not read and the score code, are left at none -, for an option of no vocabulary
and for every option of the
vocabulary (definition-core/static/option-vocabulary.json), with the provider
key of AuthBlock beside it, and the option base_url, which the generator
writes from provider.params of a self-hosted model, with a value of every kind
YAML gives. Each shape is put into the manifest of a model of
LLM-Definitions - the first that mdb loads and generates, with an api_key
auth - and goes the way `mdb generate` takes it: ModelManifest.model_validate,
then render_assets. The result is a list of
    {"shape": <name>, "key": <option>, "written": <options.json text> | null,
     "refused": <the message of mdb> | null}

With <names.json>, a list of option names, it writes names in place of
shapes: each name alone as an enum of two values in that manifest - an
informational option, which mdb writes under a name of any text (an option
that reaches the score code needs a name of letters, digits and
underscores) -, and every name written alone together in one manifest -
    {"names": [{"key": <name>, "written": ..., "refused": ...}],
     "together": {"keys": [<name>], "written": ..., "refused": ...}}
"""
import copy
import json
import math
import sys
import typing
from pathlib import Path

import yaml

repository = Path(sys.argv[1])
out = Path(sys.argv[2])
sys.path.insert(0, str(repository / 'Model-Definition-Builder' / 'cli' / 'src'))

import warnings  # noqa: E402

warnings.simplefilter('ignore')
import mdb  # noqa: E402
from mdb.core import generator  # noqa: E402
from mdb.core import manifest as manifest_module  # noqa: E402

core = generator.CoreAssets.load(repository / 'Model-Definition-Builder' / 'definition-core')


def write(data):
    """mdb's way from a manifest to its options.json: written, or refused."""
    try:
        model = manifest_module.ModelManifest.model_validate(data)
        rendered = generator.render_assets(model, core)
    except Exception as error:  # every refusal of mdb, whatever its class
        return None, f'{type(error).__name__}: {error}'.strip()
    text = rendered['options.json']
    return (text.decode('utf-8') if isinstance(text, bytes) else text), None


# The manifests a shape can be put into: every model of LLM-Definitions with
# an api_key auth that mdb generates without options. An option of the
# vocabulary goes into the first whose score template supports it.
bases = []
for folder in sorted((repository / 'LLM-Definitions').iterdir()):
    source = folder / manifest_module.MANIFEST_FILENAME
    if not source.is_file():
        continue
    data = yaml.safe_load(source.read_text(encoding='utf-8'))
    if ((data.get('provider') or {}).get('auth') or {}).get('mode') != 'api_key':
        continue
    data['options'] = {}
    if write(data)[0] is not None:
        bases.append(data)
if not bases:
    raise SystemExit('no model of LLM-Definitions with an api_key auth that mdb generates without options')
base = bases[0]
UNSUPPORTED = 'is not supported by score template family'

if len(sys.argv) > 3:
    names = json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
    OPTION = {'type': 'enum', 'default': 'high', 'values': ['low', 'high'], 'informational': True}
    alone = []
    for name in names:
        data = copy.deepcopy(base)
        data['options'] = {name: dict(OPTION)}
        written, refused = write(data)
        alone.append({'key': name, 'written': written, 'refused': refused})
    keys = [entry['key'] for entry in alone if entry['written'] is not None]
    data = copy.deepcopy(base)
    data['options'] = {name: dict(OPTION) for name in keys}
    written, refused = write(data)
    out.write_text(json.dumps({'mdb': mdb.__file__, 'base': base['model_id'], 'names': alone, 'together': {'keys': keys, 'written': written, 'refused': refused}}, indent=1), encoding='utf-8')
    print(f'{len(names)} names, {len(keys)} written alone, together {"written" if written is not None else "refused"}, mdb from {mdb.__file__}')
    raise SystemExit(0)


def base_for(key):
    """The first manifest whose score template supports the option, or None."""
    for candidate in bases:
        data = copy.deepcopy(candidate)
        data['options'] = {key: {'default': 0, 'type': 'number'}}
        refused = write(data)[1]
        if not refused or UNSUPPORTED not in refused:
            return candidate
    return None

# Values of each kind a field of OptionSpec takes, by the kinds of its
# annotation. A kind without values here stops the script: the shapes follow
# the code. Among them the values the score code would read otherwise than
# options.json says (round 26): a text with a comma, with blanks or quotes at
# its ends, one read as a number or as true, and a number the page sends as
# 1e-7 or 1e+21 - mdb refuses each.
VALUES_OF_KIND = {
    float: [0.5, math.nan, math.inf, 1e-07, 1e21],
    int: [3],
    bool: [True, False],
    str: ['', 'low', '2', 'a,b', ' a ', '"a"', 'TRUE'],
    type(None): [None],
}


def kinds(annotation):
    args = typing.get_args(annotation)
    if typing.get_origin(annotation) is typing.Literal:
        return [('literal', value) for value in args]
    if args and typing.get_origin(annotation) in (typing.Union, getattr(__import__('types'), 'UnionType', typing.Union)):
        found = []
        for arg in args:
            found.extend(kinds(arg))
        return found
    if typing.get_origin(annotation) is list:
        return [('list', args[0])]
    return [('kind', annotation)]


def values_of(name, annotation):
    found = []
    for kind, of in kinds(annotation):
        if kind == 'literal':
            found.append(of)
        elif kind == 'list':
            if of is not str:
                raise SystemExit(f'OptionSpec.{name}: a list of {of}, which this script has no values for')
            found.extend([[], ['low', 'high'], ['high', 'low', 'max', 'min', 'mid', 'off'], ['low', 'a,b'], [' low', 'high'], ['True', 'False']])
        elif of in VALUES_OF_KIND:
            found.extend(VALUES_OF_KIND[of])
        else:
            raise SystemExit(f'OptionSpec.{name}: values of {of}, which this script has none for')
    return found


fields = manifest_module.OptionSpec.model_fields
# The fields that never reach options.json are written with one value each
# (min and max shape a range text, which the page does not read; informational
# keeps an option from the provider, not from options.json; value_map is the
# translation the score code sends, and options.json holds the normalized
# values); every other field with each of its values.
SINGLE = {'min': None, 'max': None, 'range': None, 'informational': None, 'value_map': None}
values = {name: values_of(name, field.annotation) for name, field in fields.items() if name not in SINGLE}
unknown = [name for name in fields if name not in SINGLE and name not in ('type', 'default', 'values', 'label', 'description')]
if unknown:
    raise SystemExit(f'OptionSpec has fields this script does not know: {", ".join(unknown)}')


def specs():
    for type_ in values['type']:
        for default in values['default']:
            for option_values in values['values']:
                # label and description are texts or none (OptionSpec): both
                # left out, or both given.
                for label, description in [(None, None), ('My Label', 'A description')]:
                    spec = {'type': type_, 'default': default, 'values': option_values, 'label': label, 'description': description}
                    yield {name: value for name, value in spec.items() if value is not None}


def shape_name(key, spec):
    return f'{key} ' + json.dumps(spec, sort_keys=True, default=str)


results = []
# Options of the vocabulary no LLM template supports (those of embedding
# models) are in no options.json of an LLM.
not_of_an_llm = []
keys = ['my_option', *sorted(core.vocabulary)]
for key in keys:
    home = base if key == 'my_option' else base_for(key)
    if home is None:
        not_of_an_llm.append(key)
        continue
    for spec in specs():
        data = copy.deepcopy(home)
        data['options'] = {key: {name: value for name, value in spec.items()}}
        written, refused = write(data)
        results.append({'shape': shape_name(key, spec), 'key': key, 'written': written, 'refused': refused})

# The provider key: AuthBlock.key_name of each kind, beside no option.
for key_name in values_of('key_name', manifest_module.AuthBlock.model_fields['key_name'].annotation):
    data = copy.deepcopy(base)
    data['provider']['auth'] = {'mode': 'api_key', 'key_name': key_name}
    written, refused = write(data)
    results.append({'shape': 'auth key_name ' + json.dumps(key_name, default=str), 'key': 'API_KEY', 'written': written, 'refused': refused})

# The provider key from the container's environment (auth.mode env): no
# API_KEY entry at all - alone, and beside an option the judge reads by name.
for options in ({}, {'temperature': {'default': 0.5}}):
    data = copy.deepcopy(base)
    data['provider']['auth'] = {'mode': 'env'}
    data['options'] = copy.deepcopy(options)
    written, refused = write(data)
    results.append({'shape': 'auth env ' + json.dumps(options), 'key': 'API_KEY', 'written': written, 'refused': refused})

# An option named API_KEY in a manifest without a provider key.
for spec in specs():
    data = copy.deepcopy(base)
    data['provider']['auth'] = {'mode': 'none'}
    data['options'] = {'API_KEY': spec}
    written, refused = write(data)
    results.append({'shape': shape_name('API_KEY', spec), 'key': 'API_KEY', 'written': written, 'refused': refused})

# base_url of a self-hosted model: provider.params.base_url of every kind YAML gives.
for base_url in ['http://localhost:11434/v1', '', 5, 0.5, True, None, ['http://a'], {'url': 'http://a'}]:
    data = copy.deepcopy(base)
    data['runtime']['template'] = 'openai_compat_selfhosted'
    data['provider']['endpoint'] = None
    data['provider']['params'] = {'base_url': base_url}
    written, refused = write(data)
    results.append({'shape': 'params base_url ' + json.dumps(base_url), 'key': 'base_url', 'written': written, 'refused': refused})

out.write_text(json.dumps({'mdb': mdb.__file__, 'base': base['model_id'], 'notOfAnLlm': not_of_an_llm, 'results': results}, indent=1), encoding='utf-8')
print(f'{len(results)} shapes, {sum(1 for r in results if r["written"] is not None)} written, mdb from {mdb.__file__}')
