# The studio server: a workspace of .prowl files, checked for free.
#
#   prowl-studio [--root DIR] [--host 127.0.0.1] [--port 8788] [--open]
#
# Loopback by default and deliberately. A run spends money and the @file tool reads the
# filesystem; neither belongs on an interface reachable from anywhere else by accident.

import os, json, time, asyncio, argparse, webbrowser
from typing import Optional, List, Dict, Any

import requests

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, embed, export, history, lang, workspace
from ..lib.prowl import prowl
from ..lib.vllm import PREFILL
from ..lib.log import log
from ..lib.error import APIError, GenerationError

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

app = FastAPI(title='Prompt Owl', version='0.2.0')


@app.exception_handler(workspace.WorkspaceError)
async def _workspace_error(request: Request, e: workspace.WorkspaceError):
    return JSONResponse(status_code=400, content={'error': str(e)})


def stack(ws):
    # Rebuilt per request. Files change under an editor constantly and the parse is cheap, so
    # holding one is a cache invalidation problem bought for nothing.
    return core.build(workspace.folders(ws), include_library=False)


class NewWorkspace(BaseModel):
    name: str


class ScriptBody(BaseModel):
    prowl: str
    prout: Optional[str] = None   # None leaves an existing template alone, "" deletes it
    folder: str = ''


class RenameBody(BaseModel):
    to: str


class StackBody(BaseModel):
    scripts: List[str]
    inputs: Dict[str, str] = {}
    models: List[str] = []
    atomic: bool = False
    provider: Optional[str] = None
    sweep: List[str] = []      # inputs whose box holds one value per line, run one at a time
    chat: Optional[bool] = None


class EmbedBody(BaseModel):
    stack: str
    variable: str
    by: str = 'model'                    # the axis the group came from, and which slice of it
    group: Optional[str] = None          # None or 'all' means every recorded value
    embed_model: Optional[str] = None    # the embedder, not the model whose values these are
    threshold: float = embed.THRESHOLD


class CheckBody(BaseModel):
    scripts: List[str]
    inputs: Dict[str, str] = {}


class RunBody(BaseModel):
    run_id: str
    scripts: List[str]
    inputs: Dict[str, str] = {}
    models: List[str] = []                   # empty means whatever PROWL_MODEL is set to
    atomic: bool = False
    continue_ratio: float = 0.5
    extra: Optional[Dict[str, Any]] = None   # provider pinning, seed, response_format
    stack_name: Optional[str] = None         # recorded with every sample, if this run has one
    chat: Optional[bool] = None              # None follows the endpoint and PROWL_CHAT


# run_id -> {'stop': bool}. The client picks the id so it can arm the stop button before the
# first token, rather than waiting for a response that will not arrive until the run is over.
RUNS: Dict[str, dict] = {}


@app.get('/api/health')
def health():
    return {'ok': True, 'root': workspace.root(), 'budget': core.budget(),
            'endpoint': os.getenv('PROWL_VLLM_ENDPOINT'),
            'model': os.getenv('PROWL_MODEL'),
            # never the key itself, only whether a run has any chance of working
            'has_key': bool(os.getenv('PROWL_VENDOR_API_KEY'))}


@app.get('/api/lang')
def language():
    return lang.describe()


# The catalogue needs no key and changes slowly, so one fetch serves the session.
CATALOGUE = os.getenv('PROWL_MODELS_URL', 'https://openrouter.ai/api/v1/models')
_cat: Dict[str, Any] = {'at': 0.0, 'rows': [], 'error': None}


def catalogue(force=False):
    if not force and _cat['rows'] and time.time() - _cat['at'] < 1800:
        return _cat['rows']
    try:
        d = requests.get(CATALOGUE, timeout=20).json()['data']
    except Exception as e:
        _cat['error'] = f"{type(e).__name__}: {e}"
        return _cat['rows']
    rows = []
    for m in d:
        sp = m.get('supported_parameters') or []
        pr = m.get('pricing') or {}
        rows.append({
            'id': m['id'],
            'name': m.get('name') or m['id'],
            'context': m.get('context_length'),
            'prompt_price': float(pr.get('prompt') or 0),
            'completion_price': float(pr.get('completion') or 0),
            # `stop` is the one that decides whether prowl works at all
            'stop': 'stop' in sp,
            # 171 of the 284 models that support `stop` also declare `reasoning`, and a reasoning
            # model spends a small budget entirely on thinking and returns an empty string with
            # finish_reason "length". That is the single most common way a prowl declaration
            # fails on a modern model, so it is worth a badge and a default.
            'reasoning': 'reasoning' in sp,
            'structured': 'structured_outputs' in sp,
            'seed': 'seed' in sp,
            'logprobs': 'logprobs' in sp,
            'n': 'n' in sp,
            'instruct_type': (m.get('architecture') or {}).get('instruct_type'),
        })
    _cat.update(at=time.time(), rows=rows, error=None)
    return rows


