---
name: prompt-owl
description: >
  Run and drive Prompt Owl — the local studio for composing, running and measuring prowl stacks.
  Use when the user wants to start or stop the studio server, run a `.prowl` stack across several
  models, sweep an input, compare models variable by variable, look at what a variable does across
  many runs (distinct ratio, entropy, mean/sd, embedding clusters), or drive the studio UI from a
  browser tool. Also use when a harness needs to run prowl stacks headlessly over HTTP instead of
  through Python. For *writing* `.prowl` syntax itself, use the `prowl-script` skill.
---

# Running Prompt Owl

Prompt Owl is a prompting language (`.prowl`) plus a local studio for it. The studio is a FastAPI
server on loopback with a zero-build web client, and **everything the UI does, it does over an
HTTP API you can call directly** — which is usually the better move from inside a harness.

Two skills, split by what you are doing:

- **`prowl-script`** — writing and debugging `.prowl` syntax. Declarations, types, stops, budgets.
- **this one** — operating the studio: starting it, running stacks, sweeping, measuring.

## Starting and stopping it

Install once, from the repo root:

```bash
pip install -e ".[studio]"
```

Then, and this is the part harnesses get wrong: **never start it with a blocking shell call.** It
is a server; it does not return.

**Claude Code** — the repo ships `.claude/launch.json` with a `prompt-owl` entry, so use the
browser-pane tools and let them own the process:

- `preview_start` with `{name: "prompt-owl"}` — starts it and opens a tab, returns a `serverId`
  and a `tabId`
- `preview_logs` with the `serverId` — startup errors, tracebacks
- `preview_stop` with the `serverId` — stops it

**Restart it after editing any Python under `prowl/`.** Uvicorn runs without `--reload` here, so a
stale process silently serves the old code. Static files under `prowl/studio/web/` only need a
page reload.

**Any other harness** — background the process and keep the handle:

```bash
python -m prowl.studio.server --env .env --port 8788
```

Flags: `--root DIR` (workspaces, default `~/.prowl/workspaces`), `--host` (default `127.0.0.1`),
`--port` (default `8788`), `--env FILE` (KEY=VALUE, existing environment wins), `--open`.

Loopback is the default deliberately: a run spends money and the `@file` tool reads the
filesystem. Binding elsewhere logs a warning; do not do it without being asked.

### Environment

`--env` reads a plain `KEY=VALUE` file. The keys that matter:

| key | meaning |
|---|---|
| `PROWL_VENDOR_API_KEY` | required — without it every run fails and the header says so |
| `PROWL_VLLM_ENDPOINT` | base URL, e.g. `https://openrouter.ai/api` |
| `PROWL_MODEL` | the model used when a run names none |
| `PROWL_MAX_COMPLETION_TOKENS` | budget ceiling per run, default 20000 |
| `PROWL_EMBED_MODEL` | default embedder, `baai/bge-m3` |
| `PROWL_CHAT` | `1` to talk to `/v1/chat/completions` by assistant prefill |

`GET /api/health` reports `has_key` (never the key), the endpoint, the model and the budget.

## The API is the interface

Prefer this over clicking. Everything is under `/api`, JSON in and out.

| method | path | does |
|---|---|---|
| GET | `/health` | key present, endpoint, budget, workspace root |
| GET | `/lang` | prowl's own patterns, types, stops, option names, error codes |
| GET | `/models?q=&free=&limit=` | catalogue search, filtered to models that support `stop` |
| GET | `/model-endpoints?id=` | which backends serve one model id, at what quantization |
| GET/POST | `/workspaces` | list, create |
| GET | `/w/{ws}/scripts` | every script with what it declares, requires, calls |
| GET/PUT/DELETE | `/w/{ws}/script/{name}` | read, write (`{prowl, prout, folder}`), delete |
| POST | `/w/{ws}/script/{name}/rename` | `{to}` — moves the `.prout` with it |
| GET/PUT/DELETE | `/w/{ws}/stacks[/{name}]` | saved stacks |
| POST | `/w/{ws}/validate` | `{scripts, inputs}` — **free**, no generation |
| POST | `/w/{ws}/run` | SSE stream, see below |
| POST | `/run/{run_id}/stop` | stops a run within one declaration |
| GET | `/w/{ws}/history?stack=&by=&clean=&limit=` | recorded samples + statistics |
| DELETE | `/w/{ws}/history?stack=` | drop recorded samples |
| POST | `/w/{ws}/embed` | cluster a variable's values by meaning |

**Always `validate` before `run`.** It costs nothing and returns `errors`, `declarations`,
`max_completion_tokens`, `over_budget`, `inputs_required` and `inputs_missing`. A run whose worst
case exceeds the budget is refused rather than truncated.

### Running

```bash
curl -N -X POST localhost:8788/api/w/naming/run -H 'content-type: application/json' -d '{
  "run_id": "r1", "scripts": ["w_wuxia", "name"], "inputs": {},
  "models": ["meta-llama/llama-3.1-8b-instruct", "mistralai/mistral-nemo"],
  "extra": {"provider": {"order": ["DeepInfra"], "allow_fallbacks": false}},
  "stack_name": "wuxia-name"
}'
```

The client picks `run_id` so it can stop the run before the first token arrives. Models in the
list run **concurrently**; a pool of six finishes in the time of the slowest.

Server-sent events, every one carrying its `model`:

