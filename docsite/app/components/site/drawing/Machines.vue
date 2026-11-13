<!-- A chain runs on your machine; one step goes out to another machine and
     comes back. The board's 536-wide drawing and a 300-wide one for a narrow
     panel. The other machine's lane is blue: it runs somewhere else. -->
<script setup lang="ts">
const LABEL = 'A chain runs on your machine, one step runs on another machine, and the chain comes back'
const SIZES = [
  {
    cls: 's-draw-big', w: 536, g: 44, h: 212, inset: 18,
    xs: [128, 246, 364], start: 40, end: 496, turns: [220, 316], out: [210, 326],
  },
  {
    cls: 's-draw-small', w: 300, g: 36, h: 212, inset: 14,
    xs: [56, 132, 208], start: 20, end: 280, turns: [118, 182], out: [110, 190],
  },
]
// Lane centres: your machine's line, the other machine's.
const TOP = 62
const LOW = 170
</script>

<template>
  <svg
    v-for="s in SIZES"
    :key="s.w"
    role="img"
    :aria-label="LABEL"
    class="s-draw"
    :class="s.cls"
    :viewBox="`0 0 ${s.w} ${s.h}`"
    :style="{ maxWidth: `${s.w}px` }"
  >
    <rect
      class="s-draw-lane"
      x="1"
      y="1"
      :width="s.w - 2"
      height="98"
      rx="12"
    />
    <rect
      class="s-draw-lane-blue"
      x="1"
      y="113"
      :width="s.w - 2"
      height="98"
      rx="12"
    />
    <text
      class="s-draw-label s-draw-text"
      :x="s.inset"
      y="26"
    >YOUR MACHINE</text>
    <text
      class="s-draw-label"
      :x="s.w - s.inset"
      y="26"
      text-anchor="end"
    >BOARD · GATES · PUSHES</text>
    <text
      class="s-draw-label s-draw-blue"
      :x="s.inset"
      y="138"
    >ANOTHER MACHINE</text>
    <text
      class="s-draw-label s-draw-blue"
      :x="s.w - s.inset"
      y="138"
      text-anchor="end"
    >RUNS THE STEP</text>
    <g class="s-draw-line">
      <polyline :points="`${s.start},${TOP} ${s.xs[0]},${TOP}`" />
      <polyline :points="`${s.xs[0]! + s.g},${TOP} ${s.turns[0]},${TOP} ${s.turns[0]},${LOW} ${s.xs[1]},${LOW}`" />
      <polyline :points="`${s.xs[1]! + s.g},${LOW} ${s.turns[1]},${LOW} ${s.turns[1]},${TOP} ${s.xs[2]},${TOP}`" />
      <polyline :points="`${s.xs[2]! + s.g},${TOP} ${s.end},${TOP}`" />
      <polyline :points="`${s.xs[1]! - 7},${LOW - 5} ${s.xs[1]},${LOW} ${s.xs[1]! - 7},${LOW + 5}`" />
      <polyline :points="`${s.xs[2]! - 7},${TOP - 5} ${s.xs[2]},${TOP} ${s.xs[2]! - 7},${TOP + 5}`" />
    </g>
    <SiteDrawingNode
      :x="s.xs[0]!"
      :y="TOP - s.g / 2"
      :g="s.g"
      glyph="kraft"
    />
    <SiteDrawingNode
      :x="s.xs[1]!"
      :y="LOW - s.g / 2"
      :g="s.g"
      glyph="agent"
      tone="blue"
    />
    <SiteDrawingNode
      :x="s.xs[2]!"
      :y="TOP - s.g / 2"
      :g="s.g"
      glyph="kraft"
    />
    <text
      class="s-draw-label"
      :x="s.out[0]"
      y="110"
      text-anchor="end"
    >OUT</text>
    <text
      class="s-draw-label"
      :x="s.out[1]"
      y="110"
    >BACK</text>
  </svg>
</template>
