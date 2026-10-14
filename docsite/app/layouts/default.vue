<!-- Docus's default layout plus the /next/ banner; docs.vue does the same. The
     banner is a labelled region of its own (VersionBanner.vue). #main-content is where the skip link lands
     (server/plugins/skip-link.ts). -->
<template>
  <VersionBanner />
  <UMain id="main-content" tabindex="-1">
    <slot />
  </UMain>
</template>
