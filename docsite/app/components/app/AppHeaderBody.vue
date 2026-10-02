<script setup lang="ts">
// Docus's own AppHeaderBody.vue (docus 5.13.0), the phone nav drawer, with
// exact link matching: see utils/exactNavigation.ts.
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
</script>

<template>
  <UContentNavigation
    :collapsible="false"
    :highlight="contentNavVariants.highlight ?? true"
    :highlight-color="contentNavVariants.highlightColor"
    :variant="contentNavVariants.variant ?? 'link'"
    :color="contentNavVariants.color"
    :navigation="items"
  />
</template>
