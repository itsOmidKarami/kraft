import { at, draw, type Look, type Meta, type Seg } from './shared.ts'

export const meta: Meta = { loop: 22, still: 13, height: 360 }

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
