# Changelog

## 0.2.0 (unreleased)

Not on PyPI yet — `pip install -e ".[studio]"` from a clone. The headline is that a declaration
can say what kind of value it expects, and that there is now a studio for running a stack across
several models and looking at what each variable actually does over many runs.

### Added

- **Variable types.** A declaration may name the kind of value it expects:

  ```prowl
  {answer:number(24, 0.0)}
  {sure:bool(6, 0.0)}
  {mood:word(8, 0.3)}
  {story:text(1024, 0.8)}
  {steps:list(300, 0.2)}
  ```

  The type decides the stop sequence, how the completion is read, and **what counts as a value at
  all**, so `max_tokens` goes back to being a runaway guard instead of doubling as a shape hint.

  **A type annotates a declaration; it never makes one.** The parentheses are what declares, as
  they always have been — `{answer:number}` with no arguments raises `ValidationError` (1009),
  because a reference only splices a value that already exists and has nothing to type.

  | type | stops at | holds |
  |---|---|---|
  | `word` | newline | the first word, stripped of surrounding punctuation |
  | `line` | newline | one line, invalid if it ends in `:` |
  | `number` | newline | the last number in the text |
  | `bool` | newline | `true`/`false`, invalid if neither |
  | `text` | next markdown header | the prose |
  | `list` | next markdown header | the text, invalid with no items |

  `text` and `list` stop at `\n#` and **not** at a blank line. A blank line is inside prose, not
  the end of it; that default silently truncated a chain-of-thought after one sentence.

  Unknown types raise `ValidationError` (1008) at validate time, before anything is generated.

- **Truncation is visible.** `finish_reason == 'length'` was discarded, so a value cut off
  mid-word was indistinguishable from a finished one. Variables now carry `truncated`, it is
  logged when it happens, and it is reported in the failure when a value could not be read.

- **Retries key on validity, not emptiness.** The old loop retried `while completion == ""`, so
  anything that survived cleanup was accepted whatever it contained: `{answer(2, 0.0)}` returned
  `"Let's denote"` and `{answer(8, 0.0)}` returned `"The final answer is: $\boxed{"`, both scored
  as answers. A typed declaration that yields no value retries and then raises `GenerationError`
  naming the type, the truncation and the last value seen.

