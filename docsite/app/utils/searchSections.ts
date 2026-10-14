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
// A page's syntax-highlighting <style> is not text anyone wrote.
const IGNORED = ['style']

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
    for (const node of page.body?.value ?? []) {
      const level = typeof node === 'string' ? 0 : Number(/^h([1-6])$/.exec(node[0])?.[1] ?? 0)
      if (level) {
        const title = textOf(node).trim()
        while (trail.length && trail[trail.length - 1]!.level >= level) trail.pop()
        sections.push({ id: `${page.path}#${(node as [string, Record<string, unknown>])[1].id}`, title, titles: [page.title, ...trail.map(t => t.title)], content: '', level })
        trail.push({ level, title })
      } else {
        const current = sections[sections.length - 1]!
        current.content = `${current.content} ${textOf(node).trim()}`.trim()
      }
    }
    return sections
  })
}
