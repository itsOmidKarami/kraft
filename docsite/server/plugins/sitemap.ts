// Docus writes each sitemap <loc> as a bare page path (/get-started), with no
// host and no base path, which a crawler cannot resolve. Prefix both.
//
// The home page (app/pages/index.vue) is not a content page, so Docus lists
// it in no sitemap: the build at the root of the domain adds it. The others
// redirect to it and are not listed.
export default defineNitroPlugin((nitroApp) => {
  const runtime = useRuntimeConfig()
  const base = runtime.sitemapBase as string
  const home = runtime.app.baseURL === '/'
  nitroApp.hooks.hook('beforeResponse', (event, response) => {
    if (event.path.endsWith('/sitemap.xml') && typeof response.body === 'string') {
      response.body = response.body.replace(/<loc>\//g, `<loc>${base}/`)
      if (home) response.body = response.body.replace('</urlset>', `  <url>\n    <loc>${base}/</loc>\n  </url>\n</urlset>`)
    }
  })
})