@app.get('/api/models')
def models(q: str = '', stop: bool = True, free: bool = False, limit: int = 50):
    """Search the model catalogue. `stop` defaults to True because a model without stop
    sequences cannot run a prowl script correctly -- every variable generates to max_tokens and
    is trimmed afterwards, which costs the full budget and gets the shapes wrong."""
    rows = catalogue()
    terms = [t for t in q.lower().split() if t]
    out = []
    for r in rows:
        if stop and not r['stop']:
            continue
        # A negative price is the router's "varies" sentinel (openrouter/auto reports -1e6).
        # It is not free, and sorting on it puts a meta-model above every real one.
        priced = r['completion_price'] >= 0
        if free and not (priced and r['completion_price'] == 0):
            continue
        hay = f"{r['id']} {r['name']}".lower()
        if all(t in hay for t in terms):
            out.append(r)
    out.sort(key=lambda r: (0 if r['completion_price'] >= 0 else 1, r['completion_price'], r['id']))
    return {'total': len(rows), 'matched': len(out), 'models': out[:limit],
            'stale': _cat['error']}


@app.get('/api/model-endpoints')
def model_endpoints(id: str):
    """Which backends serve one model id, at what quantization and price. Same id, different
    machines: pinning is the difference between measuring a model and measuring routing."""
    try:
        d = requests.get(f"https://openrouter.ai/api/v1/models/{id}/endpoints", timeout=20).json()
        eps = (d.get('data') or {}).get('endpoints') or []
    except Exception as e:
        return {'id': id, 'endpoints': [], 'error': f"{type(e).__name__}: {e}"}
    return {'id': id, 'endpoints': [{
        'provider': e.get('provider_name'),
        'quantization': e.get('quantization'),
        'context': e.get('context_length'),
        'completion_price': float((e.get('pricing') or {}).get('completion') or 0),
        'stop': 'stop' in (e.get('supported_parameters') or []),
    } for e in eps]}


# Short, unambiguous, and its continuation is one token. A model that restates it instead of
# continuing it is not doing prefix continuation, whatever the endpoint claims.
PROBE = 'The capital of France is'


def verdict(text, prompt=PROBE):
    if text is None:
        return 'no-choice'
    t = text.strip()
    if not t:
        return 'empty'
    # OpenRouter serves /v1/completions for chat-only models by adapting them, and the adapter
    # gives itself away: the model restates the prompt rather than carrying on from it.
    if prompt.strip().lower().startswith(t.lower()[:16]):
        return 'echoes'
    return 'continues'


@app.get('/api/model-probe')
def model_probe(id: str, chat: bool = False):
    """Can this model actually continue a document. One five-token request, because the catalogue
    cannot answer it: `stop` support is listed, prefix continuation is not."""
    base = (os.getenv('PROWL_VLLM_ENDPOINT') or '').rstrip('/')
    key = os.getenv('PROWL_VENDOR_API_KEY')
    if not base:
        return {'id': id, 'error': 'PROWL_VLLM_ENDPOINT is not set'}
    thinking = any(m['id'] == id and m.get('reasoning') for m in catalogue())
    payload = {'model': id, 'max_tokens': 5, 'temperature': 0.0}
    if thinking:
        payload['reasoning'] = {'enabled': False}
    if chat:
        url = f"{base}/v1/chat/completions"
        # the same envelope a chat run uses, so the probe measures what a run would get
        payload['messages'] = [{'role': 'user', 'content': PREFILL},
                               {'role': 'assistant', 'content': PROBE}]
    else:
        url = f"{base}/v1/completions"
        payload['prompt'] = PROBE
    headers = {'content-type': 'application/json'}
    if key:
        headers['Authorization'] = f"Bearer {key}"
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        d = r.json()
    except Exception as e:
        return {'id': id, 'ok': False, 'verdict': 'unreachable', 'error': f"{type(e).__name__}: {e}"}
    choices = d.get('choices') or []
    if not choices:
        err = d.get('error')
        return {'id': id, 'ok': False, 'verdict': 'unsupported', 'reasoning': thinking,
                'status': r.status_code,
                'error': (err.get('message') if isinstance(err, dict) else err) or r.text[:160]}
    c = choices[0]
    text = c.get('text') if not chat else ((c.get('message') or {}).get('content'))
    v = verdict(text)
    return {'id': id, 'ok': v == 'continues', 'verdict': v, 'reasoning': thinking,
            'sample': (text or '')[:60], 'chat': chat,
            'advice': {
                'continues': 'runs prowl natively',
                'echoes': 'restates the prompt instead of continuing it — tick `chat` to use assistant prefill',
                'empty': 'returned nothing in five tokens' + (' (reasoning model)' if thinking else ''),
                'unsupported': 'this endpoint refused the request',
                'no-choice': 'answered without a choice',
            }.get(v, '')}


