---
name: prowl-script
description: >
  Write, debug, or review `.prowl` scripts and `.prout` output templates for Prompt Owl
  (github.com/lks-ai/prowl) — the declarative prefix-continuation prompting language where
  `{name(max_tokens, temperature)}` declares a generated variable and `{name}` references it.
  Use when the user asks to write a prowl script or prompt, convert a prompt or chain-of-thought
  into `.prowl`, build a ProwlStack, add a prowl tool, design `.prout` templates, or debug a
  script that generates the wrong shape / runs away / comes back empty. Also use when working in
  D:\prowl, D:\prompt-library, or D:\vts on anything that emits prowl syntax. Not for LangChain,
  DSPy, or generic prompt engineering that won't be executed by prowl.
---

# Writing prowl

Prowl is not a template engine and not an agent framework. It is **one growing prompt string**.
`prowl.fill()` walks the script left to right; every `{var(n, t)}` it hits becomes a raw
*completions* call on everything accumulated so far, and the result is spliced in before the walk
continues. A finished run is a single continuous token sequence whose spans happen to be named.

Everything below follows from that.

## Syntax

Two forms, and the difference between them is the whole language.

```
{name(max_tokens, temperature)}    DECLARATION — calls the LLM, splices the result, stores it
{name}                             REFERENCE   — splices the stored value, calls nothing
```

**The parentheses are what declares. Nothing else does.**

### Why they are different — read this before writing anything

The script is one growing prompt string.

A **declaration** is the only thing that spends a token. It sends everything accumulated so far as
the prompt, takes the completion, and writes it into the document at that point.

A **reference** spends nothing. It takes a value some earlier declaration already produced and
drops it into the document *again*, further down — so that a **later** declaration is conditioned
on it. That is the entire mechanism by which one step of a chain can see the previous one, and it
is what `.prout` templates are made of. A reference exists so a value can be *reused in the prompt
later*, nothing else.

Consequences, all of which are the same fact:

- **A variable with no parentheses is never generated.** `{verdict}` alone produces nothing. If
  nothing earlier declared it and no input supplies it, the literal text `{verdict}` is left
  sitting in the prompt for the model to read.
- **A type on a reference is meaningless**, and prowl raises `ValidationError` 1009 rather than
  guess what was meant. A type shapes a *generation*: it picks the stop sequence, decides how the
  raw completion is read, and decides what counts as a value at all. A reference already has its
  value — there is nothing left to shape.

  So `{verdict:bool}` is **always** a mistake, and it is one of exactly two things:

  ```
  {verdict:bool(8, 0.0)}   declare it — ask the model for a boolean here
  {verdict}                reference it — splice the one declared earlier
  ```

Variable names match `[a-zA-Z_0-9]+`. A colon introduces a type on a declaration and nothing else:
no dots, no arrays, no namespacing. `{cause.hypothesis}` and `{items[0]}` are not prowl — they
parse as literal text and pass through untouched.

Types, available since 0.2, always come with `(max_tokens, temperature)`:

