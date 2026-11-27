<!-- An animation of Kraft's item page in a blog post: `::blog-animation` with
     `name`, `alt` and `still`, the image the feed shows in its place
     (server/utils/feedHtml.ts). This wrapper fits the stage to the post's
     width, runs the clock while it is on screen, holds the still frame with
     reduced motion and before the page's script runs, and holds the styles
     every view shares. Each animation is a timeline in
     utils/blogAnimation/<name>.ts and a view in animation/<Name>.vue: add one
     there and list it in ANIMATIONS. -->
<script setup lang="ts">
import Gate from './animation/Gate.vue'
import Slices from './animation/Slices.vue'
import Subagents from './animation/Subagents.vue'
import { meta as gate } from '~/utils/blogAnimation/gate'
import { meta as slices } from '~/utils/blogAnimation/slices'
import { meta as subagents } from '~/utils/blogAnimation/subagents'

const ANIMATIONS = {
  slices: { view: Slices, meta: slices },
  gate: { view: Gate, meta: gate },
  subagents: { view: Subagents, meta: subagents },
}

const props = defineProps<{ name: keyof typeof ANIMATIONS, alt: string, still?: string }>()

const W = 860
const { view, meta } = ANIMATIONS[props.name]
const t = ref(meta.still)
const scale = ref(1)
// Paused by the reader's button or by reduced motion; ready once the page's script runs, so the button only shows when it works.
const paused = ref(false)
const ready = ref(false)
const box = useTemplateRef<HTMLElement>('box')

let raf = 0
let visible = false
let go = () => {}
let stop = () => {}
onMounted(() => {
  const el = box.value!
  const resize = new ResizeObserver(() => (scale.value = el.clientWidth / W))
  resize.observe(el)
  let start = 0
  let from = 0
  const tick = (now: number) => {
    start ||= now
    t.value = (from + (now - start) / 1000) % meta.loop
    raf = requestAnimationFrame(tick)
  }
  // Runs from where it stands, only while on screen and not paused.
  go = () => {
    cancelAnimationFrame(raf)
    if (!visible || paused.value) return
    start = 0
    from = t.value
    raf = requestAnimationFrame(tick)
  }
  const calm = matchMedia('(prefers-reduced-motion: reduce)')
  const still = () => {
    paused.value = calm.matches
    if (calm.matches) t.value = meta.still
    go()
  }
  calm.addEventListener('change', still)
  paused.value = calm.matches
  const seen = new IntersectionObserver(([e]) => {
    visible = e!.isIntersecting
    go()
  })
  seen.observe(el)
  ready.value = true
  stop = () => {
    resize.disconnect()
    seen.disconnect()
    calm.removeEventListener('change', still)
    cancelAnimationFrame(raf)
  }
})
onBeforeUnmount(() => stop())

function toggle() {
  paused.value = !paused.value
  go()
}
</script>

<template>
  <div class="s-anim-wrap">
    <figure
      ref="box"
      class="s-anim"
      role="img"
      :aria-label="alt"
      :style="{ aspectRatio: `${W} / ${meta.height}` }"
    >
      <div
        class="a-stage"
        :style="{ width: `${W}px`, height: `${meta.height}px`, transform: `scale(${scale})` }"
        aria-hidden="true"
      >
        <component
          :is="view"
          :t="t"
        />
      </div>
    </figure>
    <div class="s-anim-foot">
      <p
        class="s-anim-note"
        aria-hidden="true"
      >
        Illustration: Kraft's item page, drawn with a made-up run.
      </p>
      <button
        v-if="ready"
        type="button"
        class="s-anim-pause"
        :aria-pressed="paused"
        @click="toggle"
      >
        {{ paused ? 'Play' : 'Pause' }}<span class="s-anim-sr"> the animation</span>
      </button>
    </div>
  </div>
</template>

<style>
/* The app's look (frontend/src/ng/graph/graph.css, item panes) in the site's
   theme: the site's grounds and text, and the app's signal colours. Not
   scoped, since the views draw these parts; every rule starts with .s-anim. */
.site .s-anim-wrap {
  margin: 28px 0;
}

.site .s-anim-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
}

