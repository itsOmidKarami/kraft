<!-- One glyph box in a drawing: `g` px square at x, y. `done` is a solid
     muted outline, `blue` runs at once or elsewhere, `todo` is dashed (a
     node still to come). Colours: site.css's .s-draw rules. -->
<script setup lang="ts">
import type { Glyph } from '~/utils/glyphs'

const p = withDefaults(defineProps<{ x: number, y: number, glyph: Glyph, g?: number, tone?: 'done' | 'blue' | 'todo' }>(), { g: 44, tone: 'done' })
const s = computed(() => p.g / 52.8)
const at = computed(() => (p.g - 24 * s.value) / 2)
</script>

<template>
  <g
    class="s-draw-node"
    :class="{ 's-draw-blue': tone === 'blue', 's-draw-todo': tone === 'todo' }"
  >
    <rect
      :x="x"
      :y="y"
      :width="g"
      :height="g"
      :rx="g >= 44 ? 12 : 10"
    />
    <g
      :transform="`translate(${x + at} ${y + at}) scale(${s})`"
      :stroke-width="2 / s"
    ><path
      v-for="d in GLYPHS[glyph]"
      :key="d"
      :d="d"
    /></g>
  </g>
</template>
