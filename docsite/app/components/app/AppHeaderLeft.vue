<script setup lang="ts">
// Docus's own AppHeaderLeft.vue (docus 5.13.0), the header logo, except that on
// the docs builds it goes to the one home page, at the root of the domain,
// from every version: the home is not versioned (pages/index.vue). A plain
// <a>, not NuxtLink, like the version menu: from /next/ or /vN/ the home
// belongs to another build. package.json pins docus to exactly 5.13.0 for this copy.
const appConfig = useAppConfig()
const site = useSiteConfig()
const { localePath } = useDocusI18n()
const at = useDocsVersion()

const ariaLabel = appConfig.header?.title || site.name
</script>

<template>
  <a
    v-if="at.current"
    :href="at.latest?.base ?? '/'"
    :aria-label="ariaLabel"
  >
    <AppHeaderLogo class="h-6 w-auto shrink-0" />
  </a>
  <NuxtLink
    v-else
    :to="localePath('/')"
    :aria-label="ariaLabel"
  >
    <AppHeaderLogo class="h-6 w-auto shrink-0" />
  </NuxtLink>
</template>
