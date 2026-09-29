// Docus writes each sitemap <loc> as a bare page path (/get-started), with no
// host and no /kraft/ base, which a crawler cannot resolve. Prefix both.
export default defineNitroPlugin((nitroApp) => {
  const base = useRuntimeConfig().sitemapBase as string
  nitroApp.hooks.hook('beforeResponse', (event, response) => {
    if (event.path.endsWith('/sitemap.xml') && typeof response.body === 'string') {
      response.body = response.body.replace(/<loc>\//g, `<loc>${base}/`)
    }
  })
})
