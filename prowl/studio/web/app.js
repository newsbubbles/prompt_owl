// The stack is the unit of work here, not the script. A script checked on its own reports
// variables "referenced before anything declares them" that an earlier script in the stack
// declares perfectly well, so every check below runs against the whole ordered stack.

import {useLang, analyse, render, conform} from './editor.js'

const $ = s => document.querySelector(s)
const el = (t, c, x) => { const n = document.createElement(t); if (c) n.className = c; if (x !== undefined) n.textContent = x; return n }

const S = {
  lang: null, ws: null, browser: [], stack: [], open: null, tools: [], models: [],
  dirty: new Set(), inputs: {}, needs: [], run: null, result: null, marks: null,
  // the stack under composition: its saved name, the spec it was saved as, its recorded past
  stackName: null, saved: null, hist: null, histSig: null, sweep: null, cap: 0,
}

async function api(path, body, method) {
  const r = await fetch('/api' + path, body === undefined ? undefined : {
    method: method || 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body),
  })
  const d = await r.json()
  if (!r.ok) throw new Error(d.error || d.detail || r.status)
  return d
}

// ---------------------------------------------------------------- streaming

async function stream(path, body, on) {
  const r = await fetch('/api' + path, {
    method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(await r.text())
  const reader = r.body.getReader(), dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const {value, done} = await reader.read()
    if (done) break
    buf += dec.decode(value, {stream: true})
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2)
      let ev = 'message', data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) on(ev, JSON.parse(data))
    }
  }
}

// ---------------------------------------------------------------- workspace

async function boot() {
  const [h, lang, ws] = await Promise.all([api('/health'), api('/lang'), api('/workspaces')])
  S.lang = lang
  useLang(lang)
  restoreModels()
  if (!S.models.length && h.model) S.models = [h.model]
  renderModels()
  S.budget = h.budget
  S.spent = {prompt: 0, completion: 0, cost: 0, elapsed: 0, runs: 0}
  S.noKey = !h.has_key
  renderSpend()
  $('#status').title = `${h.endpoint || 'no endpoint'} · ${h.root}`

  const pick = $('#ws-pick')
  pick.replaceChildren(...ws.workspaces.map(w => el('option', null, w)))
  if (ws.workspaces.length) { S.ws = ws.workspaces[0]; pick.value = S.ws; restoreInputs(); await loadBrowser() }
}

async function loadBrowser() {
  const d = await api(`/w/${S.ws}/scripts`)
  S.browser = d.scripts
  S.tools = d.tools || []
  const ul = $('#browser'); ul.replaceChildren()
  for (const s of d.scripts) {
    const li = el('li', 'entry')
    // a real button, so the list is reachable by keyboard and announces itself as clickable
    const b = el('button', 'row' + (S.stack.includes(s.name) ? ' in' : ''))
    b.type = 'button'
    b.title = S.stack.includes(s.name) ? `${s.name} is in the stack` : `add ${s.name} to the stack`
    b.append(el('span', 'name', s.name))
    if (s.folder && !s.folder.endsWith(S.ws + '/') && !s.folder.endsWith(S.ws + '\\'))
      b.append(el('span', 'sub', s.folder.replace(/[\\/]$/, '').split(/[\\/]/).pop()))
    if (s.errors.length) b.append(el('span', 'tag bad', String(s.errors.length)))
    if (s.has_prout) b.append(el('span', 'tag', 'prout'))
    b.onclick = () => { if (!S.stack.includes(s.name)) S.stack.push(s.name); openScript(s.name) }
    li.append(b, act('✎', `rename ${s.name}`, () => renameScript(s.name)),
                 act('×', `delete ${s.name}`, () => deleteScript(s.name)))
    ul.append(li)
  }
  if (!d.scripts.length)
    ul.append(el('li', 'muted', 'No .prowl files here yet — press + to make one.'))
  for (const [name, folders] of Object.entries(d.collisions || {}))
    ul.append(el('li', 'muted bad', `name collision: ${name} in ${folders.join(', ')}`))
  await loadStacks()
  renderStack()
}

const NAME_OK = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/   // mirrors workspace.NAME on the server

function act(glyph, title, fn) {
  const b = el('button', 'act', glyph)
  b.type = 'button'; b.title = title
  b.onclick = e => { e.stopPropagation(); fn() }
  return b
}

function askName(what, current) {
  const n = (prompt(what, current || '') || '').trim()
  if (!n) return null
  if (!NAME_OK.test(n)) { alert(`"${n}" is not a usable name — letters, digits, dot, dash and underscore only.`); return null }
  return n
}

async function renameScript(name) {
  const to = askName(`rename ${name} to`, name)
  if (!to || to === name) return
  try {
    await api(`/w/${S.ws}/script/${name}/rename`, {to})
    // the stack refers to scripts by name, so it has to move with the file
    S.stack = S.stack.map(n => (n === name ? to : n))
    if (S.open === name) S.open = to
    await loadBrowser(); await openScript(S.open)
  } catch (e) { alert(e.message) }
}

async function deleteScript(name) {
  if (!confirm(`Delete ${name}? The .prowl and its .prout are removed from disk.`)) return
  try {
    await api(`/w/${S.ws}/script/${name}`, {}, 'DELETE')
    S.stack = S.stack.filter(n => n !== name)
    if (S.open === name) S.open = S.stack[0] || null
    await loadBrowser(); await openScript(S.open)
  } catch (e) { alert(e.message) }
}

// ---------------------------------------------------------------- the stack

let dragFrom = null

