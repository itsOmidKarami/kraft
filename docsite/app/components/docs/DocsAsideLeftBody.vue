<script setup lang="ts">
// Docus's own DocsAsideLeftBody.vue (docus 5.13.0), with exact link matching:
// see utils/exactNavigation.ts.
const { sidebarNavigation } = useSubNavigation()

const contentNavVariants = useUIConfig('contentNavigation')

const navigation = computed(() => exactNavigation(sidebarNavigation.value))
</script>

<template>
  <UContentNavigation
    :collapsible="false"
    :highlight="contentNavVariants.highlight ?? true"
    :highlight-color="contentNavVariants.highlightColor"
    :variant="contentNavVariants.variant ?? 'link'"
    :color="contentNavVariants.color"
    :navigation="navigation"
  />
</template>
