import { at, type Look, type Meta, type Seg } from './shared.ts'

export const meta: Meta = { loop: 16, still: 12, height: 360 }

// The default chain's plan gate, as the gate view draws it (frontend/src/ng/item/gateView.ts). The gate is pending,
// and so "waiting" on the person, from the moment it is reached: its reviewer runs inside that wait. Round 1: the
// reviewer checks first and sends the plan back, so the gate stops pending and reads the round's outcome. Round 2: the
// gate is reached again, the reviewer checks the revised plan and marks it ready for you. It never approves
// (checks_first). The badge shows the open round's verdict once there is one, else the reviewer's mode; the plan's
// document shows only while the gate is pending; History, once a round has ended.
const ROUND_2 = 6.4
const REVIEWER: Seg<[Look, string, '' | 'bad' | 'ok']>[] = [
  [0, 'todo', 'checks first', ''], [0.4, 'run', 'checks first', ''], [3.2, 'done', 'reject', 'bad'],
  [ROUND_2, 'done', 'checks first', ''], [7.6, 'run', 'checks first', ''], [10.6, 'done', 'ready for you', 'ok'],
]

export function gateAt(t: number) {
  const [, reviewer, badge, tone] = at(REVIEWER, t)
  const sentBack = t >= 3.2 && t < ROUND_2
  const pending = !sentBack
  const rounds: { n: number, now: boolean, outcome: string, tone: 'run' | 'bad' }[] = t < 3.2
    ? []
    : [{ n: 1, now: t < ROUND_2, outcome: 'changes requested · auto', tone: 'bad' }]
  if (t >= ROUND_2) rounds.unshift({ n: 2, now: true, outcome: 'waiting', tone: 'run' })
  return { reviewer, badge, tone, pending, you: pending ? 'waiting' : 'changes requested · auto', sentBack, rounds }
}
