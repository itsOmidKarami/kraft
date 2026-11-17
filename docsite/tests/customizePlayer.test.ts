// The Make it yours demo's frames and view, on plain Node. The timers are the
// composable's and the layout is CSS; these pin the rules a visitor would see
// break: which frame a button lands on, where reduced motion starts, what the
// file says at each beat, that default's change never lands in with-lint.yaml,
// and when the run holds.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DUR, LAST, desktopView, holds, jump, phoneFrame, phoneView, startFrame, yamlText } from '../app/utils/customizePlayer.ts'

test('the loop runs 20 to 25 seconds', () => {
  const total = DUR.reduce((a, b) => a + b, 0)
  assert.ok(total >= 20000 && total <= 25000, `${total}ms`)
})

test('a step button jumps to its first frame while playing and to its result while paused', () => {
  assert.deepEqual([0, 1, 2].map(i => jump(true, i)), [1, 3, 6])
  assert.deepEqual([0, 1, 2].map(i => jump(false, i)), [2, 5, 7])
})

test('the server and a visitor with motion start on frame 0; reduced motion opens on the review', () => {
  assert.equal(startFrame(false), 0)
  assert.equal(startFrame(true), 8)
  assert.equal(LAST, 8)
})

test('the phone shows each desktop step as one frame, and the review last', () => {
  assert.deepEqual([0, 1, 2, 3, 5, 6, 7, 8].map(phoneFrame), [0, 1, 1, 2, 2, 3, 3, 4])
})

// APPLIED: 0 as filed, 1 the override, 2 the empty node, 3 its step and task.
test('the file gains the override at APPLIED 1, and writes it after the add block', () => {
  const at1 = yamlText(1)
  assert.match(at1, /^overrides:\n {2}implementation\.main\.implement:\n {4}harness: codex$/m)
  assert.doesNotMatch(at1, /^add:/m)
  const full = yamlText(3)
  assert.ok(full.indexOf('add:') < full.indexOf('overrides:'))
  assert.doesNotMatch(full, /fix_loop|repair_lint/)
})

test('steps: [] shows only while the node is empty', () => {
  assert.deepEqual([0, 1, 2, 3].map(a => yamlText(a).includes('steps: []')), [false, false, true, false])
  assert.match(yamlText(3), /steps:\n {8}- id: step_1\n {10}tasks:\n {12}- id: lint\n {14}extends: lint/)
})

test('default changes: the panel shows default.yaml with its edit lit, then with-lint.yaml as it was', () => {
  const before = desktopView(5)
  const upstream = desktopView(6)
  const after = desktopView(7)
  assert.equal(upstream.path, 'chains/default.yaml')
  assert.deepEqual(upstream.lines.filter(l => l.lit).map(l => l.text.trim()), ['overrides:', 'local_review:', 'message: Approve the work brief, then open the draft merge request.'])
  assert.equal(after.path, 'chains/with-lint.yaml')
  const file = (v: ReturnType<typeof desktopView>) => v.lines.filter(l => l.shown).map(l => l.text)
  assert.deepEqual(file(after), file(before))
  assert.ok(!file(after).some(l => l.includes('local_review')))
  assert.ok(!after.lines.some(l => l.lit))
})

test('default changes: local_review is selected with the new value from default, and the review stays at two changes', () => {
  const v = desktopView(6)
  assert.ok(v.nodes.find(n => n.id === 'local_review')!.sel)
  assert.equal(v.card.note, 'Inherited from default. Changes here are saved as this chain’s overrides.')
  assert.deepEqual(v.card.rows.map(r => [r.k, r.v, r.chipText]), [['message', 'Approve the work brief, then open the draft merge request.', 'default']])
  // implementation and lint keep their own marks; local_review gets none.
  assert.deepEqual(v.nodes.map(n => n.meta), ['overridden here', '·', 'added here', '·', '·'])
  const review = desktopView(LAST)
  assert.equal(review.card.title, 'Draft · 2 changes')
  assert.deepEqual(review.card.rows.map(r => r.k), ['implementation.main.implement', 'lint'])
  assert.equal(phoneView(3).path, 'chains/default.yaml')
  assert.equal(phoneView(4).path, 'chains/with-lint.yaml')
})

test('the review lights every edit of with-lint.yaml and nothing else', () => {
  const review = desktopView(LAST).lines.filter(l => l.shown && l.text !== '')
  assert.deepEqual(review.filter(l => !l.lit).map(l => l.text), ['id: with-lint', 'extends: default', 'description: The default chain, plus a lint node.'])
  const lit = (pf: number) => phoneView(pf).lines.filter(l => l.lit).map(l => l.text.trim())
  assert.equal(phoneView(4).state, '✓ RESOLVES · READY TO PUBLISH')
  assert.deepEqual(lit(1), ['overrides:', 'implementation.main.implement:', 'harness: codex'])
})

test('hover holds; keyboard focus holds except on the demo’s own controls; a mouse click never does', () => {
  assert.equal(holds({ hovered: true, focusVisible: false, onControl: false }), true)
  assert.equal(holds({ hovered: false, focusVisible: true, onControl: false }), true)
  // Tabbing onto a step button or Play, or clicking one, leaves the run going.
  assert.equal(holds({ hovered: false, focusVisible: true, onControl: true }), false)
  assert.equal(holds({ hovered: false, focusVisible: false, onControl: true }), false)
})
