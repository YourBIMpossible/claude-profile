// roadmap-band v2 — read-only status band above the prompt. Zero model tokens, no file writes.
//
// What it shows is a VIEW, not a source of truth:
//   * the last `Roadmap:` line the model emitted in this session (model-reported), captured on
//     turn.complete, or recovered from the transcript after /resume
//   * deterministic facts the mod measures itself: working/idle, age of that line, replies since,
//     age of the status anchor file, whether the anchor changed after the line, context %, plan limits
// Missing or unreadable data is shown as `unavailable` / `unverified`, never as 0% or "safe".

// Strict status-source order: the first file that exists is THE status anchor. No fallthrough past it.
const STATUS_ANCHORS = ['CURRENT-STATE.md', 'Project Docs/CURRENT-STATE.md', 'WORKLOG.md']
// Mission files are human-owned and only stat'ed here (change detection), never parsed or written.
const MISSION_PREFIX = 'NORTHSTAR'
const TICK_MS = 30_000

const join = (base, rel) => (base ? `${base.replace(/[\\/]+$/, '')}/${rel}` : rel)

const fmtAge = (ms) => {
  if (!Number.isFinite(ms) || ms < 0) return 'unknown'
  const m = Math.floor(ms / 60_000)
  if (m < 1) return '<1m'
  if (m < 60) return `${m}m`
  if (m < 1440) return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`
  return `${Math.floor(m / 1440)}d`
}

const mtimeOf = (st) => {
  if (!st) return null
  if (typeof st.mtimeMs === 'number') return st.mtimeMs
  const v = st.mtime ?? st.modifiedMs ?? st.modified
  if (v instanceof Date) return v.getTime()
  if (typeof v === 'number') return v < 1e12 ? v * 1000 : v
  if (typeof v === 'string') {
    const t = Date.parse(v)
    return Number.isFinite(t) ? t : null
  }
  return null
}

// Accepted form: the LAST non-empty line of the reply, outside any code fence, starting `Roadmap:` -
//   `Roadmap: <milestone> [status] · acceptance: 4/7 verified (scope) · next: <action> (<owner>) · pause: safe · blocker: <text>`
// or `Roadmap: no anchor — <gap>`. A Roadmap line anywhere else (quoted example, fenced block, mid-reply) is ignored.
// Fields split only at ` · ` followed by a known key, so a middle dot inside a value is preserved.
const FIELD_SPLIT = /\s+·\s+(?=(?:acceptance|next|pause|blocker):)/i
const FIELD = /^(acceptance|next|pause|blocker):[ \t]*(\S.*)$/i
const FENCE = /^[ \t]*(```|~~~)/
const FOOTER = /^[ \t]*Roadmap:[ \t]*(\S.*?)[ \t]*$/

function trailingFooter(text) {
  let fenced = false
  let last = null
  for (const line of text.split(/\r?\n/)) {
    if (FENCE.test(line)) { fenced = !fenced; last = { fence: true }; continue }
    if (line.trim()) last = { line, fenced }
  }
  if (!last || last.fence || last.fenced) return null
  const m = last.line.match(FOOTER)
  return m ? m[1] : null
}

// Returns null when there is no usable footer, { malformed } when the footer exists but has no [status].
function parseRoadmap(text) {
  if (typeof text !== 'string') return null
  const body = trailingFooter(text)
  if (body == null) return null
  if (/^no anchor\b/i.test(body)) return { noAnchor: body.replace(/^no anchor\s*[—–-]*\s*/i, '') || 'no anchor', raw: body }
  const parts = body.split(FIELD_SPLIT)
  const head = parts.shift() || ''
  const sm = head.match(/\[(proposed|active|blocked|complete)\]/i)
  if (!sm) return { malformed: true, raw: body }
  const out = { raw: body, milestone: head.replace(sm[0], '').trim(), status: sm[1].toLowerCase() }
  for (const p of parts) {
    const kv = p.match(FIELD)
    if (!kv) continue
    const key = kv[1].toLowerCase()
    if (key === 'pause') {
      const w = kv[2].trim().split(/[\s—–-]+/)[0].toLowerCase()
      out.pause = ['safe', 'attention', 'unknown'].includes(w) ? w : 'unknown'
    } else out[key] = kv[2].trim()
  }
  if (!out.pause) out.pause = 'unknown'
  return out
}

