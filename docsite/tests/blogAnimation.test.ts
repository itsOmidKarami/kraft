// The blog's animations of the item page (app/utils/blogAnimation/*.ts): what each shows at a moment, and the rules of
// the app they must not break while they play.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { gateAt, meta as gate } from '../app/utils/blogAnimation/gate.ts'
import { seconds, thousands } from '../app/utils/blogAnimation/shared.ts'
import { meta as slices, slicesAt } from '../app/utils/blogAnimation/slices.ts'
import { meta as subagents, subagentsAt } from '../app/utils/blogAnimation/subagents.ts'

const LOOPS = { slices, gate, subagents }

// Every moment of a loop, a tenth of a second apart.
const moments = (loop: number) => Array.from({ length: loop * 10 }, (_, i) => i / 10)

test('each still frame shows the animation\'s point', () => {
  const s = slicesAt(LOOPS.slices.still)
  assert.deepEqual(s.slices.map(x => x.look), ['done', 'run', 'amber'], 'one slice needs you while another runs')
  assert.equal(s.zoom, 0, 'the still is the whole group, not the zoom')
  assert.equal(s.slices[2]!.word, 'needs you')
  const g = gateAt(LOOPS.gate.still)
  assert.equal(g.badge, 'ready for you')
  assert.deepEqual(g.rounds.map(r => r.outcome), ['waiting', 'changes requested · auto'])
  const a = subagentsAt(LOOPS.subagents.still)
  assert.equal(a.running.length, 2)
  assert.equal(a.finished.length, 1)
  assert.equal(a.open, false, 'finished sub-agents stay folded while others run')
})

test('times and token counts read as the app writes them', () => {
  assert.deepEqual([seconds(45.9), seconds(60), seconds(125)], ['45s', '1m', '2m'])
  assert.deepEqual([thousands(950), thousands(4000), thousands(19_640)], ['950', '4k', '19.6k'])
})

test('a slice shows its plan tasks until it starts, and the join counts the slices it replays', () => {
  assert.deepEqual(slicesAt(0).slices.map(x => x.word), ['tasks 2–4', 'tasks 5, 6', 'tasks 7–9'])
  assert.deepEqual([17, 17.6, 18.4].map(t => slicesAt(t).joinWord), ['slice 1 of 3', 'slice 2 of 3', 'slice 3 of 3'])
  assert.equal(slicesAt(8).zoom, 1, 'zoomed into slice 1 while it runs')
  assert.deepEqual(slicesAt(8).own, { impl: 'run', verif: 'todo' })
  assert.deepEqual(slicesAt(10).own, { impl: 'done', verif: 'run' })
  assert.equal(slicesAt(12.5).zoom, 0, 'back out before slice 3 needs you')
  for (const t of moments(LOOPS.slices.loop)) {
    const s = slicesAt(t)
    if (s.join !== 'todo') assert.ok(s.slices.every(x => x.look === 'done'), `the join waits for every slice (t=${t})`)
    if (s.slices.some(x => x.look !== 'todo')) assert.equal(s.verif, 'done', `slices start from tested groundwork (t=${t})`)
  }
})

test('the gate waits for you from the moment it is reached, and its reviewer never approves', () => {
  for (const t of moments(LOOPS.gate.loop)) {
    const g = gateAt(t)
    assert.ok(['checks first', 'reject', 'ready for you'].includes(g.badge), `badge "${g.badge}" (t=${t})`)
    // gateView.ts youSub: "waiting" while pending, else the newest round's outcome.
    assert.equal(g.you, g.pending ? 'waiting' : 'changes requested · auto', `t=${t}`)
    assert.equal(g.sentBack, !g.pending, `t=${t}`)
    if (g.reviewer === 'run') assert.ok(g.pending, `the reviewer runs inside the wait (t=${t})`)
    // GatePane.tsx: History once some round has ended.
    assert.equal(g.rounds.length > 0, t >= 3.2, `t=${t}`)
  }
  assert.equal(gateAt(1).badge, 'checks first')
  assert.equal(gateAt(5).badge, 'reject')
})

test('sub-agents run before they finish, and the marks follow them', () => {
  for (const t of moments(LOOPS.subagents.loop)) {
    const a = subagentsAt(t)
    assert.equal(a.marks.filter(m => m === 'run').length, a.running.length, `t=${t}`)
    assert.equal(a.marks.filter(m => m === 'done').length, a.finished.length, `t=${t}`)
    assert.equal(a.open, !a.running.length && a.finished.length > 0, `t=${t}`)
  }
})