function renderStack() {
  const box = $('#stack'); box.replaceChildren()
  if (!S.stack.length) { box.append(el('div', 'stack-empty', 'Click a script to add it to the stack.')); refresh(); return }

  S.stack.forEach((name, i) => {
    if (i) box.append(el('span', 'arrow', '→'))
    const chip = el('div', 'chip' + (name === S.open ? ' on' : '') + (S.dirty.has(name) ? ' dirty' : ''))
    chip.draggable = true
    chip.tabIndex = 0
    chip.title = `${name} — position ${i + 1} of ${S.stack.length}\ndrag to reorder, or Alt+← / Alt+→\nDelete removes it from the stack`
    chip.append(el('span', 'ord', String(i + 1)), el('span', 'name', name))
    const drop = () => { S.stack.splice(i, 1); if (S.open === name) S.open = S.stack[0] || null; renderStack(); openScript(S.open) }
    const x = el('button', 'x', '×')
    x.type = 'button'
    x.title = `remove ${name} from the stack`
    x.onclick = e => { e.stopPropagation(); drop() }
    chip.append(x)
    chip.onclick = () => openScript(name)
    // reordering is the main thing this bar is for, so it cannot be mouse-only
    chip.onkeydown = e => {
      const to = e.key === 'ArrowLeft' ? i - 1 : e.key === 'ArrowRight' ? i + 1 : null
      if (e.altKey && to !== null && to >= 0 && to < S.stack.length) {
        e.preventDefault()
        const [m] = S.stack.splice(i, 1); S.stack.splice(to, 0, m)
        renderStack()
        ;[...document.querySelectorAll('#stack .chip')][to]?.focus()
      } else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); drop() }
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openScript(name) }
    }
    chip.ondragstart = e => { dragFrom = i; e.dataTransfer.effectAllowed = 'move' }
    chip.ondragover = e => { e.preventDefault(); chip.classList.add('over') }
    chip.ondragleave = () => chip.classList.remove('over')
    chip.ondrop = e => {
      e.preventDefault(); chip.classList.remove('over')
      if (dragFrom === null || dragFrom === i) return
      const [m] = S.stack.splice(dragFrom, 1)
      S.stack.splice(i, 0, m)
      dragFrom = null
      renderStack()
    }
    box.append(chip)
  })
  // Which stack this is, and whether it still matches the one on disk. Without a name the save
  // button can only ever mean "save as", which is what made it look like there was one stack.
  const tools = el('div', 'stack-tools')
  const drift = drifted()
  const nm = el('span', 'sname' + (drift ? ' dirty' : '') + (S.stackName ? '' : ' none'),
                S.stackName || 'untitled')
  nm.title = S.stackName
    ? (drift ? `${S.stackName} — changed since it was saved` : `saved stack ${S.stackName}`)
    : 'this stack has no name yet — saving asks for one'
  tools.append(nm, ghost('⌸ save', S.stackName ? `update ${S.stackName}` : 'name this stack and save it', saveStack))
  if (S.stackName) tools.append(ghost('save as…', 'save this order under a different name', () => saveStack(true)))
  box.append(tools)
  refresh()
}

// ---------------------------------------------------------------- saved stacks

function ghost(text, title, fn) {
  const b = el('button', 'ghost wide', text)
  b.type = 'button'; b.title = title; b.onclick = fn
  return b
}

// Everything that makes a run reproducible. The provider is part of it: the same model id on two
// backends is two quantizations, so a saved comparison that forgets it is not saved.
const spec = () => ({
  scripts: [...S.stack], inputs: {...S.inputs}, models: [...S.models],
  atomic: $('#atomic').checked, provider: ($('#provider').value || '').trim() || null,
})
const drifted = () => !!S.stackName && JSON.stringify(spec()) !== JSON.stringify(S.saved)

async function loadStacks() {
  const d = await api(`/w/${S.ws}/stacks`)
  const names = Object.keys(d.stacks || {}).sort()
  const ul = $('#stacks'); ul.replaceChildren()
  for (const name of names) {
    const saved = d.stacks[name]
    const li = el('li', 'entry')
    const b = el('button', 'row' + (name === S.stackName ? ' in' : ''))
    b.type = 'button'
    b.title = `${saved.scripts.join(' → ')}${saved.models && saved.models.length ? '\n' + saved.models.join(', ') : ''}`
    b.append(el('span', 'name', name), el('span', 'sub', `${saved.scripts.length}`))
    b.onclick = () => applyStack(name, saved)
    li.append(b, act('×', `delete stack ${name}`, async () => {
      if (!confirm(`Delete stack ${name}?`)) return
      await api(`/w/${S.ws}/stacks/${name}`, {}, 'DELETE')
      if (S.stackName === name) { S.stackName = null; S.saved = null }
      await loadStacks(); renderStack()
    }))
    ul.append(li)
  }
  if (!names.length)
    ul.append(el('li', 'muted', 'Compose an order and press save to keep it.'))
}

async function applyStack(name, saved) {
  S.stack = [...saved.scripts]
  S.inputs = {...S.inputs, ...(saved.inputs || {})}
  if (saved.models && saved.models.length) { S.models = [...saved.models]; persistModels(); renderModels() }
  if (saved.provider !== undefined && saved.provider !== null) $('#provider').value = saved.provider
  $('#atomic').checked = !!saved.atomic
  persistInputs()
  S.stackName = name
  S.saved = spec()          // compared like-for-like, so loading a stack never reads as changed
  S.open = S.stack[0] || null
  await loadBrowser()
  await openScript(S.open)
}

// A named stack saves over itself; an unnamed one asks. `save as…` is the third case, and it is
// the one that was missing: composing a variant of a loaded stack and keeping both.
async function saveStack(asNew) {
  asNew = asNew === true
  if (!S.stack.length) return
  let name = S.stackName
  if (!name || asNew) {
    name = askName(asNew ? `save this order as a new stack called` : 'save this stack as',
                   asNew ? '' : S.stack.join('-').slice(0, 40))
    if (!name) return
  }
  const body = spec()
  try {
    await api(`/w/${S.ws}/stacks/${name}`, body, 'PUT')
    S.stackName = name
    S.saved = body
    await loadStacks()
    renderStack()
  } catch (e) { alert(e.message) }
}

async function newStack() {
  if (S.stack.length && !S.stackName &&
      !confirm('Start a new stack? The order you have composed is not saved.')) return
  S.stack = []; S.stackName = null; S.saved = null
  await openScript(null)    // renders the stack too
  await loadStacks()
}

