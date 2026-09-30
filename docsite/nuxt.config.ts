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

export default defineNuxtConfig({
  extends: ['docus'],
  css: ['~/assets/css/hero.css'],
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
  // only reads one at the domain root anyway. public/robots.txt is ours.
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
  // The version switch links to the other build's root, which the crawler
  // would take for one of this build's own pages and fail as a 404. Exactly
  // that path: a prefix match on /kraft/ would skip every page.
  nitro: {
    prerender: {
      ignore: channel ? [new RegExp(`^${channel === 'next' ? '/kraft/' : '/kraft/next/'}$`)] : [],
    },
  },
})
