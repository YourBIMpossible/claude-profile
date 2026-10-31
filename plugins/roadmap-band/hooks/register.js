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

// Returns null when there is no usable footer, { malformed } when the footer exists but does not hold exactly one
// [status] after a non-empty milestone, or carries an acceptance that is not n/N with 0 <= n <= N, N >= 1.
// A field named twice also makes it malformed. A malformed footer that still reads `pause: attention` (in any of
// its pause values) or has a blocker keeps that signal ({ attention, blocker }).
const STATUS = /\[(proposed|active|blocked|complete)\]/gi
const NO_BLOCKER = /^(?:none|n\/a)$/i
const ACCEPTANCE = /^(\d+)\/(\d+)(?!\S)/
function parseRoadmap(text) {
  if (typeof text !== 'string') return null
  const body = trailingFooter(text)
  if (body == null) return null
  if (/^no anchor\b/i.test(body)) return { noAnchor: body.replace(/^no anchor\s*[—–-]*\s*/i, '') || 'no anchor', raw: body }
  const parts = body.split(FIELD_SPLIT)
  const head = parts.shift() || ''
  const fields = {}
  const seen = new Set()
  let repeated = false // a field named twice: no value may silently win, so the footer is malformed
  let anyAttention = false // any pause value of attention, even one a repeat would have overwritten
  const blockers = []
  for (const p of parts) {
    const kv = p.match(FIELD)
    if (!kv) continue
    const key = kv[1].toLowerCase()
    if (seen.has(key)) repeated = true
    seen.add(key)
    const v = kv[2].trim()
    if (key === 'pause') {
      // the first word: `safe` only as a whole word (`safe — reason` is safe, `safe-ish` is not);
      // any word starting `attention` (`attention-needed`) still asks for the owner
      const w = v.split(/[\s—–]+/)[0].toLowerCase()
      fields.pause = w.startsWith('attention') ? 'attention' : ['safe', 'unknown'].includes(w) ? w : 'unknown'
      if (fields.pause === 'attention') anyAttention = true
    } else if (key === 'blocker') {
      // `blocker: none` / `n/a` says there is no blocker
      if (!NO_BLOCKER.test(v)) { fields.blocker = v; blockers.push(v) }
    } else fields[key] = v
  }
  const malformed = () => {
    const out = { malformed: true, raw: body }
    if (anyAttention || blockers.length) Object.assign(out, { attention: true, blocker: blockers.join('; ') })
    return out
  }
  if (repeated) return malformed()
  const tags = [...head.matchAll(STATUS)]
  if (tags.length !== 1) return malformed()
  const sm = tags[0]
  const milestone = head.replace(sm[0], '').trim()
  if (!milestone) return malformed()
  if (fields.acceptance != null) {
    const am = fields.acceptance.match(ACCEPTANCE)
    if (!am || Number(am[2]) < 1 || Number(am[1]) > Number(am[2])) return malformed()
  }
  return { ...fields, raw: body, milestone, status: sm[1].toLowerCase(), pause: fields.pause || 'unknown' }
}

// Returns null only when every candidate is provably absent. A candidate that cannot be checked or read
// stops the scan (strict order, no fallthrough) and comes back as { rel, unreadable: true }.
async function findAnchor($, cwd) {
  for (const rel of STATUS_ANCHORS) {
    const p = join(cwd, rel)
    try {
      if (await $.fs.exists(p)) return { rel, mtime: mtimeOf(await $.fs.stat(p)), unreadable: false }
    } catch {
      return { rel, mtime: null, unreadable: true }
    }
  }
  return null
}

