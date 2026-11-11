// The home page's demo: one work item playing through Kraft's default chain,
// stopping at its gates. A port of design K's reference state machine, with no
// Vue and no DOM in it, so docsite/tests/homePlayer.test.ts runs it on plain
// Node against a fake clock. app/composables/useHomePlayer.ts wraps it.

export type Icon = 'agent' | 'layers' | 'pr' | 'merge'

export type Step = {
  kind: 'run' | 'gate'
  label: string
  icon?: Icon
  actor: 'AGENT' | 'KRAFT' | 'YOU'
  title: string
  detail: string
  // A gate's Request changes goes to this step, carrying this note
  // (config/chains/default.yaml's reject_to).
  back?: string
  note?: string
}

// The chain of an item filed with its spec attached: spec and spec_approval
// are skipped, as in Kraft, so it starts at plan.
export const STEPS: Step[] = [
  { kind: 'run', label: 'plan', icon: 'agent', actor: 'AGENT', title: 'An agent writes the plan.', detail: 'You attached the spec when you filed the item, so the chain starts here. A fresh Claude Code session reads it and plans the change, task by task.' },
  { kind: 'gate', label: 'plan_approval', back: 'plan', note: 'Clear the cache when a migration runs.', actor: 'YOU', title: 'Approve the plan?', detail: 'Nothing after this gate runs until you decide. If you request changes, your note goes back to plan.' },
  { kind: 'run', label: 'implementation', icon: 'agent', actor: 'AGENT', title: 'An agent writes the code.', detail: 'It works in the item’s worktree, a copy of your repo on its own branch, so your checkout stays as you left it.' },
  { kind: 'run', label: 'verification', icon: 'layers', actor: 'KRAFT', title: 'Your tests run, then a code review.', detail: 'Kraft runs the tests itself, in a fix loop capped by attempts, time and spend.' },
  { kind: 'gate', label: 'local_review', back: 'implementation', note: 'Add a test for a stale entry.', actor: 'YOU', title: 'Approve the change?', detail: 'You read the diff before any merge request exists. Approve, and Kraft opens a draft one; request changes, and your note goes back to implementation.' },
  { kind: 'run', label: 'draft_merge_request', icon: 'pr', actor: 'KRAFT', title: 'Kraft opens a draft merge request.', detail: 'It waits for CI and the automated reviewers, and an agent fixes what they flag.' },
  { kind: 'gate', label: 'final_review', back: 'implementation', note: 'Log cache misses at debug level, not info.', actor: 'YOU', title: 'Approve the merge?', detail: 'You review the whole item and its summary. This is your last call.' },
  { kind: 'run', label: 'merge', icon: 'merge', actor: 'KRAFT', title: 'Kraft merges.', detail: 'It marks the request ready, waits for an approving review if your forge requires one, merges, and waits for CI on the merged commit.' },
]

export type VState = 'idle' | 'run' | 'pass' | 'find'

// One fix-loop run inside verification: round 1 finds something, repair
// commits, round 2 passes. The judge runs before repair, from the second
// repair on, so it never runs here.
export type VFrame = {
  t: VState
  r: VState
  p: VState
  end: 'idle' | 'fail' | 'pass'
  loop: boolean
  round: 1 | 2
  caption: string
}

export const V_FRAMES: VFrame[] = [
  { t: 'run', r: 'idle', p: 'idle', end: 'idle', loop: false, round: 1, caption: 'Kraft runs your tests itself, only for the scopes this change touched.' },
  { t: 'pass', r: 'run', p: 'idle', end: 'idle', loop: false, round: 1, caption: 'Then a separate agent reviews the diff. It can read the code but not change it.' },
  { t: 'pass', r: 'find', p: 'idle', end: 'fail', loop: true, round: 1, caption: 'It finds one thing: the stale-entry check misses an expired key. The fix loop opens.' },
  { t: 'pass', r: 'find', p: 'run', end: 'fail', loop: true, round: 1, caption: 'The first round skips the judge: a repair agent fixes the finding and commits.' },
  { t: 'run', r: 'idle', p: 'pass', end: 'idle', loop: false, round: 2, caption: 'Round 2 runs everything again from the start.' },
  { t: 'pass', r: 'run', p: 'pass', end: 'idle', loop: false, round: 2, caption: 'The review runs again on the fixed code.' },
  { t: 'pass', r: 'pass', p: 'pass', end: 'pass', loop: false, round: 2, caption: 'No findings: the node passes. If the repairs had run out, the item would stop and show you why.' },
]

