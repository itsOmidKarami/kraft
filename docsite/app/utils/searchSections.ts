// What Nuxt Content's queryCollectionSearchSections does, with one change:
// its text of a section joins the text of every element with nothing, so a
// table row read "RouteWhat it doesGET /api/budget/today" and a list of links
// "...is bounded.Agent harnesses: which...". Here a table cell or a list item
// ends in " · ". (A hook that put the separator in the page's own body leaked
// it into raw/*.md and llms-full.txt, which are made from that body.) The page
// is one section up to its first heading, then one per heading, as there.
type MinimarkNode = string | [string, Record<string, unknown>, ...MinimarkNode[]]
type Page = { path: string, title: string, description: string, body: { value: MinimarkNode[] } }
export type SearchSection = { id: string, title: string, titles: string[], content: string, level: number }

const ENDS_AN_ITEM = ['td', 'th', 'li']
// Only links to pages that are their own hits, so they only repeated those
// pages' words (a query for "stop reason" found every page that links to one).
const LINK_LISTS = ['Related', 'Next steps', 'See also', 'In this section']
// A page's syntax-highlighting <style> is not text anyone wrote.
const IGNORED = ['style']

// The index keeps the page's HTML entities (&lt; &gt; &#39;), which a result
// would show literally, so they are decoded here, once: `&amp;lt;` becomes
// `&lt;` and no further. An apostrophe then becomes a typographic one (’),
// because Nuxt UI escapes a snippet back to HTML before it shortens it, and
// its cut can land inside the five-character &#39; (Kraft-w49rs: "...#39;s
// own"); ’ is not escaped. shortcut: a cut can still split &quot; &amp; &lt;
// or &gt;, much rarer in prose; a fix for those has to replace Nuxt UI's
// highlight (utils/search.js truncateHTMLFromStart).
const entities: Record<string, string> = { lt: '<', gt: '>', amp: '&', quot: '"', apos: '\'' }
const plain = (text: string) => text.replace(/&(#x[0-9a-f]+|#\d+|lt|gt|amp|quot|apos);/gi, (match, name: string) => {
  if (name[0] !== '#') return entities[name.toLowerCase()] ?? match
  const code = name[1] === 'x' || name[1] === 'X' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10)
  return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : match
}).replaceAll('\'', '’')

const textOf = (node: MinimarkNode): string => {
  if (typeof node === 'string') return node
  const [tag, , ...children] = node
  if (IGNORED.includes(tag)) return ''
  return children.map(textOf).join('') + (ENDS_AN_ITEM.includes(tag) ? ' · ' : '')
}

export function searchSections(pages: Page[]): SearchSection[] {
  return pages.flatMap((page) => {
    const sections: SearchSection[] = [{ id: page.path, title: page.title || '', titles: [], content: (page.description || '').trim(), level: 1 }]
    const trail: { level: number, title: string }[] = []
    let skipFrom = 0 // the level of a link list being skipped, until a heading at or above it
    for (const node of page.body?.value ?? []) {
      const level = typeof node === 'string' ? 0 : Number(/^h([1-6])$/.exec(node[0])?.[1] ?? 0)
      if (skipFrom && (!level || level > skipFrom)) continue
      skipFrom = 0
      if (level) {
        const title = textOf(node).trim()
        if (LINK_LISTS.includes(title)) {
          skipFrom = level
          continue
        }
        while (trail.length && trail[trail.length - 1]!.level >= level) trail.pop()
        sections.push({ id: `${page.path}#${(node as [string, Record<string, unknown>])[1].id}`, title, titles: [page.title, ...trail.map(t => t.title)], content: '', level })
        trail.push({ level, title })
      } else {
        const current = sections[sections.length - 1]!
        current.content = `${current.content} ${textOf(node).trim()}`.trim()
      }
    }
    return sections.map(section => ({ ...section, title: plain(section.title), titles: section.titles.map(plain), content: plain(section.content) }))
  })
}