async function openScript(name) {
  S.open = name
  renderStack()
  const ed = $('#editor')
  if (!name) { ed.value = ''; ed.dataset.name = ''; paint(); return }
  const d = await api(`/w/${S.ws}/script/${name}`)
  ed.value = d.prowl || ''
  ed.dataset.name = name

  // The client reimplements masking and the shape rule so the editor can respond to a keystroke
  // without a round trip. This is the check that it still agrees with the interpreter that will
  // actually run the file.
  const drift = conform(ed.value, d.outline)
  if (drift.length) console.warn('[prowl] editor disagrees with the interpreter:', drift)
  paint()
}

// ---------------------------------------------------------------- painting

// Everything a script needs to be read correctly is context from the rest of the stack: which
// variables exist by the time this script runs, which come in as inputs, which tools are loaded.
function context() {
  const declared = new Set()
  for (const name of S.stack) {
    if (name === S.open) break
    const s = S.browser.find(x => x.name === name)
    if (s) for (const v of s.declares) declared.add(v)
  }
  return {declared, inputs: new Set([...S.needs, ...Object.keys(S.inputs)]), tools: new Set(S.tools)}
}

let paintQueued = false
function paint() {
  if (paintQueued) return
  paintQueued = true
  requestAnimationFrame(() => {
    paintQueued = false
    const src = $('#editor').value
    if (!$('#editor').dataset.name) { $('#hl').innerHTML = ''; $('#lints').replaceChildren(); $('#caret').replaceChildren(); return }
    const a = analyse(src, context())
    S.marks = a
    $('#hl').innerHTML = render(src, a.cls)
    syncScroll()
    renderLints(a)
    renderCaret()
  })
}

function syncScroll() {
  const ta = $('#editor'), hl = $('#hl').parentElement
  hl.scrollTop = ta.scrollTop
  hl.scrollLeft = ta.scrollLeft
}

const LEVEL = {err: 'err', warn: 'warn', hint: 'hint'}

function renderLints(a) {
  const box = $('#lints'); box.replaceChildren()
  const sorted = [...a.lints].sort((x, y) => (x.at || 0) - (y.at || 0))
  for (const l of sorted) {
    const lvl = LEVEL[l.level] || 'err'
    const row = el('div', 'lint ' + lvl)
    if (l.code) row.append(el('code', null, String(l.code)))
    row.append(el('span', null, l.message))
    row.onclick = () => {
      const ta = $('#editor')
      ta.focus(); ta.setSelectionRange(l.at, l.at)
      ta.blur(); ta.focus()
    }
    box.append(row)
  }
}

// Which declaration the caret is in, and what will actually happen to it.
function renderCaret() {
  const bar = $('#caret'); bar.replaceChildren()
  if (!S.marks) return
  const at = $('#editor').selectionStart
  const d = S.marks.decls.find(x => at >= x.start && at <= x.end)
  if (!d) {
    const r = S.marks.refs.find(x => at >= x.start && at <= x.end)
    if (r) {
      bar.append(el('span', 'k', `{${r.name}}`))
      bar.append(el('span', null, r.kind === 'p-ref' ? 'reference — splices a value declared earlier'
        : r.kind === 'p-input' ? 'input — supplied by the caller'
        : 'nothing declares or supplies this; the literal text stays in the prompt'))
    } else {
      bar.append(el('span', null, `${S.marks.decls.length} declarations · ${S.marks.refs.length} references`))
    }
    return
  }
  bar.append(el('span', 'k', d.name))
  if (d.type) bar.append(el('span', 't', ':' + d.type))
  bar.append(el('span', null, `${d.max} tokens`), el('span', null, `temp ${d.temp}`))
  bar.append(el('span', d.shape === 'block' ? 's' : null,
    d.shape === 'block' ? 'block — keeps newlines, gets the full budget' : 'inline — cut at the first newline'))
  bar.append(el('span', null, 'stops ' + d.stops.map(s => `"${S.marks.show(s)}"`).join(' ')))
}

async function save() {
  const name = $('#editor').dataset.name
  if (!name || !S.dirty.has(name)) return
  await api(`/w/${S.ws}/script/${name}`, {prowl: $('#editor').value}, 'PUT')
  S.dirty.delete(name)
  await loadBrowser()
}

// ------------------------------------------------------- validate + forecast

let checkTimer = null
function refresh() { clearTimeout(checkTimer); checkTimer = setTimeout(check, 250) }

async function check() {
  const f = $('#forecast')
  if (!S.ws || !S.stack.length) { S.needs = []; f.replaceChildren(); $('#inputs').replaceChildren(); return }
  const v = await api(`/w/${S.ws}/validate`, {scripts: S.stack, inputs: S.inputs})
  S.needs = v.inputs_required
  S.cap = v.max_completion_tokens
  renderInputs(v.inputs_missing || [])
  // Samples belong to the order that produced them, so changing the order retires the view of
  // them. Fetched only while the pane is open: a keystroke should not cost a round trip.
  if (S.histSig !== sig()) { S.histSig = sig(); S.hist = null; if (visible('history')) loadHistory() }

  f.replaceChildren()
  f.append(el('span', null, `${S.stack.length} scripts`))
  f.append(el('span', null, `${v.declarations} declarations`))
  f.append(el('span', v.over_budget ? 'bad' : null, `≤ ${v.max_completion_tokens.toLocaleString()} completion tokens`))
  f.append(el('span', v.ok ? 'ok' : 'bad', v.ok ? 'valid' : `${v.errors.length} problem${v.errors.length > 1 ? 's' : ''}`))
  showErrors(v.errors)
  const run = $('#run')
  run.disabled = !v.ok || v.over_budget
  // a disabled button with no explanation is a dead end; say which thing is in the way
  run.title = v.over_budget ? `worst case ${v.max_completion_tokens.toLocaleString()} tokens is over the ${v.budget.toLocaleString()} budget`
    : (v.inputs_missing || []).length ? `fill in ${v.inputs_missing.join(', ')}`
    : !v.ok ? `${v.errors.length} problem${v.errors.length > 1 ? 's' : ''} — see the Errors tab`
    : 'run the stack  (Ctrl+Enter)'
  paint()   // what counts as bound, as an input, or as dangling depends on the stack order
}

const grow = t => { t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 140) + 'px' }

