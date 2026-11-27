<!-- One plan splits into three slices built at once, then Kraft joins them.
     Three sizes from the boards: `card` (Home's card, 404 wide), `wide` (Scale
     up, 536) and `compact` (a phone, 258, smaller glyphs and a shorter label).
     Its colours are site.css's .s-draw rules. -->
<script setup lang="ts">
const props = withDefaults(defineProps<{ size?: 'card' | 'wide' | 'compact' }>(), { size: 'card' })

const SIZES = {
  card: { w: 404, g: 44, r: 12, gap: 14, bus: 34, lift: 36, label: '3 SLICES, BUILT AT ONCE' },
  wide: { w: 536, g: 44, r: 12, gap: 14, bus: 56, lift: 36, label: '3 SLICES, BUILT AT ONCE' },
  compact: { w: 258, g: 36, r: 10, gap: 12, bus: 24, lift: 20, label: '3 SLICES' },
}

const d = computed(() => {
  const { w, g, r, gap, bus, lift, label } = SIZES[props.size]
  const ys = [0, g + gap, 2 * (g + gap)]
  const mid = ys.map(y => y + g / 2)
  const left = g + bus
  const right = w - g - bus
  const sx = (w - g) / 2
  const scale = g / 52.8
  const glyph = (x: number, y: number) => `translate(${x + (g - 24 * scale) / 2} ${y + (g - 24 * scale) / 2}) scale(${scale})`
  const labelY = ys[2]! + g + lift
  return { w, g, r, ys, mid, left, right, sx, glyph, stroke: 2 / scale, label, labelY, h: labelY + 8 }
})
</script>

<template>
  <svg
    role="img"
    aria-label="One plan splits into three slices that are built at once, then Kraft joins them"
    class="s-draw"
    :viewBox="`0 0 ${d.w} ${d.h}`"
    :style="{ maxWidth: `${d.w}px` }"
  >
    <g class="s-draw-line">
      <polyline :points="`${d.g},${d.mid[1]} ${d.left},${d.mid[1]}`" />
      <polyline :points="`${d.left},${d.mid[0]} ${d.left},${d.mid[2]}`" />
      <polyline :points="`${d.right},${d.mid[0]} ${d.right},${d.mid[2]}`" />
      <polyline :points="`${d.right},${d.mid[1]} ${d.w - d.g},${d.mid[1]}`" />
      <template
        v-for="m in d.mid"
        :key="m"
      >
        <polyline :points="`${d.left},${m} ${d.sx},${m}`" />
        <polyline :points="`${d.sx + d.g},${m} ${d.right},${m}`" />
      </template>
    </g>
    <g
      v-for="y in d.ys"
      :key="y"
      class="s-draw-node s-draw-blue"
    >
      <rect
        :x="d.sx"
        :y="y"
        :width="d.g"
        :height="d.g"
        :rx="d.r"
      />
      <g
        :transform="d.glyph(d.sx, y)"
        :stroke-width="d.stroke"
      ><path
        v-for="p in GLYPHS.agent"
        :key="p"
        :d="p"
      /></g>
    </g>
    <g
      v-for="x in [0, d.w - d.g]"
      :key="x"
      class="s-draw-node"
    >
      <rect
        :x="x"
        :y="d.ys[1]"
        :width="d.g"
        :height="d.g"
        :rx="d.r"
      />
      <g
        :transform="d.glyph(x, d.ys[1]!)"
        :stroke-width="d.stroke"
      ><path
        v-for="p in GLYPHS.kraft"
        :key="p"
        :d="p"
      /></g>
    </g>
    <text
      class="s-draw-label"
      x="0"
      :y="d.labelY"
    >ONE PLAN</text>
    <text
      class="s-draw-label s-draw-blue"
      :x="d.w / 2"
      :y="d.labelY"
      text-anchor="middle"
    >{{ d.label }}</text>
    <text
      class="s-draw-label"
      :x="d.w"
      :y="d.labelY"
      text-anchor="end"
    >JOINED</text>
  </svg>
</template>
