// "Skip to content" has to be the first thing a Tab reaches, and the header is
// rendered before any layout, so a layout cannot put it there. A link written
// into the page's HTML ahead of the app does, and needs no JavaScript: it is
// outside #__nuxt, so hydration leaves it alone. It points at the element the
// layouts and the error page mark #main-content. Styles: assets/css/a11y.css.
export default defineNitroPlugin((nitroApp) => {
  nitroApp.hooks.hook('render:html', (html) => {
    html.bodyPrepend.unshift('<a class="skip-link" href="#main-content">Skip to content</a>')
  })
})