// Rebuilt only when the set of names changes. Replacing the fields on every keystroke throws away
// focus and the caret with them, which is what made this panel unusable.
function renderInputs(missing) {
  const box = $('#inputs')
  const have = [...box.querySelectorAll('[data-input]')].map(n => n.dataset.input)
  const same = have.length === S.needs.length && have.every((n, i) => n === S.needs[i])
  if (!same) {
    box.replaceChildren()
    if (S.needs.length) {
      box.append(el('span', 'label', 'inputs'))
      for (const name of S.needs) {
        const wrap = el('label', 'input')
        wrap.dataset.input = name
        wrap.append(el('span', 'nm', name))
        const ta = el('textarea')
        ta.rows = 1; ta.spellcheck = false; ta.value = S.inputs[name] || ''
        ta.oninput = () => { S.inputs[name] = ta.value; grow(ta); persistInputs(); refresh() }
        wrap.append(ta)
        box.append(wrap)
        grow(ta)
      }
    }
  }
  const gone = new Set(missing)
  for (const wrap of box.querySelectorAll('[data-input]'))
    wrap.classList.toggle('missing', gone.has(wrap.dataset.input))
}

// Inputs are per workspace and survive a reload: retyping the same topic to try one more model
// is the kind of friction that stops you trying one more model.
const inputKey = () => `prowl.studio.inputs.${S.ws}`
function persistInputs() {
  try { localStorage.setItem(inputKey(), JSON.stringify(S.inputs)) } catch (e) { /* private mode */ }
}
function restoreInputs() {
  try { S.inputs = JSON.parse(localStorage.getItem(inputKey()) || '{}') } catch (e) { S.inputs = {} }
}

// ---------------------------------------------------------------- the pool

function persistModels() {
  try { localStorage.setItem('prowl.studio.models', JSON.stringify(S.models)) } catch (e) {}
}
function restoreModels() {
  try { S.models = JSON.parse(localStorage.getItem('prowl.studio.models') || '[]') } catch (e) { S.models = [] }
}

function renderModels() {
  const box = $('#models'); box.replaceChildren()
  for (const m of S.models) {
    const chip = el('span', 'mchip')
    chip.append(el('span', null, short(m)))
    chip.title = m
    const x = el('button', 'x', '×')
    x.type = 'button'; x.title = `remove ${m}`
    x.onclick = () => { S.models = S.models.filter(n => n !== m); persistModels(); renderModels(); refresh() }
    chip.append(x)
    box.append(chip)
  }
  // The button says how many generations pressing it starts: models × repeats, not models.
  const run = $('#run')
  const total = Math.max(1, S.models.length) * (parseInt($('#repeats').value, 10) || 1)
  run.textContent = total > 1 ? `▶ Run ×${total}` : '▶ Run'
}

function addModel(id) {
  id = (id || '').trim()
  if (!id || S.models.includes(id)) return
  S.models.push(id); persistModels(); renderModels(); refresh()
}

// One model id is served by several backends at different quantization, and they do not answer the
// same way: llama-3.3-70b returns HELIOTROPE on DeepInfra and a row of underscores on Novita.
// Unpinned, a model comparison is partly a routing comparison.
function pinned() {
  const p = ($('#provider').value || '').trim()
  return p ? {provider: {order: [p], allow_fallbacks: false}} : null
}

// ---------------------------------------------------------------- the run

function newId() { return 'r' + Math.random().toString(36).slice(2, 10) }

// One fill is an anecdote. Repeats are what turn a stack into a measurement, so the loop lives
// next to the run button rather than in a script somebody has to write first.
async function sweep() {
  const n = Math.max(1, Math.min(200, parseInt($('#repeats').value, 10) || 1))
  const pool = Math.max(1, S.models.length)
  if (n * pool > 20) {
    const worst = S.cap * n * pool
    if (!confirm(`${n} runs × ${pool} model${pool > 1 ? 's' : ''} = ${n * pool} generations, ` +
                 `up to ${worst.toLocaleString()} completion tokens. That is real money. Go ahead?`)) return
  }
  S.sweep = {n, i: 0, stop: false}
  for (let i = 0; i < n && !S.sweep.stop; i++) {
    S.sweep.i = i + 1
    // A sweep that keeps going after a failure spends the rest of the budget on the same error.
    if (!await go()) break
  }
  S.sweep = null
  $('#stop').textContent = '■ Stop'
  await loadHistory()
}

async function go() {
  const id = newId()
  const models = S.models.length ? S.models : [null]
  const multi = models.length > 1
  S.run = {id, models: Object.fromEntries(models.map(m => [key(m), {vars: {}, done: null}])), order: []}
  S.result = null
  let ok = false
  $('#run').hidden = true; $('#stop').hidden = false
  $('#stop').textContent = S.sweep ? `■ Stop (${S.sweep.i}/${S.sweep.n})` : '■ Stop'

  // One model streams into the document; several stream into the grid, because four documents
  // side by side is four walls of text and the question is always "which variable differs".
  showPane(multi ? 'compare' : 'document')
  const doc = $('#pane-document'); doc.replaceChildren()
  const live = el('pre', 'doc live'); doc.append(live)
  let span = null, current = null
  if (multi) renderCompare()

  const body = {
    run_id: id, scripts: S.stack, inputs: S.inputs, atomic: $('#atomic').checked,
    models: S.models, extra: pinned(), stack_name: S.stackName,
  }

  try {
    await stream(`/w/${S.ws}/run`, body, (ev, d) => {
      const slot = S.run.models[key(d.model)]
      if (ev === 'start') {
        live.append(el('span', 'sys', `▶ ${d.scripts.join(' → ')}  ·  ${d.declarations} declarations\n`))
      } else if (ev === 'token') {
        if (multi) return
        if (d.variable !== current) {
          current = d.variable
          live.append(el('span', 'sys', `\n· ${d.variable}\n`))
          span = el('span', 'gen'); live.append(span)
        }
        span.append(document.createTextNode(d.text || ''))
        live.scrollTop = live.scrollHeight
      } else if (ev === 'var') {
        if (slot) slot.vars[d.name] = d
        if (!S.run.order.includes(d.name)) S.run.order.push(d.name)
        if (multi) renderCompare()
      } else if (ev === 'script_end') {
        if (!multi) { live.append(el('span', 'sys', `\n■ ${d.task}\n`)); current = null }
      } else if (ev === 'error') {
        showErrors(d.errors || [], d.failed_variable)
        showPane('errors')
      } else if (ev === 'done') {
        if (slot) slot.done = d
        if (d.ok) ok = true
        bank(d.usage)
        if (multi) renderCompare()
        else { S.result = d; d.ok ? settle(d) : (showErrors(d.errors || [], d.failed_variable), showPane('errors')) }
      } else if (ev === 'finished' && multi) {
        renderCompare()
      }
    })
  } catch (e) {
    showErrors([{message: String(e.message || e)}])
    showPane('errors')
  } finally {
    $('#run').hidden = false; $('#stop').hidden = true
  }
  if (!S.sweep && visible('history')) await loadHistory()
  return ok
}

