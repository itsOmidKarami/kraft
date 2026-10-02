// The copies of the docs written for LLMs carry the router's links, which only
// resolve inside the site's own app. Make each one absolute.
//
// - raw/<page>.md (what llms.txt, "Copy page" and "Open in Claude/ChatGPT"
//   serve) keeps `[x](/reference/harnesses)` and `![x](/diagrams/y.svg)`,
//   which resolve at the domain root, outside /kraft/.
// - llms-full.txt joins every page into one file, so a page's own `#anchor`
//   links all landed on the landing page.
// dev/check_llm_docs.py fails the docs build when either comes back.

// `](/path` and the `href="/path"` of the raw HTML some components leave
// (also `src` and `to`) to `<base>/path`. Fenced code is not spared: the
// stringifier can glue a fence to the line before it ("a CI secret.```bash"),
// so no line-based reading of fences is reliable, and no page has a
// root-relative link in a code block to protect. `//` is a protocol-relative
// URL, not ours.
function absolutizeLinks(markdown: string, base: string): string {
  return markdown
    .replace(/\]\(\/(?!\/)/g, `](${base}/`)
    .replace(/(\s(?:href|src|to)=")\/(?!\/)/g, `$1${base}/`)
}

type Node = [string, Record<string, unknown>, ...unknown[]]

// Point each `#frag` href in a minimark body at `pageUrl`.
function anchorsToPage(children: unknown[], pageUrl: string): void {
  for (const child of children) {
    if (!Array.isArray(child)) continue
    const [, props, ...rest] = child as Node
    if (props && typeof props.href === 'string' && props.href.startsWith('#')) {
      props.href = `${pageUrl}${props.href}`
    }
    anchorsToPage(rest, pageUrl)
  }
}

export default defineNitroPlugin((nitroApp) => {
  const base = useRuntimeConfig().sitemapBase as string // no trailing slash

  nitroApp.hooks.hook('beforeResponse', (event, response) => {
    if (/\/raw\/.+\.md$/.test(event.path) && typeof response.body === 'string') {
      response.body = absolutizeLinks(response.body, base)
    }
  })

  // Run by @nuxt/content for each page before it is stringified into
  // llms-full.txt, which prefixes only the domain onto a link's href.
  nitroApp.hooks.hook('content:llms:generate:document', (_event, doc) => {
    const { body, path } = doc as { body?: { value?: unknown[] }; path?: string }
    if (body?.value && path) anchorsToPage(body.value, `${base}${path}`)
  })
})
