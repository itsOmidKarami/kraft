<!-- One of the two glyphs (utils/glyphs.ts) as a stroke icon in currentColor. -->
<script setup lang="ts">
import type { Glyph } from '~/utils/glyphs'

withDefaults(defineProps<{ name: Glyph, size?: number }>(), { size: 20 })
</script>

<template>
  <svg
    aria-hidden="true"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
  ><path
    v-for="d in GLYPHS[name]"
    :key="d"
    :d="d"
  /></svg>
</template>