.site .s-anim-pause {
  flex: none;
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid var(--s-border);
  border-radius: 6px;
  background: transparent;
  color: var(--s-text-sub);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.site .s-anim-pause:hover {
  background: var(--s-raised);
}

.site .s-anim-pause:focus-visible {
  outline: 2px solid var(--s-text);
  outline-offset: 2px;
}

.site .s-anim-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.site .s-anim-note {
  margin: 0;
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

.dark .s-anim {
  --a-ok: #7acc98;
  --a-bad: #ec8082;
}


.s-anim .a-layer {
  position: absolute;
  inset: 0;
}

.s-anim .a-stage {
  position: absolute;
  left: 0;
  top: 0;
  transform-origin: 0 0;
  font-size: 12px;
  background-image: radial-gradient(circle at 1px 1px, var(--s-hairline) 1px, transparent 1.5px);
  background-size: 24px 24px;
}

.s-anim .a-stage svg {
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}


.s-anim .a-lines {
  position: absolute;
  left: 0;
  top: 0;
  overflow: visible;
}

.s-anim .a-edge {
  stroke: var(--s-stroke);
}

.s-anim .a-edge.is-todo {
  stroke-dasharray: 4 4;
}

.s-anim .a-dot {
  fill: var(--s-stroke);
  stroke: none;
}

.s-anim .a-start {
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

.s-anim .a-start svg {
  fill: currentColor;
}

.s-anim .a-end {
  position: absolute;
  width: 16px;
  height: 16px;
  box-sizing: border-box;
  border-radius: 4px;
  border: 1px solid var(--s-stroke);
  background: var(--s-card);
}


.s-anim .a-caption {
  position: absolute;
  width: 120px;
  text-align: center;
  font: 400 12px/1.3 var(--a-mono);
  color: var(--s-text-sub);
}

.s-anim .a-node {
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

.s-anim .a-box {
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

.s-anim .a-box.is-gv {
  width: 46px;
  height: 46px;
}

.s-anim .a-box.is-todo {
  border-style: dashed;
  color: var(--s-glyph-todo);
}

.s-anim .a-box.is-amber {
  border: 1.5px solid var(--a-warn);
  color: var(--a-warn);
}

/* Running: a light circles the border every 1.8s (graph.css .is-current.is-running). */
.s-anim .a-box.is-run {
  border: 1.5px solid color-mix(in srgb, var(--a-run) 30%, transparent);
  background: linear-gradient(var(--s-raised) 0 0) padding-box, conic-gradient(from var(--a-ang), transparent 0 55%, var(--a-run) 90%, transparent 100%) border-box var(--s-raised);
  color: var(--s-text);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--s-text-sub) 12%, transparent);
  animation: a-trace 1.8s linear infinite;
}

.s-anim .a-box.is-sel {
  box-shadow: 0 0 0 2px var(--s-ground), 0 0 0 4px var(--s-text);
}

@keyframes a-trace {
  to {
    --a-ang: 360deg;
  }
}

.s-anim .a-label {
  padding: 0 3px;
  border-radius: 3px;
  background: var(--s-ground);
  text-align: center;
  font: 400 12px/1.3 var(--a-mono);
  color: var(--s-text-sub);
  white-space: nowrap;
}

.s-anim .a-label.is-todo {
  color: var(--s-text-muted);
}

.s-anim .a-label:is(.is-run, .is-amber, .is-on) {
  font-weight: 600;
  color: var(--s-text);
}

.s-anim .a-label.is-you {
  color: var(--s-text);
}

.s-anim .a-meta {
  margin-top: -4px;
  padding: 0 3px;
  border-radius: 3px;
  background: var(--s-ground);
  white-space: nowrap;
  color: var(--s-text-muted);
}

.s-anim .a-meta.is-amber {
  color: var(--a-warn);
}

/* The gate view (graph.css .gateview-*). */

.s-anim .a-pane {
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


.s-anim .a-pane-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font: 400 14px/1.4 var(--a-mono);
  color: var(--s-text);
}

.s-anim .a-pane-title.is-big {
  font-size: 15px;
}

.s-anim .a-pane-title svg {
  color: var(--s-text-sub);
}

.s-anim .a-pane-sub {
  margin-top: -6px;
  color: var(--s-text-muted);
}

.s-anim .a-crumbs {
  font: 12px var(--a-mono);
  color: var(--s-text-muted);
}


.s-anim .a-section {
  margin-top: 4px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--s-text-muted);
}


.s-anim .a-pip {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--a-run);
}

.s-anim .a-pip.is-ok {
  background: var(--a-ok);
}

.s-anim .a-pip.is-bad {
  background: var(--a-bad);
}


@media (prefers-reduced-motion: reduce) {
  .s-anim .a-box.is-run {
    animation: none;
  }
}
</style>
