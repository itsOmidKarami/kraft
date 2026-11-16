// Which blog posts are drafts (blog-posts.mjs): a draft is left out of the
// collection, the prerender list, the sitemap and the feed, so it must be
// read from the front matter alone.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isDraft } from '../blog-posts.mjs'

const cases: [string, string, boolean][] = [
  ['draft: true in the front matter', '---\ntitle: A\ndraft: true\n---\nBody', true],
  ['trailing spaces after true', '---\ndraft: true  \ntitle: A\n---\n', true],
  ['draft: false', '---\ntitle: A\ndraft: false\n---\n', false],
  ['no draft line', '---\ntitle: A\n---\n', false],
  ['a draft line in the body only', '---\ntitle: A\n---\ndraft: true\n', false],
  ['no front matter', 'draft: true\n', false],
]

for (const [name, text, draft] of cases) {
  test(`isDraft: ${name}`, () => assert.equal(isDraft(text), draft))
}
