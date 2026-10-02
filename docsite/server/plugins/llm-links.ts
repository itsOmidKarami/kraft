// The copies of the docs written for LLMs carry the router's links, which only
// resolve inside the site's own app. Make each one absolute.
//
// - raw/<page>.md (what llms.txt, "Copy page" and "Open in Claude/ChatGPT"
//   serve) keeps `[x](/reference/harnesses)` and `![x](/diagrams/y.svg)`,
//   which resolve at the domain root, outside /kraft/.
// - llms-full.txt joins every page into one file, so a page's own `#anchor`
//   links all landed on the landing page.
// dev/check_llm_docs.py fails the docs build when either comes back.

// `](/path`, the `href="/path"` of the raw HTML some components leave (also
// `src` and `to`) and the `"to":"/path"` inside a component's JSON prop (the
// landing hero's links) to `<base>/path`. `//` is a protocol-relative URL, not
// ours.
//
// Fenced code is not spared: the stringifier can glue a fence to the line
// before it ("a CI secret.```bash"), so no line-based reading of fences is
// reliable. Do not write a root-relative link (`](/x)`) in a code sample, or it
// is rewritten there too; no page does today.
//
// A page's own `](#frag)` links stay bare here: the raw page holds the
// heading they name, so they resolve where it is read. (llms-full.txt joins
// every page into one file, which is why it needs them resolved below.)
const JSON_LINK = /("(?:href|src|to)":")\/(?!\/)/g

function absolutizeLinks(markdown: string, base: string): string {
  return markdown
    .replace(/\]\(\/(?!\/)/g, `](${base}/`)
    .replace(/(\s(?:href|src|to)=")\/(?!\/)/g, `$1${base}/`)
    .replace(JSON_LINK, `$1${base}/`)
}

const LINK_KEYS = new Set(['href', 'src', 'to'])

// In a minimark body (nodes are [tag, props, ...children]; a prop can hold
// objects, like the hero's links), point each `#frag` at `pageUrl` and each
// root-relative path at `base`. @nuxt/content prefixes only the domain onto a
// node's own href, which leaves the hero's `:links` (a JSON string) as it was.
function absolutizeProps(value: unknown, base: string, pageUrl: string): void {
  if (Array.isArray(value)) {
    for (const item of value) absolutizeProps(item, base, pageUrl)
  } else if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const [key, item] of Object.entries(record)) {
      if (LINK_KEYS.has(key) && typeof item === 'string') {
        if (item.startsWith('#')) record[key] = `${pageUrl}${item}`
        else if (item.startsWith('/') && !item.startsWith('//')) record[key] = `${base}${item}`
      } else if (typeof item === 'string') {
        // A `:links="[{...}]"` prop is a JSON string until it is stringified.
        record[key] = item.replace(JSON_LINK, `$1${base}/`)
      } else {
        absolutizeProps(item, base, pageUrl)
      }
    }
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
    if (body?.value && path) absolutizeProps(body.value, base, `${base}${path}`)
  })
})
