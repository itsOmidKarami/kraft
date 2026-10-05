// A section that moved to another page (content/redirects.yml): a reader who
// opens or clicks `/page#old-anchor` is sent to its new address. The server
// never sees a fragment, so this only ever matches in the browser.
export default defineNuxtPlugin(() => {
  const moved = useRuntimeConfig().public.movedSections as Record<string, string>
  addRouteMiddleware(
    'moved-sections',
    (to) => moved[`${to.path.replace(/(.)\/$/, '$1')}${to.hash}`],
    { global: true },
  )
})
