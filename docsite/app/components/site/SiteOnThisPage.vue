<!-- The strip under a capability page's head: a link to each block. -->
<script setup lang="ts">
defineProps<{ links: { id: string, label: string }[] }>()
</script>

<template>
  <nav
    class="s-onpage"
    aria-labelledby="on-this-page"
  >
    <div class="s-wrap s-onpage-row">
      <span
        id="on-this-page"
        class="s-label-small"
      >On this page</span>
      <a
        v-for="l in links"
        :key="l.id"
        :href="`#${l.id}`"
      >{{ l.label }}</a>
    </div>
  </nav>
</template>
