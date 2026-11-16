// The blog's posts: docsite/blog/*.md, one file per post, its name the slug
// (/blog/<name>). They are site code, not content: dev/build_docs_site.sh
// takes docsite/content from a release tag but everything else in docsite/
// from this checkout, so the build at / always has main's posts, and a post
// is live on the docs deploy that follows its merge, with no release.
//
// A post with `draft: true` in its front matter is left out of everything:
// the collection (content.config.ts), so it is not even in the data a page
// downloads, the prerender list, the sitemap and the feed. KRAFT_BLOG_DRAFTS=1
// keeps drafts in, to preview one with `just docs` or `npm run dev`.
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

export const blogDir = fileURLToPath(new URL('./blog', import.meta.url))

const showDrafts = process.env.KRAFT_BLOG_DRAFTS === '1'

// Only the front matter's own `draft:` line counts, not one in the body.
export function isDraft(text) {
  const front = /^---\n([\s\S]*?)\n---/.exec(text)
  return !!front && /^draft:\s*true\s*$/m.test(front[1])
}

// Every post file, relative to blogDir, and whether it is a draft.
function posts() {
  if (!existsSync(blogDir)) return []
  return readdirSync(blogDir)
    .filter(name => name.endsWith('.md') && name !== 'README.md')
    .map(name => ({ name, draft: isDraft(readFileSync(`${blogDir}/${name}`, 'utf8')) }))
}

// The files the collection leaves out: every draft, unless drafts are shown.
export const draftFiles = showDrafts ? [] : posts().filter(p => p.draft).map(p => p.name)

// The routes the build writes: the index, the feed and each published post.
export const blogRoutes = [
  '/blog',
  '/blog/feed.xml',
  ...posts().filter(p => showDrafts || !p.draft).map(p => `/blog/${p.name.replace(/\.md$/, '')}`),
]
