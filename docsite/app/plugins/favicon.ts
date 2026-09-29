// Docus hard-codes <link rel="icon" href="/favicon.ico">, which ignores
// app.baseURL and 404s on every page under /kraft/. The icon.svg link in
// nuxt.config.ts is the real icon, so drop the other one.
export default defineNuxtPlugin(() => {
  injectHead().hooks.hook('tags:resolve', (ctx) => {
    ctx.tags = ctx.tags.filter(t => !(t.tag === 'link' && t.props.href === '/favicon.ico'))
  })
})
