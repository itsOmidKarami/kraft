<script setup lang="ts">
// Docus's own DocsAsideRight.vue (docus 5.13.0), with one change: the entries
// of "On this page" carry break opportunities. A browser breaks a line after a
// space or a hyphen and after nothing else in
// `POST /api/work-items/{id}/gates/{gate}/approve`, so the entry was cut inside
// a word ("approv" / "e": 267 cuts at 1024px and 71 at 1280px, most of them on
// the six HTTP route pages). <wbr> goes after a `/`, `_`, `-`, `.`, `?`, `=` or
// `&` that stands between two other characters (not between two of them:
// `--start-side` has no break after its first hyphen), so an entry breaks
// there, and inside a segment only when the segment is longer than the column
// (break-words, as in app.config.ts, contentToc, which the slot replaces).
// package.json pins docus to exactly 5.13.0 for this copy: on a bump, copy the
// new release's file again and reapply the #link slot.
import type { DocsCollectionItem } from '@nuxt/content'

const props = defineProps<{
  page?: DocsCollectionItem | null
}>()

const links = computed(() => props.page?.body?.toc?.links || [])

const { subNavigationMode } = useSubNavigation()
const appConfig = useAppConfig()
const { t } = useDocusI18n()

const contentTocVariants = useUIConfig('contentToc')

const segments = (text: string) => text.split(/(?<=[^/_.\-?=&\s][/_.\-?=&])(?=[^/_.\-?=&\s])/)
</script>

<template>
  <div>
    <UContentToc
      v-if="links.length"
      :highlight="contentTocVariants.highlight ?? true"
      :highlight-color="contentTocVariants.highlightColor"
      :highlight-variant="contentTocVariants.highlightVariant ?? 'circuit'"
      :color="contentTocVariants.color"
      :title="appConfig.toc?.title || t('docs.toc')"
      :links="links"
      :class="{ 'hidden lg:block': subNavigationMode }"
    >
      <template #link="{ link }">
        <span data-slot="linkText" class="truncate whitespace-normal! break-words">
          <template v-for="(part, index) in segments(link.text)" :key="index"><wbr v-if="index">{{ part }}</template>
        </span>
      </template>
      <template #bottom>
        <DocsAsideRightBottom />
      </template>
    </UContentToc>

    <DocsAsideMobileBar :links="links" />
  </div>
</template>
