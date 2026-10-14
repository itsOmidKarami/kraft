<!-- Docus's AppSearch.vue (docus 5.13.0) for the search this site uses (no
     full-text index), plus sections made by utils/searchSections.ts (a table
     cell or list item ends in " · " so snippets do not run them together, and
     a page's syntax-highlighting <style>, which showed up in result snippets
     as Shiki's CSS, is left out). The
     index keeps the HTML entities of the page's text (&lt; &gt; &#39;), which
     the results would show literally, so decode them. On a /next/ URL answered
     by the stable build's root 404.html this searches the release docs, so the
     dialog says so. A Reference hit is ranked lower (below), so "raise budget"
     opens on the guide and not on the HTTP route. -->
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

// Reference pages are the best match for nearly any word that is also the
// name of a route or a verb ("raise budget" found POST .../budget/raise
// first and the guide ninth), so a Reference hit is ranked as if it scored
// REFERENCE_PENALTY worse (Fuse: 0 is exact, 1 is none; the guide that should
// lead scored about 0.64 where that route scored 0.17), which lets a good hit
// elsewhere lead but not hide it. A query that is itself a route, a flag or a
// verb (it has a / - _ or .) is a search for the reference, so it gets none.
// The match limit counts hits across every section before they are grouped,
// so at the default 12 a dozen reference rows left no room for the guide.
// Fuse is handed the page's path as one more, near-weightless key, because
// that is the only way its sort function can see which page a hit is: the
// path is the first value of its `item`.
const REFERENCE_PENALTY = 0.55
const term = ref('')
type Hit = { score: number, idx: number, item: Record<number, { v: string }> }
const rank = (hit: Hit) => hit.score
  + (!/[/\-_.]/.test(term.value) && hit.item[0]!.v.startsWith('/reference') ? REFERENCE_PENALTY : 0)
const fuse = {
  resultLimit: 40,
  fuseOptions: {
    keys: [{ name: 'to', weight: 0.01 }],
    sortFn: (a: Hit, b: Hit) => rank(a) - rank(b) || a.idx - b.idx,
  },
}

const stableOnNext = computed(() => at.value.channel === 'next' && docs.channel !== 'next')
const releaseDocs = computed(() => `the ${docs.stableVersion || 'release'} docs`)
</script>

<template>
  <LazyUContentSearch
    :files="files"
    :navigation="navigation"
    :fuse="fuse"
    v-model:search-term="term"
    :color-mode="!forcedColorMode"
    :title="stableOnNext ? `Search ${releaseDocs}` : undefined"
    :description="stableOnNext ? `This page is not in the next docs; this searches ${releaseDocs}.` : undefined"
    :placeholder="stableOnNext ? `Search ${releaseDocs}…` : undefined"
  />
</template>
