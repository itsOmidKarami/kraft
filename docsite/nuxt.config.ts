import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

// The site is served from the root of the custom domain (kraft-sdlc.dev), so
// the latest release's docs are at /, an older major's at /vN/ and main's at
// /next/. A build under another base (a project page at /kraft/) needs it here,
// or every Nuxt-generated asset URL (_nuxt/*, _payload.json) 404s and the page
// loads unstyled. dev/build_docs_site.sh builds the docs once per version,
// each with the whole list (dev/docs_versions.py) and its own id from it.
const baseURL = process.env.KRAFT_DOCS_BASE || '/'
type DocsVersion = { id: string, label: string, base: string, tag: string, name: string }
const versions: DocsVersion[] = JSON.parse(process.env.KRAFT_DOCS_VERSIONS || '[]')
// This build's id: latest, vN or next. Empty for a local or PR build, which
// shows no version UI.
const version = process.env.KRAFT_DOCS_VERSION || ''
// Only the latest release's pages belong in search results.
const frozen = version !== '' && version !== 'latest'
// The bare origin. Site config joins app.baseURL onto it itself, so an origin
// that already ends in /kraft/ doubles the prefix (og:image at /kraft/kraft/).
const origin = 'https://kraft-sdlc.dev'

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

// What is new on main since the latest release: dev/gen_docs_unreleased.py
// writes the Unreleased page from the "Added in next" notes and names the pages
// that carry one. Only main's docs and a local build have such notes (a release
// stamps them into numbers), so a released site gets no page, no banner link
// and no marks. A tag's build never runs it: its content has no notes left.
const unreleased: { page: string | null, routes: Record<string, 'new' | 'updated'> } = version === 'next' || version === ''
  ? JSON.parse(execFileSync('python3', [fileURLToPath(new URL('../dev/gen_docs_unreleased.py', import.meta.url)), fileURLToPath(new URL('./content', import.meta.url))], { encoding: 'utf8' }))
  : { page: null, routes: {} }
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

// What 404.html shows before the app has loaded. Nuxt writes 404.html as a
// client-only shell (an empty #__nuxt), so a missing page painted blank until
// the JavaScript arrived, and with it off or failing stayed blank. This goes
// inside #__nuxt and is replaced when the app mounts, with error.vue's page,
// which says the same. GitHub Pages serves this one file for a missing path
// under / and under every other version alike, so it cannot know which docs
// the reader wanted and links to all of them, at the addresses of the version
// list, not this build's base. Styled inline, in the site's own colour tokens
// (entry.css is linked in the head), so it does not need the build's
// utility classes.
const missingPage = `<style>
.nf{box-sizing:border-box;min-height:100vh;padding:1.5rem;display:flex;flex-direction:column;background:var(--ui-bg,#fff);color:var(--ui-text,#334155);font-family:'IBM Plex Sans',system-ui,sans-serif}
.nf a{color:var(--ui-primary,#7c3aed);text-underline-offset:4px}
.nf header{font:600 1.125rem/1.5rem 'IBM Plex Mono',ui-monospace,monospace}
.nf header a{color:var(--ui-text-highlighted,#0f172a);text-decoration:none}
.nf header a::before{content:"› ";color:var(--ui-primary,#7c3aed)}
.nf main{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:.75rem}
.nf p{margin:0}
.nf .code{font:600 1rem/1.5rem 'IBM Plex Mono',ui-monospace,monospace;color:var(--ui-primary,#7c3aed)}
.nf h1{margin:0;font-family:'IBM Plex Mono',ui-monospace,monospace;letter-spacing:-0.02em;font-weight:600;font-size:2.25rem;line-height:2.5rem;color:var(--ui-text-highlighted,#0f172a)}
.nf .links{display:flex;flex-wrap:wrap;justify-content:center;gap:.5rem 1.5rem;margin-top:.75rem}
</style>
<div class="nf">
<header><a href="/">kraft</a></header>
<main id="main-content" tabindex="-1">
<p class="code">404</p>
<h1>No page at this address</h1>
<p>It may have been renamed or moved.</p>
<p class="links">${versions.map(v => `<a href="${v.base}">${v.name[0]!.toUpperCase()}${v.name.slice(1)}</a>`).join(' ')}</p>
</main>
</div>`

