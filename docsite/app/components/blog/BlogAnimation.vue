<!-- An animation of Kraft's item page in a blog post: `::blog-animation` with
     `name` (slices, gate or subagents), `alt` and `still`, the image the feed
     shows in its place (server/utils/feedHtml.ts). It is drawn as the app draws
     it, from the app's own states and words (utils/blogAnimations.ts), at the
     post's width. It plays only while it is on screen; with reduced motion, and
     before the page's script runs, it holds its still frame. -->
<script setup lang="ts">
import { type AnimationName, gateAt, LOOPS, slicesAt, subagentsAt } from '~/utils/blogAnimations'

const props = defineProps<{ name: AnimationName, alt: string, still?: string }>()

const W = 860
// The sub-agents' pane needs more height than the chains.
const H = props.name === 'subagents' ? 440 : 360
const t = ref(LOOPS[props.name].still)
const scale = ref(1)
const box = useTemplateRef<HTMLElement>('box')

let raf = 0
let stop = () => {}
onMounted(() => {
  const el = box.value!
  const resize = new ResizeObserver(() => (scale.value = el.clientWidth / W))
  resize.observe(el)
  const calm = matchMedia('(prefers-reduced-motion: reduce)')
  let start = 0
  const tick = (now: number) => {
    start ||= now
    t.value = ((now - start) / 1000) % LOOPS[props.name].loop
    raf = requestAnimationFrame(tick)
  }
  const seen = new IntersectionObserver(([e]) => {
    cancelAnimationFrame(raf)
    if (e!.isIntersecting && !calm.matches) raf = requestAnimationFrame(tick)
  })
  seen.observe(el)
  stop = () => {
    resize.disconnect()
    seen.disconnect()
    cancelAnimationFrame(raf)
  }
})
onBeforeUnmount(() => stop())

const s = computed(() => slicesAt(t.value))
const g = computed(() => gateAt(t.value))
const a = computed(() => subagentsAt(t.value))
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
// An edge is dashed until what it leads to has started.
const solid = (on: boolean) => (on ? 'a-edge' : 'a-edge is-todo')
</script>

