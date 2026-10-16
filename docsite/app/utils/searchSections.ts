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
// A CLI page's "Verbs at a glance" table has one row per verb, whose first
// cell links to the section that explains it. Each row is also a section of
// its own, titled as the command is typed, so "approve" finds `kraft item
// approve` and not only the prose around it.
const VERB_INDEX = 'Verbs at a glance'
// Under these a table row is a lookup of its own: a Troubleshooting row is
// the message Kraft shows, and a configuration row is a field of a YAML file,
// and "task failed in node" or "budget.daily_usd" is typed to find that row,
// which a section of forty rows (its text one block) ranks behind its prose.
const ROW_PAGES = ['/troubleshooting/', '/reference/configuration/']

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

const rowsOf = (node: MinimarkNode): MinimarkNode[] =>
  typeof node === 'string' ? [] : node[0] === 'tr' ? [node] : node.slice(2).flatMap(child => rowsOf(child as MinimarkNode))

const hrefOf = (node: MinimarkNode): string => {
  if (typeof node === 'string') return ''
  if (node[0] === 'a') return String(node[1].href ?? '')
  return node.slice(2).map(child => hrefOf(child as MinimarkNode)).find(Boolean) ?? ''
}

// A section made for a verb carries this prefix in its title; the search
// (components/app/AppSearch.vue) exempts it from the Reference penalty.
export const VERB_TITLE = 'kraft '

function verbSections(page: Page, table: MinimarkNode): SearchSection[] {
  return rowsOf(table).flatMap((row) => {
    const [, , first, second] = row as [string, unknown, MinimarkNode?, MinimarkNode?]
    const href = first ? hrefOf(first) : ''
    if (!first || !second || !href.startsWith('#')) return []
    return [{ id: `${page.path}${href}`, title: VERB_TITLE + textOf(first).replace(/ · $/, '').trim(), titles: [page.title], content: textOf(second).replace(/ · $/, '').trim(), level: 2 }]
  })
}

// A row of a table, as a section under the heading it is in: its first cell
// is the title and the other cells are the text.
function rowSections(section: SearchSection, table: MinimarkNode): SearchSection[] {
  return rowsOf(table).flatMap((row) => {
    const [, , first, ...rest] = row as [string, unknown, MinimarkNode?, ...MinimarkNode[]]
    const header = [first, ...rest].some(cell => typeof cell !== 'string' && cell?.[0] === 'th')
    const cells = rest.map(cell => textOf(cell).replace(/ · $/, '').trim())
    const title = first ? textOf(first).replace(/ · $/, '').trim() : ''
    if (header || !title || !cells.some(Boolean)) return []
    return [{ id: section.id, title, titles: [...section.titles, section.title].filter(Boolean), content: cells.join(' · '), level: section.level + 1 }]
  })
}

// Nuxt UI shows the title and the start of the text side by side, so a text
// that opens with its own title read "Install Install Kraft with uv".
const withoutTitle = (title: string, content: string) =>
  title && content.toLowerCase().startsWith(title.toLowerCase()) && /^\W|^$/.test(content.slice(title.length))
    ? content.slice(title.length).replace(/^[\s:\-–—.,]+/, '')
    : content

export function searchSections(pages: Page[]): SearchSection[] {
  return pages.flatMap((page) => {
    const sections: SearchSection[] = [{ id: page.path, title: page.title || '', titles: [], content: (page.description || '').trim(), level: 1 }]
    const trail: { level: number, title: string }[] = []
    let heading = sections[0]! // the section the text being read belongs to
    let inVerbIndex = false // the heading just read is a CLI page's verb index
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
        inVerbIndex = title === VERB_INDEX
        while (trail.length && trail[trail.length - 1]!.level >= level) trail.pop()
        heading = { id: `${page.path}#${(node as [string, Record<string, unknown>])[1].id}`, title, titles: [page.title, ...trail.map(t => t.title)], content: '', level }
        sections.push(heading)
        trail.push({ level, title })
      } else {
        heading.content = `${heading.content} ${textOf(node).trim()}`.trim()
        if (typeof node !== 'string' && node[0] === 'table') {
          if (inVerbIndex) sections.push(...verbSections(page, node))
          else if (ROW_PAGES.some(prefix => page.path.startsWith(prefix))) sections.push(...rowSections(heading, node))
        }
      }
    }
    return sections.map((section) => {
      const title = plain(section.title)
      return { ...section, title, titles: section.titles.map(plain), content: withoutTitle(title, plain(section.content)) }
    })
  })
}
