# The studio server: a workspace of .prowl files, checked for free.
#
#   prowl-studio [--root DIR] [--host 127.0.0.1] [--port 8788] [--open]
#
# Loopback by default and deliberately. A run spends money and the @file tool reads the
# filesystem; neither belongs on an interface reachable from anywhere else by accident.

import os, argparse, webbrowser
from typing import Optional, List, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, lang, workspace
from ..lib.log import log

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


@app.get('/api/health')
def health():
    return {'ok': True, 'root': workspace.root(), 'budget': core.budget()}


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
    errors, decls, cap = core.check(s, body.scripts, body.inputs)
    # What the caller must still supply: references nothing earlier in this order declares. This
    # is the set the inputs panel is generated from, so it follows validate's own per-script rule
    # rather than a second opinion about it.
    declared, needs = set(), []
    for name in body.scripts:
        if name not in s.tasks:
            continue
        vars, _ = s.get_inspect(name)
        for ref in sorted(set(vars['referenced']) - set(vars['declared'])):
            if ref not in declared and ref not in body.inputs and ref not in needs:
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
        'collisions': workspace.collisions(ws),
    }


# Mounted last: a mount at / matches anything the API routes above did not.
if os.path.isdir(WEB):
    app.mount('/', StaticFiles(directory=WEB, html=True), name='web')


def main():
    ap = argparse.ArgumentParser(prog='prowl-studio')
    ap.add_argument('--root', help='workspace directory (default ~/.prowl/workspaces)')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8788)
    ap.add_argument('--open', action='store_true', help='open a browser at the studio')
    a = ap.parse_args()
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
