<!-- The phone header's menu: a disclosure (composables/useDisclosure.ts) whose
     list fills the screen under the header. The page behind it does not scroll
     while it is open. -->
<script setup lang="ts">
const root = useTemplateRef<HTMLElement>('root')
const button = useTemplateRef<HTMLElement>('button')
const { open, onFocusOut } = useDisclosure(root, button, '(min-width: 1024px)')
const demo = demoHref(useRuntimeConfig().app.baseURL)

watch(open, now => document.documentElement.classList.toggle('s-locked', now))
onBeforeUnmount(() => document.documentElement.classList.remove('s-locked'))
</script>

<template>
  <div
    ref="root"
    @focusout="onFocusOut"
  >
    <button
      ref="button"
      type="button"
      class="s-icon-button"
      aria-label="Menu"
      :aria-expanded="open"
      aria-controls="phone-menu"
      @click="open = !open"
    >
      <svg
        aria-hidden="true"
        width="20"
        height="20"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
      >
        <template v-if="open"><path d="M5 5l10 10" /><path d="M15 5L5 15" /></template>
        <template v-else><path d="M3 6h14" /><path d="M3 10h14" /><path d="M3 14h14" /></template>
      </svg>
    </button>
    <nav
      v-show="open"
      id="phone-menu"
      class="s-sheet s-field"
      aria-label="Main"
    >
      <div>
        <p
          id="phone-menu-product"
          class="s-label-small s-sheet-label"
        >
          Product
        </p>
        <ul
          class="s-sheet-pages"
          aria-labelledby="phone-menu-product"
        >
          <li
            v-for="p in PRODUCT_PAGES"
            :key="p.to"
          >
            <NuxtLink :to="p.to">
              <span class="s-sheet-name">{{ p.name }}</span>
              <span class="s-sheet-line">{{ p.line }}</span>
            </NuxtLink>
          </li>
        </ul>
      </div>
      <ul class="s-sheet-links">
        <li>
          <NuxtLink to="/why">
            Why Kraft
          </NuxtLink>
        </li>
        <li>
          <NuxtLink to="/get-started">
            Docs
          </NuxtLink>
        </li>
        <li><a :href="REPO">GitHub</a></li>
      </ul>
      <div class="s-sheet-actions">
        <NuxtLink
          class="s-btn s-btn-primary"
          :to="INSTALL"
        >
          Install
        </NuxtLink>
        <a
          class="s-btn s-btn-secondary"
          :href="demo"
        >Try the live demo</a>
      </div>
    </nav>
  </div>
</template>