@app.get('/api/workspaces')
def list_workspaces():
    return {'root': workspace.root(), 'workspaces': workspace.workspaces()}


@app.post('/api/workspaces')
def new_workspace(body: NewWorkspace):
    workspace.create(body.name)
    return {'name': body.name}


@app.get('/api/w/{ws}/scripts')
def scripts(ws: str):
    s = stack(ws)
    return {'scripts': core.describe(s), 'collisions': workspace.collisions(ws),
            'tools': sorted(s.tools)}


@app.get('/api/w/{ws}/script/{name}')
def get_script(ws: str, name: str):
    d = workspace.read(ws, name)
    d['outline'] = lang.outline(d['prowl'] or '')
    return d


@app.put('/api/w/{ws}/script/{name}')
def put_script(ws: str, name: str, body: ScriptBody):
    d = workspace.write(ws, name, body.prowl, prout=body.prout, folder=body.folder)
    d['outline'] = lang.outline(d['prowl'] or '')
    return d


@app.get('/api/w/{ws}/stacks')
def get_stacks(ws: str):
    return {'stacks': workspace.stacks(ws)}


@app.put('/api/w/{ws}/stacks/{name}')
def put_stack(ws: str, name: str, body: StackBody):
    return {'stacks': workspace.save_stack(ws, name, body.dict())}


@app.delete('/api/w/{ws}/stacks/{name}')
def delete_stack(ws: str, name: str):
    return {'stacks': workspace.drop_stack(ws, name)}


@app.get('/api/w/{ws}/history')
def get_history(ws: str, stack: str = '', model: str = '', variable: str = '',
                by: str = 'model', clean: bool = True, limit: int = 400):
    """Every value this workspace has produced for a stack, plus what they look like together,
    cut along whichever axis was asked for. Summarised over exactly the rows returned, so the
    statistics always describe the visible sample rather than a larger one the client cannot see."""
    rows, total = history.read(ws, stack=stack or None, model=model or None,
                               variable=variable or None, limit=max(1, min(limit, 5000)))
    if by not in history.AXES and not by.startswith('input:'):
        by = 'model'
    return {'records': rows, 'summary': history.summarise(rows, by=by, clean=clean), 'by': by,
            'clean': clean, 'keys': history.keys(rows), 'shown': len(rows), 'total': total}


@app.delete('/api/w/{ws}/history')
def delete_history(ws: str, stack: str = ''):
    return {'dropped': history.clear(ws, stack=stack or None),
            'documents': history.clear(ws, stack=stack or None, which=history.DOCS)}


