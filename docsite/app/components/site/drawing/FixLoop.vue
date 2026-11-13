<!-- The verification node's fix loop: tests, then a code review; a pass goes
     on, a failure is repaired and the whole node measured again. The board's
     536-wide drawing, and a 300-wide one for a narrow panel (site.css shows
     one by the panel's width). -->
<script setup lang="ts">
const LABEL = 'The verification node runs the tests and a code review. If it passes, the chain goes on. If it fails, an agent repairs the change and the whole node is measured again'
</script>

<template>
  <svg
    role="img"
    :aria-label="LABEL"
    class="s-draw s-draw-big"
    viewBox="0 0 536 246"
    style="max-width: 536px"
  >
    <rect
      class="s-draw-scope"
      x="1"
      y="1"
      width="350"
      height="128"
      rx="12"
    />
    <text
      class="s-draw-label"
      x="16"
      y="24"
    >VERIFICATION NODE</text>
    <g class="s-draw-line">
      <polyline points="122,74 218,74" />
      <polyline points="351,74 470,74" />
      <polyline points="463,69 470,74 463,79" />
      <polyline points="240,129 240,192" />
      <polyline points="235,185 240,192 245,185" />
      <polyline points="218,220 60,220 60,133" />
      <polyline points="55,138 60,131 65,138" />
    </g>
    <SiteDrawingNode
      :x="78"
      :y="52"
      glyph="kraft"
    />
    <SiteDrawingNode
      :x="218"
      :y="52"
      glyph="agent"
    />
    <SiteDrawingNode
      :x="478"
      :y="52"
      glyph="kraft"
      tone="todo"
    />
    <SiteDrawingNode
      :x="218"
      :y="198"
      glyph="agent"
    />
    <text
      class="s-draw-label s-draw-sub"
      x="100"
      y="116"
      text-anchor="middle"
    >TESTS</text>
    <text
      class="s-draw-label s-draw-sub"
      x="240"
      y="116"
      text-anchor="middle"
    >CODE REVIEW</text>
    <text
      class="s-draw-label"
      x="410"
      y="62"
      text-anchor="middle"
    >PASSES</text>
    <text
      class="s-draw-label"
      x="500"
      y="116"
      text-anchor="middle"
    >NEXT NODE</text>
    <text
      class="s-draw-label"
      x="254"
      y="166"
    >FAILS</text>
    <text
      class="s-draw-label s-draw-sub"
      x="274"
      y="224"
    >REPAIR</text>
    <text
      class="s-draw-label"
      x="74"
      y="206"
    >MEASURED AGAIN</text>
  </svg>
  <svg
    role="img"
    :aria-label="LABEL"
    class="s-draw s-draw-small"
    viewBox="0 0 300 290"
    style="max-width: 300px"
  >
    <rect
      class="s-draw-scope"
      x="1"
      y="1"
      width="298"
      height="128"
      rx="12"
    />
    <text
      class="s-draw-label"
      x="16"
      y="24"
    >VERIFICATION NODE</text>
    <g class="s-draw-line">
      <polyline points="82,70 188,70" />
      <polyline points="262,129 262,186" />
      <polyline points="257,179 262,186 267,179" />
      <polyline points="110,129 110,186" />
      <polyline points="105,179 110,186 115,179" />
      <polyline points="110,228 110,262 24,262 24,135" />
      <polyline points="19,142 24,135 29,142" />
    </g>
    <SiteDrawingNode
      :x="46"
      :y="52"
      :g="36"
      glyph="kraft"
    />
    <SiteDrawingNode
      :x="188"
      :y="52"
      :g="36"
      glyph="agent"
    />
    <SiteDrawingNode
      :x="244"
      :y="192"
      :g="36"
      glyph="kraft"
      tone="todo"
    />
    <SiteDrawingNode
      :x="92"
      :y="192"
      :g="36"
      glyph="agent"
    />
    <text
      class="s-draw-label s-draw-sub"
      x="64"
      y="112"
      text-anchor="middle"
    >TESTS</text>
    <text
      class="s-draw-label s-draw-sub"
      x="206"
      y="112"
      text-anchor="middle"
    >CODE REVIEW</text>
    <text
      class="s-draw-label"
      x="254"
      y="162"
      text-anchor="end"
    >PASSES</text>
    <text
      class="s-draw-label"
      x="262"
      y="250"
      text-anchor="middle"
    >NEXT NODE</text>
    <text
      class="s-draw-label"
      x="120"
      y="162"
    >FAILS</text>
    <text
      class="s-draw-label s-draw-sub"
      x="136"
      y="214"
    >REPAIR</text>
    <text
      class="s-draw-label"
      x="34"
      y="282"
    >MEASURED AGAIN</text>
  </svg>
</template>
