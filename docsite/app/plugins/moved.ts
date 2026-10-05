// Forwards an address that moved (content/redirects.yml), by the same rule
// as the forwarding page in nuxt.config.ts and `forward()` in
// dev/check_docs_redirects.py: an entry for `/page#anchor` first, then the
// page's own entry, which keeps the reader's anchor unless it names one.
//
// On the server, where there is no fragment, this is what answers a moved
// page's old address with the redirect the prerenderer writes to a file. In
// the browser it covers a link clicked inside the site, and a moved section
// of a page that still exists: a static host never sees the fragment, so only
// that page can forward it.
export default defineNuxtPlugin(() => {
  const moved = useRuntimeConfig().public.moved as Record<string, string>
  addRouteMiddleware(
    'moved',
    (to) => {
      const path = to.path.replace(/(.)\/$/, '$1')
      const page = moved[path]
      return moved[path + to.hash] ?? (page && (page.includes('#') ? page : page + to.hash))
    },
    { global: true },
  )
})
