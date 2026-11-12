<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)

// A link with `href` leaves the router: the repo, the demo's static files.
const COLUMNS = [
  {
    title: 'Product',
    links: [
      ...PRODUCT_PAGES.map(p => ({ label: p.name, to: p.to })),
      { label: 'Why Kraft', to: '/why' },
      { label: 'Live demo', href: demo },
      { label: 'What\'s new', to: '/project/whats-new' },
    ],
  },
  {
    title: 'Docs',
    links: [
      { label: 'Install', to: INSTALL },
      { label: 'Your first work item', to: '/get-started/first-work-item' },
      { label: 'Concepts', to: '/concepts' },
      { label: 'Reference', to: '/reference' },
      { label: 'FAQ', to: '/get-started/faq' },
      { label: 'Troubleshooting', to: '/troubleshooting' },
    ],
  },
  {
    title: 'Project',
    links: [
      { label: 'GitHub', href: REPO },
      { label: 'Security', to: '/project/security' },
      { label: 'Data and privacy', to: '/project/data-and-privacy' },
      { label: 'Status and support', to: '/project/status-and-support' },
      { label: 'Contributing', to: '/project/contributing' },
      { label: 'License (Apache-2.0)', href: `${REPO}/blob/main/LICENSE` },
    ],
  },
]
</script>

<template>
  <footer class="s-footer">
    <div class="s-wrap s-footer-grid">
      <div class="s-footer-brand">
        <NuxtLink
          class="s-wordmark"
          to="/"
        >
          Kraft
        </NuxtLink>
        <p>Kraft is German for force. Its English cousin, craft, means skill.</p>
        <SiteThemeSwitch />
      </div>
      <div class="s-footer-cols">
        <nav
          v-for="col in COLUMNS"
          :key="col.title"
          class="s-footer-col"
          :aria-label="col.title"
        >
          <p
            class="s-label-small"
            aria-hidden="true"
          >
            {{ col.title }}
          </p>
          <ul>
            <li
              v-for="l in col.links"
              :key="l.label"
            >
              <a
                v-if="l.href"
                :href="l.href"
              >{{ l.label }}</a>
              <NuxtLink
                v-else
                :to="l.to"
              >
                {{ l.label }}
              </NuxtLink>
            </li>
          </ul>
        </nav>
      </div>
    </div>
  </footer>
</template>
