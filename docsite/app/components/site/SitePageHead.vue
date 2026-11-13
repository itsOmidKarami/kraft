<!-- A capability page's head, on the violet field: where it sits in the site,
     the page's one h1, its lede (the default slot) and one picture beside it
     (the picture slot). With no picture the text takes one column. -->
<script setup lang="ts">
defineProps<{ label: string, title: string }>()
</script>

<template>
  <header class="s-pagehead s-field">
    <div
      class="s-wrap s-pagehead-grid"
      :class="{ 's-pagehead-solo': !$slots.picture }"
    >
      <div class="s-pagehead-text">
        <p class="s-block-label">
          {{ label }}
        </p>
        <h1 class="s-pagehead-title">
          {{ title }}
        </h1>
        <p class="s-lede">
          <slot />
        </p>
      </div>
      <slot name="picture" />
    </div>
  </header>
</template>