// Six decimal places round a real charge to $0.000000, which reads as free. Below that it says
// so instead.
const dollars = c => c >= 0.01 ? '$' + c.toFixed(4)
  : c >= 1e-6 ? '$' + c.toFixed(6)
  : c > 0 ? '<$0.000001' : '$0'

// What this session has actually spent. A static budget ceiling told you nothing you did not
// already know; the running total is the number you check before starting a sweep.
function renderSpend() {
  const p = $('#status')
  if (S.noKey) {
    p.textContent = 'no PROWL_VENDOR_API_KEY — runs will fail'
    p.classList.add('bad')
    return
  }
  const s = S.spent
  p.classList.remove('bad')
  const tok = s.prompt + s.completion
  if (!tok) { p.textContent = 'nothing spent yet'; return }
  // Not every spend is a run: an embedding call costs money and is not one, so the runs clause
  // drops out rather than reading "0 runs".
  const bits = []
  if (s.runs) bits.push(`${s.runs} run${s.runs > 1 ? 's' : ''}`)
  bits.push(`${tok.toLocaleString()} tok`, dollars(s.cost))
  if (s.elapsed) bits.push(`${s.elapsed.toFixed(1)}s`)
  p.textContent = bits.join(' · ')
  p.title = `${s.prompt.toLocaleString()} prompt + ${s.completion.toLocaleString()} completion\n` +
            `budget per run ${S.budget.toLocaleString()} completion tokens`
}

// `runs` counts runs. An embedding call spends money without being one, so it banks its cost
// with a count of zero rather than inflating the run tally.
function bank(usage, runs = 1) {
  if (!usage) return
  const s = S.spent
  s.prompt += usage.prompt_tokens || 0
  s.completion += usage.completion_tokens || 0
  s.cost += usage.cost || 0
  s.elapsed += usage.elapsed || 0
  s.runs += runs
  renderSpend()
}

const key = m => m || '(default)'
const short = m => (m || '(default)').split('/').pop()

// Variable x model. The comparison people actually want is per field: a prompt does not rot
// uniformly on a newer model, one named variable stops behaving.
function renderCompare() {
  const p = $('#pane-compare'); p.replaceChildren()
  if (!S.run) { p.append(el('div', 'hint', 'Add two or more models to compare them variable by variable.')); return }
  const models = Object.keys(S.run.models)
  const t = el('table', 'grid')
  const hdr = el('tr')
  hdr.append(el('th', null, ''))
  for (const m of models) {
    const th = el('th')
    th.append(el('div', 'm', short(m)))
    const slot = S.run.models[m]
    if (slot.done) {
      const u = slot.done.usage
      th.append(el('div', 'sub', slot.done.ok
        ? `$${(u ? u.cost : 0).toFixed(6)} · ${(u ? u.elapsed : 0).toFixed(1)}s`
        : `failed${slot.done.failed_variable ? ' on ' + slot.done.failed_variable : ''}`))
    } else {
      th.append(el('div', 'sub run', 'running…'))
    }
    hdr.append(th)
  }
  t.append(hdr)

  for (const name of S.run.order) {
    const tr = el('tr')
    tr.append(el('td', 'name', name))
    for (const m of models) {
      const slot = S.run.models[m]
      const v = slot.vars[name]
      const td = el('td', 'cell')
      if (!v) {
        const failed = slot.done && slot.done.failed_variable === name
        td.append(el('span', failed ? 'bad' : 'muted', failed ? 'raised here' : '—'))
      } else {
        td.append(el('span', 'val', (v.value || '').slice(0, 220)))
        const f = el('div', 'flags')
        if (v.type) f.append(el('span', 'tag', v.type))
        if (v.truncated) f.append(el('span', 'tag bad', 'truncated'))
        if (v.usage) f.append(el('span', 'tag', `${v.usage.completion_tokens} tok`))
        td.append(f)
      }
      tr.append(td)
    }
    t.append(tr)
  }
  p.append(t)

  // Where they disagree, which is the only reason to look at this table. Compare only the
  // models that produced a value: counting a missing cell as "different" made every row
  // differ, which is a table that says nothing.
  const differing = S.run.order.filter(n => {
    const vals = models.map(m => (S.run.models[m].vars[n] || {}).value)
                       .filter(v => v !== undefined && v !== null)
    return new Set(vals).size > 1
  })
  if (models.length > 1)
    p.append(el('div', 'hint', differing.length
      ? `${differing.length} of ${S.run.order.length} variables differ: ${differing.join(', ')}`
      : `all ${S.run.order.length} variables identical across ${models.length} models`))
}

async function halt() {
  if (S.sweep) S.sweep.stop = true   // stop means the sweep, not just the repeat now in flight
  if (S.run) await api(`/run/${S.run.id}/stop`, {})
}

// ------------------------------------------------------------- the results

