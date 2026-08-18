# Example workspaces

Five workspaces, each teaching a different thing. They are ordinary directories of `.prowl` files,
so there is nothing to install: point the studio at this folder and they are all there.

```bash
prowl-studio --root workspaces --env .env --open
```

Or copy them into your own workspace root and keep them:

```bash
cp -r workspaces/* ~/.prowl/workspaces/          # macOS, Linux, Git Bash
copy /E /I workspaces %USERPROFILE%\.prowl\workspaces   # Windows cmd
```

Three of them ship with `runs/history.jsonl`, so the **History** tab has real recorded values the
moment you open it. You can read a distribution across models without spending anything.

Those recordings were made with `meta-llama/llama-3.1-8b-instruct` and `mistralai/mistral-nemo`,
pinned to DeepInfra. One caveat on `translate`: the samples predate a punctuation change to the
`register` instruction line, so that one line reads slightly differently from the script that
produced them. Nothing else has moved.

| workspace | scripts | shows you |
|---|---|---|
| [`demo`](demo) | 3 | inputs, typed declarations, a `.prout` projection, and a deliberately broken script |
| [`bench`](bench) | 1 | a capability ladder: eight tasks in one document, each harder than the last |
| [`naming`](naming) | 6 | genre priming, and measuring how much variety a model actually has |
| [`numerology`](numerology) | 6 | numeric distributions, and what a model does off its training distribution |
| [`translate`](translate) | 7 | input sweeps, cross-model comparison, and a real experiment |

---

## `demo`

The smallest complete thing. `brief` takes a `{topic}` input and declares a `line` and a `number`;
`draft` follows it in a stack and is conditioned on both. `brief.prout` projects the result.

`sketch/broken.prowl` is a **fixture**: every construct in it is invalid on purpose, and it exists
to show what the error panel says. A type with no parentheses, a multiline variable missing its
blank line, `stops=` instead of `stop=`, and two forms that look like declarations but are inert.
Do not copy anything out of it. It also sits in a subfolder, which is how a workspace partitions.

## `bench`

One script, eight numbered tasks, each depending on the answers above it: copy a word, extract a
number, choose from a set, decide a boolean, and so on up. Run it across a model pool and the
Compare tab shows exactly which rung each model falls off.

Its own result is worth knowing: across four models at the time of writing, 31 of 32 cells passed.
**The ladder is too easy to discriminate**, which is a finding about the ladder, not the models.

## `naming`

Five one-line genre primers (`w_wuxia`, `w_norse`, `w_cyberpunk`, `w_noir`, `w_fantasy`) and one
`name` script. Stack a primer in front of `name` and the same declaration produces a different
distribution of names.

This is where the History tab came from. The question was whether small models have a narrow
naming prior, and the answer at temperature 0.9 was **no**: 12 runs gave 12 distinct names, entropy
1.00. Mode collapse, if it is there, lives at low temperature. Change the temperature in `name` and
run it 20 times to see for yourself.

## `numerology`

Six one-script stacks asking what symbolic furniture is in a model: a number between 1 and 100,
your lucky number and why, the world's luckiest and unluckiest, one die roll, and the most
meaningful number with an explanation. All at temperature 1.0, because the question is the shape of
the prior rather than the most likely answer.

`llama-3.1-8b` over 20 runs: **mean 42.6 ± 27.4**, most common `23`, with `37` and `42` twice each.
The human favourites, reproduced.

Stack two of them together and it gets more interesting. Conditioning a model into numerology
territory and then asking for arithmetic produces confident, entirely fabricated mathematics, and a
small model pushed far enough off distribution will code-switch mid-sentence.

The `meaningful` script declares a `text` alongside the number, which is what the embedding
clustering in History is for: counting distinct strings cannot tell you how many distinct
*answers* there are.

## `translate`

The largest example, and the one that shows what the studio is actually for.

`input_greeting` or `input_asking_directions` sets a source phrase and a target `{language}`;
`translation` then produces four variables: the translation, a literal back-translation, a
self-scored fidelity, and a register word. `language` is an input, and the saved stacks mark it to
**sweep** over 16 languages, so one press of Run is 16 runs per model.

Two things to look at in the recorded history:

- **fidelity self-scores are saturated.** 8.77 ± 1.25 pooled, with 47% of runs awarding themselves
  a 10. A model grading its own round trip is not a measurement.
- **the back-translations need clustering, not counting.** As strings they are 32 distinct of 32,
  which is 100% and no information. Embedded and clustered they are 14 groups with one holding 19,
  and the 13 singletons are the actual failures: one model answering in pinyin, another emitting a
  section heading instead of a sentence.

`instruct_in` and `translation_in` are an experiment rather than a utility. `instruct_in` writes
the translator's instruction *in* a chosen `{instruction_language}`, and `translation_in` uses that
generated sentence as the instruction. It is `translation` with one line changed, so the two arms
are directly comparable.

It also teaches the sharpest lesson in this folder. The first version asked the model to end its
instruction with a colon, and **0 of 32 complied**: asked to write in Japanese a model ends with
`。`, in Arabic with `۔`. Without a continuation cue, 23 of 32 translations came back as the section
heading. Anything a script *needs* to be true about a value has to be structural, not requested in
prose, so `translation_in` writes `{instruction}:` and supplies the colon itself.

---

Not included here: the prompt library lives in its own repository at
[lks-ai/prompt-library](https://github.com/lks-ai/prompt-library), and is a better place to look
for production scripts than any of the above.