- **Declaration options.** After `(max_tokens, temperature)` a declaration may carry named
  options: `stop`, `n`, `logprobs`, `model`, and the bare flags `block` / `inline`.

  ```prowl
  {label(12)}                                  temperature defaults to 0.0
  {code(600, 0.1, stop=```)}                   its own stop, so blank lines are legal
  {cause(200, 0.7, n=3)}                       alternatives kept in var.data['candidates']
  {plan(400, 0.2, model=qwen/qwen3-32b)}       per-variable model
  {story(1024, 0.8, block)}                    force multiline regardless of whitespace
  {note(30, 0.5, inline)}                      force single-line
  ```

  `stop` is per-variable and overrides the run default, so one script can mix a one-line answer
  with a code block. `block`/`inline` override the surrounding-whitespace heuristic, which
  remains the default so no existing script changes.

  **Unknown option names raise `ValidationError` (1005) rather than being forwarded.** Options
  become request parameters, and silently passing a typo through is precisely how `stops=`
  instead of `stop=` disabled stop sequences on every auto-continuation. `-validate` reports
  them without generating anything.

- `temperature` is optional; it defaults to `prowl.TEMPERATURE` (0.0).

- **Chat endpoints, by assistant prefill.** The growing document goes into a pre-started assistant
  turn and the model carries on writing it. The mechanism is unchanged and only the envelope
  differs, so `VLLM` normalises a chat choice back into a completion choice and `fill()`,
  `resolve()` and every stop rule stay unaware of which endpoint answered. Set per run with
  `chat=` on `ProwlStack.run`, or `PROWL_CHAT` in the environment.

  Measured on a capability ladder, four models, provider pinned: completions and chat prefill score
  identically on three of four, and mistral-nemo drops exactly one rung — answering `'Your'` where
  it should copy a word, the model replying *as a turn* instead of continuing. So the prefix
  mechanism survives on chat endpoints. What it costs is tokens: 19–91% more, because the user turn
  is re-sent on every declaration.

  This also makes otherwise-unusable models work. `gpt-4o-mini` restates the prompt verbatim on
  `/v1/completions` — OpenRouter serves chat-only models there by adapting them, and the adapter
  gives itself away — but continues correctly through prefill.

- **`prowl studio`** — a local sandbox for writing, arranging, running and **measuring** stacks.
  `pip install prompt-owl[studio]`, then `prowl-studio`. Loopback only by default: a run spends
  money and `@file` reads the filesystem.

  A workspace is just a directory of `.prowl` and `.prout` files under `~/.prowl/workspaces`
  (`--root` to move it, subdirectories to partition). What you edit in the studio is a file the
  CLI and the MCP server can also see — the sandbox is never a place where scripts live that
  prowl itself cannot reach.

  `GET /api/lang` serves the interpreter's own constants — `PATTERN_FILL`, `PATTERN_CALL`,
  `PATTERN_ARGS`, `PATTERN_LIST`, `PATTERN_MASK`, `TYPES`, `OPTIONS`, `STOPS` — so the editor's
  highlighter compiles prowl's grammar instead of carrying a copy of it. The patterns are valid
  JavaScript regex exactly as written; only the flags differ. A highlighter that disagrees with
  the interpreter is worse than none, because it paints inert prose as a live declaration.

  `prowl/studio/core.py` is the single definition of "assemble a stack and check it", and
  `prowl/mcp.py` now imports it rather than keeping its own.

  A **stack** is a saved primitive, not a gesture: `stacks.json` beside the scripts holds the
  order, its inputs, its model pool, `atomic`, `chat`, which inputs sweep, and the provider pin —
  a saved comparison that does not record its backend is not reproducible.

- **Run history.** Every filled declaration is appended to `runs/history.jsonl` in the workspace,
  with model, provider, script, type, temperature, tokens, truncation and the inputs that produced
  it. One fill is an anecdote; the properties worth knowing only exist across runs.

  Statistics are cut along a chosen **axis** — models, providers, scripts, stacks, runs, or **any
  input the samples carry** — plus a pooled row across all groups, because pooled and split answer
  different questions. Three models each answering `7` every time is entropy 0.00 three times and
  0.00 pooled; three models each stuck on a *different* number is 0.00 three times and 1.58 pooled.

  Per group: n, distinct count and ratio, normalised entropy `H/log n` (1.00 when every run
  differed, 0.00 when they all agreed), and mean ± sample sd with a strip plot for numeric values.

  **Truncated `word`/`line`/`number`/`bool` samples are excluded by default** and the count is
  shown as `−N`. A truncated `text` is a long answer cut short and still says what it says; a
  truncated bounded value is a model that never reached the stop, and for `number` the
  last-number-wins rule then lifts a figure out of mid-sentence. Averaging those gave one model a
  mean of 33.1 over values including `0.75`. It is a judgement, so it is a toggle.

- **Clustering by meaning.** Counting distinct strings answers "how many names"; it cannot answer
  "how many answers", because the same answer written twice is two strings. Distinct values are
  embedded and leader-clustered — against the leader, never a running mean, which drifts across the
  space and swallows the corpus. 32 back-translations were 32 distinct strings (100%, no
  information) and 14 clusters, one holding 19; the 13 singletons were the actual failures.

  OpenRouter serves `/v1/embeddings` on the same key even though its model catalogue lists no
  embedding model at all, which nearly got this written off as impossible.

- **Input sweeps.** Any input can be marked to vary: its box becomes a list, one value per line,
  and the runner walks the cross product across repeats and models. Sixteen languages is a research
  question; retyping it sixteen times is why the question does not get asked.

- **Export** as `csv` (BOM'd, so Excel reads the UTF-8), `jsonl`, `json`, or as training pairs in
  `messages` (OpenAI fine-tuning) and `alpaca` shapes. The training formats exist because a run is
  one token sequence whose spans are named: `completion[:start]` is exactly the prompt that
  produced `completion[start:end]`, so one run yields **one pair per declaration**, and the prompt
  is the real growing prompt with every earlier value spliced in — not a template with the inputs
  pasted back. Finished documents persist to `runs/documents.jsonl`.

- **Model capability probing.** The catalogue lists `stop` support; it cannot say whether a model
  *continues a document*. `GET /api/model-probe` spends five tokens and classifies the answer as
  `continues`, `echoes` (a chat-only model behind an adapter — use chat prefill), `empty`, or
  `unsupported`. Models are probed when added to the pool and the chip is marked.

- **Reasoning is disabled automatically.** Given `{answer:number(8)}` a thinking model spends all
  eight tokens on reasoning and returns `""` with `finish_reason: length`, which prowl reports as
  "no number in the completion" — sending you to inspect a prompt that was fine. 171 of the 284
  stop-capable models on OpenRouter declare `reasoning`, so this is the common case, and prowl
  declarations are bounded by design. The studio sends `reasoning: {enabled: false}` unless the
  caller set it, and badges those models `think`.

- **`GET /api/probe`** sends one token down `/v1/completions` and `/v1/chat/completions` and reports
  what answered, so "is my Ollama / vLLM / OpenRouter set up right" is one request rather than a log
  hunt.

- **Themes** — `owl`, `paper`, `ember`, `terminal`, `slate`. A theme is eleven CSS variables and
  nothing else; no rule in the stylesheet names a colour directly.

- **Agent skills** in [`skills/`](skills/): `prowl-script` for writing the language, `prompt-owl`
  for running the studio and its API. `AGENTS.md` points at both for harnesses that read it, and
  `.claude/launch.json` defines the server so a browser pane can own the process from a fresh
  clone.

- **Stream levels are cumulative.** `StreamLevel.covers()` orders them
  `none < script < variable < token`, so asking for tokens also delivers the settled variable and
  the finished script. They were exclusive, which meant a caller wanting live tokens *and* the
  variable objects they resolve into — every UI — could have one or the other.

- **Variables carry `span`**, the offsets where the value landed in the finished document. A run is
  one token sequence whose spans happen to be named; without offsets that is a claim rather than
  something you can look at. In a non-atomic stack each fill prepends the previous completion
  verbatim, so spans stay valid against the final document.

- **`prowl.shape(template, start, end)`** returns `'block'` or `'inline'` for a declaration, using
  the three-character rule `fill` has always used inline. Named so that anything showing a script
  to a human can show the shape too, and agree with `fill` when it does — a missing blank line
  truncating a 1024-token narrative to one line is the most common prowl bug and it is invisible
  in the source.

- **`ProwlStack(include_library=False)`** stops the relative `prompts/` folder being appended to
  every stack, which made a stack's contents depend on the process working directory. Default is
  unchanged.

### Fixed

- **The `word` type never produced a value.** Its stops were `['\n', ' ']`, and a completion opens
  with its own leading space, so the space stop fired at offset zero and the value came back empty
  — every time, on every model. `{name:word(12, 0.7)}` after a label burned its retries and raised.
  Measured directly: `stop=['\n',' ']` returns `''`, `stop=['\n']` returns `' \`component\`'`.
  `read()` already takes the first word, so the space stop was redundant as well as fatal.

  Worth noting how it surfaced: the run failed loudly, naming `name_1`, instead of storing an empty
  string and producing a plausible table. That is the retries-key-on-validity change doing its job
  while the type it was checking was wrong.

- **An empty `.prowl` file made `ProwlStack` unconstructible.** `prowl.load` returns `""` for an
  empty file, `""` is falsy, and `add_task` chained `load(x.prowl) or load(x.md)` — so an empty
  script fell through to a `.md` that was not there, got registered with `code: None`, and the
  `inspect()` in `__init__` raised `TypeError` on it. Six scripts shipped in `prowl/prompts` are
  0 bytes, which is why `ProwlStack(folder='prowl/prompts/pov/')` did not work at all. `None` now
  means "could not read" and never "was empty", and a task with no code is refused rather than
  stored.

- **A stop firing on the first token left a variable permanently empty.** Some models open a
  completion with `\n`, which fires the `word`/`line`/`number` stop at offset zero and returns
  nothing — indistinguishable from a model with nothing to say. An empty first result now retries
  once with stops removed, and says so.

- **`word` read punctuation as a value.** `' "Evelyn Deveraux"'` became `'"Evelyn'` and `' |'`
  became `'|'`. Words are stripped of surrounding quotes, brackets and punctuation now.

- **The studio's syntax overlay drifted from the caret**, 0.47px per character — three characters
  by column 40, resetting each line, which reads as a fixed offset rather than as drift. The
  stylesheet sets the font on the `<pre>`, and the text lives in a `<code>` inside it, where the
  browser's own stylesheet says `code { font-family: monospace }` and beats inheritance. The rule
  already carried a comment about setting every property affecting glyph advance on both elements;
  it was set on both elements the rule named.

- **Scripts are read as UTF-8.** `open(path, "r")` used the locale encoding, which is cp1252 on
  Windows — so a script containing an em dash raised, was swallowed by the bare `except`, and
  came back as `None`. Found while loading the library into a studio workspace.

- **`validate()` and `forecast()` disagreed with `fill()` about ```prowl blocks.** `fill` masks
  those blocks so their braces are inert, but `inspect_vars` and `forecast` scanned the raw source.
  A variable written inside a block therefore counted as *declared* without ever being declared —
  so a stack referencing it validated clean and then failed at generation time — and `forecast`
  budgeted for a call that never happens. Both mask first now. Found by putting the studio's
  outline next to the stack's own report of the same file and noticing they named different
  variables.

  Tool calls are the deliberate exception: `run_callbacks` scans the accumulated prompt, so a
  `{@tool()}` inside a ```prowl block **does** fire, and `inspect_tools` matches that.

- **`{@script}` blocks corrupted the prompt after them.** `mask_prowl_code_blocks` replaced a
  ```` ```prowl ```` block with a shorter placeholder, but matches found on the masked text were
  used to slice the *unmasked* text. Every variable after such a block was sliced from the wrong
  offset and had its multiline/inline shape read from the wrong characters. In `prowl.prowl` this
  made `{prowl_script(4096, 0.1)}` parse as inline, so the generated script was truncated at its
  first line. The mask is now the same length as what it replaces (braces neutralized in place),
  so offsets stay valid; `unmask_prowl_code_blocks` is gone.
