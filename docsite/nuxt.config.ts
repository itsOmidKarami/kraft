import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// GitHub Pages serves this repo at /kraft/, not the domain root -- every
// Nuxt-generated asset URL (_nuxt/*, _payload.json, the _ipx image proxy)
// needs that prefix or it 404s and the page loads unstyled.
// dev/build_docs_site.sh builds main's docs a second time under /kraft/next/.
const baseURL = process.env.KRAFT_DOCS_BASE || '/kraft/'
// stable, next, or empty: a local or PR build shows no version UI.
const channel = process.env.KRAFT_DOCS_CHANNEL || ''
// The bare origin. Site config joins app.baseURL onto it itself, so an origin
// that already ends in /kraft/ doubles the prefix (og:image at /kraft/kraft/).
const origin = 'https://itsomidkarami.github.io'

// content/redirects.yml: the addresses that moved in this build's content, as
// site paths without the base. The file documents the format, and
// dev/check_docs_redirects.py reads it with the same pattern. A release tag's
// content from before the map existed has no file.
const redirectsFile = fileURLToPath(new URL('./content/redirects.yml', import.meta.url))
const redirects = (existsSync(redirectsFile) ? readFileSync(redirectsFile, 'utf8') : '')
  .split('\n')
  .flatMap((line) => {
    const entry = /^(\/[a-z0-9/._#-]*):\s+(\/[a-z0-9/._#-]*)$/.exec(line.trim())
    return entry ? [[entry[1], entry[2]] as [string, string]] : []
  })
const movedPages = redirects.filter(([from]) => !from.includes('#'))
const movedSections = redirects.filter(([from]) => from.includes('#'))
const withBase = (path: string) => `${baseURL.replace(/\/$/, '')}${path}`

// What a moved page's old address serves. The prerenderer writes a redirect
// (app/plugins/moved.ts answers the old address with one) as a bare meta
// refresh: the reader's #fragment dropped, and a blank page where refresh is
// blocked. The script keeps the
// fragment, or follows a section of the old page that went somewhere else;
// without JavaScript the meta refresh and the link remain.
function forwardingPage(from: string, to: string): string {
  const sections = Object.fromEntries(
    movedSections
      .filter(([source]) => source.startsWith(`${from}#`))
      .map(([source, target]) => [source.slice(from.length), withBase(target)]),
  )
  const href = withBase(to)
  const keepFragment = to.includes('#') ? '' : ' + location.hash'
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Moved</title>
<link rel="canonical" href="${origin}${href}">
<script>location.replace(${JSON.stringify(sections)}[location.hash] || ${JSON.stringify(href)}${keepFragment})</script>
<meta http-equiv="refresh" content="0; url=${href}">
</head><body><p>This page has moved to <a href="${href}">${href}</a>.</p></body></html>
`
}

export default defineNuxtConfig({
  extends: ['docus'],
  css: ['~/assets/css/hero.css', '~/assets/css/prose.css'],
  // Nuxt UI's code theme is Material (lighter / palenight). On the code
  // block's background its light strings were 2.2:1, keys 2.6:1 and comments
  // 2.5:1, and its dark comments 3.0:1, under WCAG AA's 4.5:1; prose.css
  // patched the comments through a generated class name that a theme change
  // renames. GitHub's high-contrast pair has no token under 4.5:1 in either
  // mode (measured on the block's #f8fafc and #1d293d), comments included, so
  // nothing needs patching. `default` is what shows before the colour mode
  // is known.
  content: {
    build: {
      markdown: {
        // `text` blocks get .line spans like the rest (rehype-text-lines.mjs).
        // Content imports a plugin by its key, so the key is the file's path.
        rehypePlugins: {
          [fileURLToPath(new URL('./rehype-text-lines.mjs', import.meta.url))]: {},
        },
        highlight: {
          theme: {
            light: 'github-light-high-contrast',
            default: 'github-light-high-contrast',
            dark: 'github-dark-high-contrast',
          },
        },
      },
    },
  },
  app: {
    baseURL,
    head: {
      link: [{ rel: 'icon', type: 'image/svg+xml', href: `${baseURL}icon.svg` }],
    },
  },
  site: {
    url: origin,
    name: 'Kraft',
    // Only the release docs belong in search results. @nuxtjs/robots writes
    // the robots meta tag from this; a tag in app.head would be overridden.
    ...(channel === 'next' ? { indexable: false } : {}),
  },
  // nuxt-llms writes no llms.txt or llms-full.txt without a domain, and Docus
  // infers none on GitHub Pages. Its links are the domain plus a router path,
  // so the domain carries this build's base (/kraft or /kraft/next).
  llms: {
    domain: `${origin}${baseURL.replace(/\/$/, '')}`,
  },
  // @nuxtjs/robots refuses to write robots.txt under a base URL, and a crawler
  // only reads one at the domain root anyway. server/routes/robots.txt.ts
  // writes ours, one per channel.
  robots: {
    robotsTxt: false,
  },
  runtimeConfig: {
    // Read by server/plugins/sitemap.ts. Docus's sitemap route ignores
    // site.url and takes a host only from NUXT_SITE_URL, which would also
    // override site.url above.
    sitemapBase: `${origin}${baseURL.replace(/\/$/, '')}`,
    public: {
      // Read by the version switch and the /next/ banner. The bases are full
      // paths, not router paths: each version is a separate build, so a link
      // to the other one must leave this build's router.
      docs: {
        channel,
        stableVersion: process.env.KRAFT_DOCS_STABLE_VERSION || '',
        stableBase: '/kraft/',
        nextBase: '/kraft/next/',
      },
      // Every moved address, old to new. app/plugins/moved.ts forwards them:
      // not a `routeRules` redirect, which Nuxt follows in the browser before
      // any middleware of ours and without looking at the fragment, so a
      // moved section of a moved page went to the page's new address.
      moved: Object.fromEntries(redirects),
    },
  },
  // The IPX image proxy double-prefixes app.baseURL for content images
  // (/kraft/_ipx/_/kraft/assets/...), 404ing every screenshot. These are
  // already correctly-sized PNGs with no need for runtime resizing, so skip
  // the proxy and serve them as plain static files instead.
  image: {
    provider: 'none',
  },
  // IBM Plex for hero.css. @nuxt/fonts (through Docus) fetches these at build
  // time and serves them from /_fonts/, so a reader's browser never calls
  // Google Fonts. Only the weights hero.css uses.
  fonts: {
    families: [
      { name: 'IBM Plex Mono', weights: [400, 500, 600], styles: ['normal'] },
      { name: 'IBM Plex Sans', weights: [400, 500], styles: ['normal'] },
    ],
  },
  nitro: {
    hooks: {
      'prerender:generate'(route) {
        const moved = movedPages.find(([from]) => from === route.route)
        if (moved) route.contents = forwardingPage(...moved)
      },
    },
    prerender: {
      // No page links to a moved page's old address, so the crawler would
      // never reach it on its own.
      routes: ['/robots.txt', ...movedPages.map(([from]) => from)],
      // The version switch links to the other build's root, which the crawler
      // would take for one of this build's own pages and fail as a 404.
      // Exactly that path: a prefix match on /kraft/ would skip every page.
      // And no 200.html: it is the SPA fallback of a host that serves one for
      // a missing path, which GitHub Pages is not (it serves 404.html). There
      // it was only a "Page not found" page answering 200 at /kraft/200, open
      // to indexing.
      ignore: [
        '/200.html',
        ...(channel ? [new RegExp(`^${channel === 'next' ? '/kraft/' : '/kraft/next/'}$`)] : []),
      ],
    },
  },
})
