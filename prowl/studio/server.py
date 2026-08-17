# The studio server: a workspace of .prowl files, checked for free.
#
#   prowl-studio [--root DIR] [--host 127.0.0.1] [--port 8788] [--open]
#
# Loopback by default and deliberately. A run spends money and the @file tool reads the
# filesystem; neither belongs on an interface reachable from anywhere else by accident.

import os, json, asyncio, argparse, webbrowser
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, lang, workspace
from ..lib.prowl import prowl
from ..lib.log import log
from ..lib.error import APIError, GenerationError

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

app = FastAPI(title='prowl studio', version='0.2.0')


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


class CheckBody(BaseModel):
    scripts: List[str]
    inputs: Dict[str, str] = {}


class RunBody(BaseModel):
    run_id: str
    scripts: List[str]
    inputs: Dict[str, str] = {}
    model: Optional[str] = None
    atomic: bool = False
    continue_ratio: float = 0.5
    extra: Optional[Dict[str, Any]] = None   # provider pinning, seed, response_format


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

    async def stop_event():
        return state['stop']

    async def token_event(text, finish_reason=None, variable_name=None):
        await q.put(('token', {'text': text, 'variable': variable_name,
                               'finish_reason': finish_reason}))

    async def variable_event(script_name, variable):
        # atomic=True is what carries `arg`, so the budget and temperature a value was declared
        # with travel with the value. Without it the results panel can only show what came back,
        # never what was asked for.
        await q.put(('var', {'script': script_name, **variable.to_dict(history=True, atomic=True)}))

    async def script_event(task, fill, output=None):
        await q.put(('script_end', {'task': task, 'output': output,
                                    'usage': fill.usage.dict()}))

    s = core.build(workspace.folders(ws), include_library=False)
    s.stop_event, s.token_event = stop_event, token_event
    s.variable_event, s.script_event = variable_event, script_event

    inputs = {k: v for k, v in body.inputs.items() if v.strip()}   # as in validate: blank is unset

    async def drive():
        try:
            errors, decls, cap = core.check(s, body.scripts, inputs)
            if errors:
                await q.put(('error', {'errors': errors}))
                return
            if cap > core.budget():
                await q.put(('error', {'errors': [{'message':
                    f"worst case {cap} completion tokens over {decls} declarations exceeds "
                    f"budget {core.budget()}"}]}))
                return
            await q.put(('start', {'scripts': body.scripts, 'declarations': decls,
                                   'max_completion_tokens': cap, 'model': body.model}))
            r = await s.run(body.scripts, inputs=inputs, atomic=body.atomic,
                            model=body.model, extra=body.extra,
                            continue_ratio=body.continue_ratio,
                            stream_level=prowl.StreamLevel.TOKEN)
            done = r.to_dict()
            done['variables'] = {k: v.to_dict(history=True, atomic=True)
                                 for k, v in r.variables.items()}
            await q.put(('done', {'ok': True, 'stopped': state['stop'], **done}))
        except GenerationError as e:
            await q.put(('error', {'failed_variable': e.variable, 'errors': [e.to_dict()]}))
        except APIError as e:
            await q.put(('error', {'fatal': e.fatal(), 'errors': [e.to_dict()]}))
        except Exception as e:
            await q.put(('error', {'errors': [{'message': f"{type(e).__name__}: {e}"}]}))
        finally:
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
    log.info(f"prowl studio http://{a.host}:{a.port}  workspaces {workspace.root()}")
    if a.open:
        webbrowser.open(f"http://{a.host}:{a.port}")
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level='warning')


if __name__ == '__main__':
    main()
