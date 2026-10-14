<!-- Docus's docs layout plus the /next/ banner; default.vue does the same.
     docs-page scopes the docs pages' heading type (assets/css/type.css) away
     from the landing pages, which use default.vue. -->
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
        <slot />
      </UPage>
    </UContainer>
  </UMain>
</template>
