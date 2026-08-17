# Changelog

## 0.2.0 (unreleased)

### Added

- **Variable types.** A declaration may name the kind of value it expects:

  ```prowl
  {answer:number}              the type carries its own budget
  {mood:word(8, 0.3)}          an explicit budget still wins
  {story:text(1024, 0.8)}
  {steps:list(300, 0.2)}
  {sure:bool}
  {title:line(40, 0.6)}
  ```

  The type decides the stop sequence, the default budget, how the completion is read, and
  **what counts as a value at all**. `max_tokens` goes back to being a runaway guard instead of
  doubling as a shape hint, so `{answer:number}` is a complete declaration.

  | type | stops at | holds | budget |
  |---|---|---|---|
  | `word` | newline or space | the first word | 8 |
  | `line` | newline | one line, invalid if it ends in `:` | 64 |
  | `number` | newline | the last number in the text | 16 |
  | `bool` | newline | `true`/`false`, invalid if neither | 8 |
  | `text` | next markdown header | the prose | 512 |
  | `list` | next markdown header | the text, invalid with no items | 300 |

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

### Fixed

- **`{@script}` blocks corrupted the prompt after them.** `mask_prowl_code_blocks` replaced a
  ```` ```prowl ```` block with a shorter placeholder, but matches found on the masked text were
  used to slice the *unmasked* text. Every variable after such a block was sliced from the wrong
  offset and had its multiline/inline shape read from the wrong characters. In `prowl.prowl` this
  made `{prowl_script(4096, 0.1)}` parse as inline, so the generated script was truncated at its
  first line. The mask is now the same length as what it replaces (braces neutralized in place),
  so offsets stay valid; `unmask_prowl_code_blocks` is gone.
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