async function findAnchor($, cwd) {
  for (const rel of STATUS_ANCHORS) {
    try {
      const p = join(cwd, rel)
      if (await $.fs.exists(p)) return { rel, mtime: mtimeOf(await $.fs.stat(p)) }
    } catch {
      // unreadable candidate: treat as absent, keep scanning the strict order
    }
  }
  return null
}

async function missionMtime($, cwd) {
  let newest = null
  try {
    for (const ent of await $.fs.list(cwd || '.')) {
      if (ent.kind !== 'file' || !ent.name.startsWith(MISSION_PREFIX) || !ent.name.endsWith('.md')) continue
      const t = mtimeOf(await $.fs.stat(join(cwd, ent.name)))
      if (t && (!newest || t > newest)) newest = t
    }
  } catch {
    // no listing: no mission-change signal
  }
  return newest
}

const KIND_LABELS = [
  [/five|5\s*h|session/i, '5h'],
  [/seven|week|7\s*d|wk/i, 'wk'],
]

async function readUsage($, now) {
  const out = { ctx: null, limits: [], ok: false }
  try {
    const u = await $.session.usage()
    out.ok = true
    if (u && u.context && Number.isFinite(u.context.percent)) out.ctx = Math.round(u.context.percent)
    for (const r of (u && u.rateLimits) || []) {
      if (!r || !Number.isFinite(r.percentUsed)) continue
      const hit = KIND_LABELS.find(([re]) => re.test(String(r.kind)))
      let reset = null
      if (typeof r.resetsAt === 'number') reset = (r.resetsAt < 1e12 ? r.resetsAt * 1000 : r.resetsAt) - now
      else if (typeof r.resetsAt === 'string' && Number.isFinite(Date.parse(r.resetsAt))) reset = Date.parse(r.resetsAt) - now
      out.limits.push({ label: hit ? hit[1] : String(r.kind), pct: Math.round(r.percentUsed), reset })
    }
  } catch {
    // leave ok=false: rendered as `usage unavailable`
  }
  return out
}

// Module-level state, shared by every hook in this file. Reset on session.end so nothing survives /clear, /resume or /branch.
const GEN = Math.random().toString(36).slice(2, 8)
let timer = null
let busy = false
let pending = false // a forced refresh requested while one was running
let fenceKey = 'gen'
let sig = ''
let ticks = 0
let S = fresh()

function fresh() {
  return { line: null, note: '', repliesSince: 0, tools: 0, workSince: false, anchor: null, mission: null, usage: null, cwd: '', sid: '', now: 0, err: '', debug: false }
}

// The fence is keyed by session id: two sessions share one store and must not silence each other.
// Hot reload re-runs session.start in a new module instance of the SAME session; only the newest instance works.
async function fence($) {
  try {
    return (await $.store.get(fenceKey)) === GEN
  } catch {
    return true
  }
}

async function refresh($, force) {
  if (busy) {
    if (force) pending = true
    return
  }
  busy = true
  try {
    await refreshOnce($, force)
  } finally {
    busy = false
  }
  if (pending) {
    pending = false
    await refresh($, true)
  }
}

