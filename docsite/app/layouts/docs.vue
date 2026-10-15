<!-- Docus's docs layout plus the /next/ banner; default.vue does the same.
     docs-page scopes the docs pages' heading type (assets/css/type.css) away
     from the landing pages, which use default.vue. #main-content (the skip
     link's target, server/plugins/skip-link.ts) wraps the page and not UMain,
     which also holds the sidebar: after "Skip to content" the next Tab would
     otherwise be the sidebar's first group, twenty stops before the h1. -->
<script setup lang="ts">
// With no contents column up to xl (app.config.ts, page), the sidebar takes 3
// of the 10 columns instead of 2: at 1024px that is 270px where "Troubleshooting"
// was cut to "Troubleshooti…" in 160.
const pageUi = {
  root: 'lg:grid lg:gap-6 xl:grid-cols-10 xl:gap-10',
  left: 'lg:col-span-3 xl:col-span-2',
  center: 'lg:col-span-7 xl:col-span-8',
}
</script>

<template>
  <VersionBanner />
  <UMain>
    <UContainer>
      <UPage class="docs-page" :ui="pageUi">
        <template #left>
          <UPageAside>
            <DocsAsideLeftTop />
            <DocsAsideLeftBody />
          </UPageAside>
        </template>
        <div id="main-content" tabindex="-1">
          <slot />
        </div>
      </UPage>
    </UContainer>
  </UMain>
</template>
