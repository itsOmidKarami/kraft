<script setup lang="ts">
// Docus's own DocsAsideLeftBody.vue (docus 5.13.0), with exact link matching
// (see utils/exactNavigation.ts) and a sidebar that folds: Docus opens every
// group of the whole site at once, so the page you are on sat several screens
// down.
// package.json pins docus to exactly 5.13.0 for this copy: on a bump, copy the
// new release's file again and reapply the exactNavigation() wrap, the
// default-open, the key, the reveal and the nav label below.
const { sidebarNavigation } = useSubNavigation()

const contentNavVariants = useUIConfig('contentNavigation')

const navigation = computed(() => exactNavigation(sidebarNavigation.value))

const root = useTemplateRef<HTMLElement>('root')
const listKey = useFoldedNavigation(root)
</script>

<template>
  <div ref="root">
    <!-- default-open opens only the groups on the way to the current page.
         Nuxt UI reads it once, when the list mounts: see useFoldedNavigation()
         for when the key gives it a fresh start. -->
    <UContentNavigation
      :key="listKey"
      aria-label="Docs"
      default-open
      :highlight="contentNavVariants.highlight ?? true"
      :highlight-color="contentNavVariants.highlightColor"
      :variant="contentNavVariants.variant ?? 'link'"
      :color="contentNavVariants.color"
      :navigation="navigation"
    />
  </div>
</template>
