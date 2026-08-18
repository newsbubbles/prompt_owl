# How far apart the values are, when counting them exactly is the wrong question.
#
# Exact uniqueness answers "how many different names": twelve strings, twelve names. It cannot
# answer "how many different answers", because `a dark harbour town` and `a harbour town at
# night` are one answer written twice. That needs vectors.
#
# Same vendor and same key as everything else here. OpenRouter serves /v1/embeddings even though
# its model catalogue lists no embedding model at all -- checking the catalogue is what nearly got
# this written off as impossible.

import os, math

import requests

DEFAULT = os.getenv('PROWL_EMBED_MODEL', 'baai/bge-m3')
# Cosine similarity above which two values are the same answer. Not a constant of nature: it is
# reported alongside every count so a cluster number is never read without the threshold that
# produced it.
THRESHOLD = 0.85


def url():
    base = (os.getenv('PROWL_VLLM_ENDPOINT') or 'https://openrouter.ai/api').rstrip('/')
    return base + '/v1/embeddings'


def vectors(texts, model=None, timeout=60):
    key = os.getenv('PROWL_VENDOR_API_KEY')
    if not key:
        raise RuntimeError('no PROWL_VENDOR_API_KEY: embedding needs the same key a run does')
    r = requests.post(url(), timeout=timeout,
                      headers={'Authorization': f"Bearer {key}", 'content-type': 'application/json'},
                      json={'model': model or DEFAULT, 'input': list(texts)})
    d = r.json()
    if 'data' not in d:
        raise RuntimeError((d.get('error') or {}).get('message') or f"embeddings failed: {r.status_code}")
    # The vendor is not required to return them in order, and a scrambled matrix is a plausible
    # wrong answer rather than an error.
    rows = sorted(d['data'], key=lambda x: x.get('index', 0))
    return [x['embedding'] for x in rows], (d.get('usage') or {})


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def cluster(vecs, threshold=THRESHOLD):
    """Leader clustering: every member is compared to the leader that started the cluster, never
    to a running mean. A mean that moves with each member drifts across the space and swallows
    the corpus into one cluster."""
    leaders, assign = [], []
    for v in vecs:
        for i, L in enumerate(leaders):
            if cosine(v, L) >= threshold:
                assign.append(i)
                break
        else:
            leaders.append(v)
            assign.append(len(leaders) - 1)
    return len(leaders), assign


def spread(vecs):
    # Mean pairwise cosine distance. 0 means every value said the same thing.
    n = len(vecs)
    if n < 2:
        return None
    total = sum(1.0 - cosine(vecs[i], vecs[j]) for i in range(n) for j in range(i + 1, n))
    return total / (n * (n - 1) / 2)


def measure(values, model=None, threshold=THRESHOLD):
    """Embed the DISTINCT values only. Twenty runs of a stack that answered the same thing twenty
    times is one vector's worth of information and twenty times the bill."""
    seen, distinct = set(), []
    for v in values:
        s = '' if v is None else str(v)
        if s not in seen:
            seen.add(s)
            distinct.append(s)
    usable = [v for v in distinct if v.strip()]
    if len(usable) < 2:
        return {'n': len(values), 'distinct': len(distinct), 'embedded': len(usable),
                'model': model or DEFAULT, 'threshold': threshold,
                'note': 'needs two non-empty distinct values'}
    vecs, usage = vectors(usable, model=model)
    groups, assign = cluster(vecs, threshold)
    members = {}
    for v, g in zip(usable, assign):
        members.setdefault(g, []).append(v)
    return {
        'n': len(values), 'distinct': len(distinct), 'embedded': len(usable),
        'model': model or DEFAULT, 'dim': len(vecs[0]), 'threshold': threshold,
        'clusters': groups,
        'spread': spread(vecs),
        'groups': sorted(members.values(), key=len, reverse=True),
        'usage': {'prompt_tokens': usage.get('prompt_tokens', 0), 'cost': usage.get('cost', 0.0)},
    }
