// A link to the dashboard demo (`/demo/` in a page) is written like any other
// page link, so it gets this build's base: /next/'s pages open main's demo at
// /next/demo/, a release's open its own. The demo is not a page of the site but
// a folder of static files beside them (dev/build_docs_site.sh), so the router
// would render it as a missing page: load it from the server instead.
export default defineNuxtPlugin(() => {
  const base = useRuntimeConfig().app.baseURL.replace(/\/$/, '')
  addRouteMiddleware(
    'demo',
    (to) => {
      if (/^\/demo(\/|$)/.test(to.path)) return navigateTo(`${base}${to.fullPath}`, { external: true })
    },
    { global: true },
  )
})
