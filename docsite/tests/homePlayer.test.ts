// The home page's demo, driven on a fake clock. These are design K's reference
// tests (play_test.js and live_test.js) ported to node:test, plus the guards the
// page needs: a click on a run step, a deferred transition, unmounting.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { STEPS, createPlayer, view } from '../app/utils/homePlayer.ts'
import type { Player, PlayerState, PlayerView } from '../app/utils/homePlayer.ts'

// A virtual clock: advance(ms) runs the due timers in time order.
function clock() {
  let now = 0
  let seq = 0
  const timers = new Map<number, { fn: () => void, at: number }>()
  return {
    timers: {
      set(fn: () => void, ms: number) {
        timers.set(++seq, { fn, at: now + (ms || 0) })
        return seq
      },
      clear(id: number) {
        timers.delete(id)
      },
    },
    pending: () => timers.size,
    advance(ms: number) {
      const end = now + ms
      for (;;) {
        let next: [number, { fn: () => void, at: number }] | null = null
        for (const entry of timers) if (entry[1].at <= end && (!next || entry[1].at < next[1].at)) next = entry
        if (!next) break
        timers.delete(next[0])
        now = next[1].at
        next[1].fn()
      }
      now = end
    },
  }
}

function setup(transition?: (apply: () => void, morph: boolean) => void) {
  const c = clock()
  const changes: PlayerState[] = []
  const player: Player = createPlayer({ timers: c.timers, onChange: s => changes.push(s), transition })
  const v = (): PlayerView => view(player.state())
  return { ...c, player, v, changes }
}

test('autoplay: no clicks at all', () => {
  const { player, v, advance } = setup()
  player.boot()
  assert.ok(v().idle && v().nodes.length === 8 && v().nodes[0]!.label === 'plan' && v().playing, 'starts idle at plan, playing')
  advance(2400)
  assert.ok(v().running && v().cap.title === 'An agent writes the plan.', 'starts on its own')
  advance(2200)
  assert.ok(v().atGate && v().cap.label === 'plan_approval' && v().autoHint && v().counting && v().calmR, 'waits at plan gate with a countdown')
  assert.equal(v().autoVerb, 'request changes', 'plan gate will be sent back')
  advance(6000)
  assert.ok(v().pressR && !v().calmR && !v().autoHint, 'shows Request changes being pressed')
  advance(700)
  assert.ok(v().running && v().note && v().cap.label === 'plan' && v().decisions === 1, 'plan runs again with the note')
  advance(2200)
  assert.ok(v().atGate && v().autoVerb === 'approve', 'second time it approves')
  advance(6000)
  assert.ok(v().pressA, 'shows Approve being pressed')
  advance(700)
  assert.ok(v().running && v().cap.label === 'implementation', 'approved plan the second time')
  advance(2200)
  assert.ok(v().verifying && !v().plainRun && v().v.tRun && v().v.round === 1 && v().v.jIdle, 'verification opens on its tests')
  advance(1100 * 2)
  assert.ok(v().v.rFind && v().v.loopOn && v().v.endFail && v().v.pIdle, 'round 1: review finds one, loop opens')
  advance(1100)
  assert.ok(v().v.pRun && v().v.round === 1, 'repair runs')
  advance(1100)
  assert.ok(v().v.pPass && v().v.tRun && v().v.rIdle && v().v.round === 2 && v().v.loopOff, 'round 2 measures again')
  advance(1100 * 2)
  assert.ok(v().v.rPass && v().v.endPass && v().v.jIdle && v().cap.label === 'verification', 'round 2 passes; judge never ran')
  advance(2400)
  assert.ok(v().atGate && v().cap.label === 'local_review' && !v().verifying, 'then local_review')
  let guard = 0
  while (!v().done && guard++ < 300) advance(100)
  assert.ok(v().done && v().decWord === 'four', `done with four decisions, got ${v().decWord}`)
  assert.ok(v().nodes.every(n => n.runDone || n.gateDone), 'all nodes done at the end')
  advance(5000)
  assert.ok(v().idle, 'loops back to idle')
  advance(2400)
  assert.ok(v().running && v().decisions === 0, 'second loop resets')
})

test('a click hands control to the visitor; Pause and Play on its own', () => {
  const { player, v, advance } = setup()
  player.boot()
  advance(2400 + 2200)
  assert.ok(v().atGate && v().cap.label === 'plan_approval', 'at plan gate')
  player.approve()
  advance(0)
  assert.ok(v().running && v().stopped && !v().playing, 'click takes over')
  advance(2200 + 1100 * 6 + 2400)
  assert.ok(v().atGate && v().cap.label === 'local_review' && !v().autoHint && !v().counting, 'gate waits, no countdown')
  advance(60000)
  assert.ok(v().atGate && v().cap.label === 'local_review', 'still waiting a minute later')
  player.reject()
  advance(0)
  assert.ok(v().running && v().cap.label === 'implementation' && v().decisions === 2 && v().noteFrom === 'local_review', 'manual reject counts and goes back')

  // Play on its own resumes; Pause freezes even the verification frames.
  player.toggle()
  advance(2200)
  assert.ok(v().verifying && v().playing, 'play resumes')
  player.toggle()
  advance(60000)
  assert.ok(v().verifying && v().v.tRun && v().stopped, 'pause freezes')
  player.toggle()
  advance(1100)
  assert.ok(v().v.rRun, 'unpause continues')
  player.dispose()
})