<template>
  <div class="s-anim-wrap">
  <figure
    ref="box"
    class="s-anim"
    role="img"
    :aria-label="alt"
    :style="{ height: `${H * scale}px` }"
  >
    <div
      class="a-stage"
      :style="{ width: `${W}px`, height: `${H}px`, transform: `scale(${scale})` }"
      aria-hidden="true"
    >
      <template v-if="name === 'slices'">
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
      </template>

      <template v-else-if="name === 'gate'">
        <div class="a-gate">
          <div class="a-gate-row">
            <div class="a-gate-item">
              <span :class="`a-box is-gv is-${g.reviewer}`"><svg
                viewBox="0 0 24 24"
                width="17"
                height="17"
              ><path d="M12 8V4H8" /><rect
                width="16"
                height="12"
                x="4"
                y="8"
                rx="2"
              /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M15 13v2" /><path d="M9 13v2" /></svg></span>
              <span class="a-label">auto_review</span>
              <span :class="`a-badge${g.tone ? ` is-${g.tone}` : ''}`">{{ g.badge }}</span>
            </div>
            <span :class="`a-gate-line${g.yours ? ' is-ran' : ''}`" />
            <div class="a-gate-item">
              <span class="a-diamond-box"><span :class="`a-diamond${g.yours ? ' is-yours' : ''}`" /></span>
              <span :class="`a-label is-you${g.yours ? ' is-on' : ''}`">you</span>
              <span :class="`a-badge${g.yours ? ' is-warn' : ''}`">{{ g.you }}</span>
            </div>
          </div>
          <p class="a-gate-msg">
            Review and approve the implementation plan.
          </p>
          <span class="a-pill"><svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
          ><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /></svg>plan.md</span>
          <span :class="`a-reject-line${g.sentBack ? ' is-taken' : ''}`" />
          <span class="a-reject-word">{{ g.sentBack ? 'rejected to' : 'reject' }}</span>
          <span :class="`a-reject-target${g.sentBack ? ' is-taken' : ''}`"><svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
          ><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /></svg>plan</span>
        </div>
        <div class="a-pane is-card">
          <span class="a-pane-title"><svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
          ><rect
            x="6"
            y="6"
            width="12"
            height="12"
            rx="1"
            transform="rotate(45 12 12)"
          /></svg>plan_approval</span>
          <span class="a-pane-sub">gate node</span>
          <span class="a-section">History · {{ g.rounds.length }} {{ g.rounds.length === 1 ? 'round' : 'rounds' }}</span>
          <div
            v-for="r in g.rounds"
            :key="r.n"
            :class="`a-round${r.now ? ' is-now' : ''}`"
          >
            <span class="a-round-head"><span :class="`a-pip is-${r.tone}`" />Round {{ r.n }}{{ r.now ? ' · now' : '' }}</span>
            <span class="a-round-out">{{ r.outcome }}</span>
          </div>
        </div>
      </template>

      <template v-else>
        <svg
          class="a-lines"
          width="520"
          :height="H"
        >
          <path
            d="M 80 180 L 238 180"
            :class="solid(a.on)"
          />
          <path
            d="M 282 180 L 432 180"
            :class="solid(false)"
          />
        </svg>
        <span
          class="a-start"
          style="left: 60px; top: 170px"
        ><svg
          viewBox="0 0 24 24"
          width="9"
          height="9"
        ><polygon points="6 3 20 12 6 21 6 3" /></svg></span>
        <span
          class="a-end"
          style="left: 432px; top: 172px"
        />
        <span
          class="a-caption"
          style="left: 200px; top: 118px"
        >main</span>
        <div
          class="a-node"
          style="left: 200px; top: 158px"
        >
          <span class="a-box-wrap">
            <span :class="`a-box is-sel is-${a.on ? 'run' : 'todo'}`"><svg
              viewBox="0 0 24 24"
              width="17"
              height="17"
            ><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" /><path d="M20 3v4" /><path d="M22 5h-4" /><path d="M4 17v2" /><path d="M5 18H3" /></svg></span>
            <span
              v-if="a.running.length"
              class="a-count"
            >{{ a.running.length }}</span>
          </span>
          <span class="a-label is-run">implement</span>
          <span class="a-meta">{{ a.on ? `running · ${a.ran}` : 'not yet' }}</span>
          <span
            v-if="a.marks.length"
            class="a-marks"
          ><span
            v-for="(m, i) in a.marks"
            :key="i"
            :class="`a-mark is-${m}`"
          ><svg
            v-if="m === 'done'"
            viewBox="0 0 24 24"
            width="9"
            height="9"
          ><path d="M20 6 9 17l-5-5" /></svg></span></span>
        </div>
        <div class="a-pane">
          <span class="a-crumbs">default › implementation › main ›</span>
          <span class="a-pane-title is-big"><svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
          ><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" /></svg>implement</span>
          <span class="a-pane-sub">{{ a.on ? `agent task · running ${a.ran}` : 'agent task · not yet' }}</span>
          <span class="a-tabs"><span class="is-on">Overview</span><span>Input</span><span>Output</span><span>Log</span><span>Config</span></span>
          <span class="a-facts"><span>status</span><span>{{ a.on ? 'running' : 'not yet' }}</span><span>harness</span><span>claude</span></span>
          <span
            v-if="a.running.length || a.finished.length"
            class="a-section"
          >Sub-agents</span>
          <template v-if="a.running.length">
            <span class="a-group"><span class="a-pip is-run" />Running <span class="a-n">{{ a.running.length }}</span></span>
            <div
              v-for="r in a.running"
              :key="r.name"
              class="a-agent"
            >
              <span class="a-agent-head"><span class="a-pip is-run" /><span class="a-agent-name">{{ r.name }}</span><span class="a-n">{{ r.time }}</span></span>
              <span class="a-agent-meta"><span class="is-mono">Explore</span><span>{{ r.tokens }}</span><span>{{ r.uses }}</span></span>
              <span class="a-agent-doing">{{ r.doing }}</span>
            </div>
          </template>
          <template v-if="a.finished.length">
            <span class="a-group"><span class="a-caret">{{ a.open ? '▼' : '▶' }}</span><span class="a-pip is-ok" />Finished <span class="a-n">{{ a.finished.length }}</span></span>
            <template v-if="a.open">
              <div
                v-for="r in a.finished"
                :key="r.name"
                class="a-agent"
              >
                <span class="a-agent-head"><span class="a-pip is-ok" /><span class="a-agent-name">{{ r.name }}</span><span class="a-n">{{ r.time }}</span></span>
                <span class="a-agent-meta"><span class="is-mono">Explore</span><span>{{ r.tokens }}</span><span>{{ r.uses }}</span></span>
              </div>
            </template>
          </template>
        </div>
      </template>
    </div>
  </figure>
  <p
    class="s-anim-note"
    aria-hidden="true"
  >
    Illustration: Kraft's item page, drawn with a made-up run.
  </p>
  </div>
