<!-- Docus's default layout plus the /next/ banner; docs.vue does the same. The
     banner sits in a labelled region of its own, or it is the one block of the
     page outside every landmark. #main-content is where the skip link lands
     (server/plugins/skip-link.ts). -->
<script setup lang="ts">
const at = useDocsChannel()
</script>

<template>
  <div v-if="at.channel === 'next'" role="region" aria-label="Version notice">
    <VersionBanner />
  </div>
  <UMain id="main-content" tabindex="-1">
    <slot />
  </UMain>
</template>