```
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

`max_tokens` stays a runaway guard — the type does the shaping, so the number is there to stop a
runaway, not to describe the value. But it still has to have room: a bounded type that runs out of
budget comes back `truncated`, and for `number` the last-number-wins rule then reports a figure
from the middle of an unfinished sentence. `{pick:number(4)}` truncated 15 of 37 samples into
`1`, `-2` and `140`; `number(8)` on the same prompt truncated none.

**`word` stops at a newline only, not at a space.** It used to stop at both, and a completion that
opened with a space fired the stop at offset zero and came back empty every time — the value was
never wrong, it never existed.

**Prefer a type over a tight `max_tokens`.** `{answer(2, 0.0)}` was the old way of saying "this
is short," and it fails silently: modern models open with `"Let's denote"` or
`"The final answer is: $\boxed{"` and the budget runs out before the value. `{answer:number}`
reads the number wherever it lands and *raises* if there isn't one.

`text` and `list` stop at the next header, **never at a blank line** — a blank line is inside
prose, not the end of it. Unknown types fail at validate time, before any generation.

A declaration may also carry named options after the positional args, and `temperature`
is optional:

```
{label(12)}                              temperature defaults to 0.0
{code(600, 0.1, stop=```)}               its own stop — blank lines become legal here
{cause(200, 0.7, n=3)}                   alternatives land in var.data['candidates']
{plan(400, 0.2, model=qwen/qwen3-32b)}   per-variable model
{story(1024, 0.8, block)}                force multiline whatever the whitespace says
{note(30, 0.5, inline)}                  force single-line
```

Known options are exactly `stop`, `n`, `logprobs`, `model`, `block`, `inline`. **Anything else is
an error, not a silent pass-through** — options become request parameters, and a typo that reaches
the API does nothing visible while quietly disabling the thing you meant.

The arg list must still **start with an integer** to count as a declaration. That is what keeps
prose like ``use the syntax {variable_name(max_tokens, temperature)}`` inert, so a script can
document prowl to the model without generating into itself.

A `{name}` that was never declared is an **input**: it's supplied by the caller via
`inputs={...}`, and if nothing supplies it the reference is left in the prompt verbatim as
`{name}`. Undeclared references are how a script takes arguments.

Tool calls are the third thing, and they aren't variables:

```
{@toolname(arg, arg)}
```

Arguments are bare variable names or integers. The tool's return text is spliced in *and* stored
under the tool's own name as a variable.

## The instruction is the prose above the blank

There is no system prompt and no "role". The model sees the document so far and continues it. So
the sentence immediately before a variable is the instruction, and it should end in a colon:

```prowl
## Monster Type
Enter a D&D Monster type:
- Type: {monster_type(24, 0.2)}
```

Write the script as the document you want to exist. The model fills the gaps because they are
gaps, not because it was told to.

## Inline vs multiline is decided by whitespace

`prowl.fill` reads the character before `{` and the two after `}`:

- **Multiline** — the variable is alone on its line *and* followed by a blank line. Gets the full
  token budget, keeps newlines, auto-extracts lists.
- **Inline** — anything else. The value is stripped of ` .-_*>#`` ` and newlines, and a value
  containing a newline is truncated at the first one.

```prowl
- Name: {story_name(24, 0.7)}          <- inline, one short phrase

Write the story:
{story(1024, 0.8)}
                                        <- multiline (blank line above is required)
```

Forgetting the blank line after a long variable is the single most common bug. It silently
truncates a 1024-token narrative to its first line.

## Stop tokens are why markdown structure works

Default stops are `prowl.STOPS = ["\n\n", "\n#", "##"]` — one constant shared by `prowl.fill`,
`ProwlStack` and the CLI. A generation ends at a blank line or at a markdown header. That means
**headers are the control flow**: each `##` section is a natural bound on the variable inside it,
and the model can't run past it into the next section.

The bare `"##"` fires *anywhere in a line*, on purpose: models routinely run a header onto the
current line (`"Since## Step 2"`), which `"\n#"` cannot catch. The cost is that a variable
generating code, or prose that legitimately contains `##`, dies mid-line — override it per
declaration with `stop=` when that is what you are generating.

(Before 0.2 there were three different defaults depending on the entry point. If you are on 0.1.x
and a variable dies somewhere none of these explain, that is why.)

Write scripts as tidy markdown documents and the stops do the segmentation for free. If you're
generating code or prose with blank lines in it, override `stops` at the call site or the first
paragraph break will end the variable early.

## Budgets and temperature carry meaning

Both arguments are required on a declaration, and the numbers are part of the instruction — a
6-token budget tells the model this is a number, not an essay.

| shape | tokens | temp |
|---|---|---|
| a number, a stat roll | 5–8 | 0.1–0.6 |
| a word, a label, a mood | 8–14 | 0.0–0.3 |
| a name, a title, a short phrase | 12–24 | 0.3–0.9 |
| one line of reasoning | 20–40 | 0.0–0.2 |
| a bullet or numbered list | 120–500 | 0.1–0.5 |
| a paragraph | 200–320 | 0.2–0.6 |
| prose, a story, a scene | 512–1800 | 0.3–0.8 |

Low temperature for anything *derived* from what's already written (extraction, critique,
summary, structured fields). High only where the script genuinely wants variance (names, themes,
creative prose). Deriving at high temperature is how scripts hallucinate.

## Lists are automatic

If a multiline value contains lines matching `*`, `+`, `-`, or `1.`, they're parsed into
`var.list` alongside the raw text. Nothing extra is needed — asking for a numbered list *is* the
data extraction step. `{@list(var)}` then picks from it, and `{@each(var, script)}` runs a script
per item.

```prowl
Write a Grocery List as a numbered list with up to ten items, only include the names:
{groceries(120, 0.4)}

- Ingredient 1: {@list(groceries)}
- Ingredient 2: {ingredient_2(12, 0.1)}
```

## Re-declaring a variable is revision, not overwrite

Declaring the same name twice pushes the old value onto `var.history` and generates a new one.
The prompt still contains the first value, so the model sees its own earlier answer and can
correct it. This is the whole ablation ladder in `D:\prompt-library\benchmark\`:

```prowl
## Question
{question}

## Answer
{answer(2, 0.0)}

## Double Check
If your answer might have been incorrect, write the correct answer:
{answer(2, 0.0)}
```

`{@each(var, script)}` also treats a variable's history as a list when it has no bullet list.

## `.prout` — the projection

A `.prout` file sitting next to `script.prowl` is an **output template**: references only, never
declarations, never shown to the LLM during the run. After the script finishes, the stack fills it
and returns it in `result.out()`.

```
tot.prowl   ->  generates crit_questions(500) and crit_answers(700)
tot.prout   ->  "# Considerations\n{crit_answers}"
```

This is the compression operator. A script can think in 1200 tokens and hand forward 700. Build
long-running hierarchies (worlds, books, multi-stage analysis) by running stacks `atomic=True` and
carrying the `.prout` projections forward instead of the raw completions — otherwise every stage
inherits every token of every earlier stage.

## Stacking

```python
stack = ProwlStack(folder=['prompts/monster/', 'prompts/thought/'])
r = await stack.run(['monster', 'tot'], inputs={'monster_name': 'Grelth'})
r.get()    # {var: value}
r.out()    # joined .prout projections
```

By default each script is appended to the previous completion — one continuous document across
the whole stack. `atomic=True` runs each independently.

`stack.validate()` runs before anything generates: it checks that every referenced variable is
declared by an earlier script in the stack, that required tools are loaded, and that referenced
scripts exist. Write scripts so their inputs are explicit and this catches ordering mistakes for
free.

## Gotchas

- **`max_tokens=1` doesn't work.** Known bug — auto-continue guards on `> 1` and the single-line
  cleanup can blank the value, which re-enters the retry loop forever. Use `2`. This is why every
  benchmark script says `{answer(2, 0.0)}`.
- **A single-line value ending in `:` is discarded and retried.** The model was about to write a
  list. Either rephrase so a colon isn't natural, or make it multiline.
- **Empty generations retry with rising temperature** — 4 attempts, temperature climbing toward
  1.0, then `ValueError: Cannot Generate Value`. If you see that error the problem is almost
  always the instruction above the blank, not the model.
- **Everything before a variable is context, including other scripts in the stack.** Long stacks
  get expensive fast: the prompt is re-sent in full for every declaration. Count declarations, not
  scripts.
- **`{@tool(...)}` calls are resolved when the walk reaches them** — they fire before the next
  declaration, so a tool's output can condition the variable after it.

## Writing a new script

1. Write the finished document with the blanks left blank. Headers first.
2. Put an instruction line ending in `:` above each blank.
3. Name each blank for what it *is*, not what it does — the name is read by humans in `r.get()`
   and by `.prout`.
4. Set budget from the shape, temperature from whether it's derived or invented.
5. Blank line after every multiline variable.
6. Order so that everything a variable needs already appears above it.
7. Add a `.prout` if anything downstream consumes this script.
