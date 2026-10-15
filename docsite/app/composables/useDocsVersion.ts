// Which docs version the reader is on, and the current page's path inside it,
// for links to the same page in another version. Usually that is this build's
// own version, but the root 404.html comes from the latest build and answers a
// missing page under /v1/ or /next/ too. There the router path is
// /v1/<page>, so read the version off the full path, not the build: the first
// path segment against the bases of the version list.
export function useDocsVersion() {
  const docs = useRuntimeConfig().public.docs
  const baseURL = useRuntimeConfig().app.baseURL
  const route = useRoute()
  return computed(() => {
    const full = baseURL + route.path.replace(/^\//, '')
    // A local or PR build has no list, so no version UI.
    const latest = docs.versions.find(v => v.base === '/')
    const current = docs.versions.find(v => v.base !== '/' && `${full}/`.startsWith(v.base)) ?? latest
    const home = current?.base ?? '/'
    const page = full.slice(home.length).replace(/^\//, '')
    return {
      current,
      latest,
      versions: docs.versions,
      home,
      samePage: (other: string) => other + page,
    }
  })
}
