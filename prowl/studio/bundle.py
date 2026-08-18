# A workspace in one file, so it can be handed to someone else.
#
# A workspace is already just a directory of .prowl and .prout files, so the bundle is that
# directory plus its stacks.json and a small manifest. Nothing here is a new format: unzip it
# anywhere and the CLI can run it.
#
# The import side is the part that matters. A bundle arrives from someone else, and the scripts
# inside it can call tools that read the filesystem, so unpack() refuses anything it cannot place
# safely and reports what the scripts will be able to do before anything is run.

import io, os, json, time, zipfile

from . import workspace, core

MANIFEST = 'prompt-owl.json'
FORMAT = 1
KEEP = ('.prowl', '.prout')
RECORDS = ('.jsonl',)              # under runs/, and only when asked for

MAX_FILES = 2000
MAX_BYTES = 32 * 1024 * 1024       # uncompressed, so a zip bomb is refused rather than unpacked
MAX_ENTRY = 4 * 1024 * 1024

# Tools that reach outside the conversation. Not a blocklist: an imported script is allowed to use
# them, but you should be told before you press Run rather than after.
REACH = {'file': 'reads and writes files on this machine',
         'search': 'makes web searches',
         'navigate': 'fetches web pages',
         'comfy': 'drives a local ComfyUI',
         'recall': 'reads a vector store',
         'collect': 'writes to a vector store'}


def survey(ws):
    """What is in a workspace: scripts, their declarations, the tools they call."""
    s = core.build(workspace.folders(ws), include_library=False)
    described = core.describe(s)
    tools = sorted({t for d in described for t in d['tools']})
    return {
        'scripts': [d['name'] for d in described],
        'declares': sorted({v for d in described for v in d['declares']}),
        'requires': sorted({v for d in described for v in d['requires']}),
        'tools': tools,
        'reach': {t: REACH[t] for t in tools if t in REACH},
        'stacks': sorted(workspace.stacks(ws)),
    }


def pack(ws, runs=False):
    """The workspace as zip bytes. Recorded runs are left out unless asked for: they are usually
    the largest thing in there and they carry the inputs every run was given."""
    base = workspace.path(ws)
    if not os.path.isdir(base):
        raise workspace.WorkspaceError(f"no workspace `{ws}`")
    buf, written = io.BytesIO(), []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith('.') and (runs or d not in workspace.RESERVED)]
            for f in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, f), base).replace(os.sep, '/')
                ext = os.path.splitext(f)[1]
                keep = ext in KEEP or rel == 'stacks.json' or (
                    runs and rel.startswith('runs/') and ext in RECORDS)
                if not keep:
                    continue
                z.write(os.path.join(dirpath, f), rel)
                written.append(rel)
        info = survey(ws)
        z.writestr(MANIFEST, json.dumps({
            'format': FORMAT, 'kind': 'prompt-owl-workspace', 'name': ws,
            'at': round(time.time(), 3), 'files': written, 'runs_included': bool(runs),
            **info,
        }, indent=1, sort_keys=True))
    return buf.getvalue(), written


def manifest(data):
    """Read a bundle's manifest without unpacking it. Advisory only: the manifest is written by
    whoever made the bundle, so unpack() checks the entries themselves regardless."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if MANIFEST not in z.namelist():
                return {}
            return json.loads(z.read(MANIFEST).decode('utf-8'))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, UnicodeDecodeError):
        return {}


def place(rel):
    """Where an entry is allowed to land, or None to skip it. Returns the path segments.

    Everything about the name is checked here rather than trusted: `..`, absolute paths, drive
    letters and backslashes all get an entry refused, and every remaining segment has to be a
    legal workspace name."""
    rel = rel.replace('\\', '/')
    if rel.startswith('/') or (len(rel) > 1 and rel[1] == ':'):
        raise workspace.WorkspaceError(f"refusing `{rel}`: absolute path")
    parts = [p for p in rel.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        raise workspace.WorkspaceError(f"refusing `{rel}`: path escapes the workspace")

    name = parts[-1]
    stem, ext = os.path.splitext(name)
    if ext in KEEP:
        workspace.valid(stem, 'script')
    elif len(parts) == 1 and name == 'stacks.json':
        pass
    elif parts[0] == 'runs' and len(parts) == 2 and ext in RECORDS:
        pass
    else:
        return None                # not part of a workspace; quietly left out
    for p in parts[:-1]:
        workspace.valid(p, 'folder')
    return parts


def unpack(data, name):
    """Create a workspace from bundle bytes. Refuses to write into one that already exists, so an
    import can never quietly replace work."""
    workspace.valid(name, 'workspace')
    if os.path.isdir(workspace.path(name)):
        raise workspace.WorkspaceError(f"workspace `{name}` already exists; import under another name")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise workspace.WorkspaceError('that file is not a zip archive')

    with z:
        entries = [i for i in z.infolist() if not i.is_dir()]
        if len(entries) > MAX_FILES:
            raise workspace.WorkspaceError(f"{len(entries)} files is more than a workspace should hold")
        total = sum(i.file_size for i in entries)
        if total > MAX_BYTES:
            raise workspace.WorkspaceError(f"unpacks to {total // 1024 // 1024}MB, over the {MAX_BYTES // 1024 // 1024}MB limit")

        plan, skipped = [], []
        for i in entries:
            if i.filename == MANIFEST:
                continue
            # A symlink in a zip is a pointer at whatever it likes, including outside the root.
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise workspace.WorkspaceError(f"refusing `{i.filename}`: symlink")
            if i.file_size > MAX_ENTRY:
                raise workspace.WorkspaceError(f"`{i.filename}` is {i.file_size // 1024}KB, too large for a script")
            parts = place(i.filename)
            if parts is None:
                skipped.append(i.filename)
            else:
                plan.append((i, parts))

        if not plan:
            raise workspace.WorkspaceError('nothing in that zip looks like a workspace')

        workspace.create(name)
        written = []
        for i, parts in plan:
            # path() confines under the workspace root and raises otherwise, which is the second
            # check on the same question. Both are cheap and this one is the load-bearing one.
            dest = workspace.path(name, *parts)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(i) as src, open(dest, 'wb') as out:
                out.write(src.read())
            written.append('/'.join(parts))

    return {'name': name, 'files': written, 'skipped': skipped, **survey(name)}
