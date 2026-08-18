# Prompt Owl — for agents

Prompt Owl is a declarative prefix-continuation prompting language (`.prowl`) plus a local studio
for composing, running and measuring prompt stacks.

The one fact everything else follows from: **a prowl script is one growing prompt string.**
`prowl.fill()` walks it left to right, and every `{name(max_tokens, temperature)}` becomes a raw
completions call on everything accumulated so far, spliced back in before the walk continues. A
finished run is a single token sequence whose spans happen to be named.

## Read these before working here

- **[`skills/prowl-script/SKILL.md`](skills/prowl-script/SKILL.md)** — writing `.prowl`. The
  difference between `{name(8, 0.0)}` and `{name}` is the whole language, and getting it wrong is
  the most common failure.
- **[`skills/prompt-owl/SKILL.md`](skills/prompt-owl/SKILL.md)** — running the studio, its HTTP
  API, and measuring a stack across models and inputs.

## Layout

```
prowl/lib/          the interpreter — prowl.py (fill, types, stops), stack.py, vllm.py
prowl/tools/        {@tool(...)} implementations — each is a folder with tool.py
prowl/studio/       the local studio: server.py, core.py, history.py, embed.py, web/
prowl/prompts/      the bundled prompt library
skills/             the two agent skills above
```

## Working rules

- **Restart the studio server after editing anything under `prowl/`.** It runs without `--reload`,
  so a stale process serves the old code and the bug you are chasing is not there any more.
- **`validate` before `run`.** `POST /api/w/{ws}/validate` costs nothing and catches undeclared
  references, missing tools and over-budget stacks before a single token is spent.
- **Runs cost money.** Say what a sweep will cost before starting a large one.
- **Pin the provider when comparing models.** One model id is served by several backends at
  different quantization; unpinned, a model comparison is partly a routing comparison.
- **Do not add comments that restate the code.** Comments here carry the reason a thing is the way
  it is — usually a measurement or a bug that made it necessary. Match that.
- When you change stop behaviour, type behaviour, endpoint paths or error codes, update the skill
  that states them **in the same commit**.