| event | when |
|---|---|
| `start` | scripts and declaration count, once |
| `token` | streamed text, with `variable` |
| `var` | a declaration finished — value, type, usage, `span`, `history` |
| `script_end` | one script done, with its usage |
| `error` | validation or budget refusal; nothing generated |
| `done` | per model: `ok`, `completion`, `variables`, `usage`, or `failed_variable` |
| `finished` | all models done |

`GenerationError` comes back as `ok: false` with **`failed_variable`** — which variable stopped
filling on that model. That is the number worth reporting when comparing models.

### Measuring

Every filled declaration is appended to `runs/history.jsonl` in the workspace, keyed by the stack
signature (`script/script`), with model, provider, type, temperature, tokens, truncation and the
inputs that produced it. So statistics survive restarts and are readable with `jq`.

`GET /w/{ws}/history` takes **`by`**, which is the whole point:

`model` · `provider` · `script` · `stack_name` · `run` · `none` · **`input:<name>`**

Input axes are discovered from the samples; the response lists them in `keys`. Each variable also
gets a pooled row across all groups — pooled and split answer different questions.

Per group: `n`, `unique`, `unique_folded`, `top`, `mode_share`, `entropy` (normalised `H/log n`:
1.00 when every run differed, 0.00 when they all agreed), and `numeric` (`mean`, sample `sd`,
`min`, `max`) when every value parses as a finite number.

**`clean=true` (default) drops truncated `word`/`line`/`number`/`bool` samples from the
statistics** and reports how many under `dropped`. A truncated bounded value is a model that never
reached the stop — it wrote prose — and for `number` the last-number-wins rule then lifts a figure
out of mid-sentence. `text` and `list` are never dropped.

`POST /w/{ws}/embed` with `{stack, variable, by, group}` embeds the **distinct** values and leader-
clusters them, returning `clusters`, `spread` (mean pairwise cosine distance), the groups and the
cost. Use it when the values are sentences: 32 back-translations were 32 distinct strings (100%,
no information) and 14 clusters, one holding 19 — the 13 singletons were the actual failures.

## The loop that produces a result

1. `validate` the stack. Fix errors before spending anything.
2. Pin a provider. One model id is served by several backends at different quantization and they
   do not answer the same way — unpinned, a model comparison is partly a routing comparison.
3. Run it **many times**. One fill is an anecdote; `n=1` has no entropy and no sd.
4. Vary one thing at a time — a model list, or one input.
5. Read `history` along the axis that matches the question you asked.
6. Cluster by meaning if the values are longer than a word.

## Driving the UI in a browser tool

Only when the question is about the interface itself, or when showing the user something. For
everything else the API is faster and does not depend on rendering.

The sandboxed browser pane **does not support `prompt()` or `confirm()`**, and both are used for
naming stacks and confirming large sweeps. Stub them first or those clicks throw:

```js
window.prompt = () => 'my-stack-name'
window.confirm = () => true
```

Handles worth knowing:

| selector | is |
|---|---|
| `#ws-pick` | workspace `<select>` — set `.value`, then `dispatchEvent(new Event('change'))` |
| `#browser .row` | a script; clicking adds it to the stack |
| `#stacks .row` | a saved stack; clicking loads the whole configuration |
| `#stack-new` | start a new, unnamed stack |
| `#stack .chip` | one script in the stack; draggable, `Alt+←/→` reorders |
| `#model` / `#provider` | model search box (Enter adds), provider pin |
| `#repeats` | run count; fire `change` after setting `.value` |
| `#run` / `#stop` | run reads `▶ Run ×N` for inputs × repeats × models |
| `.inputs .vary` | mark an input to vary — its box becomes one value per line |
| `.tabbar.sub button[data-pane=…]` | `document` `compare` `variables` `history` `errors` `raw` |
| `#status` | the spend pill: runs, tokens, dollars, seconds |

State is on the page but not exported; read results from the DOM or, better, call the API.

A run takes as long as it takes. Poll rather than sleeping blindly:

```js
new Promise(r => setTimeout(() => r({
  done: document.querySelector('#stop').hidden,
  progress: document.querySelector('#stop').textContent,
  spend: document.querySelector('#status').textContent,
}), 20000))
```

## Gotchas

- **Restart the server after touching Python.** No `--reload`.
- **An empty `.prowl` makes the whole stack unconstructible.** `load()` returns `""`, which is
  falsy, and the task ends up with `code: None`. Zero-byte scripts in a folder break every script
  in it.
- **Reasoning models return empty content and bill the full budget.** A `{n:number(8)}` on a
  thinking model fails as "no number in the completion". Send `reasoning: {enabled: false}` in
  `extra`, or leave those models out of small-budget benches.
- **Temperature 0 is not deterministic.** Four pinned calls to one model gave three identical
  answers and one different, and the odd one flipped a pass/fail grade.
- **Provider pinning and `json_schema` are mutually exclusive** — pinning to a backend that lacks
  `structured_outputs` returns HTTP 404 "No endpoints found".
- **Scripts are read and written as UTF-8 explicitly.** Do not let a locale decide.
- **The budget refuses, it does not truncate.** A runaway `{story(4096)}` across a model pool is
  real money, so an over-budget stack will not start.
- **Cost is real but small.** A 40-generation sweep of short declarations is well under a cent.
  Say what a sweep will cost before starting a large one; the UI confirms above 20 generations.
