<!-- Docus's AppSearch.vue (docus 5.13.0) for the search this site uses (no
     full-text index), plus sections made by utils/searchSections.ts (a table
     cell or list item ends in " · " so snippets do not run them together, and
     a page's syntax-highlighting <style>, which showed up in result snippets
     as Shiki's CSS, is left out). The
     index keeps the HTML entities of the page's text (&lt; &gt; &#39;), which
     the results would show literally, so decode them. On a /next/ URL answered
     by the stable build's root 404.html this searches the release docs, so the
     dialog says so. Results come grouped by section in the order a learner
     needs them (below), not in the order of the best match, so "raise budget"
     opens on the guide and not on the HTTP route. -->
<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'

const props = defineProps<{
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
  const pages = await queryCollection('docs').where('extension', '=', 'md').select('path', 'title', 'description', 'body').all()
  return searchSections(pages as never).map(section => ({
    ...section,
    title: decode(section.title),
    titles: section.titles?.map(decode),
    content: decode(section.content),
  }))
}, {
  server: false,
})

// preserveGroupOrder lists a group per section in this order, each by its own
// match score, so a reference hit is demoted, not hidden. Project sits above
// Reference because a policy or a changelog answers more newcomers than a
// route table. A section not named here (a new one) goes last.
const sectionOrder = ['get-started', 'concepts', 'guides', 'troubleshooting', 'project', 'reference']
const rank = (item: ContentNavigationItem) => {
  const at = sectionOrder.indexOf(item.path.split('/').pop() ?? '')
  return at < 0 ? sectionOrder.length : at
}
const orderedSections = computed(() => props.navigation?.toSorted((a, b) => rank(a) - rank(b)))

// The match limit counts hits across every section before they are grouped,
// so at the default 12 a dozen reference rows left no room for the guide.
const resultLimit = 40

const stableOnNext = computed(() => at.value.channel === 'next' && docs.channel !== 'next')
const releaseDocs = computed(() => `the ${docs.stableVersion || 'release'} docs`)
</script>

<template>
  <LazyUContentSearch
    :files="files"
    :navigation="orderedSections"
    preserve-group-order
    :fuse="{ resultLimit }"
    :color-mode="!forcedColorMode"
    :title="stableOnNext ? `Search ${releaseDocs}` : undefined"
    :description="stableOnNext ? `This page is not in the next docs; this searches ${releaseDocs}.` : undefined"
    :placeholder="stableOnNext ? `Search ${releaseDocs}…` : undefined"
  />
</template>
