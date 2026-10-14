<!-- Docus's docs layout plus the /next/ banner; default.vue does the same.
     docs-page scopes the docs pages' heading type (assets/css/type.css) away
     from the landing pages, which use default.vue. The banner's region and
     #main-content are explained there. -->
<script setup lang="ts">
const at = useDocsChannel()
</script>

<template>
  <div v-if="at.channel === 'next'" role="region" aria-label="Version notice">
    <VersionBanner />
  </div>
  <UMain id="main-content" tabindex="-1">
    <UContainer>
      <UPage class="docs-page">
        <template #left>
          <UPageAside>
            <DocsAsideLeftTop />
            <DocsAsideLeftBody />
          </UPageAside>
        </template>
        <slot />
      </UPage>
    </UContainer>
  </UMain>
</template>
