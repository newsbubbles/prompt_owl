# Agent skills

Two skills ship with Prompt Owl. They are plain markdown with YAML frontmatter, so any harness
that reads skills can use them, and any harness that doesn't can still read them as documentation.

| skill | for |
|---|---|
| [`prowl-script`](prowl-script/SKILL.md) | writing and debugging `.prowl` syntax: declarations, types, stops, budgets, `.prout` |
| [`prompt-owl`](prompt-owl/SKILL.md) | running the studio, the HTTP API, sweeping, measuring across models |

The split is deliberate: one is about the language, the other about the tool. Writing a script and
running a benchmark are different jobs and pulling both into context at once mostly wastes it.

## Claude Code

Per project, from the repo root:

```bash
mkdir -p .claude/skills && cp -r skills/* .claude/skills/
```

Or for every project:

```bash
cp -r skills/* ~/.claude/skills/
```

Claude Code picks them up by their frontmatter `description`, so nothing else needs configuring.
`.claude/launch.json` in this repo already defines a `prompt-owl` server entry, so the browser
pane can start and stop the studio without a blocking shell call.

## Codex and other harnesses

`AGENTS.md` at the repo root points at both files. If your harness reads `AGENTS.md`, that is
enough. If it reads a directory of prompts, point it at `skills/`. If it reads neither, the
fastest path is to paste `skills/prompt-owl/SKILL.md` into context, since it is written to be read
cold, and its API section is the whole interface.

## Keeping them true

Both skills state facts about code in this repo: stop sequences, type behaviour, endpoint paths,
error codes. When you change any of those, change the skill in the same commit. A skill that has
drifted is worse than no skill, because it is read with confidence.

`GET /api/lang` serves prowl's live patterns, types, option names and error codes, so a harness
that wants ground truth rather than prose can ask the running server.
