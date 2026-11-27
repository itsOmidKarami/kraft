<script setup lang="ts">
// Scale up, a marketing page: useSitePage.ts says which build draws it.
definePageMeta({ layout: 'site', header: false, footer: false })

const { home, target } = useSitePage('/scale')
</script>

<template>
  <ScalePage v-if="home" />
  <p v-else>
    This page is at <a :href="target">{{ target }}</a>.
  </p>
</template>
