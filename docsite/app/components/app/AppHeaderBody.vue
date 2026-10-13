<script setup lang="ts">
// Docus's own AppHeaderBody.vue (docus 5.13.0), the phone nav drawer, with
// exact link matching (see utils/exactNavigation.ts) and folding groups, as in
// docs/DocsAsideLeftBody.vue.
// package.json pins docus to exactly 5.13.0 for this copy: on a bump, copy the
// new release's file again and reapply the exactNavigation() wrap, the
// default-open, the key and the reveal.
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

const route = useRoute()
const root = useTemplateRef<HTMLElement>('root')
useRevealCurrentLink(root)
</script>

<template>
  <div ref="root">
    <UContentNavigation
      :key="route.path"
      default-open
      :highlight="contentNavVariants.highlight ?? true"
      :highlight-color="contentNavVariants.highlightColor"
      :variant="contentNavVariants.variant ?? 'link'"
      :color="contentNavVariants.color"
      :navigation="items"
    />
  </div>
</template>
