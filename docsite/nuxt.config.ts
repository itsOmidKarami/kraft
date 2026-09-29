// GitHub Pages serves this repo at /kraft/, not the domain root -- every
// Nuxt-generated asset URL (_nuxt/*, _payload.json, the _ipx image proxy)
// needs that prefix or it 404s and the page loads unstyled.
const baseURL = '/kraft/'
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
  },
  runtimeConfig: {
    // Read by server/plugins/sitemap.ts. Docus's sitemap route ignores
    // site.url and takes a host only from NUXT_SITE_URL, which would also
    // override site.url above.
    sitemapBase: `${origin}${baseURL.replace(/\/$/, '')}`,
  },
  // The IPX image proxy double-prefixes app.baseURL for content images
  // (/kraft/_ipx/_/kraft/assets/...), 404ing every screenshot. These are
  // already correctly-sized PNGs with no need for runtime resizing, so skip
  // the proxy and serve them as plain static files instead.
  image: {
    provider: 'none',
  },
})
