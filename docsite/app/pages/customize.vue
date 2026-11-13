<script setup lang="ts">
// Make it yours, a marketing page: useSitePage.ts says which build draws it.
definePageMeta({ layout: 'site', header: false, footer: false })

const { home, target } = useSitePage('/customize')
</script>

<template>
  <CustomizePage v-if="home" />
  <p v-else>
    This page is at <a :href="target">{{ target }}</a>.
  </p>
</template>
