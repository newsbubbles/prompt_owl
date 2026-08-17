# MCP server over ProwlStack, so an agent can write a script, check it for free, run it,
# and read back the whole variable graph. Every tool is a call into the stack; nothing here
# reimplements the interpreter.
#   prowl-mcp [folder ...]      folders also read from PROWL_FOLDER, os.pathsep separated

import os, re, sys

from mcp.server.fastmcp import FastMCP

from .lib.prowl import prowl
from .lib.stack import ProwlStack
from .lib.log import log
from .lib.error import APIError, ValidationError, GenerationError
from .tools.out.tool import OutputTemplateTool
from .tools.file.tool import FileTool
from .tools.time.tool import TimeTool
from .tools.include.tool import IncludeTool
from .tools.list.tool import ListTool
from .tools.script.tool import ScriptTool
from .tools.comfy.tool import ComfyTool
from .tools.each.tool import EachTool

server = FastMCP("prowl")

FOLDERS = []
# Refuse rather than truncate. A runaway {story(4096)} across a model pool is real money.
BUDGET = int(os.getenv('PROWL_MAX_COMPLETION_TOKENS', '20000'))


def build():
    s = ProwlStack(folder=list(FOLDERS), silent=True)
    s.add_tools(OutputTemplateTool(s), FileTool(), IncludeTool(s), ScriptTool(s),
                ComfyTool(), TimeTool(), ListTool(s), EachTool(s))
    return s


def check(s, scripts, inputs):
    errors = s.validate(scripts, s.process_inputs(inputs or {}), report=True)
    decls, cap = s.forecast(scripts)
    return errors, decls, cap


@server.tool()
def list_scripts() -> list:
    """Every .prowl script on the configured folders. For each: the variables it declares, the
    variables it only references (which must be supplied as inputs or declared by an earlier
    script in the stack), the tools it calls, and whether it has a .prout output template."""
    s = build()
    out = []
    for name in sorted(s.tasks):
        vars, tools = s.get_inspect(name)
        folder = s.tasks[name]['folder']
        out.append({
            'name': name,
            'folder': folder,
            'declares': vars['declared'],
            'requires': sorted(set(vars['referenced']) - set(vars['declared'])),
            'tools': tools['tools']['required'],
            'has_prout': os.path.exists(folder + name + '.prout'),
            'errors': [e.to_dict() for e in vars.get('errors', [])],
        })
    return out


@server.tool()
def read_script(name: str) -> dict:
    """Source of a script and of its .prout output template if one exists."""
    s = build()
    if name not in s.tasks:
        return {'error': f"`{name}` not found in {FOLDERS}"}
    folder = s.tasks[name]['folder']
    prout = None
    if os.path.exists(folder + name + '.prout'):
        with open(folder + name + '.prout', encoding='utf-8') as f:
            prout = f.read()
    return {'name': name, 'prowl': s.tasks[name]['code'], 'prout': prout}


@server.tool()
def validate(scripts: list, inputs: dict = None) -> dict:
    """Check a stack without generating anything. Free, and it is the loop to stay in while
    writing a script. Returns any validation errors plus the worst-case completion tokens a run
    would spend, so the cost is known before it is paid."""
    errors, decls, cap = check(build(), scripts, inputs)
    return {'ok': not errors, 'errors': errors or [],
            'declarations': decls, 'max_completion_tokens': cap, 'budget': BUDGET}


@server.tool()
async def run(scripts: list, inputs: dict = None, model: str = None,
              atomic: bool = False, stops: list = None) -> dict:
    """Run a stack and return the whole variable graph: every variable with its value, its
    prior values if it was declared more than once, its candidates if it was declared with n>1,
    and its token usage. Validates first, and refuses if the worst case exceeds the budget."""
    s = build()
    errors, decls, cap = check(s, scripts, inputs)
    if errors:
        return {'ok': False, 'errors': errors}
    if cap > BUDGET:
        return {'ok': False, 'errors': [{'message':
            f"worst case {cap} completion tokens over {decls} declarations exceeds budget {BUDGET}"}]}
    try:
        r = await s.run(scripts, inputs=inputs or {}, stops=stops, atomic=atomic, model=model)
    except GenerationError as e:
        return {'ok': False, 'failed_variable': e.variable, 'errors': [e.to_dict()]}
    except APIError as e:
        return {'ok': False, 'fatal': e.fatal(), 'errors': [e.to_dict()]}
    return {'ok': True, 'model': model, **r.to_dict()}


@server.tool()
async def run_pool(scripts: list, models: list, inputs: dict = None, atomic: bool = False) -> dict:
    """Run the same stack across several models to measure how well a prompt travels.

    A prompt written for one model can stop working on a more capable one. In prowl that shows
    up precisely: a named variable stops filling, burns its retries and raises, so the result
    says WHICH variable rotted rather than only that the output got worse. Each entry reports
    ok, failed_variable, the variable values and usage for that model."""
    s = build()
    errors, decls, cap = check(s, scripts, inputs)
    if errors:
        return {'ok': False, 'errors': errors}
    if cap * len(models) > BUDGET:
        return {'ok': False, 'errors': [{'message':
            f"worst case {cap} x {len(models)} models = {cap * len(models)} completion tokens "
            f"exceeds budget {BUDGET}; raise PROWL_MAX_COMPLETION_TOKENS or cut the pool"}]}

    results = []
    for model in models:
        entry = {'model': model, 'ok': False}
        try:
            r = await build().run(scripts, inputs=inputs or {}, atomic=atomic, model=model)
            entry.update(ok=True, variables=r.get(), usage=r.usage.dict())
        except GenerationError as e:
            entry.update(failed_variable=e.variable, error=e.message)
        except APIError as e:
            entry.update(error=e.message, fatal=e.fatal())
        except Exception as e:
            entry.update(error=f"{type(e).__name__}: {e}")
        results.append(entry)

    filled = [x for x in results if x['ok']]
    return {
        'ok': True,
        'scripts': scripts,
        'results': results,
        'filled': f"{len(filled)}/{len(models)}",
        'rotted_variables': sorted({x['failed_variable'] for x in results if 'failed_variable' in x}),
    }


def main():
    FOLDERS.extend(sys.argv[1:] or
                   [f for f in os.getenv('PROWL_FOLDER', 'prompts/').split(os.pathsep) if f])
    log.info(f"prowl-mcp folders {FOLDERS} budget {BUDGET}")
    server.run()


if __name__ == "__main__":
    main()
