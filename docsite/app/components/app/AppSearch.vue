<!-- Docus's AppSearch.vue (docus 5.13.0) for the search this site uses (no
     full-text index), plus ignoredTags: a page's syntax-highlighting <style>
     was indexed as text and showed up in result snippets as Shiki's CSS. The
     index keeps the HTML entities of the page's text (&lt; &gt; &#39;), which
     the results would show literally, so decode them. On a /next/ URL answered
     by the stable build's root 404.html this searches the release docs, so the
     dialog says so. -->
<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'

defineProps<{
  navigation?: ContentNavigationItem[]
}>()

const { forced: forcedColorMode } = useDocusColorMode()
const docs = useRuntimeConfig().public.docs
const at = useDocsChannel()

const entities: Record<string, string> = { lt: '<', gt: '>', amp: '&', quot: '"', apos: '\'' }
// One pass, so `&amp;lt;` becomes `&lt;` and no further.
const decode = (text: string) => text.replace(/&(#x[0-9a-f]+|#\d+|lt|gt|amp|quot|apos);/gi, (match, name: string) => {
  if (name[0] !== '#') return entities[name.toLowerCase()] ?? match
  const code = name[1] === 'x' || name[1] === 'X' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10)
  return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : match
})

const { data: files } = useLazyAsyncData('search_docs', async () => {
  const sections = await queryCollectionSearchSections('docs', { ignoredTags: ['style'] })
  return sections.map(section => ({
    ...section,
    title: decode(section.title),
    titles: section.titles?.map(decode),
    content: decode(section.content),
  }))
}, {
  server: false,
})

const stableOnNext = computed(() => at.value.channel === 'next' && docs.channel !== 'next')
const releaseDocs = computed(() => `the ${docs.stableVersion || 'release'} docs`)
</script>

<template>
  <LazyUContentSearch
    :files="files"
    :navigation="navigation"
    :color-mode="!forcedColorMode"
    :title="stableOnNext ? `Search ${releaseDocs}` : undefined"
    :description="stableOnNext ? `This page is not in the next docs; this searches ${releaseDocs}.` : undefined"
    :placeholder="stableOnNext ? `Search ${releaseDocs}…` : undefined"
  />
</template>
