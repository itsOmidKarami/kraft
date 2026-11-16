// The blog's RSS feed (blog-posts.mjs), at /blog/feed.xml. Each item carries
// the whole post as HTML in content:encoded, so a feed reader gets the post in
// full and a site that imports from the feed (dev.to) has the body to copy.
// Only the build at / prerenders it (nuxt.config.ts).
type Node = string | [string, Record<string, unknown>, ...Node[]]

const VOID = new Set(['img', 'br', 'hr'])
const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

// The post body as plain HTML: the minimark tree Nuxt Content stores, with
// site links made absolute and a code block as its source text, since a feed
// reader has none of the site's styles.
export function toHtml(node: Node, origin: string): string {
  if (typeof node === 'string') return escape(node)
  const [tag, props, ...children] = node
  // The code highlighter's colours: the site's, not the reader's.
  if (tag === 'style' || tag === 'script') return ''
  // The site's video player is a script; a feed reader gets a link to the video.
  if (tag === 'site-video') return `<p><a href="https://www.youtube.com/watch?v=${escape(String(props.id))}">${escape(String(props.title))}</a></p>`
  if (tag === 'pre') return `<pre><code>${escape(String(props.code ?? ''))}</code></pre>`
  const attrs = Object.entries(props)
    .filter(([key, value]) => ['href', 'src', 'alt', 'title'].includes(key) && typeof value === 'string')
    .map(([key, value]) => ` ${key}="${escape((value as string).startsWith('/') ? `${origin}${value}` : value as string)}"`)
    .join('')
  if (VOID.has(tag)) return `<${tag}${attrs}>`
  return `<${tag}${attrs}>${children.map(c => toHtml(c, origin)).join('')}</${tag}>`
}

const cdata = (s: string) => `<![CDATA[${s.replace(/]]>/g, ']]]]><![CDATA[>')}]]>`

export default defineEventHandler(async (event) => {
  // Only the build at / writes the feed, so its base is the bare origin.
  const origin = useRuntimeConfig(event).sitemapBase as string
  const posts = await queryCollection(event, 'blog').order('date', 'DESC').all()
  const items = posts.map((post) => {
    const link = `${origin}${post.path}`
    const body = (post.body as unknown as { value: Node[] }).value.map(n => toHtml(n, origin)).join('\n')
    return `    <item>
      <title>${escape(post.title)}</title>
      <link>${link}</link>
      <guid isPermaLink="true">${link}</guid>
      <pubDate>${new Date(`${post.date}T00:00:00Z`).toUTCString()}</pubDate>
      <dc:creator>${escape(post.author)}</dc:creator>
${post.tags.map(t => `      <category>${escape(t)}</category>\n`).join('')}      <description>${escape(post.description)}</description>
      <content:encoded>${cdata(body)}</content:encoded>
    </item>`
  })
  setHeader(event, 'content-type', 'application/rss+xml; charset=utf-8')
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Kraft blog</title>
    <link>${origin}/blog</link>
    <description>Writing from the Kraft project.</description>
    <language>en</language>
    <atom:link href="${origin}/blog/feed.xml" rel="self" type="application/rss+xml"/>
${items.join('\n')}
  </channel>
</rss>
`
})
