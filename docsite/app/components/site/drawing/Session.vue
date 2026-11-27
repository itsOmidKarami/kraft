<!-- Why Kraft's head: a session, where you stay through every agent step, over
     a chain of agent and Kraft steps that reaches you only at its gates. The
     board's 536-wide drawing and a 300-wide one for a narrow panel. Amber is
     "you": the You lane, the line you hold and the gates. -->
<script setup lang="ts">
import type { Glyph } from '~/utils/glyphs'

const LABEL = 'Two drawings. In a session, you stay for all five agent steps. With Kraft, a chain of agent and Kraft steps runs on, and reaches you at two gates'
// The chain's steps in order; `gate` is a stop for you, drawn in the You lane.
const CHAIN: (Glyph | 'gate')[] = ['agent', 'gate', 'agent', 'kraft', 'gate', 'kraft', 'kraft']
const SIZES = [
  { cls: 's-draw-big', w: 536, g: 36, you: 16, session: [108, 496], chain: [76, 496] },
  { cls: 's-draw-small', w: 300, g: 28, you: 12, session: [64, 282], chain: [44, 282] },
]
const spread = ([a, b]: number[], n: number) => Array.from({ length: n }, (_, i) => a! + (b! - a!) * i / (n - 1))
const sizes = SIZES.map(s => ({
  ...s,
  steps: spread(s.session, 5),
  chainAt: spread(s.chain, CHAIN.length).map((x, i) => ({ x, step: CHAIN[i]! })),
}))
</script>

<template>
  <svg
    v-for="s in sizes"
    :key="s.w"
    role="img"
    :aria-label="LABEL"
    class="s-draw s-draw-session"
    :class="s.cls"
    :viewBox="`0 0 ${s.w} 324`"
    :style="{ maxWidth: `${s.w}px` }"
  >
    <text
      class="s-draw-label s-draw-text"
      x="0"
      y="12"
    >A SESSION</text>
    <text
      class="s-draw-label"
      :x="s.w"
      y="12"
      text-anchor="end"
    >YOU STAY FOR ALL OF IT</text>
    <rect
      class="s-draw-you-lane"
      x="0"
      y="24"
      :width="s.w"
      height="56"
      rx="12"
    />
    <text
      class="s-draw-label s-draw-you"
      :x="s.you"
      y="45"
    >YOU</text>
    <line
      class="s-draw-you-line"
      :x1="s.session[0]"
      y1="52"
      :x2="s.session[1]"
      y2="52"
    />
    <g class="s-draw-line">
      <polyline :points="`${s.session[0]},120 ${s.session[1]},120`" />
    </g>
    <SiteDrawingNode
      v-for="x in s.steps"
      :key="x"
      :x="x - s.g / 2"
      :y="120 - s.g / 2"
      :g="s.g"
      glyph="agent"
    />

    <text
      class="s-draw-label s-draw-text"
      x="0"
      y="196"
    >KRAFT</text>
    <text
      class="s-draw-label"
      :x="s.w"
      y="196"
      text-anchor="end"
    >IT REACHES YOU AT THE GATES</text>
    <rect
      class="s-draw-you-lane"
      x="0"
      y="208"
      :width="s.w"
      height="56"
      rx="12"
    />
    <text
      class="s-draw-label s-draw-you"
      :x="s.you"
      y="229"
    >YOU</text>
    <g class="s-draw-line">
      <polyline :points="`${s.chain[0]},304 ${s.chain[1]},304`" />
    </g>
    <template
      v-for="c in s.chainAt"
      :key="c.x"
    >
      <g
        v-if="c.step === 'gate'"
        class="s-draw-gate"
      >
        <line
          :x1="c.x"
          y1="252"
          :x2="c.x"
          y2="304"
        />
        <rect
          :x="c.x - 9"
          y="231"
          width="18"
          height="18"
          rx="4"
          :transform="`rotate(45 ${c.x} 240)`"
        />
        <circle
          :cx="c.x"
          cy="240"
          r="2.5"
        />
        <circle
          :cx="c.x"
          cy="304"
          r="5"
        />
      </g>
      <SiteDrawingNode
        v-else
        :x="c.x - s.g / 2"
        :y="304 - s.g / 2"
        :g="s.g"
        :glyph="c.step"
      />
    </template>
  </svg>
</template>