@app.get('/api/w/{ws}/export')
def export_history(ws: str, stack: str = '', format: str = 'csv', variable: str = ''):
    """Results in a shape something else already reads. `csv` and `jsonl` carry the samples;
    `messages` and `alpaca` carry prompt/completion pairs cut out of the finished documents by
    the spans, one per declaration."""
    rows, _ = history.read(ws, stack=stack or None, variable=variable or None)
    docs, _ = history.documents(ws, stack=stack or None) if format in ('messages', 'alpaca') else ([], 0)
    try:
        body = export.render(format, rows, docs, variable=variable or None)
    except ValueError as e:
        return JSONResponse(status_code=400, content={'error': str(e)})
    media, ext = export.FORMATS[format]
    name = f"{ws}-{(stack or 'all').replace('/', '-')}.{ext}"
    # Excel reads UTF-8 CSV only with a BOM, and a mangled name column is the first thing anyone
    # notices about an export.
    text = ('﻿' + body) if format == 'csv' else body
    return Response(content=text.encode('utf-8'), media_type=media,
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


@app.get('/api/probe')
def probe():
    """Does the configured endpoint actually answer, and in which shape. One token each, so it
    costs nothing and settles 'is my Ollama/vLLM/OpenRouter set up right' without reading logs."""
    base = (os.getenv('PROWL_VLLM_ENDPOINT') or '').rstrip('/')
    model = os.getenv('PROWL_MODEL')
    key = os.getenv('PROWL_VENDOR_API_KEY')
    if not base:
        return {'endpoint': None, 'error': 'PROWL_VLLM_ENDPOINT is not set'}
    headers = {'content-type': 'application/json'}
    if key:
        headers['Authorization'] = f"Bearer {key}"

    def attempt(path, payload, pick):
        url = base + path
        try:
            r = requests.post(url, json={'model': model, 'max_tokens': 1, **payload},
                              headers=headers, timeout=20)
        except Exception as e:
            return {'url': url, 'ok': False, 'error': f"{type(e).__name__}: {e}"}
        try:
            d = r.json()
        except ValueError:
            return {'url': url, 'ok': False, 'status': r.status_code, 'error': r.text[:200]}
        choices = d.get('choices') or []
        got = pick(choices[0]) if choices else None
        return {'url': url, 'ok': r.status_code == 200 and got is not None,
                'status': r.status_code,
                'error': ((d.get('error') or {}).get('message') if isinstance(d.get('error'), dict)
                          else d.get('error')),
                'sample': (got or '')[:40]}

    return {
        'endpoint': base, 'model': model, 'has_key': bool(key),
        # prowl wants completions; chat works by assistant prefill when it does not
        'completions': attempt('/v1/completions', {'prompt': 'The capital of France is'},
                               lambda c: c.get('text')),
        'chat': attempt('/v1/chat/completions',
                        {'messages': [{'role': 'user', 'content': 'Say ok'}]},
                        lambda c: ((c.get('message') or {}).get('content'))),
    }


@app.post('/api/w/{ws}/embed')
def embed_variable(ws: str, body: EmbedBody):
    """Group one variable's recorded values by meaning rather than by string. Counting distinct
    strings answers "how many names"; it cannot answer "how many answers", because the same answer
    written twice is two strings. This spends money, so it is a button and not a page load."""
    rows, _ = history.read(ws, stack=body.stack or None, variable=body.variable or None)
    if body.group is not None and body.group != 'all':
        rows = [r for r in rows if history.axis(r, body.by) == body.group]
    if not rows:
        return {'error': 'nothing recorded for that variable'}
    try:
        return embed.measure([r.get('value') for r in rows], model=body.embed_model,
                             threshold=body.threshold)
    except Exception as e:
        return JSONResponse(status_code=400, content={'error': f"{type(e).__name__}: {e}"})


@app.post('/api/w/{ws}/script/{name}/rename')
def rename_script(ws: str, name: str, body: RenameBody):
    # server side so the .prout moves with its script and a clash is refused rather than raced
    return workspace.rename(ws, name, body.to)


@app.delete('/api/w/{ws}/script/{name}')
def delete_script(ws: str, name: str):
    workspace.remove(ws, name)
    return {'ok': True}


@app.post('/api/w/{ws}/validate')
def validate(ws: str, body: CheckBody):
    s = stack(ws)
    # An empty box is not an answer. Counting it as supplied silences the very error that says the
    # variable has nothing behind it, and lets a run start with a blank spliced into the prompt.
    supplied = {k: v for k, v in body.inputs.items() if v.strip()}
    errors, decls, cap = core.check(s, body.scripts, supplied)

    # What the stack TAKES, which is a property of the scripts and their order and nothing else.
    # Deriving this from what is still missing instead makes the list shrink as it is filled in,
    # and a form that deletes the field you are typing into is not a form.
    declared, needs = set(), []
    for name in body.scripts:
        if name not in s.tasks:
            continue
        vars, _ = s.get_inspect(name)
        for ref in sorted(set(vars['referenced']) - set(vars['declared'])):
            if ref not in declared and ref not in needs:
                needs.append(ref)
        declared.update(vars['declared'])
    cap_budget = core.budget()
    return {
        'ok': not errors,
        'errors': errors,
        'declarations': decls,
        'max_completion_tokens': cap,
        'budget': cap_budget,
        'over_budget': cap > cap_budget,
        'inputs_required': needs,
        'inputs_missing': [n for n in needs if n not in supplied],
        'collisions': workspace.collisions(ws),
    }


@app.post('/api/w/{ws}/run')
async def run(ws: str, body: RunBody):
    # Server-sent events, not a websocket. The run is one-way; the only thing the client says
    # during it is "stop", which is its own POST flipping the flag stop_event already polls.
    # An SSE stream is also curl-able, which matters the first time streaming misbehaves.
    q: asyncio.Queue = asyncio.Queue()
    state = {'stop': False}
    RUNS[body.run_id] = state

    models = body.models or [None]           # None means whatever PROWL_MODEL is set to
    inputs = {k: v for k, v in body.inputs.items() if v.strip()}   # as in validate: blank is unset

    # Every value this run produces, written once when the run ends. Which backend answered is
    # part of the sample: the same id on two providers is two quantizations, and a distribution
    # pooled across them is a distribution of the router.
    pin = (((body.extra or {}).get('provider') or {}).get('order') or [None])[0]
    sig, samples = history.signature(body.scripts), []

    # A reasoning model given `{answer:number(8)}` spends all eight tokens thinking and returns
    # "" with finish_reason "length" -- which prowl reports as "no number in the completion",
    # sending you to look at a prompt that was fine. Prowl declarations are bounded by design, so
    # thinking is turned off unless the caller asked for it. 171 of the 284 stop-capable models
    # on OpenRouter declare it, so this is the common case, not an edge one.
    thinkers = {m['id'] for m in catalogue() if m.get('reasoning')}

    def request_extra(model):
        e = dict(body.extra or {})
        if model in thinkers and 'reasoning' not in e:
            e['reasoning'] = {'enabled': False}
        return e or None

    async def stop_event():
        return state['stop']

    # One rig per model. ProwlStack holds run state on the instance, so models running
    # concurrently need their own -- that is the multithreading warning in stack.py reached by a
    # different road. Every event carries its model so the client can column them.
    def rig(model):
        s = core.build(workspace.folders(ws), include_library=False)

        async def token_event(text, finish_reason=None, variable_name=None):
            await q.put(('token', {'model': model, 'text': text, 'variable': variable_name,
                                   'finish_reason': finish_reason}))

        async def variable_event(script_name, variable):
            # atomic=True is what carries `arg`, so the budget and temperature a value was declared
            # with travel with the value. Without it the results panel can only show what came back,
            # never what was asked for.
            samples.append({
                'at': round(time.time(), 3), 'run': body.run_id,
                'stack': sig, 'stack_name': body.stack_name,
                'model': model or os.getenv('PROWL_MODEL'), 'provider': pin,
                'chat': bool(body.chat), 'script': script_name,
                'variable': variable.name, 'type': variable.type,
                'value': variable.value, 'temp': variable.temperature, 'max': variable.max_tokens,
                'tokens': variable.usage.completion_tokens if variable.usage else None,
                'truncated': bool(variable.truncated), 'inputs': history.clip(inputs),
            })
            await q.put(('var', {'model': model, 'script': script_name,
                                 **variable.to_dict(history=True, atomic=True)}))

        async def script_event(task, fill, output=None):
            await q.put(('script_end', {'model': model, 'task': task, 'output': output,
                                        'usage': fill.usage.dict()}))

        s.stop_event, s.token_event = stop_event, token_event
        s.variable_event, s.script_event = variable_event, script_event
        return s

    async def drive_one(model):
        try:
            r = await rig(model).run(body.scripts, inputs=inputs, atomic=body.atomic,
                                     model=model, extra=request_extra(model), chat=body.chat,
                                     continue_ratio=body.continue_ratio,
                                     stream_level=prowl.StreamLevel.TOKEN)
            done = r.to_dict()
            done['variables'] = {k: v.to_dict(history=True, atomic=True)
                                 for k, v in r.variables.items()}
            # Keep the finished document and where each declaration landed in it. Without the
            # spans a value is a value; with them the run is a set of prompt/completion pairs.
            spans = {}
            for name, v in r.variables.items():
                for h in v.hist():
                    if h.get('span'):
                        spans.setdefault(name, []).append(h['span'])
            history.save_document(ws, {
                'at': round(time.time(), 3), 'run': body.run_id, 'stack': sig,
                'stack_name': body.stack_name, 'model': model or os.getenv('PROWL_MODEL'),
                'provider': pin, 'chat': bool(body.chat), 'inputs': history.clip(inputs),
                'completion': r.completion, 'spans': spans, 'usage': r.usage.dict(),
            })
            await q.put(('done', {'model': model, 'ok': True, 'stopped': state['stop'], **done}))
        except GenerationError as e:
            # the whole point of comparing models: WHICH variable stopped filling on this one
            await q.put(('done', {'model': model, 'ok': False, 'failed_variable': e.variable,
                                  'errors': [e.to_dict()]}))
        except APIError as e:
            await q.put(('done', {'model': model, 'ok': False, 'fatal': e.fatal(),
                                  'errors': [e.to_dict()]}))
        except Exception as e:
            await q.put(('done', {'model': model, 'ok': False,
                                  'errors': [{'message': f"{type(e).__name__}: {e}"}]}))

    async def drive():
        try:
            probe = core.build(workspace.folders(ws), include_library=False)
            errors, decls, cap = core.check(probe, body.scripts, inputs)
            if errors:
                await q.put(('error', {'errors': errors}))
                return
            total = cap * len(models)
            if total > core.budget():
                await q.put(('error', {'errors': [{'message':
                    f"worst case {cap} completion tokens x {len(models)} model(s) = {total} "
                    f"exceeds budget {core.budget()}"}]}))
                return
            await q.put(('start', {'scripts': body.scripts, 'declarations': decls,
                                   'max_completion_tokens': cap, 'models': models}))
            # concurrently: a pool of six run in the time of the slowest, not the sum
            await asyncio.gather(*(drive_one(m) for m in models))
            await q.put(('finished', {'models': models}))
        except Exception as e:
            await q.put(('error', {'errors': [{'message': f"{type(e).__name__}: {e}"}]}))
        finally:
            # A run that failed halfway still produced the values it got to, and those are the
            # ones worth looking at when a model starts drifting.
            try:
                history.append(ws, samples)
            except Exception as e:
                log.warn(f"could not record run history: {type(e).__name__}: {e}")
            await q.put((None, None))

    async def events():
        task = asyncio.create_task(drive())
        try:
            while True:
                kind, data = await q.get()
                if kind is None:
                    break
                yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"
        finally:
            RUNS.pop(body.run_id, None)
            if not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/run/{run_id}/stop')
def stop_run(run_id: str):
    state = RUNS.get(run_id)
    if not state:
        return {'ok': False, 'error': 'no such run'}
    # stop_event is polled before every script and before every declaration, so this lands
    # within one generation and the partial result still comes back on `done`.
    state['stop'] = True
    return {'ok': True}


@app.middleware('http')
async def revalidate(request: Request, call_next):
    # The client is edited while it is open. A cached style.css against a fresh app.js is a theme
    # picker that changes nothing and a bug report about a feature that works -- no-cache still
    # allows 304s, so this costs a conditional request and nothing else.
    r = await call_next(request)
    if not request.url.path.startswith('/api'):
        r.headers['Cache-Control'] = 'no-cache'
    return r


# Mounted last: a mount at / matches anything the API routes above did not.
if os.path.isdir(WEB):
    app.mount('/', StaticFiles(directory=WEB, html=True), name='web')


def env_file(path):
    # KEY=VALUE lines, with the existing environment winning. Not worth a dependency, and a studio
    # that cannot find a key fails in a way that looks like prowl's fault rather than a missing .env.
    if not path or not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def main():
    ap = argparse.ArgumentParser(prog='prowl-studio')
    ap.add_argument('--root', help='workspace directory (default ~/.prowl/workspaces)')
    ap.add_argument('--env', default='.env', help='KEY=VALUE file for endpoint, key and model')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8788)
    ap.add_argument('--open', action='store_true', help='open a browser at the studio')
    a = ap.parse_args()
    env_file(a.env)
    if a.root:
        os.environ['PROWL_WORKSPACE_ROOT'] = a.root
    os.makedirs(workspace.root(), exist_ok=True)
    if a.host not in ('127.0.0.1', 'localhost', '::1'):
        log.warn(f"listening on {a.host}: this serves scripts that spend money and read files")
    log.info(f"Prompt Owl http://{a.host}:{a.port}  workspaces {workspace.root()}")
    if a.open:
        webbrowser.open(f"http://{a.host}:{a.port}")
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level='warning')


if __name__ == '__main__':
    main()
