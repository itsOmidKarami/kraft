<script setup lang="ts">
// The home page. With this file in app/pages Docus defines no landing route or
// collection of its own (docus/utils/pages.ts), so no build renders a
// content/index.md. useSitePage.ts says which build draws it.
definePageMeta({ layout: 'site', header: false, footer: false })

const { home, target } = useSitePage('/')
</script>

<template>
  <HomePage v-if="home" />
  <p v-else>
    The home page of Kraft is at <a :href="target">{{ target }}</a>.
  </p>
</template>
