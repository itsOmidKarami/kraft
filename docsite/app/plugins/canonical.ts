// Docus builds the canonical link and og:url as site.url + route.path, and
// route.path has no /kraft/ base, so every page pointed at a 404 on the bare
// origin. site.url must stay the bare origin (see nuxt.config.ts), so put the
// base back here instead.
export default defineNuxtPlugin(() => {
  const base = useRuntimeConfig().app.baseURL.replace(/\/$/, '')
  const origin = useSiteConfig().url.replace(/\/$/, '')
  // The home page comes through as the bare origin, with no trailing slash.
  const fix = (url: unknown) => {
    if (url === origin) return `${origin}${base}/`
    return typeof url === 'string' && url.startsWith(`${origin}/`) && !url.startsWith(`${origin}${base}/`)
      ? `${origin}${base}${url.slice(origin.length)}`
      : url
  }
  injectHead().hooks.hook('tags:resolve', (ctx) => {
    for (const t of ctx.tags) {
      if (t.tag === 'link' && t.props.rel === 'canonical') t.props.href = fix(t.props.href)
      if (t.tag === 'meta' && t.props.property === 'og:url') t.props.content = fix(t.props.content)
    }
  })
})
