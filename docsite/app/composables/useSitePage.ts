// A marketing page (layouts/site.vue): its title and description come from
// site-pages.mjs. It is site code, not content, so a merge to main changes it
// on the next deploy rather than at a release. Only the build at the root of
// the domain draws it: /next/ and /vN/ are built from the same code and answer
// the page's address with a redirect to the root's, so there is one copy.
export function useSitePage(path: string) {
  const pages = useRuntimeConfig().public.sitePages as { path: string, title: string, description: string }[]
  const page = pages.find(p => p.path === path)
  if (!page) throw new Error(`${path} is not in site-pages.mjs`)
  const home = isHomeBuild(useRuntimeConfig().app.baseURL)
  const target = `${useSiteConfig().url.replace(/\/$/, '')}${path}`

  if (home) {
    useSeo({ title: page.title, description: page.description, type: 'website' })
    defineOgImage('Docs', {
      headline: 'Kraft',
      title: page.title,
      description: formatOgDescription(page.title, page.description),
    })
  } else {
    // A static host cannot redirect, so the page does: the refresh, a script
    // that replaces the history entry, and a plain link for a reader with
    // neither. `data-fixed` keeps plugins/canonical.ts from putting this
    // build's base on the canonical, which names the one page at the root.
    useHead({
      title: 'Kraft',
      link: [{ rel: 'canonical', href: target, 'data-fixed': '' }],
      meta: [
        { 'http-equiv': 'refresh', 'content': `0; url=${target}` },
        { name: 'robots', content: 'noindex' },
      ],
      script: [{ innerHTML: `location.replace(${JSON.stringify(target).replace(/</g, '\\u003c')} + location.hash)` }],
    })
  }
  return { home, target }
}
