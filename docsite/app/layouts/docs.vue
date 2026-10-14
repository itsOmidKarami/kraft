<!-- Docus's docs layout plus the /next/ banner; default.vue does the same.
     docs-page scopes the docs pages' heading type (assets/css/type.css) away
     from the landing pages, which use default.vue. #main-content (the skip
     link's target, server/plugins/skip-link.ts) wraps the page and not UMain,
     which also holds the sidebar: after "Skip to content" the next Tab would
     otherwise be the sidebar's first group, twenty stops before the h1. -->
<template>
  <VersionBanner />
  <UMain>
    <UContainer>
      <UPage class="docs-page">
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
