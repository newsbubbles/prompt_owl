![logo_128](https://github.com/user-attachments/assets/83bcbe59-3ed5-41a4-b5ff-2a893dce81bc)
# Prompt Owl v0.2
*Give your Prompts Wings!*

A declarative prompting language. A `.prowl` script is **one growing prompt string**: the
interpreter walks it left to right, and every `{name(max_tokens, temperature)}` it reaches becomes
a completion call on everything written so far, spliced back in before the walk continues.

A finished run is a single continuous token sequence whose spans happen to be named. Every idea
below follows from that one fact.

```prowl
# Character
## Name
A name for a wandering swordsman:
- Name: {name:word(12, 0.9)}

## Reputation
What {name} is known for, in one line:
{reputation:line(60, 0.7)}
```

| | |
|---|---|
| **Install & run** | [Installation](#installation) · [Quick start](#quick-start) · [Endpoints](#pointing-it-at-a-backend) |
| **Write** | [Declarations](#declarations-and-references) · [Types](#types) · [Options](#options) · [Shape](#inline-vs-multiline) · [Stops](#stops-are-the-control-flow) |
| **Run** | [Stacks](#stacking) · [Tools](#using-tools) · [CLI](#command-line) · [Studio](#prompt-owl-studio) · [MCP](#mcp) |
| **Everything new in 0.2** | [CHANGELOG.md](CHANGELOG.md) |

- [![Colab](https://img.shields.io/badge/Google%20Colab-%23F9AB00.svg?style=for-the-badge&logo=google-colab&logoColor=white) PrOwl Demo](https://colab.research.google.com/drive/1x-9mkpawC3kh_3FKJcl7QE3UkRkvtAXL?usp=sharing)
- [![Colab](https://img.shields.io/badge/Google%20Colab-%23F9AB00.svg?style=for-the-badge&logo=google-colab&logoColor=white) Scripting with PrOwl](https://colab.research.google.com/drive/1_StW54A8B4mAdRa4piBRxFxnhhyaaYuS#scrollTo=oTqFPlXHjZTN)
- Come join the community on [Discord](https://discord.gg/CeZX7QaShm)

---

## Installation

```bash
pip install prompt-owl              # the language, the CLI, the tools
pip install prompt-owl[studio]      # + the local studio
pip install prompt-owl[mcp]         # + the MCP server
```

From a clone, `pip install -e ".[studio]"`.

Set up an environment file for your OpenAI-compatible endpoint. A copy lives in
`prowl/env.placeholder`:

```bash
PROWL_VLLM_ENDPOINT=http://localhost:8000
PROWL_MODEL=mistralai/Mistral-7B-Instruct-v0.2

# paid APIs — OpenRouter, OpenAI, anything speaking the same shape
# PROWL_VENDOR_API_KEY=...

# optional
# PROWL_COMPLETIONS_ENDPOINT=/v1/completions   # if your server puts it elsewhere
# PROWL_MAX_COMPLETION_TOKENS=20000            # studio budget ceiling per run
# PROWL_CHAT=1                                 # talk to a chat endpoint by assistant prefill
PROWL_COMFY_ENDPOINT=127.0.0.1:8188
# FAL_KEY=...
```

### Pointing it at a backend

Prowl wants `/v1/completions`, because prefix continuation is what the language *is*. The base URL
is the origin — prowl appends the path.

| backend | `PROWL_VLLM_ENDPOINT` | notes |
|---|---|---|
| vLLM | `http://localhost:8000` | the reference setup |
| Ollama | `http://localhost:11434` | its **OpenAI-compatible** paths. Not `/api/generate` — that shape returns `response`, not `choices[].text` |
| llama.cpp server | `http://localhost:8080` | |
| OpenRouter | `https://openrouter.ai/api` | needs a key. 284 of 414 models support `stop`, and a model without it cannot run a script correctly |
| chat-only endpoints | as above | set `PROWL_CHAT=1`. The document goes into a pre-started assistant turn and the model carries on writing it — same mechanism, different envelope |

Unsure whether yours works? Start the studio and click the pill, or `curl localhost:8788/api/probe`.
It sends one token down each path and tells you what answered.

---

## Quick start

Write `hello.prowl`:

```prowl
# Monster
## Type
Enter a D&D monster type:
- Type: {monster_type:word(12, 0.2)}

## Description
Describe a {monster_type} in two sentences:

{description:text(200, 0.6)}
```

Run it:

```python
import asyncio
from prowl.lib.prowl import prowl

r = asyncio.run(prowl.fill(open('hello.prowl').read()))

print(r.completion)   # the finished document
print(r.get())        # {'monster_type': 'Aberration', 'description': '...'}
```

Or from the terminal, without writing any Python:

```bash
prowl -folder=. hello
```

---

## Writing prowl

There is no system prompt and no "role". The model sees the document so far and continues it. So
**the sentence immediately before a variable is the instruction**, and it should end in a colon.
Write the script as the document you want to exist; the model fills the gaps because they are
gaps, not because it was told to.

### Declarations and references

```
{name(max_tokens, temperature)}    DECLARATION — calls the model, splices the result, stores it
{name}                             REFERENCE   — splices a stored value, calls nothing
```

**The parentheses are what declares. Nothing else does.**

A declaration is the only thing that spends a token. A reference costs nothing: it drops an
existing value into the document *again*, further down, so that a **later** declaration is
conditioned on it. That is the whole mechanism by which one step of a chain sees the previous one.

A `{name}` that nothing declares is an **input**, supplied by the caller via `inputs={...}`.
Undeclared references are how a script takes arguments.

### Types

New in 0.2. The type picks the stop sequence, decides how the raw completion is read, and decides
what counts as a value at all.

```prowl
{answer:number(24, 0.0)}
{sure:bool(6, 0.0)}
{mood:word(8, 0.3)}
{title:line(40, 0.6)}
{story:text(1024, 0.8)}
{steps:list(300, 0.2)}
```

| type | stops at | holds |
|---|---|---|
| `word` | newline | the first word, stripped of surrounding punctuation |
| `line` | newline | one line; invalid if it ends in `:` |
| `number` | newline | the **last** number in the text |
| `bool` | newline | `true`/`false`; invalid if neither |
| `text` | next markdown header | the prose |
| `list` | next markdown header | the text; invalid with no items |

**Prefer a type over a tight `max_tokens`.** `{answer(2, 0.0)}` was the old way of saying "this is
short", and it fails silently: models open with `"Let's denote"` or `"The final answer is:
$\boxed{"` and the budget runs out before the value. `{answer:number}` reads the number wherever it
lands and *raises* if there isn't one.

**A type annotates a declaration; it never makes one.** `{answer:number}` with no arguments raises
`ValidationError` 1009 — a reference already has its value, so there is nothing left to shape.

### Options

After the positional arguments, a declaration may carry named options. `temperature` is optional
and defaults to `0.0`.

```prowl
{label(12)}                              temperature defaults to 0.0
{code(600, 0.1, stop=```)}               its own stop — blank lines become legal here
{cause(200, 0.7, n=3)}                   alternatives land in var.data['candidates']
{plan(400, 0.2, model=qwen/qwen3-32b)}   per-variable model
{story(1024, 0.8, block)}                force multiline whatever the whitespace says
{note(30, 0.5, inline)}                  force single-line
```

Known options are exactly `stop`, `n`, `logprobs`, `model`, `block`, `inline`. Anything else raises
rather than being forwarded — options become request parameters, and a typo that reaches the API
does nothing visible while quietly disabling the thing you meant.

The argument list must **start with an integer** to count as a declaration. That is what keeps
prose like `use the syntax {variable_name(max_tokens, temperature)}` inert, so a script can
document prowl to the model without generating into itself.

### Inline vs multiline

Decided by whitespace, from the character before `{` and the two after `}`:

- **multiline** — the variable is alone on its line *and* followed by a blank line. Full token
  budget, keeps newlines, auto-extracts lists.
- **inline** — anything else. Stripped and truncated at the first newline.

```prowl
- Name: {story_name(24, 0.7)}          <- inline, one short phrase

Write the story:
{story(1024, 0.8)}
                                        <- multiline (the blank line above is required)
```

Forgetting that blank line is the single most common prowl bug. It silently truncates a 1024-token
narrative to its first line. Use the `block` option, or the studio, which shows the shape of every
declaration as you type.

### Stops are the control flow

The default is `prowl.STOPS = ["\n\n", "\n#", "##"]`. A generation ends at a blank line or a
markdown header, which means **each `##` section is a natural bound on the variable inside it**.
Write scripts as tidy markdown documents and the segmentation is free.

Bare `"##"` fires anywhere in a line, on purpose: models routinely run a header onto the current
line (`"Since## Step 2"`), which `"\n#"` cannot catch. It appeared in 40–54% of responses on the
Llama family without it and 0% with it. Generating code or prose that contains `##`? Override per
declaration with `stop=`.

### Lists are automatic

A multiline value whose lines match `*`, `+`, `-` or `1.` is parsed into `var.list` alongside the
raw text. Asking for a numbered list *is* the data extraction step.

```prowl
Write a grocery list as a numbered list of up to ten items, names only:
{groceries(120, 0.4)}

- Ingredient 1: {@list(groceries)}
- Ingredient 2: {ingredient_2(12, 0.1)}
```

### Re-declaring is revision, not overwrite

Declaring the same name twice pushes the old value onto `var.history` and generates a new one. The
prompt still contains the first value, so the model sees its own earlier answer and can correct it.

```prowl
## Answer
{answer:number(8, 0.0)}

## Double Check
If that answer might have been wrong, write the correct one:
{answer:number(8, 0.0)}
```

### `.prout` — the projection

A `.prout` file beside `script.prowl` is an output template: references only, never declarations,
never shown to the model during the run. After the script finishes, the stack fills it and returns
it from `result.out()`.

This is the compression operator. A script can think in 1200 tokens and hand forward 700. Build
long-running hierarchies by running stacks `atomic=True` and carrying `.prout` projections forward
instead of raw completions — otherwise every stage inherits every token of every earlier stage.

---

## Stacking

```python
import asyncio
from prowl.lib.stack import ProwlStack

stack = ProwlStack(folder=['prompts/monster/', 'prompts/thought/'])
r = asyncio.run(stack.run(['monster', 'tot'], inputs={'monster_name': 'Grelth'}))

print(r.completion)   # the whole document
print(r.get())        # {var: value}
print(r.out())        # joined .prout projections
```

By default each script is appended to the previous completion — one continuous document across the
whole stack. `atomic=True` runs each independently.

`stack.validate()` runs before anything generates: every referenced variable is declared by an
earlier script, required tools are loaded, referenced scripts exist. `stack.forecast()` reports
declaration count and worst-case tokens. Both are free.

---

## Using Tools

Tools are pre-scripted rather than model-chosen, which is the difference between prowl and a ReAct
agent. `{@toolname(arg, arg)}` runs when the walk reaches it, splices its return text, and stores
it under the tool's own name.

```prowl
# Make an Image
## Subject
Describe a subject for the image. Surprise me:
{subject(200, 0.6)}

## Prompt Composition
Compose a comma-delimited set of key phrases summarising the above:
{prompt(520, 0.0)}

{@comfy(prompt)}
```

Half the namespace is control flow — `@each` (for-each over a generated list), `@include` /
`@script` (subroutine), `@out` (projection), `@list`, `@concat`, and `@prowl`, which generates a
prowl script and runs it. The rest is I/O: `@file`, `@search`, `@recall`, `@collect`, `@navigate`,
`@time`, `@comfy`.

---

## Prompt Owl Studio

```bash
pip install -e ".[studio]"
prowl-studio --env .env --open
```

A local workbench for composing, running and **measuring** stacks. Loopback by default, because a
run spends money and `@file` reads the filesystem.

- **The stack is the unit.** Drag scripts into order; save that order, its inputs, its model pool
  and its provider pin as a named stack in the workspace.
- **Syntax colouring compiled from prowl's own grammar** — `/api/lang` serves the interpreter's
  patterns, so the editor cannot disagree with what will actually run. Every declaration shows its
  shape, budget, temperature and stops.
- **Run across a model pool concurrently** and compare **variable by variable**, because prompt rot
  is per-field: a prompt does not decay uniformly on a newer model, one named variable stops
  behaving.
- **History.** Every filled declaration is appended to `runs/history.jsonl`. Cut the statistics
  across models, providers, scripts, runs, or **any input** — distinct ratio, normalised entropy,
  mean ± sd with a strip plot, and clustering by meaning through an embedding model when the values
  are sentences.
- **Sweeps.** Mark an input to vary and it becomes a list, one value per line; the runner walks
  inputs × repeats × models.
- **Export** as `csv`, `jsonl`, `json`, or as training pairs (`messages`, `alpaca`) — because a run
  is one token sequence whose spans are named, `completion[:start]` is exactly the prompt that
  produced `completion[start:end]`, so one run yields one pair per declaration.
- **Model probing.** The catalogue says which models support `stop`; it cannot say which can
  continue a document. The studio spends five tokens and tells you: `continues`, `echoes` (a
  chat-only model behind an adapter — tick `chat`), or `unsupported`. It also disables reasoning
  automatically, since a thinking model spends a small budget entirely on thought and returns `""`.

Everything the UI does it does over an HTTP API under `/api`, so a script or an agent can drive it
without a browser at all. See [`skills/prompt-owl/SKILL.md`](skills/prompt-owl/SKILL.md) for the
full surface.

---

## Skills for coding agents

Two skills ship in [`skills/`](skills/) — one for writing `.prowl`, one for running the studio — so
Claude Code, Codex and similar harnesses arrive knowing how this works:

```bash
mkdir -p .claude/skills && cp -r skills/* .claude/skills/
```

See [`skills/README.md`](skills/README.md), or [`AGENTS.md`](AGENTS.md) for harnesses that read it.

---

## Command line

```bash
prowl -folder=prompts/ input output
```

Runs `prompts/input.prowl` then `prompts/output.prowl` as a stack. The CLI is built to be used
non-interactively:

| flag | does |
|---|---|
| `-json` | one result object on stdout |
| `-validate` | check the stack and exit without generating |
| `-input=KEY=VALUE` | supply an input variable (repeatable) |
| `-stdin` | read inputs as a JSON object |
| `-atomic` | run each script independently |
| `-stop="\n\n,\n#"` | override stop tokens |
| `-quiet` | silence diagnostics |

Exit codes: `0` ok, `1` validation failed, `2` generation failed, `3` auth/credit, `4` usage.
Diagnostics go to stderr and stdout carries results only, so `prowl ... -json | jq` works.

## MCP

```bash
pip install prompt-owl[mcp]
prowl-mcp
```

Exposes ProwlStack over the Model Context Protocol: list and read scripts, validate, forecast, run
one stack or a pool of models. Shares its stack assembly with the studio, so an agent and a human
see the same thing for the same folder.

---

## Backstory

After months of wrestling with LangChain’s linear and string-burdened approach to prompt composition, I decided enough was enough. I wanted prompts to feel as intuitive as HTML 1.0—simple, declarative, and powerful. With PrOwl, I cranked out more prompt completions on day one than I had in seven months of LangChain. Now, prompts take center stage and Python coding is just there to support the prompt engineering. PrOwl makes prompt engineering the first-class citizen it deserves to be.

## The PrOwl Advantage

0. **Built for Local LLMs**: While many frameworks prioritize OpenAI compatibility, PrOwl was designed with [vLLM](https://github.com/vllm-project/vllm/) and [Mistral Instruct 7B](https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.2) in mind, with possibilities to support even more LLM clients.
1. **Simplicity & Accessibility**: `.prowl` scripts are lightweight and designed for effortless integration into your Python code through the `ProwlStack`. Think less “boilerplate” and more direct, async prompt magic.
2. **Minimalism at Its Core**: With a tiny interpreter focusing on just the essential language features, PrOwl keeps your codebase clean and functions well-defined.
3. **Smart Problem Solving**:
   - **Separation of Concerns**: Keep your code and prompt composition distinct.
   - **Multi-Step Composition**: Build complex, multi-step prompts that flow naturally.
   - **Variable-First Generation**: Get data and text as part of the same generation, so you don’t waste time extracting variables later.
   - **Chaining & Stacking**: Every generated variable conditions subsequent outputs—a step beyond traditional chaining.
   - **Built-In Tools**: Create and utilize custom tools seamlessly.
   - **Hallucination Control**: Guide your LLM through a structured process to minimize off-track outputs.
   - **Multi-Generation Support**: Declare a variable multiple times and track its evolution like in a proper scripting language.
4. **Augmented Prompt Engineering**: Design your script alongside the LLM, integrating human standards into machine-generated output. It isn't "alignment", it is *conditioning*.

## PrOwl is a Quine!

Not exactly a quine in the classic sense, but the `@prowl` tool almost is—it can finish its own script and even use tools within its prompt. Early in development, I realized PrOwl scripts can evolve: an LLM-based app writing an LLM-based app. In other words, your `.prowl` files aren’t just scripts—they’re self-referential, self-improving, and fully capable of generating dynamic, tool-integrated content.

The interesting natural conclusion of this dynamic is that an Agent could easily be made which prompts itself, or creates prompts for other agents; a meta-agent if you will.

## PrOwl Agents?

Agents are all the rage these days (see GPT Store, Crew.ai, AutoGen, LangChain, etc.). PrOwl isn’t necessarily an agent framework, but it lets you model agent tasks by integrating tools (like RAG, filesystem access, or the `@comfy` tool). Think of it as an Augmented Prompt Engineering framework—ideal for creating AI apps that are conditioned directly by user requests. It is trivial to create an Agentic workflow using `.prowl` scripts and some python for your agent class. In as far as the `@prowl` tool, it will eventually begin to write it's own scripts well, including tool usage after I can fine-tune a model using the ~100 scripts I've amassed over the last year.

The end game on Agents is to get a community going around Prompt Owl where people are actively testing, sharing, and publishing prompts to the training queue, and then get some money together to do fine-tuning on a much larger set of training examples.

---

## Unit testing your prompts

Your prompts are scripts, so they get version control, diffs and tests.

```bash
prowl -folder=prompts/ input intent output -validate
```

`-validate` catches undeclared references, unknown types, unknown options, missing tools and
over-budget stacks without generating anything. Wire it into CI and a broken prompt fails the build
like a broken import.

## Known issues

- **`max_tokens=1` is not usable.** A completion's first token is usually a leading space or
  punctuation, so one token rarely contains the value: `{d:number(1, 0.0)}` raises after its
  retries and `{d(1, 0.0)}` returns whatever single token arrived. Give a type and give it room —
  `{d:number(8, 0.0)}`. Since 0.2 this fails loudly instead of looping.
- Bare `##` in the stop defaults ends a variable that legitimately contains `##` mid-line. Override
  with `stop=` on that declaration.

Full history in [CHANGELOG.md](CHANGELOG.md).

---

Embrace prompt-first development with PrOwl—small in lines, big in vision. Happy prompting!
