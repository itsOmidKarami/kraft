<script setup lang="ts">
// Why Kraft, a marketing page: useSitePage.ts says which build draws it.
definePageMeta({ layout: 'site', header: false, footer: false })

const { home, target } = useSitePage('/why')
</script>

<template>
  <WhyPage v-if="home" />
  <p v-else>
    This page is at <a :href="target">{{ target }}</a>.
  </p>
</template>
