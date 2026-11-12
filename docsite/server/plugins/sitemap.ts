// Docus writes each sitemap <loc> as a bare page path (/get-started), with no
// host and no base path, which a crawler cannot resolve. Prefix both.
//
// The marketing pages (site-pages.mjs) are not content pages, so Docus lists
// them in no sitemap: the build at the root of the domain adds them. The others
// redirect to it and do not list them.
export default defineNitroPlugin((nitroApp) => {
  const runtime = useRuntimeConfig()
  const base = runtime.sitemapBase as string
  const home = runtime.app.baseURL === '/'
  const pages = (runtime.public.sitePages as { path: string }[])
    .map(p => `  <url>\n    <loc>${base}${p.path}</loc>\n  </url>\n`).join('')
  nitroApp.hooks.hook('beforeResponse', (event, response) => {
    if (event.path.endsWith('/sitemap.xml') && typeof response.body === 'string') {
      response.body = response.body.replace(/<loc>\//g, `<loc>${base}/`)
      if (home) response.body = response.body.replace('</urlset>', `${pages}</urlset>`)
    }
  })
})
