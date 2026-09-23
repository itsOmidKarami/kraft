export default defineNuxtConfig({
  extends: ['docus'],
  css: ['~/assets/css/hero.css'],
  app: {
    head: {
      link: [{ rel: 'icon', type: 'image/svg+xml', href: '/icon.svg' }],
    },
  },
  site: {
    url: 'https://itsomidkarami.github.io/kraft/',
  },
})
