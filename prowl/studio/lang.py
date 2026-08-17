# What the editor's highlighter compiles, read straight off the interpreter rather than copied.
#
# The failure this exists to prevent: a highlighter with its own grammar paints
# `{variable_name(max_tokens, temperature)}` as a live declaration when the leading-digit rule
# makes it inert prose, and the author spends an afternoon on a script that was never going to
# generate. Serving prowl's own constants makes that disagreement impossible by construction.

from ..lib.prowl import prowl

# The patterns are valid JavaScript regex exactly as written; only the flags differ.
REGEX_FLAGS = {'FILL': 'g', 'CALL': 'g', 'ARGS': 'g', 'LIST': 'gm', 'MASK': 'gs'}

CODES = {
    1001: 'script not found in the workspace',
    1002: 'tool not loaded in the stack',
    1003: 'required script does not come earlier in the stack',
    1004: 'variable referenced before anything declares it',
    1005: 'unknown declaration option',
    1006: 'missing or non-numeric (max_tokens, temperature)',
    1007: 'stray declaration argument -- quote multi-value options',
    1008: 'unknown variable type',
    1009: 'a type on a reference; the parentheses are what declares',
}


def describe():
    return {
        'patterns': {
            'FILL': {'source': prowl.PATTERN_FILL, 'flags': REGEX_FLAGS['FILL'],
                     'groups': ['name', 'type', 'args']},
            'CALL': {'source': prowl.PATTERN_CALL, 'flags': REGEX_FLAGS['CALL'],
                     'groups': ['tool', 'args']},
            'ARGS': {'source': prowl.PATTERN_ARGS, 'flags': REGEX_FLAGS['ARGS'], 'groups': []},
            'LIST': {'source': prowl.PATTERN_LIST, 'flags': REGEX_FLAGS['LIST'],
                     'groups': ['item']},
            'MASK': {'source': prowl.PATTERN_MASK, 'flags': REGEX_FLAGS['MASK'], 'groups': []},
        },
        'types': prowl.TYPES,
        'options': list(prowl.OPTIONS),
        'option_flags': list(prowl.FLAGS),
        'stops': prowl.STOPS,
        'temperature': prowl.TEMPERATURE,
        'strip': prowl.PATTERN_STRIP,
        'truthy': list(prowl.TRUE),
        'falsy': list(prowl.FALSE),
        'codes': CODES,
    }


def outline(source):
    # Every brace prowl would act on, with the shape it would resolve to. The client recomputes
    # this as you type -- a gutter that needs a round trip is a gutter nobody reads -- so this is
    # the reference it is checked against, not a substitute for it.
    import re
    masked = prowl.mask_prowl_code_blocks(source + "\n")
    out = []
    for m in re.finditer(prowl.PATTERN_FILL, masked):
        entry = {'name': m.group(1), 'type': m.group(2), 'args': m.group(3),
                 'start': m.start(), 'end': m.end(),
                 'kind': 'declaration' if m.group(3) is not None else 'reference'}
        if m.group(3) is not None:
            entry['shape'] = prowl.shape(source + "\n", m.start(), m.end())
        out.append(entry)
    return out
