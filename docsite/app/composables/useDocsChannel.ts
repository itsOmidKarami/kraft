// Which docs channel the reader is on, and the current page's path inside it,
// for links to the same page in the other channel. Usually that is this
// build's own channel, but the root 404.html comes from the stable build and
// answers a missing page under /kraft/next/ too. There the router path is
// /next/<page>, so read the channel off the full path, not the build.
export function useDocsChannel() {
  const docs = useRuntimeConfig().public.docs
  const baseURL = useRuntimeConfig().app.baseURL
  const route = useRoute()
  return computed(() => {
    const full = baseURL + route.path.replace(/^\//, '')
    const next = `${full}/`.startsWith(docs.nextBase)
    const base = next ? docs.nextBase : docs.stableBase
    const page = full.slice(base.length).replace(/^\//, '')
    return {
      // stable, next, or empty: a local or PR build shows no version UI.
      channel: docs.channel ? (next ? 'next' : 'stable') : '',
      home: base,
      samePage: (other: string) => other + page,
    }
  })
}
