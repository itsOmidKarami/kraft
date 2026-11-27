<!-- The marketing pages (docsite/DESIGN.md, assets/css/site.css). A page using
     it sets header: false and footer: false, so Docus's app.vue draws neither
     of its own. #main-content is where the skip link lands
     (server/plugins/skip-link.ts). The /next/ and /vN/ builds answer these
     pages with a redirect (composables/useSitePage.ts), so they get no header
     or footer. -->
<script setup lang="ts">
const home = isHomeBuild(useRuntimeConfig().app.baseURL)
</script>

<template>
  <div class="site">
    <SiteHeader v-if="home" />
    <main
      id="main-content"
      tabindex="-1"
    >
      <slot />
    </main>
    <SiteFooter v-if="home" />
  </div>
</template>
