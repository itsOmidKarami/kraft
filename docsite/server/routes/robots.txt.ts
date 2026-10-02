// This build's robots.txt. A crawler reads robots.txt only at the domain root,
// which belongs to another repo, so this file under /kraft/ or /kraft/next/
// steers nothing; it should still not contradict the pages. The /next/ build
// keeps its pages out of search with a noindex tag on each, so its robots.txt
// allows crawling (a crawler that may not fetch a page never sees the tag) and
// names no sitemap. Every other build points at its own.
export default defineEventHandler((event) => {
  const config = useRuntimeConfig(event)
  const next = config.public.docs.channel === 'next'
  setHeader(event, 'content-type', 'text/plain; charset=utf-8')
  return [
    'User-agent: *',
    'Allow: /',
    ...(next ? [] : ['', `Sitemap: ${config.sitemapBase}/sitemap.xml`]),
    '',
  ].join('\n')
})
