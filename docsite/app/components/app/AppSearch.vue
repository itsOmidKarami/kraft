<!-- Docus's AppSearch.vue (docus 5.13.0) for the search this site uses (no
     full-text index), plus ignoredTags: a page's syntax-highlighting <style>
     was indexed as text and showed up in result snippets as Shiki's CSS. -->
<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'

defineProps<{
  navigation?: ContentNavigationItem[]
}>()

const { forced: forcedColorMode } = useDocusColorMode()

const { data: files } = useLazyAsyncData('search_docs', () => queryCollectionSearchSections('docs', { ignoredTags: ['style'] }), {
  server: false,
})
</script>

<template>
  <LazyUContentSearch
    :files="files"
    :navigation="navigation"
    :color-mode="!forcedColorMode"
  />
</template>
