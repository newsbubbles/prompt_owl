# Getting the results out, in formats something else already reads.
#
# A log is where measurements go to die. These are the four shapes people actually load: a table,
# the raw records, and two training formats -- because a prowl run is one token sequence whose
# spans are named, so `completion[:start]` is exactly the prompt that produced
# `completion[start:end]`, and one run yields one training pair per declaration.

import io, csv, json

# Sample columns, in the order a person reads them. Inputs are appended after these, one column
# per input name seen, since they vary per workspace.
COLUMNS = ['at', 'run', 'stack', 'stack_name', 'model', 'provider', 'chat', 'script', 'variable',
           'type', 'value', 'temp', 'max', 'tokens', 'truncated']

FORMATS = {
    'csv':      ('text/csv', 'csv'),
    'jsonl':    ('application/x-ndjson', 'jsonl'),
    'json':     ('application/json', 'json'),
    'messages': ('application/x-ndjson', 'messages.jsonl'),
    'alpaca':   ('application/x-ndjson', 'alpaca.jsonl'),
}


def to_csv(rows):
    keys = sorted({k for r in rows for k in (r.get('inputs') or {})})
    buf = io.StringIO(newline='')
    # utf-8-sig at the response layer, not here: Excel needs the BOM to read UTF-8, everything
    # else is happy either way.
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(COLUMNS + [f"input.{k}" for k in keys])
    for r in rows:
        ins = r.get('inputs') or {}
        w.writerow([r.get(c, '') for c in COLUMNS] + [ins.get(k, '') for k in keys])
    return buf.getvalue()


def to_jsonl(rows):
    return ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows)


def to_json(rows):
    return json.dumps(rows, ensure_ascii=False, indent=1)


def pairs(docs, variable=None, minimum=1):
    """Every declaration in every run document, as (prompt, completion).

    The prompt is the real one -- the whole growing document up to that declaration, including
    every value spliced in before it. That is what conditioning means here, and it is why these
    pairs are worth more than a template with the inputs pasted back in."""
    out = []
    for d in docs:
        text = d.get('completion') or ''
        for name, spans in (d.get('spans') or {}).items():
            if variable and name != variable:
                continue
            for span in spans:
                start, end = span
                if not (0 <= start < end <= len(text)):
                    continue
                value = text[start:end]
                if len(value.strip()) < minimum:
                    continue
                out.append({'prompt': text[:start], 'completion': value, 'variable': name,
                            'model': d.get('model'), 'stack': d.get('stack'),
                            'inputs': d.get('inputs') or {}})
    return out


def to_messages(docs, variable=None):
    """OpenAI fine-tuning shape. The document goes in as the user turn and the value as the
    assistant turn, which is the same assistant-prefill correspondence prowl uses to talk to chat
    endpoints in the first place."""
    return ''.join(json.dumps({'messages': [
        {'role': 'user', 'content': p['prompt']},
        {'role': 'assistant', 'content': p['completion']},
    ]}, ensure_ascii=False) + '\n' for p in pairs(docs, variable))


def to_alpaca(docs, variable=None):
    return ''.join(json.dumps({
        'instruction': p['prompt'], 'input': '', 'output': p['completion'],
    }, ensure_ascii=False) + '\n' for p in pairs(docs, variable))


def render(fmt, rows, docs, variable=None):
    if fmt == 'csv':
        return to_csv(rows)
    if fmt == 'jsonl':
        return to_jsonl(rows)
    if fmt == 'json':
        return to_json(rows)
    if fmt == 'messages':
        return to_messages(docs, variable)
    if fmt == 'alpaca':
        return to_alpaca(docs, variable)
    raise ValueError(f"unknown format `{fmt}`: one of {', '.join(sorted(FORMATS))}")
