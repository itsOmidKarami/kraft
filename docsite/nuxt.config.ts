// GitHub Pages serves this repo at /kraft/, not the domain root -- every
// Nuxt-generated asset URL (_nuxt/*, _payload.json, the _ipx image proxy)
// needs that prefix or it 404s and the page loads unstyled.
const baseURL = '/kraft/'

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
    url: 'https://itsomidkarami.github.io/kraft/',
  },
  // The IPX image proxy double-prefixes app.baseURL for content images
  // (/kraft/_ipx/_/kraft/assets/...), 404ing every screenshot. These are
  // already correctly-sized PNGs with no need for runtime resizing, so skip
  // the proxy and serve them as plain static files instead.
  image: {
    provider: 'none',
  },
})
