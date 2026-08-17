# Changelog

## 0.2.0 (unreleased)

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
  | `word` | newline or space | the first word |
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

- **`prowl studio`** — a local sandbox for writing, arranging and running stacks.
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

- **`prowl.shape(template, start, end)`** returns `'block'` or `'inline'` for a declaration, using
  the three-character rule `fill` has always used inline. Named so that anything showing a script
  to a human can show the shape too, and agree with `fill` when it does — a missing blank line
  truncating a 1024-token narrative to one line is the most common prowl bug and it is invisible
  in the source.

- **`ProwlStack(include_library=False)`** stops the relative `prompts/` folder being appended to
  every stack, which made a stack's contents depend on the process working directory. Default is
  unchanged.

### Fixed

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