- **Streamed runs reported a fabricated token count.** The streaming path counted SSE *chunks* and
  called them completion tokens, with `prompt_tokens` hard-coded to `0` — a run measured at 36
  tokens that really spent 40, and no prompt side at all, which is the expensive side. The vendor
  sends real usage on the final chunk and it was being dropped on the floor. It is now read, and
  streamed and non-streamed runs of the same template agree exactly.

  No request flag turns this on: `stream_options: {"include_usage": true}` and OpenRouter's own
  `usage: {"include": true}` were both measured against a bare stream and all three return byte
  identical usage. The data was always there.

  When a vendor genuinely sends no usage, the chunk count is still returned but carries
  `estimated: True`, so a counted number can never again be mistaken for a reported one.

- **`Usage` carries `cost`.** The vendor prices the call and returns it; there is no reason to
  re-derive it from a rate card. `Usage.cost(prompt_multiplier, completion_multiplier)` is
  unchanged for callers estimating against their own rates.

- **A null `finish_reason` on a late chunk could erase an earlier one.** The streaming loop
  overwrote the reason on every chunk, so a vendor that reports `length` and then sends a trailing
  usage chunk without it would lose the truncation. Last non-null wins. A usage chunk carrying no
  `choices` at all no longer raises into the error log either.

- **Auto-continuation ran without stop sequences.** `auto_continue` passed `stops=` where the API
  expects `stop=`, so continuations generated to `max_tokens` and were trimmed after the fact.
  Only visible when `continue_ratio > 0`, which is the `ProwlStack` default.
