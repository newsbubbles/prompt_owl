import sys, os, json, asyncio, re
from prowl.lib.prowl import prowl
from prowl.lib.stack import ProwlStack
from prowl.lib.log import log
from prowl.lib.error import APIError, ValidationError
from prowl.tools.out.tool import OutputTemplateTool
from prowl.tools.file.tool import FileTool
from prowl.tools.time.tool import TimeTool
from prowl.tools.include.tool import IncludeTool
from prowl.tools.list.tool import ListTool
from prowl.tools.script.tool import ScriptTool
from prowl.tools.comfy.tool import ComfyTool
from prowl.tools.each.tool import EachTool

version = "0.2.0"

USAGE = """prowl [scripts...] [flags]

  -folder=PATH[,PATH]  folders to load .prowl scripts from (default prompts/)
  -model=NAME          model override
  -stop=A,B            stop sequences (default \\n\\n,\\n#)
  -atomic              run each script on its own, don't chain completions
  -input=KEY=VALUE     supply an input variable, repeatable
  -stdin               read a JSON object of input variables from stdin
  -json                write one JSON result object to stdout
  -validate            check the stack and exit, no generation
  -quiet               silence diagnostics on stderr

Results go to stdout, diagnostics go to stderr.
Exit: 0 ok, 1 validation failed, 2 generation failed, 3 auth/credit, 4 usage."""

OK, E_VALIDATE, E_GENERATE, E_AUTH, E_USAGE = 0, 1, 2, 3, 4


def parse_scripts(argv):
    scripts, flags, inputs = [], {}, {}
    for s in argv:
        if not s.startswith('-'):
            scripts.append(s)
            continue
        k = s.lstrip('-')
        if '=' not in k:
            flags[k] = True
            continue
        var, val = k.split('=', 1)
        if var == 'input':
            key, _, v = val.partition('=')
            inputs[key] = v
        elif ',' in val:
            flags[var] = [v.encode().decode('unicode_escape') for v in val.split(',')]
        else:
            flags[var] = val.encode().decode('unicode_escape')
    return scripts, flags, inputs


def emit(obj, as_json):
    # stdout is results only
    if as_json:
        json.dump(obj, sys.stdout, indent=1, default=str)
        sys.stdout.write("\n")
        return
    if not obj.get('ok'):
        print(obj['error']['message'])
        return
    if 'completion' not in obj:
        print("ok")
        return
    print(obj['completion'])
    for out in obj.get('output') or []:
        print(f"\n[[{out['task']}]]\n")
        print(out['output'])


def parse_loops(scripts):
    # `a b ..3` repeats the block three times, carrying variables and completion forward
    st = ' '.join(scripts)
    matches = re.findall(r'\.\.(?:\d+|\.{1})', st)
    if not matches:
        return None
    counts = [int(m[2:]) for m in matches if m[2:].isdigit()]
    parts = [s.strip() for s in re.split(r'(\.\.(?:\d+|\.{1}))', st) if s and s.strip()]
    blocks = [p for p in parts if p not in matches]
    return list(zip((b.split(' ') for b in blocks), counts))


def main():
    scripts, flags, inputs = parse_scripts(sys.argv[1:])
    as_json = 'json' in flags

    if 'quiet' in flags:
        log.sink = lambda level, message: None

    if 'help' in flags or 'h' in flags:
        print(USAGE)
        return OK

    if not scripts:
        return compose()

    if 'stdin' in flags:
        try:
            inputs.update(json.load(sys.stdin))
        except json.JSONDecodeError as e:
            log.error(f"-stdin expects a JSON object of input variables: {e}")
            return E_USAGE

    folder = ['prompts/']
    if 'folder' in flags:
        folder.extend([flags['folder']] if isinstance(flags['folder'], str) else flags['folder'])

    log.info(f"Prompt Owl (PrOwl) version {version}")
    log.info(f"Working from {os.getcwd()}, folders {folder}, scripts {scripts}")

    stack = ProwlStack(folder=folder, silent='quiet' in flags)
    stack.add_tools(OutputTemplateTool(stack), FileTool(), IncludeTool(stack), ScriptTool(stack),
                    ComfyTool(), TimeTool(), ListTool(stack), EachTool(stack))

    loops = parse_loops(scripts)
    flat = [s for block, _ in loops for s in block] if loops else scripts

    errors = stack.validate(flat, stack.process_inputs(inputs), report=True)
    if errors:
        emit({'ok': False, 'scripts': flat, 'errors': errors,
              'error': {'message': errors[0]['message']}}, as_json)
        return E_VALIDATE
    if 'validate' in flags:
        emit({'ok': True, 'scripts': flat, 'errors': []}, as_json)
        return OK

    async def once(blocks, variables=None, prefix=""):
        return await stack.run(blocks, inputs=inputs, stops=flags.get('stop'), variables=variables,
                               prefix=prefix, atomic='atomic' in flags, model=flags.get('model'))

    try:
        if loops:
            for blocks, runs in loops:
                variables, prefix = {}, ""
                for _ in range(runs):
                    result = asyncio.run(once(blocks, variables=variables, prefix=prefix))
                    variables, prefix = result.variables, result.completion
        else:
            result = asyncio.run(once(scripts))
    except APIError as e:
        log.error(e.message)
        emit({'ok': False, 'scripts': flat, 'error': e.to_dict()}, as_json)
        return E_AUTH if e.fatal() else E_GENERATE
    except Exception as e:
        log.error(f"{type(e).__name__}: {e}")
        emit({'ok': False, 'scripts': flat, 'error': {'message': f"{type(e).__name__}: {e}"}}, as_json)
        return E_GENERATE

    emit({'ok': True, 'scripts': flat, **result.to_dict()}, as_json)
    return OK


def compose():
    # no scripts given: the augmented prompt composer, interactive only
    from prowl.tools.prowl.tool import ProwlProwlTool
    if not sys.stdin.isatty():
        print(USAGE)
        return E_USAGE
    log.info(f"PrOwl: Augmented Prompt Composer version {version}")
    stack = ProwlStack('prompts/world/')
    stack.add_tools(ProwlProwlTool(stack), ScriptTool(stack))
    request = input("> ")
    r = asyncio.run(stack.run(['prowl'], inputs={
        'user_request': request,
        'example_script': 'creative',
        'variable_name': "{variable_name}",
    }, stops=["```"]))
    print(r.completion)
    return OK


if __name__ == "__main__":
    sys.exit(main())
