// Which build draws the home page, and which answers its root with a redirect.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isHomeBuild } from '../app/utils/home.ts'

const cases: [string, string, boolean][] = [
  ['the root build has the home', '/', true],
  ['main\'s build at /next/ redirects', '/next/', false],
  ['a frozen major at /v1/ redirects', '/v1/', false],
  ['a project page build at /kraft/ redirects', '/kraft/', false],
]

for (const [name, base, expected] of cases) {
  test(name, () => assert.equal(isHomeBuild(base), expected))
}