// Milliseconds.
export const TIMING = { idle: 2400, run: 2200, gateWait: 2000, press: 700, vFrame: 1100, vHold: 2400, doneHold: 5000 }

export type Timers = {
  set(fn: () => void, ms: number): number
  clear(id: number): void
}

export type LogRow = { gate: string, approved: boolean, sent: boolean, note: string }

export type PlayerState = {
  // -1 is the paused item before it starts; STEPS.length is merged.
  step: number
  decisions: number
  note: boolean
  noteText: string
  noteFrom: string
  // The visitor pressed a button: gates wait for them.
  hands: boolean
  // The Pause button.
  paused: boolean
  // plan_approval has been sent back once in this run.
  sentBack: boolean
  // The button the demo is pressing at a gate.
  press: '' | 'a' | 'r'
  // The verification frame.
  sub: number
  log: LogRow[]
  // The gate approved a moment ago, which gets a one-time stamp.
  just: number
}

export type PlayerNode = {
  label: string
  l1: string
  l2: string
  l3: string
  rDoneAgent: boolean
  rDoneLayers: boolean
  rDonePr: boolean
  rDoneMerge: boolean
  rCurAgent: boolean
  rCurLayers: boolean
  rCurPr: boolean
  rCurMerge: boolean
  rTodoAgent: boolean
  rTodoLayers: boolean
  rTodoPr: boolean
  rTodoMerge: boolean
  labDone: boolean
  labCur: boolean
  labTodo: boolean
  stateWord: string
  connDone: boolean
  connTodo: boolean
  runDone: boolean
  runCur: boolean
  runTodo: boolean
  gateDone: boolean
  gateJust: boolean
  gateDoneQuiet: boolean
  gateCur: boolean
  gateTodo: boolean
}

export type PlayerVerify = {
  round: number
  caption: string
  loopOn: boolean
  loopOff: boolean
  jIdle: boolean
  endIdle: boolean
  endFail: boolean
  endPass: boolean
  // tIdle tRun tPass tFind, then the same for r (review) and p (repair).
  tIdle: boolean
  tRun: boolean
  tPass: boolean
  tFind: boolean
  rIdle: boolean
  rRun: boolean
  rPass: boolean
  rFind: boolean
  pIdle: boolean
  pRun: boolean
  pPass: boolean
  pFind: boolean
}

export type PlayerNow = { label: string, word: string, pos: string, wait: boolean, calm: boolean }

export type PlayerView = {
  nodes: PlayerNode[]
  idle: boolean
  done: boolean
  running: boolean
  verifying: boolean
  plainRun: boolean
  v: PlayerVerify
  atGate: boolean
  cap: { label?: string, actor: string, title: string, detail: string }
  note: boolean
  noteText: string
  noteFrom: string
  decisions: number
  log: LogRow[]
  now: PlayerNow
  decWord: string
  autoHint: boolean
  autoVerb: string
  counting: boolean
  pressA: boolean
  calmA: boolean
  pressR: boolean
  calmR: boolean
  playing: boolean
  stopped: boolean
}

export type Player = {
  state(): PlayerState
  boot(): void
  start(): void
  approve(): void
  reject(): void
  toggle(): void
  dispose(): void
}

export type PlayerOptions = {
  timers: Timers
  onChange: (s: PlayerState) => void
  // Wraps a state change so the caller can animate it. `morph` is true when the
  // change enters or leaves verification. It must call `apply` exactly once;
  // the next tick is scheduled only after it has.
  transition?: (apply: () => void, morph: boolean) => void
}

const WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']

const VERIFICATION = STEPS.findIndex(x => x.label === 'verification')

// The paused item before it starts: what the server renders.
export function initialState(): PlayerState {
  return { step: -1, decisions: 0, note: false, noteText: '', noteFrom: '', hands: false, paused: false, sentBack: false, press: '', sub: 0, log: [], just: -1 }
}

function fresh(): Partial<PlayerState> {
  return { step: 0, decisions: 0, note: false, sentBack: false, press: '', sub: 0, log: [], just: -1 }
}

