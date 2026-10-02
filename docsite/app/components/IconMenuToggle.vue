<!-- Docus's phone menu button, which draws its icon in an SVG and so had no
     accessible name. Same button, named. -->
<script setup lang="ts">
import DocusIconMenuToggle from 'docus/app/components/IconMenuToggle.vue'

defineProps<{ open: boolean }>()
</script>

<template>
  <DocusIconMenuToggle
    :open="open"
    :aria-label="open ? 'Close navigation' : 'Open navigation'"
    :aria-expanded="open"
  />
</template>
