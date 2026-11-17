import { at, type Look, type Meta, type Seg } from './shared.ts'

export const meta: Meta = { loop: 16, still: 12, height: 360 }

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