// The dashboard demo at /demo/ is a single-page app with no file for most of
// its addresses (/demo/work-items/<id>), so a reload or a shared link there gets
// this 404.html. It sends such an address back to the demo's own page as ?to=,
// which frontend/demo/shim.ts's restoreAddress puts back before the router
// reads it. Before anything else in the head, so the 404 never paints.
const demoReturn = `<script>(function(l){var b='/demo/';if(l.pathname.indexOf(b)===0&&l.pathname!==b)l.replace(b+'?to='+encodeURIComponent(l.pathname.slice(b.length-1)+l.search+l.hash))})(location)</script>`

// A browser breaks a line after a space or a hyphen and after nothing else in
// `POST /api/work-items/{id}/gates/{gate}/approve`, so an "On this page" entry
// (and the phone contents bar's) was cut inside a word ("approv" / "e": 267
// cuts at 1024px and 71 at 1280px, most on the six HTTP route pages). A
// zero-width space after a `/`, `_`, `-`, `.`, `?`, `=` or `&` that stands
// between two other characters (not between two of them: `--start-side` has
// no break after its first hyphen) is a place to break; the entry still breaks
// inside a segment only when the segment is longer than the column
// (break-words, app.config.ts, contentToc). Only the toc's `text` changes: the
// ids, the headings, the search sections and the page title are not touched.
const breakable = (text: string) => text.replace(/(?<=[^/_.\-?=&\s][/_.\-?=&])(?=[^/_.\-?=&\s])/g, '​')
type TocLink = { text: string, children?: TocLink[] }
const breakableLinks = (links: TocLink[] = []) => links.forEach((link) => {
  link.text = breakable(link.text)
  breakableLinks(link.children)
})

// GitHub Pages serves /guides/ from guides/index.html and /guides from
// guides.html, and a section's index page is written only as guides.html, so
// the address with the slash was the 404 page. Each section folder gets a copy
// of its index as index.html (the copy's asset URLs are absolute, so it loads
// from the deeper address). Only a folder with no index.html of its own: a
// page folder that already has one is left as built.
function addSectionIndexes(dir: string) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith('_') || entry.name === 'raw') continue
    const folder = join(dir, entry.name)
    if (existsSync(`${folder}.html`) && !existsSync(join(folder, 'index.html'))) copyFileSync(`${folder}.html`, join(folder, 'index.html'))
    addSectionIndexes(folder)
  }
}

