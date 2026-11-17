// The blog's RSS feed (blog-posts.mjs), at /blog/feed.xml. Each item carries
// the whole post as HTML in content:encoded, so a feed reader gets the post in
// full and a site that imports from the feed (dev.to) has the body to copy.
// Only the build at / prerenders it (nuxt.config.ts).
import { type FeedNode, feedHtml } from '../../utils/feedHtml'
const cdata = (s: string) => `<![CDATA[${s.replace(/]]>/g, ']]]]><![CDATA[>')}]]>`

export default defineEventHandler(async (event) => {
  // Only the build at / writes the feed, so its base is the bare origin.
  const origin = useRuntimeConfig(event).sitemapBase as string
  const posts = await queryCollection(event, 'blog').order('date', 'DESC').all()
  const items = posts.map((post) => {
    const link = `${origin}${post.path}`
    const body = (post.body as unknown as { value: FeedNode[] }).value.map(n => feedHtml(n, origin)).join('\n')
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
