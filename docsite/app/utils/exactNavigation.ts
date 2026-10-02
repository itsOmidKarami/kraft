import type { ContentNavigationItem } from '@nuxt/content'

// Nuxt UI matches a sidebar link by prefix unless the link says `exact`, so a
// section's Overview (/reference) lit up beside the page under it
// (/reference/cli/item), and no link was marked as the current page. An exact
// link is active on its own page alone, and Nuxt UI then gives it
// aria-current="page".
export function exactNavigation(items: ContentNavigationItem[] | null | undefined): ContentNavigationItem[] {
  return (items || []).map(item => ({
    ...item,
    exact: true,
    ...(item.children ? { children: exactNavigation(item.children) } : {}),
  }))
}
