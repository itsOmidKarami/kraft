<!-- Docus's AppSearch.vue (docus 5.13.0) for the search this site uses (no
     full-text index), plus sections made by utils/searchSections.ts (a table
     cell or list item ends in " · " so snippets do not run them together, and
     a page's syntax-highlighting <style>, which showed up in result snippets
     as Shiki's CSS, is left out), and the page's HTML entities are decoded
     there too. On a /next/ or /v1/ URL
     answered by the latest build's root 404.html this searches the latest
     docs, so the dialog says so. A Reference hit is ranked lower
     (utils/searchRank.ts), so "raise budget" opens on the guide and not on
     the HTTP route, unless the query is the text of a title. -->
<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'
import type { Hit } from '~/utils/searchRank'

const props = defineProps<{
  navigation?: ContentNavigationItem[]
}>()

const { forced: forcedColorMode } = useDocusColorMode()
const docs = useRuntimeConfig().public.docs
const at = useDocsVersion()

const { data: files } = useLazyAsyncData('search_docs', async () => {
  const pages = await queryCollection('docs').where('extension', '=', 'md').select('path', 'title', 'description', 'body').all()
  return searchSections(pages as never)
}, {
  server: false,
})

// Fuse is handed the page's path as one more, near-weightless key, because
// that is the only way its sort function can see which page a hit is: the
// path, the title and the text are values 0, 1 and 3 of its `item` (2 is the
// description, which search items lack). Nuxt UI merges these options with
// defu, which puts our keys ahead of its own: a Nuxt UI upgrade that changes
// that merge moves them, and every hit then counts as a Reference hit or none.
// The order of the hits is utils/searchRank.ts.
const term = ref('')
const fuse = {
  resultLimit: 40,
  fuseOptions: {
    keys: [{ name: 'to', weight: 0.01 }],
    sortFn: (a: Hit, b: Hit) => rankHit(a, term.value) - rankHit(b, term.value) || a.idx - b.idx,
  },
}

// Nuxt UI shows results one group per top-level section of the navigation
// (Guides, Concepts, Reference), each group in rank order and the groups in the
// order of their best hit, so a guide's six subsections all came before the
// Reference verb that was the second-best hit. Wrapped in one parent they are
// a single group, in rank order (each hit still says its section in front).
const searchNavigation = computed(() => props.navigation?.length
  ? [{ title: '', path: '/__results', children: props.navigation }] as ContentNavigationItem[]
  : props.navigation)

// The root 404.html answers a missing page of another version too, and its
// search is this build's: say so.
const elsewhere = computed(() => at.value.current && at.value.current.id !== docs.version
  ? { was: at.value.current.name, searched: docs.versions.find(v => v.id === docs.version)?.name ?? 'these docs' }
  : null)
</script>

<template>
  <LazyUContentSearch
    :files="files"
    :navigation="searchNavigation"
    :fuse="fuse"
    v-model:search-term="term"
    :color-mode="!forcedColorMode"
    :title="elsewhere ? `Search ${elsewhere.searched}` : undefined"
    :description="elsewhere ? `This page is not in ${elsewhere.was}; this searches ${elsewhere.searched}.` : undefined"
    :placeholder="elsewhere ? `Search ${elsewhere.searched}…` : undefined"
  />
</template>