// Every generated value carries the offsets where it landed in the finished document, so this
// is the run rendered as what it actually is: one token sequence whose spans happen to be named.
function settle(d) {
  const spans = []
  for (const [name, v] of Object.entries(d.variables || {}))
    for (const h of (v.history || [v]))
      if (h.span) spans.push({name, ...h})
  spans.sort((a, b) => a.span[0] - b.span[0])

  const doc = $('#pane-document'); doc.replaceChildren()
  const pre = el('pre', 'doc')
  let at = 0
  for (const s of spans) {
    if (s.span[0] < at) continue                       // overlap: keep the first
    pre.append(document.createTextNode(d.completion.slice(at, s.span[0])))
    const mark = el('span', 'gen' + (s.truncated ? ' cut' : ''), d.completion.slice(s.span[0], s.span[1]))
    mark.title = `${s.name}${s.type ? ':' + s.type : ''}` +
      (s.usage ? ` · ${s.usage.completion_tokens} tok` : '') + (s.truncated ? ' · truncated' : '')
    mark.onclick = () => showPane('variables')
    pre.append(mark)
    at = s.span[1]
  }
  pre.append(document.createTextNode(d.completion.slice(at)))
  doc.append(pre)
  if (d.stopped) doc.append(el('div', 'hint bad', 'stopped early — the rest of the stack did not run'))

  renderVariables(d)
  renderRaw(d)
  renderUsage(d.usage)
  showErrors([])          // a clean run clears the marker a previous failure left on the tab
}

// ------------------------------------------------------------- run history

const sig = () => S.stack.join('/')
const visible = name => !$('#pane-' + name).hidden
const clock = at => new Date(at * 1000).toLocaleTimeString()
const num = x => Math.abs(x) >= 100 || Number.isInteger(x) ? x.toLocaleString(undefined, {maximumFractionDigits: 2}) : x.toPrecision(3)

async function loadHistory() {
  if (!S.ws || !S.stack.length) { S.hist = null; renderHistory(); return }
  S.histSig = sig()
  try { S.hist = await api(`/w/${S.ws}/history?stack=${encodeURIComponent(sig())}&limit=400`) }
  catch (e) { S.hist = null }
  renderHistory()
  if (S.result) renderVariables(S.result)   // the past-values affordance lives on those rows
}

function samples(variable, model) {
  if (!S.hist) return []
  return S.hist.records.filter(r => r.variable === variable && (!model || r.model === model))
}

function values(box, rows) {
  for (const r of [...rows].reverse()) {
    const line = el('div', 'vrow')
    line.append(el('span', 'when', clock(r.at)))
    line.append(el('span', 'v', r.value === null || r.value === undefined ? '—' : String(r.value)))
    if (r.truncated) line.append(el('span', 'tag bad', 'cut'))
    const ins = Object.entries(r.inputs || {}).map(([k, v]) => `${k}: ${v}`)
    line.title = [r.model, r.provider, ...ins].filter(Boolean).join('\n')
    box.append(line)
  }
}

// Counting distinct strings answers "how many names". It cannot answer "how many answers", since
// the same answer written twice is two strings — so the values go through an embedding model and
// come back grouped. Every count is shown with the threshold that produced it; without it a
// cluster count is a number with a knob hidden behind it.
function renderGroups(box, d) {
  const out = el('div', 'groups')
  if (d.error || d.note) {
    out.append(el('div', d.error ? 'bad' : 'hint', d.error || d.note))
    box.append(out); return
  }
  bank({prompt_tokens: d.usage.prompt_tokens, cost: d.usage.cost}, 0)
  const head = el('div', 'hhead')
  head.append(el('span', null, `${d.clusters} cluster${d.clusters === 1 ? '' : 's'} of ${d.embedded} distinct`),
              el('span', null, `spread ${d.spread.toFixed(3)}`),
              el('span', 'dim', `${d.model} · cosine ≥ ${d.threshold}`))
  out.append(head)
  for (const g of d.groups) {
    const row = el('div', 'vrow')
    row.append(el('span', 'when', `${g.length}×`), el('span', 'v', g.join('   ·   ')))
    out.append(row)
  }
  box.append(out)
}

// The same declaration filled twenty times. Which is where the questions live that a single run
// cannot answer: how far does this number move, and how many names does this model really have.
function renderHistory() {
  const p = $('#pane-history'); p.replaceChildren()
  const h = S.hist
  if (!h || !h.records.length) {
    p.append(el('div', 'hint', S.stack.length
      ? 'Nothing recorded for this order yet. Set the × next to Run and every value is kept here.'
      : 'Compose a stack to see what it has produced before.'))
    return
  }
  const runs = new Set(h.records.map(r => r.run)).size
  const head = el('div', 'hhead')
  head.append(el('span', null, `${runs} run${runs > 1 ? 's' : ''}`),
              el('span', null, h.shown < h.total ? `last ${h.shown} of ${h.total} samples` : `${h.total} samples`),
              el('span', 'dim', sig()))
  const wipe = ghost('clear', `delete every recorded sample for ${sig()}`, async () => {
    if (!confirm(`Delete all ${h.total} recorded samples for ${sig()}?`)) return
    await api(`/w/${S.ws}/history?stack=${encodeURIComponent(sig())}`, {}, 'DELETE')
    await loadHistory()
  })
  wipe.classList.add('right')
  head.append(wipe)
  p.append(head)

  const t = el('table')
  const hdr = el('tr')
  for (const c of ['variable', 'model', 'n', 'distinct', 'spread', 'most common']) hdr.append(el('th', null, c))
  t.append(hdr)

  let last = null
  for (const s of h.summary) {
    const st = s.stats
    const tr = el('tr', 'clickable')
    tr.append(el('td', 'name', s.variable === last ? '' : s.variable))
    last = s.variable
    const m = el('td', 'val'); m.append(el('span', null, short(s.model)))
    if (s.temp !== null && s.temp !== undefined) m.append(el('span', 'tag', 'T' + s.temp))
    tr.append(m)
    tr.append(el('td', 'num', String(st.n)))

    const uq = el('td', 'num', `${st.unique_folded}`)
    uq.title = st.unique === st.unique_folded ? 'distinct values, ignoring case and spacing'
      : `${st.unique_folded} ignoring case and spacing, ${st.unique} exactly`
    tr.append(uq)

    const sp = el('td', 'num')
    if (st.entropy !== undefined) {
      const e = el('div', st.entropy < 0.6 ? 'bad' : null, 'H ' + st.entropy.toFixed(2))
      e.title = 'normalised entropy: 1.00 when every run answered differently, 0.00 when they all agreed'
      sp.append(e)
    }
    if (st.numeric && st.numeric.sd !== null)
      sp.append(el('div', 'dim', `${num(st.numeric.mean)} ± ${num(st.numeric.sd)}`))
    else if (st.numeric)
      sp.append(el('div', 'dim', num(st.numeric.mean)))
    tr.append(sp)

    const top = el('td', 'val')
    const [v, c] = st.top[0]
    top.append(el('span', null, v || '(empty)'))
    if (c > 1) top.append(el('span', 'tag', `${c}× ${(st.mode_share * 100).toFixed(0)}%`))
    if (s.truncated) top.append(el('span', 'tag bad', `${s.truncated} cut`))
    tr.append(top)
    t.append(tr)

    const det = el('tr', 'detail'); det.hidden = true
    const cell = el('td', 'vals'); cell.colSpan = 6
    const bar = el('div', 'vbar')
    const embed = ghost('≈ group by meaning', 'embed the distinct values and cluster them — a fraction of a cent', async () => {
      embed.disabled = true; embed.textContent = 'embedding…'
      bar.querySelectorAll('.groups').forEach(n => n.remove())
      try { renderGroups(bar, await api(`/w/${S.ws}/embed`, {stack: sig(), variable: s.variable, model: s.model || null})) }
      catch (e) { renderGroups(bar, {error: e.message}) }
      embed.disabled = false; embed.textContent = '≈ group by meaning'
    })
    bar.append(embed)
    cell.append(bar)
    values(cell, samples(s.variable, s.model))
    det.append(cell)
    tr.onclick = () => { det.hidden = !det.hidden }
    tr.title = 'every recorded value for this variable'
    t.append(det)
  }
  p.append(t)
}

