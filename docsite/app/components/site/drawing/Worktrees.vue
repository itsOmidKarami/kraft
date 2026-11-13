<!-- Your checkout is one line; three work items fork from it onto a branch and
     worktree of their own and run side by side. The board's 536-wide drawing
     and a 300-wide one for a narrow panel, from one set of measures. -->
<script setup lang="ts">
const LABEL = 'Your checkout is one line. Three work items each fork from it onto a branch and worktree of their own and run side by side'
const SIZES = [
  { cls: 's-draw-big', w: 536, g: 36, xs: [112, 76, 40], ys: [104, 164, 224], h: 246 },
  { cls: 's-draw-small', w: 300, g: 30, xs: [96, 66, 36], ys: [96, 146, 196], h: 220 },
]
const sizes = SIZES.map(s => ({
  ...s,
  // Two glyphs per line, half a glyph apart and half a glyph in from the end.
  gx: [s.w - 3 * s.g, s.w - 1.5 * s.g],
}))
</script>

<template>
  <svg
    v-for="s in sizes"
    :key="s.w"
    role="img"
    :aria-label="LABEL"
    class="s-draw"
    :class="s.cls"
    :viewBox="`0 0 ${s.w} ${s.h}`"
    :style="{ maxWidth: `${s.w}px` }"
  >
    <polyline
      class="s-draw-main"
      :points="`0,40 ${s.w},40`"
    />
    <text
      class="s-draw-label s-draw-text"
      x="0"
      y="22"
    >YOUR CHECKOUT</text>
    <text
      class="s-draw-label"
      :x="s.w"
      y="22"
      text-anchor="end"
    >LEFT AS IT WAS</text>
    <template
      v-for="(x, i) in s.xs"
      :key="x"
    >
      <g class="s-draw-line">
        <polyline :points="`${x},40 ${x},${s.ys[i]} ${s.w},${s.ys[i]}`" />
      </g>
      <SiteDrawingNode
        :x="s.gx[0]!"
        :y="s.ys[i]! - s.g / 2"
        :g="s.g"
        glyph="agent"
      />
      <SiteDrawingNode
        :x="s.gx[1]!"
        :y="s.ys[i]! - s.g / 2"
        :g="s.g"
        glyph="kraft"
        tone="blue"
      />
      <text
        class="s-draw-label s-draw-sub"
        :x="x + 16"
        :y="s.ys[i]! - s.g / 3"
      >WORK ITEM {{ i + 1 }}</text>
    </template>
  </svg>
</template>
