// The diagrams as text for raw/*.md and llms-full.txt: each kind of diagram
// through the tag parser (the stringified markup, quotes unescaped) and the
// node walker, one row per kind.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { diagramsToMarkdown, diagramsToNodes } from '../server/utils/diagramText.ts'

const tag = (name: string, props: Record<string, unknown>, caption: string) =>
  `<${name} ${Object.entries(props).map(([k, v]) => `:${k}="${JSON.stringify(v)}"`).join(' ')} caption="${caption}">\n\n\n\n</${name}>`

const rows: [string, string, Record<string, unknown>, string][] = [
  ['flow with a branch', 'diagram-flow',
    { stages: [{ name: 'verify', gate: 'you approve', loop: 'fix loop', branches: [{ when: 'fails', name: 'fix', back: true }] }, { name: 'land' }] },
    'Diagram: The chain.\n\n- verify (gate: you approve; repeats: fix loop)\n  - if fails: back to fix\n- land'],
  ['parallel flow groups', 'diagram-flow',
    { groups: [{ label: 'Over Tailscale', stages: [{ name: 'phone' }, { name: 'tailnet', note: 'encrypts' }] }], parallel: true },
    'Diagram: The chain.\n\n- Over Tailscale\n  - phone\n  - tailnet: encrypts'],
  ['graph edges', 'diagram-graph',
    { nodes: [{ name: 'active', edges: [{ to: 'paused' }, { label: 'fails', to: 'failed', dashed: true }] }] },
    'Diagram: The chain.\n\n- active\n  - → paused\n  - fails → failed (dashed)'],
  ['nest', 'diagram-nest',
    { parts: [{ name: 'repo', inside: [{ name: 'item', loop: 'fix loop' }] }] },
    'Diagram: The chain.\n\n- repo\n  - item (repeats: fix loop)'],
  ['sequence numbered, note under its message', 'diagram-sequence',
    { messages: [{ from: 'You', to: 'API', say: 'create' }, { note: 'later', over: 'API' }, { from: 'API', to: 'You', say: 'ok', reply: true }] },
    'Diagram: The chain.\n\n1. You → API: create\n   - note over API: later\n2. API → You: ok (reply)'],
]

for (const [id, name, props, expected] of rows) {
  test(`raw tag: ${id}`, () => {
    assert.equal(diagramsToMarkdown(`before\n\n${tag(name, props, 'The chain.')}\n\nafter`), `before\n\n${expected}\n\nafter`)
  })
}

test('raw tag: quotes inside a value, and a tag it cannot read, are kept', () => {
  const quoted = tag('diagram-flow', { stages: [{ name: 'say "hi"' }] }, 'He said "go".')
  assert.equal(diagramsToMarkdown(quoted), 'Diagram: He said "go".\n\n- say "hi"')
  const broken = '<diagram-flow :stages="[{" caption="x">\n</diagram-flow>'
  assert.equal(diagramsToMarkdown(broken), broken)
})

test('nodes: a diagram becomes a paragraph and a list in place', () => {
  const body: unknown[] = [['p', {}, 'a'], ['diagram-graph', { ':nodes': JSON.stringify([{ name: 'x', edges: [{ to: 'y' }] }]), caption: 'C' }], ['p', {}, 'b']]
  assert.equal(diagramsToNodes(body), 1)
  assert.deepEqual(body, [['p', {}, 'a'], ['p', {}, 'Diagram: C'], ['ul', {}, ['li', {}, 'x', ['ul', {}, ['li', {}, '→ y']]]], ['p', {}, 'b']])
})
