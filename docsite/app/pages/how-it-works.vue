<script setup lang="ts">
// How it works, a marketing page: useSitePage.ts says which build draws it.
definePageMeta({ layout: 'site', header: false, footer: false })

const { home, target } = useSitePage('/how-it-works')
</script>

<template>
  <HowPage v-if="home" />
  <p v-else>
    This page is at <a :href="target">{{ target }}</a>.
  </p>
</template>
