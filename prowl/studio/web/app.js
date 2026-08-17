// The stack is the unit of work here, not the script. A script checked on its own reports
// variables "referenced before anything declares them" that an earlier script in the stack
// declares perfectly well, so every check below runs against the whole ordered stack.

const $ = s => document.querySelector(s)
const el = (t, c, x) => { const n = document.createElement(t); if (c) n.className = c; if (x !== undefined) n.textContent = x; return n }

const S = {
  lang: null, ws: null, browser: [], stack: [], open: null,
  dirty: new Set(), inputs: {}, needs: [], run: null, result: null,
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
  if (h.model) $('#model').value = h.model
  $('#status').textContent = h.has_key ? `budget ${h.budget.toLocaleString()} tok`
                                       : 'no PROWL_VENDOR_API_KEY — runs will fail'
  $('#status').classList.toggle('bad', !h.has_key)
  $('#status').title = `${h.endpoint || 'no endpoint'} · ${h.root}`

  const pick = $('#ws-pick')
  pick.replaceChildren(...ws.workspaces.map(w => el('option', null, w)))
  if (ws.workspaces.length) { S.ws = ws.workspaces[0]; pick.value = S.ws; await loadBrowser() }
}

async function loadBrowser() {
  const d = await api(`/w/${S.ws}/scripts`)
  S.browser = d.scripts
  const ul = $('#browser'); ul.replaceChildren()
  for (const s of d.scripts) {
    const li = el('li', 'row' + (S.stack.includes(s.name) ? ' in' : ''))
    li.append(el('span', 'name', s.name))
    if (s.folder && !s.folder.endsWith(S.ws + '/') && !s.folder.endsWith(S.ws + '\\'))
      li.append(el('span', 'sub', s.folder.replace(/[\\/]$/, '').split(/[\\/]/).pop()))
    if (s.errors.length) li.append(el('span', 'tag bad', String(s.errors.length)))
    if (s.has_prout) li.append(el('span', 'tag', 'prout'))
    li.onclick = () => { if (!S.stack.includes(s.name)) S.stack.push(s.name); openScript(s.name) }
    ul.append(li)
  }
  for (const [name, folders] of Object.entries(d.collisions || {}))
    ul.append(el('li', 'muted bad', `name collision: ${name} in ${folders.join(', ')}`))
  renderStack()
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
    chip.append(el('span', 'ord', String(i + 1)), el('span', 'name', name))
    const x = el('button', 'x', '×')
    x.onclick = e => { e.stopPropagation(); S.stack.splice(i, 1); if (S.open === name) S.open = S.stack[0] || null; renderStack(); openScript(S.open) }
    chip.append(x)
    chip.onclick = () => openScript(name)
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
  const atomic = $('#atomic').checked
  box.append(el('span', 'mode', atomic ? 'each script runs alone' : 'one growing prompt'))
  refresh()
}

