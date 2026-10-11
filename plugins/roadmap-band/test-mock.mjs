// Logic harness for register.js with a mocked mods API. Proves state logic only; says nothing about host rendering.
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const src = process.argv[2]
let inst = 0
const base = pathToFileURL(path.resolve(src)).href
// a fresh module instance per host: the mod keeps its state in module variables, as it does when reloaded
async function makeHost(over = {}) {
  const mod = await import(base + '?i=' + inst++)
  const hooks = {}
  const timers = []
  const store = over.store || new Map()
  const env = { now: 1_800_000_000_000, cwd: 'F:/repo', sid: 's1', files: { 'WORKLOG.md': 1_799_999_000_000 }, messages: [], usageFail: false, invalid: 0 }
  Object.assign(env, over.env || {})
  const $ = {
    fs: {
      exists: async (p) => Object.keys(env.files).some((k) => p.endsWith(k)),
      stat: async (p) => ({ mtimeMs: env.files[Object.keys(env.files).find((k) => p.endsWith(k))] }),
      list: async () => [],
    },
    clock: { now: async () => env.now, every: (ms, fn) => { const t = { ms, fn, cancelled: false, cancel() { this.cancelled = true } }; timers.push(t); return t } },
    store: { get: async (k) => store.get(k), set: async (k, v) => { store.set(k, v) } },
    env: { get: async () => '1' },
    session: {
      cwd: async () => env.cwd, id: async () => env.sid, messages: async () => env.messages,
      usage: async () => { env.usageCalls = (env.usageCalls || 0) + 1; if (env.block) { const g = env.block; env.block = null; await g } if (env.usageFail) throw new Error('x'); return { context: { percent: 41.2 }, rateLimits: [{ kind: 'five_hour', percentUsed: 23.4, resetsAt: env.now / 1000 + 7800 }, { kind: 'weekly', percentUsed: 40, resetsAt: 'bogus' }] } },
    },
    ui: { invalidate: () => { env.invalidations = (env.invalidations || 0) + 1 }, resolve: () => ({ Box: (p) => ({ ...p, t: 'Box' }), Text: (p) => ({ ...p, t: 'Text' }) }) },
  }
  const on = (ev, a, b) => { (hooks[ev] = hooks[ev] || []).push(b || a) }
  mod.register(on)
  const call = async (ev, e, gen = false) => {
    let out
    for (const h of hooks[ev] || []) {
      if (gen) { const it = h($, e, async function* (x) { return x }); let r = await it.next(); while (!r.done) r = await it.next(); out = r.value }
      else out = await h($, e, async (x) => x)
    }
    return out
  }
  const flat = (n) => (n == null ? '' : typeof n === 'string' ? n : Array.isArray(n) ? n.map(flat).join('') : flat(n.children))
  const render = async (working = false) => {
    const tree = await call('ui.render', { props: { isWorking: working } })
    return flat(tree.children.slice(0, 2))
  }
  return { env, call, render, timers, store }
}

const results = []
const ok = (name, cond, extra = '') => { results.push([cond ? 'PASS' : 'FAIL', name, extra]) }
const LINE = 'Roadmap: Pilot [active] · acceptance: 3/5 verified (pilot) · next: owner go (you) · pause: safe'