// The demo decides each gate itself, sending plan_approval back once per run,
// until the visitor presses a button.
function autoVerb(s: PlayerState): 'r' | 'a' {
  const x = STEPS[s.step]
  return x && x.label === 'plan_approval' && !s.sentBack ? 'r' : 'a'
}

function approved(s: PlayerState): Partial<PlayerState> {
  const g = STEPS[s.step]!
  return {
    step: s.step + 1,
    decisions: s.decisions + 1,
    note: false,
    press: '',
    sub: 0,
    just: s.step,
    log: s.log.concat([{ gate: g.label, approved: true, sent: false, note: '' }]),
  }
}

function sentBack(s: PlayerState): Partial<PlayerState> {
  const g = STEPS[s.step]!
  return {
    step: STEPS.findIndex(x => x.label === g.back),
    decisions: s.decisions + 1,
    note: true,
    noteText: g.note,
    noteFrom: g.label,
    sentBack: true,
    press: '',
    sub: 0,
    just: -1,
    log: s.log.concat([{ gate: g.label, approved: false, sent: true, note: g.note! }]),
  }
}

export function view(s: PlayerState): PlayerView {
  const idle = s.step < 0
  const done = s.step >= STEPS.length
  const cur = !idle && !done ? STEPS[s.step]! : null
  const nodes: PlayerNode[] = STEPS.map((x, i) => {
    const reached = done || i < s.step
    const isCur = i === s.step
    const gate = x.kind === 'gate'
    const linked = i > 0 && (done || i <= s.step)
    const parts = x.label.split('_')
    const seg = (k: number) => (parts[k] || '') + (k < parts.length - 1 ? '_' : '')
    const todo = !gate && !reached && !isCur
    const justDone = gate && reached && i === s.just && s.step === i + 1
    return {
      label: x.label,
      l1: seg(0),
      l2: seg(1),
      l3: seg(2),
      rDoneAgent: !gate && reached && x.icon === 'agent',
      rDoneLayers: !gate && reached && x.icon === 'layers',
      rDonePr: !gate && reached && x.icon === 'pr',
      rDoneMerge: !gate && reached && x.icon === 'merge',
      rCurAgent: !gate && isCur && x.icon === 'agent',
      rCurLayers: !gate && isCur && x.icon === 'layers',
      rCurPr: !gate && isCur && x.icon === 'pr',
      rCurMerge: !gate && isCur && x.icon === 'merge',
      rTodoAgent: todo && x.icon === 'agent',
      rTodoLayers: todo && x.icon === 'layers',
      rTodoPr: todo && x.icon === 'pr',
      rTodoMerge: todo && x.icon === 'merge',
      labDone: reached,
      labCur: isCur,
      labTodo: !reached && !isCur,
      stateWord: reached ? 'done' : isCur ? (gate ? 'waiting for you' : 'running') : 'not run yet',
      connDone: i > 0 && linked,
      connTodo: i > 0 && !linked,
      runDone: !gate && reached,
      runCur: !gate && isCur,
      runTodo: todo,
      gateDone: gate && reached,
      gateJust: justDone,
      gateDoneQuiet: gate && reached && !justDone,
      gateCur: gate && isCur,
      gateTodo: gate && !reached && !isCur,
    }
  })

  const gating = !!cur && cur.kind === 'gate'
  const auto = !s.hands && !s.paused
  const F = V_FRAMES[Math.min(s.sub, V_FRAMES.length - 1)]!
  const verifying = !!cur && cur.label === 'verification'
  const v = {
    round: F.round,
    caption: F.caption,
    loopOn: F.loop,
    loopOff: !F.loop,
    jIdle: true,
    endIdle: F.end === 'idle',
    endFail: F.end === 'fail',
    endPass: F.end === 'pass',
    tIdle: F.t === 'idle',
    tRun: F.t === 'run',
    tPass: F.t === 'pass',
    tFind: F.t === 'find',
    rIdle: F.r === 'idle',
    rRun: F.r === 'run',
    rPass: F.r === 'pass',
    rFind: F.r === 'find',
    pIdle: F.p === 'idle',
    pRun: F.p === 'run',
    pPass: F.p === 'pass',
    pFind: F.p === 'find',
  }
  const now: PlayerNow = idle
    ? { label: 'filed', word: 'paused', pos: `${STEPS.length} steps`, wait: false, calm: true }
    : done
      ? { label: 'merge', word: 'merged', pos: `${STEPS.length} of ${STEPS.length}`, wait: false, calm: true }
      : { label: cur!.label, word: gating ? 'waiting for you' : 'running', pos: `${s.step + 1} of ${STEPS.length}`, wait: gating, calm: !gating }
  return {
    nodes,
    idle,
    done,
    running: !!cur && cur.kind === 'run',
    verifying,
    plainRun: !verifying,
    v,
    atGate: gating,
    cap: cur || { actor: '', title: '', detail: '' },
    note: s.note && !!cur && cur.kind === 'run',
    noteText: s.noteText,
    noteFrom: s.noteFrom,
    decisions: s.decisions,
    log: s.log,
    now,
    decWord: WORDS[s.decisions] || String(s.decisions),
    autoHint: gating && auto && !s.press,
    autoVerb: gating && autoVerb(s) === 'r' ? 'request changes' : 'approve',
    counting: gating && auto && !s.press,
    pressA: gating && s.press === 'a',
    calmA: !(gating && s.press === 'a'),
    pressR: gating && s.press === 'r',
    calmR: !(gating && s.press === 'r'),
    playing: auto,
    stopped: !auto,
  }
}

