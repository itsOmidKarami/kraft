// Every page of a build's content, as the address Docus serves it at. The
// prerender crawler finds pages by the links on the pages it has already
// written, starting from the build's root, and a build whose root links to
// none of them (the redirect stub of /next/ and /vN/) would write only the
// root. nuxt.config.ts seeds the prerenderer with this list instead, so no
// build depends on its root's links, and fails the build when a page of it
// has no HTML afterwards.
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// The address of a content file, relative to content/. The same mapping as
// route() in dev/check_docs_redirects.py (docsite/tests/contentRoutes.test.ts
// holds the two together): number prefixes and `.md` dropped, an `index.md`
// standing for its folder.
export function routeOf(relative) {
  const parts = relative.replace(/\.md$/, '').split('/').map(part => part.replace(/^\d+\./, ''))
  if (parts[parts.length - 1] === 'index') parts.pop()
  return `/${parts.join('/')}`
}

// Every Markdown file under `dir`, relative and with `/` separators. Not the
// .navigation.yml files or redirects.yml, which are no pages.
function markdownFiles(dir, prefix = '') {
  return readdirSync(join(dir, prefix), { withFileTypes: true }).flatMap((entry) => {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name
    if (entry.isDirectory()) return markdownFiles(dir, relative)
    return entry.name.endsWith('.md') ? [relative] : []
  })
}

// The routes of every page under `contentDir`; none when there is no such
// folder.
export function contentRoutes(contentDir) {
  if (!existsSync(contentDir)) return []
  return markdownFiles(contentDir).map(routeOf).sort()
}

// The routes of `routes` that have no HTML file under `publicDir`: Nitro
// writes /a/b as a/b.html, or a/b/index.html, and the build copies the first
// to the second for a section's index (addSectionIndexes in nuxt.config.ts).
export function pagesMissingFrom(publicDir, routes) {
  return routes.filter((route) => {
    const path = route === '/' ? '' : route.slice(1)
    return !existsSync(join(publicDir, path, 'index.html')) && !(path && existsSync(join(publicDir, `${path}.html`)))
  })
}
