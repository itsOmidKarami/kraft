<!-- The header's Product menu: a disclosure (composables/useDisclosure.ts), a
     button and a list of links, not an ARIA menu. -->
<script setup lang="ts">
const root = useTemplateRef<HTMLElement>('root')
const button = useTemplateRef<HTMLElement>('button')
const { open, onFocusOut } = useDisclosure(root, button, '(max-width: 1023px)')
</script>

<template>
  <div
    ref="root"
    class="s-menu"
    @focusout="onFocusOut"
  >
    <button
      ref="button"
      type="button"
      class="s-menu-button"
      :aria-expanded="open"
      aria-controls="product-menu"
      @click="open = !open"
    >
      Product<svg
        aria-hidden="true"
        width="12"
        height="12"
        viewBox="0 0 12 12"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      ><path d="M3 4.5l3 3 3-3" /></svg>
    </button>
    <ul
      v-show="open"
      id="product-menu"
      class="s-menu-list"
    >
      <li
        v-for="p in PRODUCT_PAGES"
        :key="p.to"
      >
        <NuxtLink
          class="s-menu-item"
          :to="p.to"
        >
          <span class="s-menu-name">{{ p.name }}</span>
          <span class="s-menu-line">{{ p.line }}</span>
        </NuxtLink>
      </li>
    </ul>
  </div>
</template>
