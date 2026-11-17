// The blog's three animations of Kraft's item page (components/blog/BlogAnimation.vue): where each part stands at
// time t, in seconds into its loop. The states and words are the app's own (frontend/src/ng/item/groupView.ts,
// gateRounds.ts, reviewOutcome.ts, panes/Subagents.tsx); the timings and example names are made up, and each post that
// shows one says it is an illustration. Pure functions, so a paused frame, the server's render and a test agree.

export type Look = 'todo' | 'run' | 'done' | 'amber'
export type AnimationName = 'slices' | 'gate' | 'subagents'

// Each animation's loop, and the moment its still frame shows: with reduced motion, before the page's script runs,
// and in the image the feed and copies elsewhere get.
export const LOOPS: Record<AnimationName, { loop: number, still: number }> = {
  slices: { loop: 22, still: 13 },
  gate: { loop: 16, still: 12 },
  subagents: { loop: 18, still: 9 },
}

type Seg<T extends unknown[]> = [number, ...T]
// The last segment that has started by t; before the first, the first.
function at<T extends unknown[]>(segs: Seg<T>[], t: number): Seg<T> {
  let cur = segs[0]!
  for (const s of segs) if (t >= s[0]) cur = s
  return cur
}

// A sliced work item: the groundwork, three slices, then the join. A slice shows its plan tasks until it starts, then
// the node its child item is on, "needs you" in amber while it waits for a person, and "done". Midway the camera zooms
// into slice 1 to show it is a work item with its own chain (the slice body: implementation, then verification), and
// back out, as the overview video zooms into verification.
const IMPL: Seg<[Look]>[] = [[0, 'todo'], [0.4, 'run'], [3, 'done']]
const VERIF: Seg<[Look]>[] = [[0, 'todo'], [3, 'run'], [5, 'done']]
const SLICES: { plan: string, segs: Seg<[Look, string]>[] }[] = [
  { plan: 'tasks 2–4', segs: [[5.2, 'run', 'implementation'], [9, 'run', 'verification'], [10.6, 'done', 'done']] },
  { plan: 'tasks 5, 6', segs: [[5.55, 'run', 'implementation'], [11.2, 'run', 'verification'], [13.6, 'done', 'done']] },
  { plan: 'tasks 7–9', segs: [[5.9, 'run', 'implementation'], [12.4, 'amber', 'needs you'], [14.2, 'run', 'implementation'], [15, 'run', 'verification'], [16.4, 'done', 'done']] },
]
const JOIN: Seg<[Look, string]>[] = [[0, 'todo', ''], [16.8, 'run', 'slice 1 of 3'], [17.55, 'run', 'slice 2 of 3'], [18.3, 'run', 'slice 3 of 3'], [19, 'done', '']]

// The zoom into slice 1: in over 1 s, out over 1.2 s, eased in and out.
const ZOOM_IN = 6.2
const ZOOM_OUT = 10.9
const clamp01 = (x: number) => Math.min(1, Math.max(0, x))
const easeInOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2)
const draw = (t: number, from: number, length: number) => easeInOut(clamp01((t - from) / length))

export function slicesAt(t: number) {
  const slices = SLICES.map((s) => {
    const [, look, word] = t < s.segs[0]![0] ? [0, 'todo' as Look, s.plan] : at(s.segs, t)
    return { look, word }
  })
  const [, join, joinWord] = at(JOIN, t)
  // Slice 1's own chain, from the node its child is on.
  const first = slices[0]!
  const own = { impl: first.look === 'todo' ? 'todo' : first.word === 'implementation' ? 'run' : 'done', verif: first.word === 'verification' ? 'run' : first.look === 'done' ? 'done' : 'todo' } as { impl: Look, verif: Look }
  return { impl: at(IMPL, t)[1], verif: at(VERIF, t)[1], slices, join, joinWord, zoom: draw(t, ZOOM_IN, 1) * (1 - draw(t, ZOOM_OUT, 1.2)), own }
}

// The default chain's plan gate. Round 1: the reviewer checks first and sends the plan back. Round 2: it checks the
// revised plan and hands it to the person, ready for you; the gate then waits. It never approves (checks_first).
const REVIEWER: Seg<[Look, string, '' | 'bad' | 'ok']>[] = [
  [0, 'todo', 'checks first', ''], [0.4, 'run', 'checks first', ''], [3.2, 'done', 'reject', 'bad'],
  [6.4, 'todo', 'runs first when reached', ''], [7.6, 'run', 'checks first', ''], [10.6, 'done', 'ready for you', 'ok'],
]

export function gateAt(t: number) {
  const [, reviewer, badge, tone] = at(REVIEWER, t)
  const yours = t >= 10.6
  const rounds: { n: number, now: boolean, outcome: string, tone: 'run' | 'bad' }[] = [
    { n: 1, now: t < 6.4, outcome: t >= 3.2 ? 'changes requested · auto' : 'waiting', tone: t >= 3.2 ? 'bad' : 'run' },
  ]
  if (t >= 6.4) rounds.unshift({ n: 2, now: true, outcome: 'waiting', tone: 'run' })
  return { reviewer, badge, tone, yours, you: yours ? 'waiting' : 'decides after the agent', sentBack: t >= 3.2 && t < 6.4, rounds }
}

// An implement task that opens three sub-agents. The pane lists the running ones with what each is doing now, and
// folds the finished ones away until none is still running; the marks under the task's box follow them.
const START = 0.4
const AGENTS = [
  { from: 3, to: 10.5, name: 'Find every caller of the cache lookup', tools: ['Grep', 'Read', 'Grep', 'Read'], tok: 2600 },
  { from: 3.6, to: 8, name: 'Check the search tests for cache coverage', tools: ['Glob', 'Read', 'Read'], tok: 3100 },
  { from: 4.2, to: 13, name: 'Read how reindex records content hashes', tools: ['Grep', 'Read', 'Bash', 'Read'], tok: 2200 },
]

export const seconds = (s: number) => s < 60 ? `${Math.floor(s)}s` : `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, '0')}s`
const thousands = (n: number) => n < 1000 ? String(Math.round(n)) : `${(n / 1000).toFixed(1)}k`

export function subagentsAt(t: number) {
  const row = (a: typeof AGENTS[number]) => {
    const ran = Math.min(t, a.to) - a.from
    const uses = 1 + Math.floor(ran * 0.8)
    return {
      name: a.name, time: seconds(ran), tokens: `${thousands(4000 + ran * a.tok)} tokens`, uses: `${uses} tool use${uses === 1 ? '' : 's'}`,
      doing: `running ${a.tools[Math.floor(ran / 1.6) % a.tools.length]}`,
    }
  }
  const running = AGENTS.filter(a => t >= a.from && t < a.to).map(row)
  const finished = AGENTS.filter(a => t >= a.to).sort((a, b) => a.to - b.to).map(row)
  return {
    on: t >= START,
    ran: t >= START ? seconds(t - START) : '',
    running,
    finished,
    open: !running.length && finished.length > 0,
    marks: AGENTS.filter(a => t >= a.from).map(a => (t >= a.to ? 'done' : 'run') as Look),
  }
}
