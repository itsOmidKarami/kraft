<script setup lang="ts">
// Product tour, a marketing page: useSitePage.ts says which build draws it.
definePageMeta({ layout: 'site', header: false, footer: false })

const { home, target } = useSitePage('/tour')
</script>

<template>
  <TourPage v-if="home" />
  <p v-else>
    This page is at <a :href="target">{{ target }}</a>.
  </p>
</template>