async function refreshOnce($, force) {
  try {
    if (!(await fence($))) {
      if (timer) timer.cancel()
      timer = null
      pending = false
      return
    }
    ticks += 1
    S.err = ''
    const now = await $.clock.now()
    const cwd = String((await $.session.cwd()) || '')
    let sid = ''
    try { sid = String((await $.session.id()) || '') } catch { /* identity unavailable: line cannot be verified */ }
    S.now = now
    S.cwd = cwd
    S.sid = sid
    S.anchor = await findAnchor($, cwd)
    S.mission = await missionMtime($, cwd)
    S.usage = await readUsage($, now)
    const l = S.line
    const next = [
      l ? l.raw : '', l ? Math.floor((now - (l.at || now)) / 60_000) : '', S.repliesSince,
      S.anchor ? `${S.anchor.rel}:${S.anchor.mtime}` : 'none', S.mission, cwd, sid, S.note, l ? l.workSince : '',
      S.usage.ctx, S.usage.limits.map((x) => `${x.label}${x.pct}${x.reset == null ? '' : Math.floor(x.reset / 60_000)}`).join(','),
    ].join('|')
    if (force || next !== sig) {
      sig = next
      $.ui.invalidate('ui.render')
    }
  } catch {
    S.err = 'band read failed; showing last good state'
  }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    S = fresh()
    try { S.debug = !!(await $.env.get('ROADMAP_BAND_DEBUG')) } catch { /* debug off */ }
    let sid = ''
    try { sid = String((await $.session.id()) || '') } catch { /* identity unavailable */ }
    fenceKey = `gen:${sid || 'nosid'}`
    pending = false
    try { await $.store.set(fenceKey, GEN) } catch { /* fence unavailable: single instance assumed */ }
    // After /resume the live capture is gone; recover the last line from the transcript. Its age is unknown.
    try {
      const msgs = await $.session.messages()
      let since = 0
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role !== 'assistant') continue
        const p = parseRoadmap(msgs[i].text)
        if (p && !p.malformed) {
          S.line = { ...p, at: null, resumed: true, cwd: '', sid: '' }
          S.repliesSince = since
          break
        }
        since += 1
      }
    } catch { /* no transcript access: start empty */ }
    if (timer) timer.cancel()
    timer = $.clock.every(TICK_MS, () => refresh($, false))
    await refresh($, true)
    if (S.line && S.line.resumed) {
      S.line.cwd = S.cwd
      S.line.sid = S.sid
    }
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    if (timer) timer.cancel()
    timer = null
    // The store is shared by every session on the machine: drop this session's fence key, unless a newer instance owns it.
    try { if ((await $.store.get(fenceKey)) === GEN) await $.store.delete(fenceKey) } catch { /* best effort */ }
    S = fresh()
    sig = ''
    pending = false
    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    if (!e.agentId) S.tools = 0
    return next(e)
  })

  // Observe only: every tool the MAIN conversation is about to run counts as activity that may have moved state.
  on('tool.call', async ($, e, next) => {
    if (!e.agentId) S.tools += 1
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    // Main conversation only; an interrupted turn proves nothing about the state.
    const worked = S.tools > 0 // main-thread tool calls ran this turn: state may have moved
    if (!e.agentId && e.isAborted && worked && S.line) S.line.workSince = true
    if (!e.agentId && !e.isAborted) {
      const p = parseRoadmap(e.answer)
      if (p && !p.malformed) {
        const now = await $.clock.now()
        S.line = { ...p, at: now, resumed: false, workSince: false, cwd: S.cwd, sid: S.sid }
        S.repliesSince = 0
        S.note = ''
      } else if (p && p.malformed) {
        S.note = 'last Roadmap line did not parse; showing the previous one'
        S.repliesSince += 1
      } else {
        S.repliesSince += 1
      }
      // Work finished without a fresh valid line: any earlier checkpoint claim no longer describes the state.
      if (worked && S.line && !(p && !p.malformed) && !e.isAborted) S.line.workSince = true
      await refresh($, true)
    }
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const { Box, Text } = $.ui.resolve(e)
    const working = !!(e.props && e.props.isWorking)
    const now = await $.clock.now()
    let cwdNow = S.cwd
    try { cwdNow = String((await $.session.cwd()) || '') } catch { /* keep the last known directory */ }
    const l = S.line
    const a = S.anchor

    // --- deterministic verification of the model's claim ---
    // Chip precedence, first match wins: WORKING (host busy) > UNVERIFIED (resumed | directory changed | other session)
    // > NO ANCHOR > NEEDS YOU > UNVERIFIED (tools ran after the line | anchor/NORTHSTAR changed after a safe line)
    // > CHECKPOINT REPORTED (pause: safe) > PAUSE UNKNOWN. No line at all: NO ROADMAP LINE.
    let chip = 'NO ROADMAP LINE'
    let tone = 'gray'
    let why = ''
    const anchorNewer = !!(l && l.at && a && a.mtime && a.mtime > l.at) || !!(l && l.at && S.mission && S.mission > l.at)
    if (l) {
      if (l.resumed) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'recovered from transcript after resume' }
      else if (l.cwd && cwdNow && l.cwd !== cwdNow) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'directory changed since the line' }
      else if (l.sid && S.sid && l.sid !== S.sid) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'line is from another session' }
      else if (l.noAnchor) { chip = 'NO ANCHOR'; tone = 'gray'; why = l.noAnchor }
      else if (l.pause === 'attention') { chip = 'NEEDS YOU'; tone = 'yellow' }
      else if (l.workSince) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'work finished after the line without a new Roadmap line' }
      else if (l.pause === 'safe' && anchorNewer) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'source changed after the line' }
      else if (l.pause === 'safe') { chip = 'CHECKPOINT REPORTED'; tone = 'green' }
      else { chip = 'PAUSE UNKNOWN'; tone = 'gray' }
    }
    if (working) { chip = 'WORKING'; tone = 'cyan'; why = 'operation running' }

    // --- row 1: what the model reports ---
    let row1 = 'No Roadmap line in this session yet (rule not loaded, or no work-finishing reply so far).'
    if (l && l.noAnchor) row1 = `No anchor: ${l.noAnchor}`
    else if (l) {
      const bits = [`${l.milestone || '?'} [${l.status}]`]
      if (l.acceptance) bits.push(`acceptance ${l.acceptance}`)
      if (l.next) bits.push(`next: ${l.next}`)
      if (l.blocker) bits.push(`BLOCKER: ${l.blocker}`)
      row1 = bits.join(' · ')
    }

    // --- row 2: what the mod itself measures ---
    const r2 = []
    if (why) r2.push(why)
    if (l) {
      const rec = l.at ? `model-reported ${fmtAge(now - l.at)} ago` : 'model-reported earlier session, age unknown'
      r2.push(`${rec}${S.repliesSince ? ` · ${S.repliesSince} repl${S.repliesSince === 1 ? 'y' : 'ies'} since` : ''}`)
    }
    if (a) r2.push(`${a.rel} ${a.mtime ? fmtAge(now - a.mtime) + ' ago' : 'age unavailable'}`)
    else r2.push('no status anchor file in this directory')
    if (S.note) r2.push(S.note)
    if (S.err) r2.push(S.err)
    const u = S.usage
    if (!u || !u.ok) r2.push('usage unavailable')
    else {
      r2.push(u.ctx == null ? 'ctx unavailable' : `ctx ${u.ctx}%`)
      if (!u.limits.length) r2.push('plan limits unavailable')
      for (const x of u.limits) r2.push(`${x.label} ${x.pct}%${x.reset != null && x.reset > 0 ? ` (resets ${fmtAge(x.reset)})` : ''}`)
    }
    if (S.debug) r2.push(`dbg gen=${GEN} ticks=${ticks} timer=${timer ? 'on' : 'off'}`)

    const chipEl = Text({ bold: true, color: tone === 'gray' ? undefined : tone, dimColor: tone === 'gray', children: [`[${chip}] `] })
    return Box({
      flexDirection: 'column',
      children: [
        Box({ flexDirection: 'row', children: [chipEl, Text({ wrap: 'truncate', children: [row1] })] }),
        Text({ dimColor: true, wrap: 'truncate', children: [r2.join(' · ')] }),
        await next(e),
      ],
    })
  })
}