test('Request changes goes where default.yaml reject_to says, carrying the gate note', () => {
  const targets: Record<string, string> = { plan_approval: 'plan', local_review: 'implementation', final_review: 'implementation' }
  for (const [gate, back] of Object.entries(targets)) {
    const { player, v, advance } = setup()
    // Put the player at the gate with the visitor in control: play to it, then take over.
    player.boot()
    advance(2400)
    while (v().cap.label !== gate || !v().atGate) {
      if (v().atGate) player.approve()
      advance(100)
    }
    player.reject()
    advance(0)
    assert.ok(v().running && v().cap.label === back, `${gate} should go back to ${back}, went to ${v().cap.label}`)
    assert.ok(v().note && v().noteFrom === gate && v().noteText.length > 10, `${gate} carries its note`)
    player.dispose()
  }
})

test('the stamp on a just-approved gate, and the receipt at merge', () => {
  const { player, v, advance } = setup()
  player.boot()
  const gi = (label: string) => v().nodes.findIndex(n => n.label === label)
  let g = 0
  while (!(v().running && v().cap.label === 'implementation') && g++ < 400) advance(100)
  const pa = v().nodes[gi('plan_approval')]!
  assert.ok(pa.gateJust && !pa.gateDoneQuiet && pa.gateDone, 'plan_approval stamps right after approval')
  advance(2200)
  assert.ok(!v().nodes[gi('plan_approval')]!.gateJust && v().nodes[gi('plan_approval')]!.gateDoneQuiet, 'stamp is gone once the next step starts')
  for (const n of v().nodes) assert.ok([n.gateDoneQuiet, n.gateJust].filter(Boolean).length <= 1, 'one done variant per gate')
  g = 0
  while (!v().done && g++ < 800) advance(100)
  const log = v().log.map(d => `${d.gate}:${d.approved ? 'ok' : 'back'}`)
  assert.deepEqual(log, ['plan_approval:back', 'plan_approval:ok', 'local_review:ok', 'final_review:ok'], 'receipt')
  assert.ok(v().log[0]!.note.length > 10, 'sent-back row carries its note')
  advance(5000 + 2400)
  assert.ok(v().running && v().log.length === 0, 'next run starts with an empty receipt')
})

test('approve on a run step is ignored', () => {
  const { player, v, advance } = setup()
  player.boot()
  advance(2400)
  assert.ok(v().running && v().cap.label === 'plan', 'on plan, a run step')
  const before = { step: player.state().step, decisions: player.state().decisions }
  player.approve()
  player.reject()
  advance(0)
  assert.equal(player.state().step, before.step)
  assert.equal(player.state().decisions, before.decisions)
  assert.ok(v().playing, 'the demo is still on its own')
})

test('a double click on Approve decides the gate once', () => {
  const { player, v, advance } = setup()
  player.boot()
  advance(2400 + 2200)
  assert.ok(v().atGate && v().cap.label === 'plan_approval')
  player.approve()
  player.approve()
  advance(0)
  assert.ok(v().running && v().cap.label === 'implementation' && v().decisions === 1, 'one decision, the next step')
})

test('a transition defers the tick, and morphs only into or out of verification', () => {
  const held: { apply: () => void, morph: boolean }[] = []
  const { player, v, advance, pending } = setup((apply, morph) => held.push({ apply, morph }))
  player.boot()
  // Nothing has changed state yet, so nothing is held until the first go().
  advance(0)
  advance(2400)
  assert.equal(held.length, 1, 'the start is held for the transition')
  assert.equal(held[0]!.morph, false, 'starting is not a morph')
  assert.equal(pending(), 0, 'no timer runs while it is held')
  held[0]!.apply()
  assert.ok(v().running && v().cap.label === 'plan')
  assert.equal(pending(), 1, 'the next tick is scheduled once it applies')

  // Walk to the step before verification, applying each change as it arrives.
  const apply = () => {
    while (held.length) held.shift()!.apply()
  }
  let g = 0
  while (!(v().running && v().cap.label === 'implementation') && g++ < 400) {
    advance(100)
    apply()
  }
  advance(2200)
  assert.equal(held.length, 1, 'entering verification is held')
  assert.equal(held[0]!.morph, true, 'entering verification morphs')
  advance(10000)
  assert.equal(held.length, 1, 'no tick runs while the transition is held')
  assert.ok(v().running && v().cap.label === 'implementation', 'still on implementation')
  held.shift()!.apply()
  assert.ok(v().verifying)
  assert.equal(pending(), 1, 'ticking again')

  // Inside verification the frames are not morphs; leaving it is.
  advance(1100)
  assert.equal(held.length, 1)
  assert.equal(held[0]!.morph, false, 'a frame is not a morph')
  held.shift()!.apply()
  while (!held.length || held[0]!.morph === false) {
    advance(100)
    if (held.length && held[0]!.morph === false) held.shift()!.apply()
  }
  assert.equal(held[0]!.morph, true, 'leaving verification morphs')
  player.dispose()
})

test('a transition that applies after dispose does nothing', () => {
  const held: (() => void)[] = []
  const { player, advance, changes, pending } = setup(apply => held.push(apply))
  player.boot()
  advance(2400)
  assert.equal(held.length, 1)
  player.dispose()
  held[0]!()
  assert.equal(changes.length, 0, 'no state change after dispose')
  assert.equal(pending(), 0)
})

test('dispose clears timers', () => {
  const { player, v, advance, pending } = setup()
  player.boot()
  advance(2400 + 2200 + 1000)
  assert.ok(pending() > 0, 'a timer is waiting')
  player.dispose()
  assert.equal(pending(), 0, 'unmount clears timers')
  const before = player.state()
  advance(60000)
  assert.equal(player.state(), before, 'nothing changes after dispose')
  assert.ok(v().atGate)
})

test('the chain is the default chain filed with a spec', () => {
  assert.deepEqual(STEPS.map(x => x.label), ['plan', 'plan_approval', 'implementation', 'verification', 'local_review', 'draft_merge_request', 'final_review', 'merge'])
})
