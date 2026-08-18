# One definition of "assemble a stack and check it", shared by the MCP server and the studio.
# Two copies drift, and then what an agent sees over MCP stops matching what a human sees in the
# studio for the same folder -- which is the one thing neither of them can debug.

import os, importlib

from ..lib.stack import ProwlStack
from ..lib.log import log

# (module, class, does the constructor take the stack). Imported one at a time rather than at the
# top of this file, because some tools are integrations with their own dependencies: the time tool
# wants pytz and the comfy tool wants pillow and websockets. Importing them all up front meant a
# plain `pip install -e ".[studio]"` could not start the server at all, and Pillow should not be
# required to run a prompt. A tool that cannot load is simply not registered, and `validate`
# already reports a script asking for a tool that is not there.
TOOLS = (
    ('..tools.out.tool', 'OutputTemplateTool', True),
    ('..tools.file.tool', 'FileTool', False),
    ('..tools.include.tool', 'IncludeTool', True),
    ('..tools.script.tool', 'ScriptTool', True),
    ('..tools.list.tool', 'ListTool', True),
    ('..tools.each.tool', 'EachTool', True),
    ('..tools.time.tool', 'TimeTool', False),
    ('..tools.comfy.tool', 'ComfyTool', False),
)

# module -> why it could not be loaded. Reported once per process, not once per stack build.
UNAVAILABLE = {}


def tools(s):
    out = []
    for module, name, wants_stack in TOOLS:
        try:
            m = importlib.import_module(module, __package__)
        except ImportError as e:
            if module not in UNAVAILABLE:
                UNAVAILABLE[module] = str(e)
                log.warn(f"tool `{name}` not loaded: {e}. "
                         f"`pip install -e \".[tools]\"` installs the built-in integrations.")
            continue
        cls = getattr(m, name)
        out.append(cls(s) if wants_stack else cls())
    return out


def budget():
    # Refuse rather than truncate. A runaway {story(4096)} across a model pool is real money.
    return int(os.getenv('PROWL_MAX_COMPLETION_TOKENS', '20000'))


def build(folders, include_library=True):
    s = ProwlStack(folder=list(folders), silent=True, include_library=include_library)
    s.add_tools(*tools(s))
    return s


def check(s, scripts, inputs=None):
    # Free, and the loop to stay in while writing: what is wrong, and what a run would cost at
    # worst, both known before anything is spent.
    errors = s.validate(scripts, s.process_inputs(inputs or {}), report=True)
    decls, cap = s.forecast(scripts)
    return errors or [], decls, cap


def describe(s):
    # Every script the stack can see, with what it declares, what it needs from earlier, and what
    # it calls. `requires` is exactly the set a caller must supply as inputs.
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
            'has_prout': os.path.exists(os.path.join(folder, name + '.prout')),
            'errors': [e.to_dict() for e in vars.get('errors', [])],
        })
    return out