{ // 1 empty start, usage failing
  const h = await makeHost({ env: { usageFail: true } })
  await h.call('session.start', {})
  const r = await h.render()
  ok('1 empty session says NO ROADMAP LINE', r.includes('NO ROADMAP LINE'), r)
  ok('1 usage failure -> "usage unavailable", no 0%', r.includes('usage unavailable') && !r.includes(' 0%'), r)
  ok('1 anchor age shown', r.includes('WORKLOG.md'), r)
}
{ // 2-5 capture, retain, malformed, source change, working, cwd change
  const h = await makeHost()
  await h.call('session.start', {})
  await h.call('turn.complete', { answer: `Done\n${LINE}` })
  let r = await h.render()
  ok('2 good line -> CHECKPOINT REPORTED', r.includes('CHECKPOINT REPORTED') && r.includes('acceptance 3/5 verified (pilot)'), r)
  ok('2 usage shown with kinds mapped', r.includes('ctx 41%') && r.includes('5h 23%') && r.includes('wk 40%'), r)
  await h.call('turn.complete', { answer: 'plain Q&A reply' })
  r = await h.render()
  ok('3 reply without line keeps last good + counts replies', r.includes('1 reply since') && r.includes('Pilot [active]'), r)
  await h.call('turn.complete', { answer: 'Roadmap: garbage with no status' })
  r = await h.render()
  ok('4 malformed line keeps last good + notes', r.includes('did not parse') && r.includes('Pilot [active]'), r)
  await h.call('turn.complete', { answer: 'x', agentId: 'sub1' })
  ok('4b subagent turn ignored', (await h.render()).includes('2 replies since'))
  await h.call('turn.complete', { answer: LINE, isAborted: true })
  ok('4c aborted turn ignored', (await h.render()).includes('2 replies since'))
  h.env.now += 60_000
  h.env.files['WORKLOG.md'] = h.env.now
  await h.call('turn.complete', { answer: 'edited anchor, no line' })
  r = await h.render()
  ok('5 anchor newer than line -> UNVERIFIED source changed', r.includes('UNVERIFIED') && r.includes('source changed'), r)
  r = await h.render(true)
  ok('6 working overrides everything', r.includes('WORKING'), r)
  h.env.cwd = 'F:/other'
  r = await h.render()
  ok('7 cwd change -> UNVERIFIED directory changed', r.includes('directory changed'), r)
}
{ // 8 resume recovery
  const h = await makeHost({ env: { messages: [{ role: 'assistant', text: `old\n${LINE}` }, { role: 'user', text: 'hi' }, { role: 'assistant', text: 'later reply' }] } })
  await h.call('session.start', {})
  const r = await h.render()
  ok('8 resume recovers line but marks UNVERIFIED, 1 reply since', r.includes('UNVERIFIED') && r.includes('recovered from transcript') && r.includes('1 reply since') && r.includes('age unknown'), r)
}
{ // 9 generation fence: second instance cancels first instance's timer
  const store = new Map()
  const a = await makeHost({ store })
  await a.call('session.start', {})
  const b = await makeHost({ store })
  await b.call('session.start', {})
  const before = a.env.invalidations || 0
  await a.timers[0].fn()
  ok('9 old generation tick cancels its timer and does no work', a.timers[0].cancelled && (a.env.invalidations || 0) === before)
  await b.call('turn.complete', { answer: LINE })
  const nb = b.env.invalidations || 0
  b.env.now += 120_000
  await b.timers[0].fn()
  ok('9 newest generation still works and redraws on change', (b.env.invalidations || 0) > nb)
  const nb2 = b.env.invalidations || 0
  await b.timers[0].fn()
  ok('9 unchanged tick does not redraw', (b.env.invalidations || 0) === nb2)
  await b.call('session.end', {})
  ok('9 session.end cancels timer', b.timers[0].cancelled)
}
{ // 10 overlap guard
  const h = await makeHost()
  await h.call('session.start', {})
  h.env.usageCalls = 0
  const tick = h.timers[0].fn
  await Promise.all([tick(), tick(), tick()])
  ok('10 three simultaneous ticks run the refresh once', h.env.usageCalls === 1, String(h.env.usageCalls))
  await tick()
  ok('10 a later tick runs again', h.env.usageCalls === 2, String(h.env.usageCalls))
}
{ // 11 stale safety: main-thread tool activity in a turn that ends without a valid line
  const h = await makeHost()
  await h.call('session.start', {})
  await h.call('turn.complete', { answer: LINE })
  ok('11 baseline shows checkpoint', (await h.render()).includes('CHECKPOINT REPORTED'))
  await h.call('turn.start', {})
  await h.call('tool.call', { tool: 'Bash' })
  await h.call('turn.complete', { answer: 'did one thing, no line' })
  let r = await h.render()
  ok('11 EXACTLY ONE tool call then no line -> UNVERIFIED, never CHECKPOINT', r.includes('UNVERIFIED') && r.includes('without a new Roadmap line') && !r.includes('CHECKPOINT REPORTED'), r)
  await h.call('turn.start', {})
  await h.call('turn.complete', { answer: 'plain answer, no tools' })
  ok('11 still UNVERIFIED (not cleared by a plain reply)', (await h.render()).includes('UNVERIFIED'))
  await h.call('turn.start', {})
  await h.call('tool.call', { tool: 'Read' })
  await h.call('turn.complete', { answer: 'x', agentId: 'sub' })
  await h.call('turn.complete', { answer: LINE })
  ok('11 a fresh valid line restores the checkpoint', (await h.render()).includes('CHECKPOINT REPORTED'))
  await h.call('turn.start', {})
  await h.call('tool.call', { tool: 'Bash', agentId: 'sub' })
  await h.call('turn.complete', { answer: 'no line' })
  ok('11 subagent tool calls do not count as main-thread work', (await h.render()).includes('CHECKPOINT REPORTED'))
  await h.call('turn.start', {})
  await h.call('tool.call', { tool: 'Edit' })
  await h.call('turn.start', {})
  await h.call('turn.complete', { answer: 'no line' })
  ok('11 turn.start resets the count (previous turn tool does not leak)', (await h.render()).includes('CHECKPOINT REPORTED'))
  await h.call('turn.start', {})
  await h.call('tool.call', { tool: 'Bash' })
  await h.call('turn.complete', { answer: 'interrupted', isAborted: true })
  ok('11 aborted tool turn marks UNVERIFIED', (await h.render()).includes('UNVERIFIED'))
}
{ // 12 fence is per session: two sessions sharing one store do not silence each other
  const store = new Map()
  const a = await makeHost({ store, env: { sid: 'sess-A' } })
  await a.call('session.start', {})
  const b = await makeHost({ store, env: { sid: 'sess-B' } })
  await b.call('session.start', {})
  const before = a.env.invalidations || 0
  a.env.now += 120_000
  await a.timers[0].fn()
  ok('12 session A keeps working after session B starts (tick not cancelled)', !a.timers[0].cancelled)
  await a.call('turn.complete', { answer: LINE })
  ok('12 session A still repaints on turn.complete', (a.env.invalidations || 0) > before)
  const b0 = b.env.invalidations || 0
  await b.call('turn.complete', { answer: LINE.replace('Pilot', 'Other') })
  ok('12 session B repaints independently', (b.env.invalidations || 0) > b0)
  ok('12 each session has its own store key', store.get('gen:sess-A') !== store.get('gen:sess-B') && store.has('gen:sess-A') && store.has('gen:sess-B'))
  // hot reload of the SAME session: newest instance wins, older one stands down
  const a2 = await makeHost({ store, env: { sid: 'sess-A' } })
  await a2.call('session.start', {})
  const a1 = a.env.invalidations || 0
  await a.timers[0].fn()
  ok('12 same-session older instance cancels after a reload', a.timers[0].cancelled && (a.env.invalidations || 0) === a1)
  const a3 = a2.env.invalidations || 0
  await a2.call('turn.complete', { answer: LINE })
  ok('12 same-session newest instance works', (a2.env.invalidations || 0) > a3 && !a2.timers[0].cancelled)
}
{ // 13 a forced refresh requested while a tick is running is kept and runs afterwards
  const h = await makeHost()
  await h.call('session.start', {})
  let release
  const gate = new Promise((res) => { release = res })
  const realCalls = () => h.env.usageCalls || 0
  h.env.block = gate // the next usage() read inside the in-flight tick waits here
  const tick = h.timers[0].fn
  const inflight = tick() // starts, awaits usage()
  await new Promise((r) => setTimeout(r, 0)) // let the tick reach the gate
  const calls0 = realCalls()
  const inv0 = h.env.invalidations || 0
  await h.call('turn.complete', { answer: LINE }) // forced refresh while busy
  ok('13 busy: forced refresh does not run concurrently', realCalls() === calls0)
  release()
  await inflight
  await new Promise((r) => setTimeout(r, 0))
  ok('13 pending forced refresh ran after the tick finished', realCalls() === calls0 + 1, `${calls0} -> ${realCalls()}`)
  ok('13 and it repainted with the new line', (h.env.invalidations || 0) > inv0 && (await h.render()).includes('CHECKPOINT REPORTED'))
  const c1 = realCalls()
  await h.timers[0].fn()
  ok('13 pending flag cleared (no phantom extra refresh)', realCalls() === c1 + 1)
}
{ // 14 parser: only a trailing, unfenced footer; middle dots preserved
  const run = async (answer) => {
    const h = await makeHost()
    await h.call('session.start', {})
    await h.call('turn.complete', { answer })
    return h.render()
  }
  let r = await run(`Done.\n\n${LINE}\n\n  \n`)
  ok('14 trailing footer with trailing blank lines parses', r.includes('Pilot [active]') && r.includes('CHECKPOINT REPORTED'), r)
  r = await run(`${LINE}\nand some text after it`)
  ok('14 footer followed by prose is ignored', r.includes('NO ROADMAP LINE'), r)
  r = await run(`Here is the format:\n\`\`\`\n${LINE}\n\`\`\`\nThanks`)
  ok('14 fenced example is ignored', r.includes('NO ROADMAP LINE'), r)
  r = await run(`Here:\n\`\`\`\n${LINE}\n\`\`\``)
  ok('14 footer-looking line as last fenced line is ignored (fence closes the reply)', r.includes('NO ROADMAP LINE'), r)
  r = await run(`Example:\n~~~\nsomething\n${LINE}`)
  ok('14 unclosed fence swallows the footer', r.includes('NO ROADMAP LINE'), r)
  r = await run(`> ${LINE}`)
  ok('14 blockquoted footer is ignored', r.includes('NO ROADMAP LINE'), r)
  r = await run(`${LINE}\n\n\`\`\`\ncode\n\`\`\`\n${LINE}`)
  ok('14 a real footer after an earlier closed fence parses', r.includes('CHECKPOINT REPORTED'), r)
  r = await run('Done\nRoadmap: Pilot [active] · next: run A · B then C (you) · pause: safe')
  ok('14 middle dot inside next: is preserved', r.includes('next: run A · B then C (you)') && r.includes('CHECKPOINT REPORTED'), r)
  r = await run('Done\nRoadmap: Phase 1 · intake [active] · acceptance: 2/4 verified (a · b) · pause: attention · blocker: waiting on x · y')
  ok('14 middle dots in milestone, acceptance and blocker preserved', r.includes('Phase 1 · intake [active]') && r.includes('acceptance 2/4 verified (a · b)') && r.includes('BLOCKER: waiting on x · y') && r.includes('NEEDS YOU'), r)
  r = await run('Done\r\nRoadmap: Pilot [complete] · pause: safe\r\n')
  ok('14 CRLF reply parses', r.includes('Pilot [complete]') && r.includes('CHECKPOINT REPORTED'), r)
  r = await run('Done\nRoadmap: no anchor — no WORKLOG here')
  ok('14 no-anchor form parses', r.includes('NO ANCHOR') && r.includes('no WORKLOG here'), r)
  r = await run('Done\nRoadmap: Pilot without status · pause: safe')
  ok('14 footer without [status] is malformed (note, no line)', r.includes('did not parse') && r.includes('NO ROADMAP LINE'), r)
  r = await run('Done\nRoadmap: Pilot [active] · pause: whenever')
  ok('14 unknown pause value normalises to unknown', r.includes('PAUSE UNKNOWN'), r)
  r = await run('Done\n**Roadmap:** Pilot [active] · pause: safe')
  ok('14 bold-wrapped label is not accepted', r.includes('NO ROADMAP LINE'), r)
}
for (const r of results) console.log(r[0], r[1], r[0] === 'FAIL' ? '\n   ' + r[2] : '')
const fails = results.filter((r) => r[0] === 'FAIL').length
console.log(`${results.length - fails}/${results.length} passed`)
process.exit(fails ? 1 : 0)
