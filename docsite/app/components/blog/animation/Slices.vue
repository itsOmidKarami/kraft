<!-- A sliced work item (utils/blogAnimation/slices.ts), drawn as the item page draws it, with a zoom into slice 1. -->
<script setup lang="ts">
import { edge as solid } from '~/utils/blogAnimation/shared'
import { meta, slicesAt } from '~/utils/blogAnimation/slices'

const props = defineProps<{ t: number }>()
const W = 860
const H = meta.height
const s = computed(() => slicesAt(props.t))
// The zoom into slice 1 (as the overview video zooms into verification): the group scales up about slice 1's box and
// fades, while slice 1's own chain grows out of that box to fill the stage.
const outer = computed(() => {
  const z = s.value.zoom
  return z ? { transformOrigin: '500px 85px', transform: `scale(${1 + 6 * z})`, opacity: Math.max(0, 1 - z * 1.6) } : {}
})
const inner = computed(() => {
  const z = s.value.zoom
  return { transformOrigin: '430px 180px', transform: `translate(${70 * (1 - z)}px, ${-95 * (1 - z)}px) scale(${0.14 + 0.86 * z})`, opacity: Math.min(1, Math.max(0, (z - 0.35) * 2)) }
})
</script>

<template>
  <div class="a-layer">
    <div
      class="a-layer"
      :style="outer"
    >
      <svg
      class="a-lines"
      :width="W"
      :height="H"
    >
      <path
        d="M 70 180 L 158 180"
        :class="solid(s.impl !== 'todo')"
      />
      <path
        d="M 202 180 L 318 180"
        :class="solid(s.verif !== 'todo')"
      />
      <path
        d="M 362 180 L 420 180"
        :class="solid(s.slices.some(x => x.look !== 'todo'))"
      />
      <path
        v-for="(x, i) in s.slices"
        :key="`in${i}`"
        :d="['M 420 180 C 450 180 450 85 478 85', 'M 420 180 L 478 180', 'M 420 180 C 450 180 450 275 478 275'][i]"
        :class="solid(x.look !== 'todo')"
      />
      <path
        v-for="(x, i) in s.slices"
        :key="`out${i}`"
        :d="['M 522 85 C 550 85 550 180 580 180', 'M 522 180 L 580 180', 'M 522 275 C 550 275 550 180 580 180'][i]"
        :class="solid(x.look === 'done')"
      />
      <path
        d="M 580 180 L 638 180"
        :class="solid(s.join !== 'todo')"
      />
      <path
        d="M 682 180 L 752 180"
        :class="solid(s.join === 'done')"
      />
      <circle
        v-for="x in [260, 420, 580]"
        :key="x"
        :cx="x"
        cy="180"
        r="3"
        class="a-dot"
      />
    </svg>
    <span
      class="a-start"
      style="left: 50px; top: 170px"
    ><svg
      viewBox="0 0 24 24"
      width="9"
      height="9"
    ><polygon points="6 3 20 12 6 21 6 3" /></svg></span>
    <span
      class="a-end"
      style="left: 752px; top: 172px"
    />
    <span class="a-frame" />
    <span
      class="a-caption"
      style="left: 440px; top: 22px"
    >slices</span>
    <span
      class="a-caption"
      style="left: 120px; top: 118px"
    >groundwork</span>
    <div
      class="a-node"
      style="left: 120px; top: 158px"
    >
      <span :class="`a-box is-${s.impl}`"><svg
        viewBox="0 0 24 24"
        width="17"
        height="17"
      ><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" /><path d="m3.3 7 8.7 5 8.7-5" /><path d="M12 22V12" /></svg></span>
      <span :class="`a-label is-${s.impl}`">implementa-<br>tion</span>
    </div>
    <div
      class="a-node"
      style="left: 280px; top: 158px"
    >
      <span :class="`a-box is-${s.verif}`"><svg
        viewBox="0 0 24 24"
        width="17"
        height="17"
      ><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" /><path d="m3.3 7 8.7 5 8.7-5" /><path d="M12 22V12" /></svg></span>
      <span :class="`a-label is-${s.verif}`">verification</span>
    </div>
    <div
      v-for="(x, i) in s.slices"
      :key="i"
      class="a-node"
      :style="{ left: '440px', top: `${[63, 158, 253][i]}px` }"
    >
      <span :class="`a-box is-${x.look}`"><svg
        viewBox="0 0 24 24"
        width="17"
        height="17"
      ><line
        x1="6"
        x2="6"
        y1="3"
        y2="15"
      /><circle
        cx="18"
        cy="6"
        r="3"
      /><circle
        cx="6"
        cy="18"
        r="3"
      /><path d="M18 9a9 9 0 0 1-9 9" /></svg></span>
      <span :class="`a-label is-${x.look}`">slice {{ i + 1 }}</span>
      <span :class="`a-meta is-${x.look}`">{{ x.word }}</span>
    </div>
    <div
      class="a-node"
      style="left: 600px; top: 158px"
    >
      <span :class="`a-box is-${s.join}`"><svg
        viewBox="0 0 24 24"
        width="17"
        height="17"
      ><circle
        cx="18"
        cy="18"
        r="3"
      /><circle
        cx="6"
        cy="6"
        r="3"
      /><path d="M6 21V9a9 9 0 0 0 9 9" /></svg></span>
      <span :class="`a-label is-${s.join}`">join</span>
      <span
        v-if="s.joinWord"
        class="a-meta"
      >{{ s.joinWord }}</span>
    </div>
    </div>
    <div
      v-if="s.zoom > 0.001"
      class="a-layer"
      :style="inner"
    >
      <svg
        class="a-lines"
        :width="W"
        :height="H"
      >
        <path
          d="M 240 180 L 308 180"
          :class="solid(s.own.impl !== 'todo')"
        />
        <path
          d="M 352 180 L 508 180"
          :class="solid(s.own.verif !== 'todo')"
        />
        <path
          d="M 552 180 L 612 180"
          :class="solid(s.own.verif === 'done')"
        />
      </svg>
      <span
        class="a-zoom-title"
        style="left: 230px; top: 92px"
      ><b>slice 1</b> · its own work item · tasks 2–4</span>
      <span
        class="a-start"
        style="left: 220px; top: 170px"
      ><svg
        viewBox="0 0 24 24"
        width="9"
        height="9"
      ><polygon points="6 3 20 12 6 21 6 3" /></svg></span>
      <span
        class="a-end"
        style="left: 612px; top: 172px"
      />
      <div
        class="a-node"
        style="left: 270px; top: 158px"
      >
        <span :class="`a-box is-${s.own.impl}`"><svg
          viewBox="0 0 24 24"
          width="17"
          height="17"
        ><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" /><path d="m3.3 7 8.7 5 8.7-5" /><path d="M12 22V12" /></svg></span>
        <span :class="`a-label is-${s.own.impl}`">implementation</span>
      </div>
      <div
        class="a-node"
        style="left: 470px; top: 158px"
      >
        <span :class="`a-box is-${s.own.verif}`"><svg
          viewBox="0 0 24 24"
          width="17"
          height="17"
        ><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" /><path d="m3.3 7 8.7 5 8.7-5" /><path d="M12 22V12" /></svg></span>
        <span :class="`a-label is-${s.own.verif}`">verification</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.a-zoom-title {
  position: absolute;
  width: 400px;
  text-align: center;
  font-size: 13px;
  color: var(--s-text-muted);
}

.a-zoom-title b {
  font: 600 13px var(--a-mono);
  color: var(--s-text);
}


.a-frame {
  position: absolute;
  left: 440px;
  top: 46px;
  width: 120px;
  height: 300px;
  box-sizing: border-box;
  border-radius: 12px;
  border: 1px solid var(--s-hairline);
  background: color-mix(in srgb, var(--s-raised) 25%, transparent);
}

</style>