</template>

<style scoped>
/* The app's look (frontend/src/ng/graph/graph.css, item panes) in the site's
   theme: the site's grounds and text, and the app's signal colours. */
.s-anim-wrap {
  margin: 28px 0;
}

.s-anim-note {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--s-text-muted);
}

.s-anim {
  --a-run: var(--s-blue);
  --a-ok: #156f41;
  --a-bad: #a13d43;
  --a-warn: var(--s-amber);
  --a-mono: ui-monospace, Menlo, monospace;
  position: relative;
  overflow: hidden;
  margin: 0;
  border: 1px solid var(--s-hairline);
  border-radius: 12px;
  background: var(--s-ground);
  color: var(--s-text);
  font-variant-numeric: tabular-nums;
}

:global(.dark) .s-anim {
  --a-ok: #7acc98;
  --a-bad: #ec8082;
}

.a-stage {
  position: absolute;
  left: 0;
  top: 0;
  transform-origin: 0 0;
  font-size: 12px;
  background-image: radial-gradient(circle at 1px 1px, var(--s-hairline) 1px, transparent 1.5px);
  background-size: 24px 24px;
}

.a-stage svg {
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.a-layer {
  position: absolute;
  inset: 0;
}

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

.a-lines {
  position: absolute;
  left: 0;
  top: 0;
  overflow: visible;
}

.a-edge {
  stroke: var(--s-stroke);
}

.a-edge.is-todo {
  stroke-dasharray: 4 4;
}

.a-dot {
  fill: var(--s-stroke);
  stroke: none;
}

.a-start {
  position: absolute;
  width: 20px;
  height: 20px;
  box-sizing: border-box;
  border-radius: 50%;
  border: 1px solid var(--s-stroke);
  background: var(--s-ground);
  color: var(--s-text-sub);
  display: flex;
  align-items: center;
  justify-content: center;
}

.a-start svg {
  fill: currentColor;
}

.a-end {
  position: absolute;
  width: 16px;
  height: 16px;
  box-sizing: border-box;
  border-radius: 4px;
  border: 1px solid var(--s-stroke);
  background: var(--s-card);
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

.a-caption {
  position: absolute;
  width: 120px;
  text-align: center;
  font: 400 12px/1.3 var(--a-mono);
  color: var(--s-text-sub);
}

.a-node {
  position: absolute;
  width: 120px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

/* A glyph box (NodeGlyph md: 44px, radius 11) in a chain state. */
@property --a-ang {
  syntax: "<angle>";
  inherits: false;
  initial-value: 0deg;
}

.a-box {
  width: 44px;
  height: 44px;
  box-sizing: border-box;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--s-stroke);
  background: var(--s-ground);
  color: var(--s-text-muted);
}

.a-box.is-gv {
  width: 46px;
  height: 46px;
}

.a-box.is-todo {
  border-style: dashed;
  color: var(--s-glyph-todo);
}

.a-box.is-amber {
  border: 1.5px solid var(--a-warn);
  color: var(--a-warn);
}

/* Running: a light circles the border every 1.8s (graph.css .is-current.is-running). */
.a-box.is-run {
  border: 1.5px solid color-mix(in srgb, var(--a-run) 30%, transparent);
  background: linear-gradient(var(--s-raised) 0 0) padding-box, conic-gradient(from var(--a-ang), transparent 0 55%, var(--a-run) 90%, transparent 100%) border-box var(--s-raised);
  color: var(--s-text);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--s-text-sub) 12%, transparent);
  animation: a-trace 1.8s linear infinite;
}

