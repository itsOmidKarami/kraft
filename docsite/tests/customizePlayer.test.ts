// The Make it yours demo's frames and view, on plain Node. The timers are the
// composable's and the layout is CSS; these pin the rules a visitor would see
// break: which frame a button lands on, where reduced motion starts, and what
// the file says at each beat.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { LAST, desktopView, jump, phoneFrame, phoneView, startFrame, yamlText } from '../app/utils/customizePlayer.ts'

test('a step button jumps to its first frame while playing and to its result while paused', () => {
  assert.deepEqual([0, 1, 2].map(i => jump(true, i)), [1, 4, 9])
  assert.deepEqual([0, 1, 2].map(i => jump(false, i)), [3, 8, 11])
})

test('the server and a visitor with motion start on frame 0; reduced motion opens on the review', () => {
  assert.equal(startFrame(false), 0)
  assert.equal(startFrame(true), 12)
  assert.equal(LAST, 12)
})

test('the phone shows each desktop edit as one frame, and the review last', () => {
  assert.deepEqual([0, 1, 3, 4, 8, 9, 11, 12].map(phoneFrame), [0, 1, 1, 2, 2, 3, 3, 4])
})

// APPLIED: 0 as filed, 1 the override, 2 the empty node, 3 its step and task, 4 its fix loop.
test('the file gains the override at APPLIED 1, and writes it after the add block', () => {
  const at1 = yamlText(1)
  assert.match(at1, /^overrides:\n {2}implementation\.main\.implement:\n {4}harness: codex$/m)
  assert.doesNotMatch(at1, /^add:/m)
  const full = yamlText(4)
  assert.ok(full.indexOf('add:') < full.indexOf('overrides:'))
})

test('steps: [] shows only while the node is empty', () => {
  assert.deepEqual([0, 1, 2, 3, 4].map(a => yamlText(a).includes('steps: []')), [false, false, true, false, false])
  assert.match(yamlText(3), /steps:\n {8}- id: step_1\n {10}tasks:\n {12}- id: lint\n {14}extends: lint/)
})

test('the final frame names the repair_lint task as the fix loop', () => {
  assert.match(yamlText(4), /fix_loop:\n {8}tasks:\n {10}- id: repair_lint\n {12}extends: repair_lint/)
  assert.doesNotMatch(yamlText(3), /repair_lint/)
})

test('the phone review frame lights every edited line, and an edit frame only its own', () => {
  const lit = (pf: number) => phoneView(pf).lines.filter(l => l.lit).map(l => l.text.trim())
  assert.equal(phoneView(4).state, '✓ RESOLVES · READY TO PUBLISH')
  assert.ok(lit(4).includes('overrides:') && lit(4).includes('- after: verification') && lit(4).includes('extends: repair_lint'))
  assert.ok(!lit(4).includes('id: with-lint'))
  assert.deepEqual(lit(1), ['overrides:', 'implementation.main.implement:', 'harness: codex'])
})

test('on the desktop, frame 3 shows only the override, lit; the review lights every edit', () => {
  const shown = (f: number) => desktopView(f).lines.filter(l => l.shown)
  assert.ok(shown(3).some(l => l.text === 'overrides:' && l.lit))
  assert.ok(!shown(3).some(l => l.text === 'add:'))
  const review = shown(LAST).filter(l => l.text !== '')
  assert.deepEqual(review.filter(l => !l.lit).map(l => l.text), ['id: with-lint', 'extends: default', 'description: The default chain, plus a lint node.'])
})
