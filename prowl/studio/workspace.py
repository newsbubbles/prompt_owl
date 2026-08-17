# A workspace is a directory of .prowl and .prout files. Nothing more, on purpose: what you edit
# in the studio is a file you can also run from the CLI or hand to the MCP server, so the sandbox
# never becomes a place where scripts live that prowl itself cannot see.

import os, re

# Strict, because these become path segments and script names, and a script name is a key in
# ProwlStack.tasks.
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
# Subdirectories that are storage, not scripts
RESERVED = ('runs',)


class WorkspaceError(ValueError):
    pass


def root():
    p = os.getenv('PROWL_WORKSPACE_ROOT') or os.path.join(os.path.expanduser('~'), '.prowl', 'workspaces')
    return os.path.abspath(p)


def valid(name, what='name'):
    if not name or not NAME.match(name):
        raise WorkspaceError(f"invalid {what} `{name}`: letters, digits, dot, dash and underscore only")
    return name


def path(ws, *parts):
    # Everything is confined under root(). A name that escapes it is a bug or an attack, never a
    # typo, so this raises rather than normalizing it into something plausible.
    r = root()
    p = os.path.abspath(os.path.join(r, valid(ws, 'workspace'), *parts))
    if p != r and not p.startswith(r + os.sep):
        raise WorkspaceError(f"path escapes the workspace root: {p}")
    return p


def workspaces():
    r = root()
    if not os.path.isdir(r):
        return []
    return sorted(n for n in os.listdir(r)
                  if NAME.match(n) and os.path.isdir(os.path.join(r, n)))


def create(ws):
    os.makedirs(path(ws), exist_ok=True)
    return ws


def folders(ws):
    # ProwlStack globs one level deep, so every subdirectory is passed as its own folder entry.
    # That is what lets a workspace partition work without the stack losing sight of any of it.
    base = path(ws)
    if not os.path.isdir(base):
        raise WorkspaceError(f"no workspace `{ws}`")
    out = [base + os.sep]
    for dirpath, dirnames, _ in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in RESERVED]
        for d in dirnames:
            out.append(os.path.join(dirpath, d) + os.sep)
    return out


def collisions(ws):
    # ProwlStack.tasks is keyed by the bare name, so two `tot.prowl` in different subfolders
    # silently resolve to whichever loaded last. Report it, rather than let a run quietly use the
    # file the author is not looking at.
    seen = {}
    for f in folders(ws):
        for n in sorted(os.listdir(f)):
            if n.endswith('.prowl'):
                seen.setdefault(n[:-len('.prowl')], []).append(os.path.relpath(f, path(ws)))
    return {k: v for k, v in seen.items() if len(v) > 1}


def locate(ws, name):
    valid(name, 'script')
    for f in folders(ws):
        if os.path.exists(os.path.join(f, name + '.prowl')):
            return f
    return None


def read(ws, name):
    f = locate(ws, name)
    if not f:
        raise WorkspaceError(f"no script `{name}` in workspace `{ws}`")
    def slurp(ext):
        p = os.path.join(f, name + ext)
        if not os.path.exists(p):
            return None
        with open(p, 'r', encoding='utf-8') as fh:
            return fh.read()
    return {'name': name, 'folder': os.path.relpath(f, path(ws)).replace(os.sep, '/'),
            'prowl': slurp('.prowl'), 'prout': slurp('.prout')}


def write(ws, name, source, prout=None, folder=''):
    valid(name, 'script')
    # An existing script is rewritten where it lives; `folder` only places a new one.
    f = locate(ws, name)
    if not f:
        f = path(ws, *[valid(p, 'folder') for p in folder.split('/') if p and p != '.'])
        os.makedirs(f, exist_ok=True)
    with open(os.path.join(f, name + '.prowl'), 'w', encoding='utf-8', newline='') as fh:
        fh.write(source)
    p = os.path.join(f, name + '.prout')
    if prout is None:
        # None leaves an existing template alone; "" is how you delete one.
        pass
    elif prout == '':
        if os.path.exists(p):
            os.remove(p)
    else:
        with open(p, 'w', encoding='utf-8', newline='') as fh:
            fh.write(prout)
    return read(ws, name)


def remove(ws, name):
    f = locate(ws, name)
    if not f:
        raise WorkspaceError(f"no script `{name}` in workspace `{ws}`")
    for ext in ('.prowl', '.prout'):
        p = os.path.join(f, name + ext)
        if os.path.exists(p):
            os.remove(p)
