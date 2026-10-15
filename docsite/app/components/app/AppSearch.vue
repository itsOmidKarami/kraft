<!-- Docus's AppSearch.vue (docus 5.13.0) for the search this site uses (no
     full-text index), plus sections made by utils/searchSections.ts (a table
     cell or list item ends in " · " so snippets do not run them together, and
     a page's syntax-highlighting <style>, which showed up in result snippets
     as Shiki's CSS, is left out), and the page's HTML entities are decoded
     there too. On a /next/ URL answered
     by the stable build's root 404.html this searches the release docs, so the
     dialog says so. A Reference hit is ranked lower (below), so "raise budget"
     opens on the guide and not on the HTTP route. -->
<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'

const props = defineProps<{
  navigation?: ContentNavigationItem[]
}>()

const { forced: forcedColorMode } = useDocusColorMode()
const docs = useRuntimeConfig().public.docs
const at = useDocsChannel()

const { data: files } = useLazyAsyncData('search_docs', async () => {
  const pages = await queryCollection('docs').where('extension', '=', 'md').select('path', 'title', 'description', 'body').all()
  return searchSections(pages as never)
}, {
  server: false,
})

// Reference pages are the best match for nearly any word that is also the
// name of a route or a verb ("raise budget" found POST .../budget/raise
// first and the guide ninth), so a Reference hit is ranked as if it scored
// REFERENCE_PENALTY worse, which lets a good hit elsewhere lead but not hide
// it. A query that is itself a route, a flag or a verb (it has a / - _ or .)
// is a search for the reference, so it gets none of what follows either.
// The CLI verbs (utils/searchSections.ts, titled "kraft item approve") are
// exempt, because "approve" should find the command.
// Fuse's own score hardly tells hits apart (every page that has the word
// scores near 0, ties go to the earlier page, and "approve" listed Overview
// first), so it counts for FUSE_WEIGHT of the rank and the rest is how much
// of the query is in the hit's title (more for a verb) and in its text, with a
// page-level hit ahead of its own subsections. The match limit counts hits
// across every section, so at the default 12 a dozen reference rows left no
// room for the guide.
// Fuse is handed the page's path as one more, near-weightless key, because
// that is the only way its sort function can see which page a hit is: the
// path, the title and the text are values 0, 1 and 3 of its `item` (2 is the
// description, which search items lack). Nuxt UI merges these options with
// defu, which puts our keys ahead of its own: a Nuxt UI upgrade that changes
// that merge moves them, and every hit then counts as a Reference hit or none.
const REFERENCE_PENALTY = 0.55
const FUSE_WEIGHT = 0.3
const TITLE_BONUS = 0.2
const VERB_BONUS = 0.1
const BODY_BONUS = 0.05
const PAGE_BONUS = 0.25
const SECTION_PENALTY = 0.15
const term = ref('')
type Hit = { score: number, idx: number, item: Record<number, { v: string }> }
// The fraction of the query's words that appear in some text.
const covers = (text: string, words: string[]) => words.filter(word => text.includes(word)).length / words.length
const rank = (hit: Hit) => {
  const [to, label = '', body = ''] = [0, 1, 3].map(i => String(hit.item[i]?.v ?? ''))
  const query = term.value.toLowerCase()
  const words = query.split(/\s+/).filter(Boolean)
  const verb = label.startsWith(VERB_TITLE)
  const reference = /[/\-_.]/.test(query)
  return FUSE_WEIGHT * hit.score
    + (!reference && to.startsWith('/reference') && !verb ? REFERENCE_PENALTY : 0)
    + (reference ? 0 : to.includes('#') ? SECTION_PENALTY : -PAGE_BONUS)
    - (words.length ? (TITLE_BONUS + (verb ? VERB_BONUS : 0)) * covers(label.toLowerCase(), words) + BODY_BONUS * covers(body.toLowerCase(), words) : 0)
}
const fuse = {
  resultLimit: 40,
  fuseOptions: {
    keys: [{ name: 'to', weight: 0.01 }],
    sortFn: (a: Hit, b: Hit) => rank(a) - rank(b) || a.idx - b.idx,
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

const stableOnNext = computed(() => at.value.channel === 'next' && docs.channel !== 'next')
const releaseDocs = computed(() => `the ${docs.stableVersion || 'release'} docs`)
</script>

<template>
  <LazyUContentSearch
    :files="files"
    :navigation="searchNavigation"
    :fuse="fuse"
    v-model:search-term="term"
    :color-mode="!forcedColorMode"
    :title="stableOnNext ? `Search ${releaseDocs}` : undefined"
    :description="stableOnNext ? `This page is not in the next docs; this searches ${releaseDocs}.` : undefined"
    :placeholder="stableOnNext ? `Search ${releaseDocs}…` : undefined"
  />
</template>
