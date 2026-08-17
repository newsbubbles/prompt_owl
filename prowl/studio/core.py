# One definition of "assemble a stack and check it", shared by the MCP server and the studio.
# Two copies drift, and then what an agent sees over MCP stops matching what a human sees in the
# studio for the same folder -- which is the one thing neither of them can debug.

import os

from ..lib.stack import ProwlStack
from ..tools.out.tool import OutputTemplateTool
from ..tools.file.tool import FileTool
from ..tools.time.tool import TimeTool
from ..tools.include.tool import IncludeTool
from ..tools.list.tool import ListTool
from ..tools.script.tool import ScriptTool
from ..tools.comfy.tool import ComfyTool
from ..tools.each.tool import EachTool


def budget():
    # Refuse rather than truncate. A runaway {story(4096)} across a model pool is real money.
    return int(os.getenv('PROWL_MAX_COMPLETION_TOKENS', '20000'))


def build(folders, include_library=True):
    s = ProwlStack(folder=list(folders), silent=True, include_library=include_library)
    s.add_tools(OutputTemplateTool(s), FileTool(), IncludeTool(s), ScriptTool(s),
                ComfyTool(), TimeTool(), ListTool(s), EachTool(s))
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