export default defineNuxtConfig({
  extends: ['docus'],
  // Off until Nuxt ships @nuxt/devtools 4: devtools 3 default-imports
  // simple-git, and the patched simple-git 4 (package.json overrides) has no
  // default export, so loading the module fails `nuxt dev` and `generate`.
  devtools: { enabled: false },
  hooks: {
    'nitro:init'(nitro) {
      nitro.hooks.hook('prerender:done', () => addSectionIndexes(nitro.options.output.publicDir))
    },
    'content:file:afterParse'({ content }) {
      breakableLinks((content as { body?: { toc?: { links?: TocLink[] } } }).body?.toc?.links)
    },
  },
  css: ['~/assets/css/type.css', '~/assets/css/hero.css', '~/assets/css/prose.css', '~/assets/css/layout.css', '~/assets/css/a11y.css'],
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
    // Only the latest release's docs belong in search results; an older
    // major's and main's carry a noindex tag. @nuxtjs/robots writes the robots
    // meta tag from this; a tag in app.head would be overridden.
    ...(frozen ? { indexable: false } : {}),
  },
  // nuxt-llms writes no llms.txt or llms-full.txt without a domain, and Docus
  // infers none on GitHub Pages. Its links are the domain plus a router path,
  // so the domain carries this build's base ('', /v1 or /next).
  llms: {
    domain: `${origin}${baseURL.replace(/\/$/, '')}`,
  },
  // @nuxtjs/robots refuses to write robots.txt under a base URL, and a crawler
  // only reads one at the domain root anyway. server/routes/robots.txt.ts
  // writes ours, one per version.
  robots: {
    robotsTxt: false,
  },
  runtimeConfig: {
    // Read by server/plugins/sitemap.ts. Docus's sitemap route ignores
    // site.url and takes a host only from NUXT_SITE_URL, which would also
    // override site.url above.
    sitemapBase: `${origin}${baseURL.replace(/\/$/, '')}`,
    public: {
      // Read by the version menu and the banners. The bases are full paths,
      // not router paths: each version is a separate build, so a link to
      // another one must leave this build's router.
      docs: {
        version,
        versions,
        unreleased,
      },
      // Every moved address, old to new. app/plugins/moved.ts forwards them:
      // not a `routeRules` redirect, which Nuxt follows in the browser before
      // any middleware of ours and without looking at the fragment, so a
      // moved section of a moved page went to the page's new address.
      moved: Object.fromEntries(redirects),
    },
  },
  // The IPX image proxy double-prefixes app.baseURL for content images
  // (/_ipx/_/assets/...), 404ing every screenshot. These are
  // already correctly-sized PNGs with no need for runtime resizing, so skip
  // the proxy and serve them as plain static files instead.
  image: {
    provider: 'none',
  },
  // IBM Plex, the site's type (assets/css/type.css). @nuxt/fonts (through
  // Docus) fetches these at build time and serves them from /_fonts/, so a
  // reader's browser never calls Google Fonts. Only the weights the CSS uses,
  // up to 600: type.css draws bold text at 600. Plex Sans has its italic for
  // the body's emphasis; a browser downloads a face only when a page uses it.
  fonts: {
    families: [
      { name: 'IBM Plex Mono', weights: [400, 500, 600], styles: ['normal'] },
      { name: 'IBM Plex Sans', weights: [400, 500, 600], styles: ['normal', 'italic'] },
    ],
  },
  nitro: {
    hooks: {
      'prerender:generate'(route) {
        const moved = movedPages.find(([from]) => from === route.route)
        if (moved) route.contents = forwardingPage(...moved)
        if (route.route === '/404.html' && typeof route.contents === 'string') {
          route.contents = route.contents.replace(/(<div id="__nuxt"[^>]*>)<\/div>/, (_, open) => `${open}${missingPage}</div>`)
          route.contents = route.contents.replace(/<head[^>]*>/, head => `${head}${demoReturn}`)
          // The shell's <head> gets its title and the <html> its language from
          // the app, which a reader without JavaScript never runs.
          if (!/<title>/.test(route.contents)) route.contents = route.contents.replace('</head>', '<title>No page at this address - Kraft</title></head>')
          if (!/<html[^>]*\slang=/.test(route.contents)) route.contents = route.contents.replace('<html', '<html lang="en"')
        }
      },
    },
    prerender: {
      // No page links to a moved page's old address, so the crawler would
      // never reach it on its own.
      routes: ['/robots.txt', ...movedPages.map(([from]) => from)],
      // The version menu links to the same page in every other build, which
      // the crawler would take for one of this build's own pages and fail as
      // a 404: a prefix match on each other version's base. The latest's is
      // /, which would skip every page, so it is only ever matched exactly.
      // And no 200.html: it is the SPA fallback of a host that serves one for
      // a missing path, which GitHub Pages is not (it serves 404.html). There
      // it was only a "Page not found" page answering 200 at /kraft/200, open
      // to indexing.
      ignore: [
        '/200.html',
        ...versions.filter(v => v.id !== version).map(v => new RegExp(v.base === '/' ? '^/$' : `^${v.base}`)),
      ],
    },
  },
})
