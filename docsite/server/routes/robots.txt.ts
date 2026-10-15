// This build's robots.txt. A crawler reads robots.txt only at the domain root,
// so the latest build's is the one that steers; a frozen major's and the /next/
// build's copies steer nothing, but they should still not contradict the pages.
// Those builds keep their pages out of search with a noindex tag on each, so
// their robots.txt allows crawling (a crawler that may not fetch a page never
// sees the tag) and names no sitemap. Every other build points at its own.
export default defineEventHandler((event) => {
  const config = useRuntimeConfig(event)
  const { version } = config.public.docs
  const frozen = version !== '' && version !== 'latest'
  setHeader(event, 'content-type', 'text/plain; charset=utf-8')
  return [
    'User-agent: *',
    'Allow: /',
    ...(frozen ? [] : ['', `Sitemap: ${config.sitemapBase}/sitemap.xml`]),
    '',
  ].join('\n')
})