function renderVariables(d) {
  const p = $('#pane-variables'); p.replaceChildren()
  const t = el('table')
  const hdr = el('tr')
  // `ctx` is the prompt each declaration was generated against. In one growing document it climbs
  // with every value before it, which is the cost of conditioning stated plainly.
  for (const h of ['name', 'type', 'value', 'ctx', 'tok/max', 'temp', 'flags']) hdr.append(el('th', null, h))
  t.append(hdr)
  for (const [name, v] of Object.entries(d.variables || {})) {
    const declared = v.arg && v.arg[0] != null      // an input has no (max_tokens, temperature)
    const tr = el('tr')
    tr.append(el('td', 'name', name))
    tr.append(el('td', 'type', v.type || ''))
    const val = el('td', 'val'); val.append(el('span', null, (v.value || '').slice(0, 160)))
    // What this variable is now, over what it has been. A value only means something next to
    // the other values the same declaration produced.
    const past = samples(name, S.models.length === 1 ? S.models[0] : null)
    if (past.length > 1) {
      const list = el('div', 'past'); list.hidden = true
      values(list, past)
      const more = el('button', 'more', `▾ ${past.length}`)
      more.type = 'button'
      more.title = `${past.length} recorded values for ${name} — click to show`
      more.onclick = () => { list.hidden = !list.hidden }
      val.append(more, list)
    }
    tr.append(val)
    tr.append(el('td', 'num', declared && v.usage ? v.usage.prompt_tokens.toLocaleString() : ''))
    tr.append(el('td', 'num', declared ? `${v.usage ? v.usage.completion_tokens : 0}/${v.arg[0]}` : ''))
    tr.append(el('td', 'num', declared ? String(v.arg[1]) : ''))
    const flags = el('td', 'flags')
    if (!declared) flags.append(el('span', 'tag', 'input'))
    if (v.truncated) flags.append(el('span', 'tag bad', 'truncated'))
    if (v.list) flags.append(el('span', 'tag', `list ${v.list.length}`))
    if (v.data && v.data.candidates) flags.append(el('span', 'tag', `n=${v.data.candidates.length}`))
    if (v.history && v.history.length > 1) flags.append(el('span', 'tag', `revised ${v.history.length - 1}×`))
    tr.append(flags)
    t.append(tr)
  }
  p.append(t)
  if (d.output && d.output.length) {
    p.append(el('h3', null, 'prout projection'))
    for (const o of d.output) p.append(el('pre', 'doc', o.output))
  }
}

function showErrors(errors, failed) {
  const p = $('#pane-errors'); p.replaceChildren()
  errors = errors || []
  document.querySelector('[data-pane="errors"]').classList.toggle('has', errors.length > 0)
  if (!errors.length) { p.append(el('div', 'hint', 'No errors.')); return }
  if (failed) p.append(el('div', 'hint bad', `generation failed on \`${failed}\``))
  for (const e of errors) {
    const b = el('div', 'err')
    if (e.code) b.append(el('code', null, String(e.code)))
    b.append(el('span', null, ' ' + (e.message || '')))
    if (e.code && S.lang && S.lang.codes[e.code]) b.append(el('div', 'muted', S.lang.codes[e.code]))
    p.append(b)
  }
}

function renderRaw(d) {
  const p = $('#pane-raw'); p.replaceChildren()
  p.append(el('pre', 'doc', JSON.stringify(d, null, 1)))
}

function renderUsage(u) {
  const box = $('#usage'); box.replaceChildren()
  if (!u) return
  box.append(el('span', null, `${u.prompt_tokens.toLocaleString()} prompt`))
  box.append(el('span', null, `${u.completion_tokens.toLocaleString()} completion`))
  box.append(el('span', u.estimated ? 'bad' : null,
    u.estimated ? 'cost unknown (estimated tokens)' : `$${(u.cost || 0).toFixed(6)}`))
  box.append(el('span', null, `${(u.elapsed || 0).toFixed(1)}s`))
}

function showPane(name) {
  for (const b of document.querySelectorAll('.tabbar.sub button')) b.classList.toggle('on', b.dataset.pane === name)
  for (const p of document.querySelectorAll('.pane')) p.hidden = p.id !== 'pane-' + name
}

// ---------------------------------------------------------------- wiring

