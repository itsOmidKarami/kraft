<!-- The marketing pages' header, on the violet field in both themes. From
     1024px: the wordmark, the Product menu, three links and Install. Below:
     the wordmark, Install and the phone menu's button. Each set is hidden at
     the other width (assets/css/site.css). -->
<template>
  <header class="s-header s-field">
    <div class="s-wrap s-header-row">
      <NuxtLink
        class="s-wordmark"
        to="/"
      >
        Kraft
      </NuxtLink>
      <nav
        class="s-nav"
        aria-label="Main"
      >
        <SiteProductMenu />
        <NuxtLink
          class="s-nav-link"
          to="/why"
        >
          Why Kraft
        </NuxtLink>
        <NuxtLink
          class="s-nav-link"
          to="/get-started"
        >
          Docs
        </NuxtLink>
        <a
          class="s-nav-link"
          :href="REPO"
        >GitHub</a>
        <NuxtLink
          class="s-btn s-btn-primary s-btn-sm"
          :to="INSTALL"
        >
          Install
        </NuxtLink>
      </nav>
      <div class="s-header-phone">
        <NuxtLink
          class="s-btn s-btn-primary s-btn-sm"
          :to="INSTALL"
        >
          Install
        </NuxtLink>
        <SitePhoneMenu />
      </div>
    </div>
  </header>
</template>
