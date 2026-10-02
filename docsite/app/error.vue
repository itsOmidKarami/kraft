<script setup lang="ts">
// Docus's own error.vue (docus 5.13.0), minus its multi-language redirect,
// which this site doesn't use, plus two things for the two docs builds. GitHub
// Pages answers a missing page anywhere, /next/ included, with the stable
// build's 404.html, so "Back to home" goes to the home of the channel in the
// URL rather than the router's /, and a 404 offers the same page in the other
// channel: a page can exist in only one of them.
import type { NuxtError } from '#app'
import type { ContentNavigationItem, PageCollections } from '@nuxt/content'
import * as nuxtUiLocales from '@nuxt/ui/locale'
import { transformNavigation } from 'docus/app/utils/navigation'

const props = defineProps<{
  error: NuxtError
}>()

const { locale, isEnabled, t } = useDocusI18n()
const docs = useRuntimeConfig().public.docs
const at = useDocsChannel()

const nuxtUiLocale = computed(() => nuxtUiLocales[locale.value as keyof typeof nuxtUiLocales] || nuxtUiLocales.en)
const lang = computed(() => nuxtUiLocale.value.code)
const dir = computed(() => nuxtUiLocale.value.dir)

useHead({
  htmlAttrs: {
    lang,
    dir,
  },
})

const localizedError = computed(() => {
  return {
    ...props.error,
    statusMessage: t('common.error.title'),
    message: t('common.error.description'),
  }
})

const other = computed(() => {
  if (!at.value.channel || props.error.statusCode !== 404) return null
  return at.value.channel === 'next'
    ? { label: `the ${docs.stableVersion || 'release'} docs`, href: at.value.samePage(docs.stableBase) }
    : { label: 'the next docs, for unreleased main', href: at.value.samePage(docs.nextBase) }
})

useSeoMeta({
  title: () => t('common.error.title'),
  description: () => t('common.error.description'),
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

    <UError :error="localizedError" :clear="!at.channel">
      <template #message>
        {{ localizedError.message }}
        <span v-if="other" class="block mt-2">
          It may exist only in <a :href="other.href" class="text-primary underline underline-offset-4">{{ other.label }}</a>.
        </span>
      </template>
      <template v-if="at.channel" #links>
        <UButton size="lg" :label="nuxtUiLocale.messages.error.clear" :to="at.home" external />
      </template>
    </UError>

    <AppFooter />

    <ClientOnly>
      <AppSearch :navigation="navigation" />
    </ClientOnly>
  </UApp>
</template>
