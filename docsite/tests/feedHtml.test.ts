// A blog post's body in the feed (server/utils/feedHtml.ts): the minimark tree
// Nuxt Content stores, as HTML a feed reader or an importer can use.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { type FeedNode, feedHtml } from '../server/utils/feedHtml.ts'

const origin = 'https://kraft-sdlc.dev'
const rows: [string, FeedNode, string][] = [
  ['text is escaped', ['p', {}, 'a < b & "c"'], '<p>a &lt; b &amp; &quot;c&quot;</p>'],
  ['a site link is made absolute', ['a', { href: '/why' }, 'Why'], '<a href="https://kraft-sdlc.dev/why">Why</a>'],
  ['an outside link is kept', ['a', { href: 'https://example.com', rel: ['nofollow'] }, 'x'], '<a href="https://example.com">x</a>'],
  ['an image keeps its alt', ['img', { src: '/assets/a.png', alt: 'A' }], '<img src="https://kraft-sdlc.dev/assets/a.png" alt="A">'],
  ['a code block is its source text', ['pre', { code: 'a <b>\n', language: 'bash' }, ['code', {}, ['span', {}, 'a']]], '<pre><code>a &lt;b&gt;\n</code></pre>'],
  ['the highlighter style is dropped', ['style', {}, 'html{}'], ''],
  ['the video is a link', ['site-video', { id: 'abc', title: 'T' }], '<p><a href="https://www.youtube.com/watch?v=abc">T</a></p>'],
  ['an animation is its still frame', ['blog-animation', { name: 'gate', still: '/blog/p/gate.png', alt: 'A "gate"' }], '<p><img src="https://kraft-sdlc.dev/blog/p/gate.png" alt="A &quot;gate&quot;"><br><em>Illustration: Kraft\'s item page, drawn with a made-up run.</em></p>'],
  ['an animation with no still is left out', ['blog-animation', { name: 'gate', alt: 'A' }], ''],
  ['a callout is unwrapped', ['note', {}, ['p', {}, 'x']], '<p>x</p>'],
]

for (const [name, node, html] of rows) {
  test(`feedHtml: ${name}`, () => assert.equal(feedHtml(node, origin), html))
}
