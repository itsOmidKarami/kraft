// The list of a build's pages that nuxt.config.ts seeds the prerenderer with,
// and the check that a build wrote every one of them.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { contentRoutes, pagesMissingFrom, routeOf } from '../content-routes.mjs'

const content = fileURLToPath(new URL('../content', import.meta.url))
const repo = fileURLToPath(new URL('../..', import.meta.url))

const routes: [string, string, string][] = [
  ['number prefixes and .md are dropped', '1.get-started/0.why-kraft.md', '/get-started/why-kraft'],
  ['a two-digit prefix too', '4.troubleshooting/01.failed-tasks-and-fix-loops.md', '/troubleshooting/failed-tasks-and-fix-loops'],
  ['an index.md is its folder', '2.concepts/index.md', '/concepts'],
  ['a nested index', '5.reference/03.configuration/02.repos/index.md', '/reference/configuration/repos'],
  ['a name with no prefix is kept', 'guides/vscode.md', '/guides/vscode'],
  ['a digit inside a name is kept', '3.guides/4.run/upgrade-from-1-4.md', '/guides/run/upgrade-from-1-4'],
  ['a root index.md is the root', 'index.md', '/'],
]
for (const [name, file, expected] of routes) {
  test(name, () => assert.equal(routeOf(file), expected))
}

test('the routes agree with route() in dev/check_docs_redirects.py for every page', () => {
  const ours = contentRoutes(content)
  const theirs = execFileSync('python3', ['-I', '-c', [
    'import sys',
    `sys.path.insert(0, ${JSON.stringify(`${repo}/dev`)})`,
    'import check_docs_redirects as c',
    'print("\\n".join(sorted(c.route(p.relative_to(c.CONTENT)) for p in c.CONTENT.rglob("*.md"))))',
  ].join('\n')], { encoding: 'utf8' }).trim().split('\n')
  assert.deepEqual(ours, theirs)
})

test('every page of the content is listed once, and no moved-pages map or navigation file', () => {
  const ours = contentRoutes(content)
  assert.ok(ours.length > 100, `only ${ours.length} routes`)
  assert.equal(new Set(ours).size, ours.length)
  assert.ok(ours.every(route => /^\/[a-z0-9/_-]*$/.test(route) && !route.includes('redirects') && !route.includes('navigation')))
})

test('a folder with no content has no routes', () => assert.deepEqual(contentRoutes(join(tmpdir(), 'no-such-content-folder')), []))

test('a build that wrote only some of the pages names the rest', () => {
  const site = mkdtempSync(join(tmpdir(), 'content-routes-'))
  try {
    mkdirSync(join(site, 'guides'), { recursive: true })
    mkdirSync(join(site, 'concepts'))
    writeFileSync(join(site, 'index.html'), '')
    writeFileSync(join(site, 'guides.html'), '')
    writeFileSync(join(site, 'guides', 'vscode.html'), '')
    writeFileSync(join(site, 'concepts', 'index.html'), '')
    assert.deepEqual(
      pagesMissingFrom(site, ['/', '/guides', '/guides/vscode', '/concepts', '/get-started', '/guides/run/restart-kraft']),
      ['/get-started', '/guides/run/restart-kraft'],
    )
    // A folder with no page in it (only _payload.json) is not the page.
    mkdirSync(join(site, 'get-started'))
    writeFileSync(join(site, 'get-started', '_payload.json'), '{}')
    assert.deepEqual(pagesMissingFrom(site, ['/get-started']), ['/get-started'])
  }
  finally {
    rmSync(site, { recursive: true })
  }
})
