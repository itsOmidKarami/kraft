<script setup lang="ts">
// Docus's own error.vue (docus 5.13.0), minus its multi-language redirect,
// which this site doesn't use, plus two things for the docs builds. GitHub
// Pages answers a missing page anywhere, /next/ and /v1/ included, with the
// latest build's 404.html, so "Back to home" goes to the home of the version
// in the URL rather than the router's /, and a 404 offers the same page in the
// other versions: a page can exist in only some of them. The 404 copy is Kraft's own,
// and the same words as the markup nuxt.config.ts bakes into 404.html, which
// is all a reader sees until this page has loaded.
import type { NuxtError } from '#app'
import type { ContentNavigationItem, PageCollections } from '@nuxt/content'
import * as nuxtUiLocales from '@nuxt/ui/locale'
import { transformNavigation } from 'docus/app/utils/navigation'

const props = defineProps<{
  error: NuxtError
}>()

const { locale, isEnabled, t } = useDocusI18n()
const at = useDocsVersion()

const nuxtUiLocale = computed(() => nuxtUiLocales[locale.value as keyof typeof nuxtUiLocales] || nuxtUiLocales.en)
const lang = computed(() => nuxtUiLocale.value.code)
const dir = computed(() => nuxtUiLocale.value.dir)

useHead({
  htmlAttrs: {
    lang,
    dir,
  },
})

const missing = computed(() => props.error.statusCode === 404)

const localizedError = computed(() => {
  return {
    ...props.error,
    statusMessage: missing.value ? 'No page at this address' : t('common.error.title'),
    message: missing.value ? 'It may have been renamed or moved.' : t('common.error.description'),
  }
})

const others = computed(() => at.value.current && props.error.statusCode === 404
  ? at.value.versions.filter(v => v.id !== at.value.current!.id)
  : [])

useSeoMeta({
  title: () => localizedError.value.statusMessage,
  description: () => localizedError.value.message,
})

const collectionName = computed(() => isEnabled.value ? `docs_${locale.value}` : 'docs')

const { data: navigation } = await useAsyncData(`navigation_${collectionName.value}`, () => queryCollectionNavigation(collectionName.value as keyof PageCollections), {
  transform: (data: ContentNavigationItem[]) => transformNavigation(data, isEnabled.value, locale.value),
  watch: [locale],
})

provide('navigation', navigation)
</script>

<template>
  <UApp :locale="nuxtUiLocale">
    <AppHeader />

    <!-- UError renders its own <main>: the skip link's target goes on it, so
         the page has one main landmark, not one inside another. -->
    <UError id="main-content" tabindex="-1" :error="localizedError" :clear="!at.current">
      <template #message>
        {{ localizedError.message }}
        <span v-if="others.length" class="block mt-2">
          It may exist only in
          <template v-for="(v, i) in others" :key="v.id">{{ i ? (i === others.length - 1 ? ' or ' : ', ') : '' }}<a :href="at.samePage(v.base)" class="text-primary underline underline-offset-4">{{ v.name }}</a></template>.
        </span>
      </template>
      <template v-if="at.current" #links>
        <UButton size="lg" :label="nuxtUiLocale.messages.error.clear" :to="at.home" external />
      </template>
    </UError>

    <AppFooter />

    <ClientOnly>
      <AppSearch :navigation="navigation" />
    </ClientOnly>
  </UApp>
</template>