- **A failed request could retry forever.** The retry path incremented its counter but never
  checked the limit, so a persistently unreachable endpoint looped indefinitely with the
  temperature escalation climbing past 1.0. Retries are now bounded and the original exception is
  raised, instead of every failure being reported as `LLM CONNECTION ERROR`.
- **Auth and credit failures now fail fast.** HTTP 401/402/403 raise `APIError` immediately rather
  than being retried and then surfacing as `Cannot Generate Value` — a billing problem no longer
  looks like a prompt problem.
- **`except:` no longer swallows `KeyboardInterrupt`.**
- OpenRouter SSE keepalive comments and the `[DONE]` sentinel no longer produce warning spam, and
  keepalives preceding a non-streaming body no longer break JSON parsing.
- Environment variables are read when a client is constructed, not at import. `load_dotenv()`
  after `import prowl` now works.

### Changed

- **Diagnostics go to stderr; stdout carries results only.** All library `print()` calls route
  through `prowl.lib.log`. Set `log.sink = lambda level, message: None` to silence, or point it
  anywhere to capture. This makes `prowl ... -json | jq` work.
- **One stop default everywhere: `prowl.STOPS = ["\n\n", "\n#", "##"]`.** Previously `prowl.fill`,
  `ProwlStack.run`, `ProwlStack.fill` and the CLI each had a different default. Callers that
  passed `stops` explicitly are unaffected.

  Bare `"##"` is kept even though it also fires mid-line. It was briefly dropped during
  development for that reason, and a benchmark run across five models showed why it exists:
  models routinely run a header onto the current line (`"Since## Step 2"`, `"2.##"`), which
  `"\n#"` cannot catch because there is no newline. The artifact appeared in **40–54% of
  responses** on the Llama family without it, and **0%** with it. Variables generating code or
  prose containing `##` now override it per declaration with `stop=`, which is the escape hatch
  that did not exist in 0.1.
- `ProwlStack.print()` takes only positional args now (it formats through `log`).

### CLI

Rewritten to be usable non-interactively, by scripts and agent harnesses.

- `-json` writes one result object to stdout
- `-validate` checks the stack and exits without generating
- `-input=KEY=VALUE` (repeatable) and `-stdin` (a JSON object) supply input variables; the CLI
  never blocks on `input()` unless it is run with no scripts and a TTY
- `-quiet` silences diagnostics
- exit codes: `0` ok, `1` validation failed, `2` generation failed, `3` auth/credit, `4` usage
- flags accept one or two leading dashes
- debug prints left in `parse_scripts` removed