.a-box.is-sel {
  box-shadow: 0 0 0 2px var(--s-ground), 0 0 0 4px var(--s-text);
}

@keyframes a-trace {
  to {
    --a-ang: 360deg;
  }
}

.a-label {
  padding: 0 3px;
  border-radius: 3px;
  background: var(--s-ground);
  text-align: center;
  font: 400 12px/1.3 var(--a-mono);
  color: var(--s-text-sub);
  white-space: nowrap;
}

.a-label.is-todo {
  color: var(--s-text-muted);
}

.a-label:is(.is-run, .is-amber, .is-on) {
  font-weight: 600;
  color: var(--s-text);
}

.a-label.is-you {
  color: var(--s-text);
}

.a-meta {
  margin-top: -4px;
  padding: 0 3px;
  border-radius: 3px;
  background: var(--s-ground);
  white-space: nowrap;
  color: var(--s-text-muted);
}

.a-meta.is-amber {
  color: var(--a-warn);
}

/* The gate view (graph.css .gateview-*). */
.a-gate {
  position: absolute;
  left: 20px;
  top: 40px;
  width: 560px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.a-gate-row {
  display: flex;
  align-items: flex-start;
}

.a-gate-item {
  width: 140px;
  padding: 4px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.a-gate-line {
  width: 56px;
  margin-top: 27px;
  border-top: 2px dashed var(--s-stroke);
}

.a-gate-line.is-ran {
  border-top-style: solid;
}

.a-diamond-box {
  width: 46px;
  height: 46px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.a-diamond {
  width: 32px;
  height: 32px;
  box-sizing: border-box;
  border-radius: 4px;
  transform: rotate(45deg);
  border: 1.5px dashed var(--s-stroke);
  background: var(--s-ground);
}

.a-diamond.is-yours {
  border: 1.5px solid var(--a-warn);
  background: var(--s-raised);
}

.a-badge {
  padding: 0 7px;
  border-radius: 5px;
  line-height: 20px;
  white-space: nowrap;
  color: var(--s-text-sub);
  border: 1px solid var(--s-border);
  background: color-mix(in srgb, var(--a-run) 18%, transparent);
}

.a-badge:is(.is-ok, .is-bad, .is-warn) {
  background: color-mix(in srgb, var(--a-tone) 9%, transparent);
  border-color: color-mix(in srgb, var(--a-tone) 45%, transparent);
  color: var(--a-tone);
}

.a-badge.is-ok {
  --a-tone: var(--a-ok);
}

.a-badge.is-bad {
  --a-tone: var(--a-bad);
}

.a-badge.is-warn {
  --a-tone: var(--a-warn);
}

.a-gate-msg {
  max-width: 380px;
  margin: 0;
  padding: 0 4px;
  background: var(--s-ground);
  text-align: center;
  font-size: 13px;
  line-height: 1.6;
  color: var(--s-text-sub);
}

.a-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 999px;
  border: 1px solid var(--s-border);
  background: var(--s-ground);
  font: 12px var(--a-mono);
  color: var(--s-text-sub);
}

.a-reject-line {
  height: 22px;
  margin-bottom: -8px;
  border-left: 1.5px dashed var(--a-bad);
}

.a-reject-line.is-taken {
  border-left-style: solid;
}

.a-reject-word {
  margin-bottom: -8px;
  padding: 0 4px;
  background: var(--s-ground);
  font: 12px var(--a-mono);
  color: var(--a-bad);
}

.a-reject-target {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px dashed var(--a-bad);
  background: var(--s-ground);
  font: 12px var(--a-mono);
  color: var(--s-text-sub);
}

.a-reject-target.is-taken {
  border-style: solid;
  background: color-mix(in srgb, var(--a-bad) 9%, var(--s-ground));
}

/* The item page's side pane: a gate's history, a task's overview. */
.a-pane {
  position: absolute;
  left: 520px;
  top: 0;
  width: 340px;
  height: 100%;
  box-sizing: border-box;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
  border-left: 1px solid var(--s-border);
  background: var(--s-card);
}

.a-pane.is-card {
  left: 600px;
  top: 40px;
  width: 230px;
  height: auto;
  padding: 14px;
  border: 1px solid var(--s-border);
  border-radius: 8px;
}

.a-pane-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font: 400 14px/1.4 var(--a-mono);
  color: var(--s-text);
}

.a-pane-title.is-big {
  font-size: 15px;
}

.a-pane-title svg {
  color: var(--s-text-sub);
}

.a-pane-sub {
  margin-top: -6px;
  color: var(--s-text-muted);
}

.a-crumbs {
  font: 12px var(--a-mono);
  color: var(--s-text-muted);
}

.a-tabs {
  display: flex;
  gap: 16px;
  border-bottom: 1px solid var(--s-hairline);
  font-size: 13px;
  color: var(--s-text-sub);
}

.a-tabs > span {
  padding-bottom: 6px;
}

.a-tabs > .is-on {
  border-bottom: 2px solid var(--s-text);
  color: var(--s-text);
}

.a-facts {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  row-gap: 6px;
  font-size: 13px;
}

.a-facts > :nth-child(odd) {
  color: var(--s-text-muted);
}

.a-section {
  margin-top: 4px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--s-text-muted);
}