async function openScript(name) {
  S.open = name
  renderStack()
  const ed = $('#editor')
  if (!name) { ed.value = ''; return }
  const d = await api(`/w/${S.ws}/script/${name}`)
  ed.value = d.prowl || ''
  ed.dataset.name = name
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
function refresh() { clearTimeout(checkTimer); checkTimer = setTimeout(check, 150) }

async function check() {
  const f = $('#forecast')
  if (!S.ws || !S.stack.length) { f.replaceChildren(); $('#inputs').replaceChildren(); return }
  const v = await api(`/w/${S.ws}/validate`, {scripts: S.stack, inputs: S.inputs})
  S.needs = v.inputs_required
  renderInputs()

  f.replaceChildren()
  f.append(el('span', null, `${S.stack.length} scripts`))
  f.append(el('span', null, `${v.declarations} declarations`))
  f.append(el('span', v.over_budget ? 'bad' : null, `≤ ${v.max_completion_tokens.toLocaleString()} completion tokens`))
  f.append(el('span', v.ok ? 'ok' : 'bad', v.ok ? 'valid' : `${v.errors.length} problem${v.errors.length > 1 ? 's' : ''}`))
  showErrors(v.errors, 'stack')
  $('#run').disabled = !v.ok || v.over_budget
}

function renderInputs() {
  const box = $('#inputs'); box.replaceChildren()
  if (!S.needs.length) return
  box.append(el('span', 'label', 'inputs'))
  for (const name of S.needs) {
    const wrap = el('label', 'input')
    wrap.append(el('span', null, name))
    const inp = el('input')
    inp.value = S.inputs[name] || ''
    inp.oninput = () => { S.inputs[name] = inp.value; refresh() }
    wrap.append(inp)
    box.append(wrap)
  }
}

// ---------------------------------------------------------------- the run

function newId() { return 'r' + Math.random().toString(36).slice(2, 10) }

async function go() {
  const id = newId()
  S.run = {id, order: [...S.stack], at: 0, live: [], usage: null}
  S.result = null
  $('#run').hidden = true; $('#stop').hidden = false
  showPane('document')
  const doc = $('#pane-document'); doc.replaceChildren()
  const live = el('pre', 'doc live'); doc.append(live)
  let span = null, current = null

  const body = {
    run_id: id, scripts: S.stack, inputs: S.inputs, atomic: $('#atomic').checked,
    model: $('#model').value.trim() || null,
  }

  try {
    await stream(`/w/${S.ws}/run`, body, (ev, d) => {
      if (ev === 'start') {
        live.append(el('span', 'sys', `▶ ${d.scripts.join(' → ')}  ·  ${d.declarations} declarations\n`))
      } else if (ev === 'token') {
        if (d.variable !== current) {
          current = d.variable
          live.append(el('span', 'sys', `\n· ${d.variable}\n`))
          span = el('span', 'gen'); live.append(span)
        }
        span.append(document.createTextNode(d.text || ''))
        live.scrollTop = live.scrollHeight
      } else if (ev === 'var') {
        S.run.live.push(d)
      } else if (ev === 'script_end') {
        S.run.at++
        live.append(el('span', 'sys', `\n■ ${d.task}\n`))
        current = null
      } else if (ev === 'error') {
        showErrors(d.errors || [], 'run', d.failed_variable)
        showPane('errors')
      } else if (ev === 'done') {
        S.result = d
        settle(d)
      }
    })
  } catch (e) {
    showErrors([{message: String(e.message || e)}], 'run')
    showPane('errors')
  } finally {
    $('#run').hidden = false; $('#stop').hidden = true; S.run = null
  }
}

async function halt() {
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
}

function renderVariables(d) {
  const p = $('#pane-variables'); p.replaceChildren()
  const t = el('table')
  const hdr = el('tr')
  for (const h of ['name', 'type', 'value', 'tok/max', 'temp', 'flags']) hdr.append(el('th', null, h))
  t.append(hdr)
  for (const [name, v] of Object.entries(d.variables || {})) {
    const declared = v.arg && v.arg[0] != null      // an input has no (max_tokens, temperature)
    const tr = el('tr')
    tr.append(el('td', 'name', name))
    tr.append(el('td', 'type', v.type || ''))
    const val = el('td', 'val'); val.append(el('span', null, (v.value || '').slice(0, 160)))
    tr.append(val)
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

function showErrors(errors, where, failed) {
  const p = $('#pane-errors'); p.replaceChildren()
  if (!errors || !errors.length) { p.append(el('div', 'hint', 'No errors.')); return }
  if (failed) p.append(el('div', 'hint bad', `generation failed on \`${failed}\``))
  for (const e of errors) {
    const b = el('div', 'err')
    if (e.code) b.append(el('code', null, String(e.code)))
    b.append(el('span', null, ' ' + (e.message || '')))
    if (e.code && S.lang && S.lang.codes[e.code]) b.append(el('div', 'muted', S.lang.codes[e.code]))
    p.append(b)
  }
  const tab = document.querySelector('[data-pane="errors"]')
  tab.classList.toggle('has', where === 'stack' ? errors.length > 0 : true)
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

$('#ws-pick').onchange = async e => { S.ws = e.target.value; S.stack = []; S.open = null; await loadBrowser() }
$('#ws-new').onclick = async () => {
  const name = prompt('workspace name'); if (!name) return
  try { await api('/workspaces', {name}); const d = await api('/workspaces')
    $('#ws-pick').replaceChildren(...d.workspaces.map(w => el('option', null, w)))
    $('#ws-pick').value = name; S.ws = name; S.stack = []; await loadBrowser()
  } catch (e) { alert(e.message) }
}
$('#script-new').onclick = async () => {
  const name = prompt('script name'); if (!name) return
  try {
    await api(`/w/${S.ws}/script/${name}`, {prowl: `## ${name}\n`}, 'PUT')
    await loadBrowser(); S.stack.push(name); openScript(name)
  } catch (e) { alert(e.message) }
}
$('#editor').oninput = () => { const n = $('#editor').dataset.name; if (n) { S.dirty.add(n); renderStack() } }
$('#editor').onblur = save
$('#editor').onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save() } }
$('#atomic').onchange = renderStack
$('#run').onclick = () => save().then(go)
$('#stop').onclick = halt
for (const b of document.querySelectorAll('.tabbar.sub button')) b.onclick = () => showPane(b.dataset.pane)

boot().catch(e => { $('#status').textContent = 'error: ' + e.message; $('#status').classList.add('bad') })
