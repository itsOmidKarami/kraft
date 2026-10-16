<script setup lang="ts">
// Docus's own AppHeaderBody.vue (docus 5.13.0), the phone nav drawer, with
// exact link matching (see utils/exactNavigation.ts) and folding groups, as in
// docs/DocsAsideLeftBody.vue.
// package.json pins docus to exactly 5.13.0 for this copy: on a bump, copy the
// new release's file again and reapply the exactNavigation() wrap, the
// default-open, the reveal and the nav label, and unmount-on-hide off, as in
// docs/DocsAsideLeftBody.vue.
import type { ContentNavigationItem } from '@nuxt/content'
import { getFirstPagePath } from 'docus/app/composables/useSubNavigation'

const navigation = inject<Ref<ContentNavigationItem[]>>('navigation')

const { subNavigationMode } = useSubNavigation()

const contentNavVariants = useUIConfig('contentNavigation')

const items = computed<ContentNavigationItem[]>(() => {
  const nav = navigation?.value || []
  if (!subNavigationMode.value) return exactNavigation(nav)
  return exactNavigation(nav.map(({ children, ...item }) => ({
    ...item,
    path: children?.length ? getFirstPagePath({ ...item, children }) : item.path,
  })))
})

// The menu is built when it opens, so the list is always fresh; only the scroll
// to the current link is needed.
const root = useTemplateRef<HTMLElement>('root')
useFoldedNavigation(root)
</script>

<template>
  <div ref="root">
    <UContentNavigation
      aria-label="Docs"
      default-open
      :unmount-on-hide="false"
      :highlight="contentNavVariants.highlight ?? true"
      :highlight-color="contentNavVariants.highlightColor"
      :variant="contentNavVariants.variant ?? 'link'"
      :color="contentNavVariants.color"
      :navigation="items"
    />
  </div>
</template>
