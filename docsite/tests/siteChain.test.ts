// How it works lists the default chain's nodes and marks its gates; this holds
// that list to the chain Kraft ships.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { DEFAULT_CHAIN_NODES } from '../app/utils/siteChain.ts'

// The chain's top-level nodes: `  - id: x`, then its `kind:` four spaces in.
const yaml = readFileSync(new URL('../../config/chains/default.yaml', import.meta.url), 'utf8')
const shipped = [...yaml.matchAll(/^ {2}- id: (\S+)\n((?: {4}.*\n|\n)*)/gm)]
  .map(([, id, body]) => ({ id, gate: /^ {4}kind: gate$/m.test(body!) }))

test('the page lists every node of the shipped chain, in order', () => {
  assert.deepEqual(DEFAULT_CHAIN_NODES.map(n => n.id), shipped.map(n => n.id))
})

test('the page marks exactly the shipped gates', () => {
  assert.deepEqual(DEFAULT_CHAIN_NODES.map(n => !!n.gate), shipped.map(n => n.gate))
})