$('#ws-pick').onchange = async e => {
  S.ws = e.target.value; S.stack = []; S.open = null; S.stackName = null; S.saved = null
  S.hist = null; S.histSig = null
  restoreInputs(); await loadBrowser()
}
$('#stack-new').onclick = newStack
$('#ws-new').onclick = async () => {
  const name = prompt('workspace name'); if (!name) return
  try { await api('/workspaces', {name}); const d = await api('/workspaces')
    $('#ws-pick').replaceChildren(...d.workspaces.map(w => el('option', null, w)))
    $('#ws-pick').value = name; S.ws = name; S.stack = []; await loadBrowser()
  } catch (e) { alert(e.message) }
}
$('#script-new').onclick = async () => {
  if (!S.ws) return alert('Make a workspace first.')
  const name = askName('new script name'); if (!name) return
  try {
    await api(`/w/${S.ws}/script/${name}`, {prowl: `## ${name}\nWrite the instruction here, ending in a colon:\n`}, 'PUT')
    await loadBrowser()
    if (!S.stack.includes(name)) S.stack.push(name)
    await openScript(name)
    $('#editor').focus()
  } catch (e) { alert(e.message) }
}
$('#editor').oninput = () => { const n = $('#editor').dataset.name; if (n) { S.dirty.add(n); renderStack() } paint() }
$('#editor').onscroll = syncScroll
$('#editor').onblur = save
$('#editor').onkeyup = renderCaret
$('#editor').onclick = renderCaret
$('#editor').onkeydown = e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save() }
  if (e.key === 'Tab') {   // a textarea that eats Tab is a textarea nobody can indent in
    e.preventDefault()
    const ta = e.target, a = ta.selectionStart, b = ta.selectionEnd
    ta.value = ta.value.slice(0, a) + '  ' + ta.value.slice(b)
    ta.selectionStart = ta.selectionEnd = a + 2
    S.dirty.add(ta.dataset.name); renderStack(); paint()
  }
}
// ---------------------------------------------------------------- model finder

const FAV = 'prowl.studio.favourites'
const favs = () => { try { return JSON.parse(localStorage.getItem(FAV) || '[]') } catch (e) { return [] } }
const money = p => p === 0 ? 'free' : '$' + (p * 1e6).toFixed(2) + '/M'

let pickTimer = null, pickOnlyFree = false
function findModels() {
  clearTimeout(pickTimer)
  pickTimer = setTimeout(async () => {
    const q = $('#model').value.trim()
    const d = await api(`/models?q=${encodeURIComponent(q)}&free=${pickOnlyFree ? 1 : 0}&limit=40`)
    renderPicker(d, q)
  }, 180)
}

function renderPicker(d, q) {
  const p = $('#picker'); p.replaceChildren(); p.hidden = false
  const head = el('div', 'phead')
  head.append(el('span', null, `${d.matched} of ${d.total} support stop`))
  const f = el('button', 'chip-toggle' + (pickOnlyFree ? ' on' : ''), 'free only')
  f.type = 'button'
  f.onclick = () => { pickOnlyFree = !pickOnlyFree; findModels() }
  head.append(f)
  p.append(head)

  const F = favs()
  const rows = q ? d.models : [...d.models].sort((a, b) => (F.includes(b.id) - F.includes(a.id)))
  if (!rows.length) p.append(el('div', 'muted', 'nothing matches'))
  for (const m of rows.slice(0, 40)) {
    const row = el('div', 'prow' + (S.models.includes(m.id) ? ' in' : ''))
    const star = el('button', 'star' + (F.includes(m.id) ? ' on' : ''), F.includes(m.id) ? '★' : '☆')
    star.type = 'button'; star.title = 'favourite'
    star.onclick = e => {
      e.stopPropagation()
      const n = F.includes(m.id) ? F.filter(x => x !== m.id) : [...F, m.id]
      localStorage.setItem(FAV, JSON.stringify(n)); findModels()
    }
    row.append(star, el('span', 'pid', m.id))
    row.append(el('span', 'ppr', money(m.completion_price)))
    row.append(el('span', 'pctx', m.context ? (m.context / 1000).toFixed(0) + 'k' : ''))
    const flags = el('span', 'pflags')
    if (m.structured) flags.append(el('span', 'tag', 'json'))
    if (m.seed) flags.append(el('span', 'tag', 'seed'))
    if (m.logprobs) flags.append(el('span', 'tag', 'logp'))
    row.append(flags)
    row.title = `${m.name}\ncontext ${m.context}\nprompt ${money(m.prompt_price)} · completion ${money(m.completion_price)}`
    row.onclick = () => { addModel(m.id); $('#model').value = ''; findModels() }
    p.append(row)
  }
}

$('#model').oninput = findModels
$('#model').onfocus = findModels
$('#model').onkeydown = e => {
  if (e.key === 'Enter') { e.preventDefault(); addModel(e.target.value.trim()); e.target.value = ''; findModels() }
  if (e.key === 'Escape') $('#picker').hidden = true
}
addEventListener('click', e => { if (!e.target.closest('.finder')) $('#picker').hidden = true })
$('#provider').value = localStorage.getItem('prowl.studio.provider') || ''
$('#provider').oninput = e => localStorage.setItem('prowl.studio.provider', e.target.value.trim())
$('#atomic').onchange = renderStack
$('#provider').addEventListener('input', renderStack)   // the pin is part of the stack, so it can drift it
$('#run').onclick = () => save().then(sweep)
$('#stop').onclick = halt
$('#repeats').onchange = () => renderModels()
for (const b of document.querySelectorAll('.tabbar.sub button')) b.onclick = () => {
  showPane(b.dataset.pane)
  if (b.dataset.pane === 'history' && (!S.hist || S.histSig !== sig())) loadHistory()
}

// Ctrl/Cmd+Enter runs from anywhere, including from inside an input you have just filled in.
addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    if (!$('#stop').hidden) halt()
    else if (!$('#run').disabled) save().then(sweep)
  }
})

boot().catch(e => { $('#status').textContent = 'error: ' + e.message; $('#status').classList.add('bad') })