.a-round {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--s-hairline);
}

.a-round.is-now {
  border-color: color-mix(in srgb, var(--s-accent) 70%, transparent);
  background: color-mix(in srgb, var(--s-accent) 10%, transparent);
}

.a-round-head,
.a-group,
.a-agent-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.a-round-head {
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
}

.a-round-out {
  color: var(--s-text-sub);
}

.a-pip {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--a-run);
}

.a-pip.is-ok {
  background: var(--a-ok);
}

.a-pip.is-bad {
  background: var(--a-bad);
}

.a-group {
  color: var(--s-text-sub);
}

.a-n {
  color: var(--s-text-muted);
}

.a-caret {
  font-size: 9px;
  color: var(--s-text-muted);
}

.a-agent {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 7px 10px;
  border-radius: 8px;
  border: 1px solid var(--s-hairline);
  background: var(--s-ground);
}

.a-agent-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.a-agent-meta {
  display: flex;
  gap: 8px;
  padding-left: 13px;
  font-size: 11px;
  color: var(--s-text-muted);
}

.a-agent-meta .is-mono {
  font-family: var(--a-mono);
}

.a-agent-doing {
  padding-left: 13px;
  font-size: 11px;
  color: var(--s-text-sub);
}

/* A task's sub-agents: a count on its box, and a mark each under it (graph.css .glyph-subagents, .sub-marks). */
.a-box-wrap {
  position: relative;
}

.a-count {
  position: absolute;
  top: -8px;
  left: -8px;
  width: 16px;
  height: 16px;
  box-sizing: border-box;
  border-radius: 50%;
  border: 1px solid var(--s-text-muted);
  background: var(--s-card);
  font: 700 11px/14px var(--a-mono);
  text-align: center;
}

.a-marks {
  margin-top: -2px;
  display: flex;
  gap: 1px;
  padding: 3px 3px 2px;
  border-radius: 0 0 12px 12px;
  background: var(--s-ground);
}

.a-mark {
  width: 13px;
  height: 13px;
  margin: 4.5px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--a-ok);
  color: var(--s-ground);
}

.a-mark svg {
  stroke-width: 3.5;
}

.a-mark.is-run {
  background: var(--a-run);
  animation: a-breathe 2s ease-in-out infinite;
}

@keyframes a-breathe {
  0%,
  100% {
    opacity: 1;
    filter: drop-shadow(0 0 3px var(--a-run));
  }

  50% {
    opacity: 0.35;
    filter: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .a-box.is-run,
  .a-mark.is-run {
    animation: none;
  }
}
</style>