export function createPlayer(opts: PlayerOptions): Player {
  const { timers, onChange, transition } = opts
  let state = initialState()
  let timer: number | undefined
  let disposed = false
  // Bumped by every go(), so a transition that applies late, after another
  // change or after dispose(), does nothing.
  let generation = 0

  const clear = () => {
    if (timer !== undefined) timers.clear(timer)
    timer = undefined
  }
  const later = (fn: () => void, ms: number) => {
    clear()
    timer = timers.set(fn, ms)
  }

  const go = (next: Partial<PlayerState>) => {
    clear()
    const mine = ++generation
    const was = STEPS[state.step]?.label
    const will = next.step == null ? was : STEPS[next.step]?.label
    const morph = (was === 'verification') !== (will === 'verification')
    // The next tick is scheduled only once the state has applied, so a
    // deferred transition can't skip a step.
    const apply = () => {
      if (disposed || mine !== generation) return
      state = { ...state, ...next }
      onChange(state)
      later(tick, 0)
    }
    if (transition) transition(apply, morph)
    else apply()
  }

  const tick = () => {
    clear()
    const c = state
    if (c.paused) return
    const auto = !c.hands
    if (c.step < 0) {
      if (auto) later(() => go(fresh()), TIMING.idle)
    } else if (c.step >= STEPS.length) {
      if (auto) later(() => go({ step: -1, note: false, press: '' }), TIMING.doneHold)
    } else if (c.step === VERIFICATION && c.sub < V_FRAMES.length - 1) {
      later(() => go({ sub: state.sub + 1 }), TIMING.vFrame)
    } else if (STEPS[c.step]!.kind === 'run') {
      const hold = c.step === VERIFICATION ? TIMING.vHold : TIMING.run
      later(() => go({ step: state.step + 1, note: false, sub: 0 }), hold)
    } else if (auto) {
      const v = autoVerb(c)
      later(() => {
        state = { ...state, press: v }
        onChange(state)
        later(() => go(v === 'r' ? sentBack(state) : approved(state)), TIMING.press)
      }, TIMING.gateWait)
    }
  }

  const atGate = () => STEPS[state.step]?.kind === 'gate'

  return {
    state: () => state,
    boot: () => later(tick, 0),
    start: () => go({ ...fresh(), hands: true }),
    // A fast double click, or a click during the demo's own press, arrives when
    // the item is no longer at a gate: it must not move a run step along.
    approve: () => {
      if (atGate()) go({ ...approved(state), hands: true })
    },
    reject: () => {
      if (atGate()) go({ ...sentBack(state), hands: true })
    },
    toggle: () => {
      const c = state
      go(!c.hands && !c.paused ? { paused: true, press: '' } : { paused: false, hands: false, press: '' })
    },
    dispose: () => {
      disposed = true
      generation++
      clear()
    },
  }
}
