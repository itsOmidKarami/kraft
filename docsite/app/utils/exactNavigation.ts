import type { ContentNavigationItem } from '@nuxt/content'

// The Reference sidebar's groups, the headings its index already uses. The
// pages stay where they are (no address moves): this only gathers them in the
// sidebar, and lifts two pages the index says sit inside Configuration, the
// environment variables and add-ons, out to the group that holds them. A page
// the lists do not name stays after the groups, so a new one is never lost.
const referenceGroups: { title: string, paths: string[] }[] = [
  { title: 'Use Kraft', paths: ['web-ui', 'cli', 'statuses'] },
  {
    title: 'Configure Kraft',
    paths: ['configuration', 'configuration/environment-variables', 'configuration/addons', 'chain-nodes', 'harnesses', 'permissions'],
  },
  { title: 'Drive Kraft from a program', paths: ['triggers', 'http-api', 'mcp-tools', 'events'] },
]

function groupReference(items: ContentNavigationItem[]): ContentNavigationItem[] {
  const at = (path: string) => `/reference/${path}`
  const find = (list: ContentNavigationItem[], path: string) => list.find(item => item.path === at(path))
  if (!find(items, 'statuses')) return items.map(item => item.children ? { ...item, children: groupReference(item.children) } : item)

  const lifted = new Set(['configuration/environment-variables', 'configuration/addons'].map(at))
  const configuration = find(items, 'configuration')
  const pages: ContentNavigationItem[] = items.map(item => item === configuration
    ? { ...item, children: item.children?.filter(child => !lifted.has(child.path)) }
    : item)
  for (const path of lifted) {
    const page = configuration?.children?.find(child => child.path === path)
    if (page) pages.push(page)
  }

  const placed = new Set<string>()
  const groups = referenceGroups.flatMap(({ title, paths }) => {
    const children = paths.flatMap((path) => {
      const page = find(pages, path)
      if (page) placed.add(page.path)
      return page ? [page] : []
    })
    return children.length ? [{ title, path: `/reference#${title}`, class: 'text-left', children } as ContentNavigationItem] : []
  })
  // The overview, then the groups, then anything unplaced.
  const overview = items.filter(item => item.path === '/reference')
  return [...overview, ...groups, ...pages.filter(item => item.path !== '/reference' && !placed.has(item.path))]
}

// A page that carries an "Added in next" note is marked in the sidebar, as new
// or as updated (the build's list, dev/gen_docs_unreleased.py). A released
// site's list is empty.
function mark(items: ContentNavigationItem[], marks: Record<string, string>): ContentNavigationItem[] {
  return items.map((item) => {
    if (item.children) return { ...item, children: mark(item.children, marks) }
    const kind = marks[item.path]
    return kind ? { ...item, badge: { label: kind === 'new' ? 'New' : 'Updated', color: 'warning', variant: 'subtle' } } : item
  })
}

// Nuxt UI matches a sidebar link by prefix unless the link says `exact`, so a
// section's Overview (/reference) lit up beside the page under it
// (/reference/cli/item), and no link was marked as the current page. An exact
// link is active on its own page alone, and Nuxt UI then gives it
// aria-current="page".
export function exactNavigation(items: ContentNavigationItem[] | null | undefined): ContentNavigationItem[] {
  const marks = useRuntimeConfig().public.docs.unreleased?.routes ?? {}
  const exact = (list: ContentNavigationItem[]): ContentNavigationItem[] => list.map(item => ({
    ...item,
    exact: true,
    ...(item.children ? { children: exact(item.children) } : {}),
  }))
  return exact(mark(groupReference(items || []), marks))
}