// Newest NORTHSTAR*.md mtime. unreadable is set when the listing fails or any mission file cannot be dated:
// change detection is then incomplete, and a safe line cannot be verified. A failing entry never hides the rest.
async function missionMtime($, cwd) {
  let newest = null
  let entries
  try {
    entries = await $.fs.list(cwd || '.')
  } catch {
    return { mtime: null, unreadable: true }
  }
  let unreadable = false
  for (const ent of entries) {
    if (ent.kind !== 'file' || !ent.name.startsWith(MISSION_PREFIX) || !ent.name.endsWith('.md')) continue
    let t = null
    try { t = mtimeOf(await $.fs.stat(join(cwd, ent.name))) } catch { /* counted below as undated */ }
    if (!t) { unreadable = true; continue }
    if (!newest || t > newest) newest = t
  }
  return { mtime: newest, unreadable }
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
let fenceOff = false // the fence key could not be written: this instance cannot be fenced and keeps working
let sig = ''
let ticks = 0
let S = fresh()

function fresh() {
  return { line: null, note: '', repliesSince: 0, tools: 0, workSince: false, anchor: null, mission: null, usage: null, cwd: '', sid: '', now: 0, err: '', debug: false }
}

// The fence is keyed by session id: two sessions share one store and must not silence each other.
// Hot reload re-runs session.start in a new module instance of the SAME session; only the newest instance works.
// Without a session id the key is per DIRECTORY (see session.start): instances in one directory share a key, so the newest
// fences the older ones (a hot reload), while sessions in other directories keep their own keys and are not silenced.
async function fence($) {
  if (fenceOff) return true
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
  const prevErr = S.err
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
      S.anchor ? `${S.anchor.rel}:${S.anchor.unreadable ? 'unreadable' : S.anchor.mtime}` : 'none', `${S.mission.mtime}:${S.mission.unreadable}`, cwd, sid, S.note, l ? l.workSince : '',
      S.usage.ctx, S.usage.limits.map((x) => `${x.label}${x.pct}${x.reset == null ? '' : Math.floor(x.reset / 60_000)}`).join(','),
    ].join('|')
    if (force || next !== sig || prevErr) {
      sig = next
      $.ui.invalidate('ui.render')
    }
  } catch {
    S.err = 'band read failed; showing last good state'
    // repaint once when the error appears, so it is not hidden until something else redraws
    if (S.err !== prevErr) try { $.ui.invalidate('ui.render') } catch { /* nothing more to do: the next render shows it */ }
  }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    S = fresh()
    try { S.debug = !!(await $.env.get('ROADMAP_BAND_DEBUG')) } catch { /* debug off */ }
    let sid = ''
    try { sid = String((await $.session.id()) || '') } catch { /* identity unavailable */ }
    // Without a session id the directory is the best identity there is. Instances in the same directory share one key, so a hot
    // reload fences the older instance (it can no longer keep refreshing beside the new one) and keys do not pile up, one per
    // directory instead of one per instance; sessions in other directories keep their own key and silence nobody. With neither
    // an id nor a directory nothing tells instances apart, so such an instance fences only itself, as before.
    let dir = ''
    if (!sid) try { dir = String((await $.session.cwd()) || '') } catch { /* directory unavailable */ }
    fenceKey = sid ? `gen:${sid}` : `gen:nosid:${dir || GEN}`
    fenceOff = false
    pending = false
    try { await $.store.set(fenceKey, GEN) } catch { fenceOff = true }
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
        if (p && p.malformed && p.attention) {
          // The same route as a live turn (turn.complete): a footer that did not parse but asks for the owner is shown as written,
          // not skipped for an older line. Its age is unknown; the directory and session checks still apply once they are known.
          S.line = { raw: p.raw, unparsed: true, pause: 'attention', blocker: p.blocker, at: null, resumed: false, fromTranscript: true, workSince: false, cwd: '', sid: '' }
          S.repliesSince = since
          S.note = 'last Roadmap line did not parse; showing it as written'
          break
        }
        since += 1
      }
    } catch { /* no transcript access: start empty */ }
    if (timer) timer.cancel()
    timer = $.clock.every(TICK_MS, () => refresh($, false))
    await refresh($, true)
    if (S.line && (S.line.resumed || S.line.fromTranscript)) {
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
      } else if (p && p.malformed && p.attention) {
        // Unparsed, but it asks for the owner: show it as written rather than an older line.
        const now = await $.clock.now()
        S.line = { raw: p.raw, unparsed: true, pause: 'attention', blocker: p.blocker, at: now, resumed: false, workSince: false, cwd: S.cwd, sid: S.sid }
        S.repliesSince = 0
        S.note = 'last Roadmap line did not parse; showing it as written'
      } else if (p && p.malformed) {
        S.note = 'last Roadmap line did not parse; showing the previous one'
        // The model's newest report is unreadable: the kept line no longer describes the state.
        if (S.line) S.line.superseded = true
        S.repliesSince += 1
      } else {
        S.repliesSince += 1
      }
      // Work finished without a fresh line: any earlier checkpoint claim no longer describes the state.
      const freshLine = !!(p && (!p.malformed || p.attention))
      if (worked && S.line && !freshLine && !e.isAborted) S.line.workSince = true
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
    // > NO ANCHOR > NEEDS YOU > UNVERIFIED (tools ran after the line | anchor/NORTHSTAR changed after a safe line
    // | refresh failed | a newer footer did not parse | anchor or NORTHSTAR unreadable or undated | no session identity)
    // > CHECKPOINT REPORTED (pause: safe) > PAUSE UNKNOWN. No line at all: NO ROADMAP LINE.
    let chip = 'NO ROADMAP LINE'
    let tone = 'gray'
    let why = ''
    const anchorNewer = !!(l && l.at && a && a.mtime && a.mtime > l.at) || !!(l && l.at && S.mission && S.mission.mtime && S.mission.mtime > l.at)
    // A safe claim the mod cannot check against the anchor is not a checkpoint.
    let unverifiable = ''
    if (S.err) unverifiable = S.err // the last refresh failed: anchor and identity facts may be stale
    else if (l && l.superseded) unverifiable = S.note
    else if (a && a.unreadable) unverifiable = `${a.rel} unreadable`
    else if (a && !a.mtime) unverifiable = `${a.rel} age unavailable`
    else if (S.mission && S.mission.unreadable) unverifiable = 'NORTHSTAR unreadable'
    else if (!S.sid || (l && !l.sid)) unverifiable = 'session identity unavailable'
    if (l) {
      if (l.resumed) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'recovered from transcript after resume' }
      else if (l.cwd && cwdNow && l.cwd !== cwdNow) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'directory changed since the line' }
      else if (l.sid && S.sid && l.sid !== S.sid) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'line is from another session' }
      else if (l.noAnchor) { chip = 'NO ANCHOR'; tone = 'gray'; why = l.noAnchor }
      else if (l.pause === 'attention') { chip = 'NEEDS YOU'; tone = 'yellow' }
      else if (l.workSince) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'work finished after the line without a new Roadmap line' }
      else if (l.pause === 'safe' && anchorNewer) { chip = 'UNVERIFIED'; tone = 'yellow'; why = 'source changed after the line' }
      else if (l.pause === 'safe' && unverifiable) { chip = 'UNVERIFIED'; tone = 'yellow'; why = unverifiable }
      else if (l.pause === 'safe') { chip = 'CHECKPOINT REPORTED'; tone = 'green' }
      else { chip = 'PAUSE UNKNOWN'; tone = 'gray' }
    }
    if (working) { chip = 'WORKING'; tone = 'cyan'; why = 'operation running' }

    // --- row 1: what the model reports ---
    let row1 = 'No Roadmap line in this session yet (rule not loaded, or no work-finishing reply so far).'
    if (l && l.noAnchor) row1 = `No anchor: ${l.noAnchor}`
    else if (l && l.unparsed) row1 = l.raw
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
    const anchorText = a ? `${a.rel} ${a.unreadable ? 'unreadable' : a.mtime ? fmtAge(now - a.mtime) + ' ago' : 'age unavailable'}` : ''
    if (a) { if (anchorText !== why) r2.push(anchorText) }
    else r2.push('no status anchor file in this directory')
    if (S.note && S.note !== why) r2.push(S.note)
    if (S.err && S.err !== why) r2.push(S.err)
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
