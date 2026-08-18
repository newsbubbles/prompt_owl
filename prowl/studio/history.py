# What a variable does when you run the same stack again.
#
# One declaration filled once is an anecdote. The properties worth knowing -- how far a number
# moves, how many distinct names a model actually has behind a prompt -- only exist across runs,
# so every filled declaration is appended here and the studio reads them back as a distribution.
#
# runs/history.jsonl, append-only, one line per filled declaration. JSONL because a run appends
# and never rewrites, a torn line costs one sample rather than the file, and jq, pandas and a vts
# harness can all read it without going through this module.

import os, json, math, threading, unicodedata

from . import workspace

FILE = ('runs', 'history.jsonl')
CLIP = 120          # per input value: enough to tell two runs apart, not a copy of the prompt
WRITE = threading.Lock()


def path(ws):
    return workspace.path(ws, *FILE)


def signature(scripts):
    # The stack a sample came from. Script names cannot contain a slash, so this splits back.
    return '/'.join(scripts)


def clip(inputs):
    return {k: (v if len(v) <= CLIP else v[:CLIP] + '…') for k, v in (inputs or {}).items()}


def append(ws, records):
    if not records:
        return 0
    p = path(ws)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    body = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records)
    # One write per run, under a lock. A pool of models finishes at once, and a line spliced into
    # another line loses both samples.
    with WRITE:
        with open(p, 'a', encoding='utf-8', newline='') as f:
            f.write(body)
    return len(records)


def read(ws, stack=None, model=None, variable=None, limit=None):
    """Returns (rows, total). `total` counts everything matching on disk, so a capped view can
    say what it is not showing rather than quietly summarising the tail."""
    p = path(ws)
    if not os.path.exists(p):
        return [], 0
    rows = []
    with open(p, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue        # a half-written last line is one lost sample, not a lost file
            if stack and r.get('stack') != stack:
                continue
            if model and r.get('model') != model:
                continue
            if variable and r.get('variable') != variable:
                continue
            rows.append(r)
    total = len(rows)
    return (rows[-limit:] if limit and total > limit else rows), total


def clear(ws, stack=None):
    p = path(ws)
    if not os.path.exists(p):
        return 0
    if not stack:
        n = sum(1 for _ in open(p, 'r', encoding='utf-8'))
        os.remove(p)
        return n
    keep, dropped = [], 0
    with open(p, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get('stack') == stack:
                dropped += 1
            else:
                keep.append(line if line.endswith('\n') else line + '\n')
    with WRITE:
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.writelines(keep)
    return dropped


# ------------------------------------------------------------------- statistics

def fold(v):
    # Case and whitespace only. Stripping by character class is how a grader of mine once erased
    # every CJK name to the empty string and reported perfect agreement.
    return ' '.join(unicodedata.normalize('NFKC', str(v)).split()).casefold()


def numbers(values):
    # All or nothing: one value that is not a number means this variable is not numeric. `nan`
    # and `inf` parse as floats and are refused here -- they would poison the mean, and json
    # writes them as bare NaN, which is not JSON and takes the whole response down with it.
    out = []
    for v in values:
        try:
            f = float(str(v).replace(',', '').strip())
        except ValueError:
            return None
        if not math.isfinite(f):
            return None
        out.append(f)
    return out


def stats(values):
    vals = [str(v) for v in values if v is not None]
    d = {'n': len(vals), 'unique': len(set(vals))}
    if not vals:
        return d

    counts = {}
    for k in (fold(v) for v in vals):
        counts[k] = counts.get(k, 0) + 1
    d['unique_folded'] = len(counts)
    d['top'] = [[k, c] for k, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
    d['mode_share'] = max(counts.values()) / len(vals)

    # Normalised entropy: 1.0 when every run answered differently, 0.0 when they all agreed.
    # Divided by log(n) rather than log(unique) on purpose -- against log(unique) a model that
    # only ever says two things scores 1.0 for saying each of them half the time.
    if len(vals) > 1:
        h = -sum((c / len(vals)) * math.log(c / len(vals)) for c in counts.values())
        d['entropy'] = h / math.log(len(vals)) if h else 0.0   # else -0.0, which reads as a bug

    nums = numbers(vals)
    if nums:
        mean = sum(nums) / len(nums)
        sd = None
        if len(nums) > 1:
            sd = math.sqrt(sum((x - mean) ** 2 for x in nums) / (len(nums) - 1))
        d['numeric'] = {'mean': mean, 'sd': sd, 'min': min(nums), 'max': max(nums)}
    return d


# Which axis the samples are cut along. `model` answers "do these models differ"; `input:language`
# answers "does this prompt hold up in Japanese" -- the same values, a different question, and the
# statistics are only worth anything once you can choose which one you are asking.
AXES = ('model', 'provider', 'stack_name', 'script', 'type', 'run', 'none')


def axis(r, by):
    if by == 'none':
        return ''
    if by.startswith('input:'):
        return (r.get('inputs') or {}).get(by[len('input:'):], '')
    return r.get(by) or ''


def keys(rows):
    # Every input name seen, so the client can offer them as axes without guessing.
    out = set()
    for r in rows:
        out.update((r.get('inputs') or {}))
    return sorted(out)


# A truncated `text` is a long answer cut short and still says what it says. A truncated `word`,
# `line`, `number` or `bool` is a model that never hit the stop -- it wrote prose, and for a
# number the "last number wins" rule then lifts a figure out of the middle of a sentence. Those
# are not answers, and averaging them in produced a mean of 33.1 out of values including 0.75.
BOUNDED = ('word', 'line', 'number', 'bool')


def describe(rows, variable, group, clean=True):
    kind = rows[-1].get('type')
    used, dropped = rows, 0
    if clean and kind in BOUNDED:
        keep = [r for r in rows if not r.get('truncated')]
        # unless that is all of them, in which case report the mess rather than an empty row
        if keep:
            used, dropped = keep, len(rows) - len(keep)
    return {
        'variable': variable, 'group': group,
        'type': kind,
        'temp': rows[-1].get('temp'),
        'models': sorted({r.get('model') for r in rows if r.get('model')}),
        'runs': len({r.get('run') for r in rows}),
        'truncated': sum(1 for r in rows if r.get('truncated')),
        'dropped': dropped,
        'stats': stats([r.get('value') for r in used]),
    }


def summarise(rows, by='model', clean=True):
    """One row per (variable, group). Variables keep the order they were first seen in rather than
    alphabetical order, because that is the order the document builds them in.

    Each variable also gets a pooled row across every group, when there is more than one. Pooled
    and split answer different questions: three models each answering `7` every time is entropy
    0.00 three times and 0.00 pooled, while three models each stuck on a *different* number is
    0.00 three times and 1.58 pooled. Only having the split numbers hides that."""
    order, groups, whole = [], {}, {}
    for r in rows:
        v = r.get('variable')
        k = (v, axis(r, by))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
        whole.setdefault(v, []).append(r)

    seen, out = set(), []
    for v, g in order:
        if v not in seen:
            seen.add(v)
            splits = sum(1 for a, _ in order if a == v)
            if splits > 1:
                d = describe(whole[v], v, 'all', clean=clean)
                d['pooled'] = splits
                out.append(d)
        out.append(describe(groups[(v, g)], v, g, clean=clean))
    return out
