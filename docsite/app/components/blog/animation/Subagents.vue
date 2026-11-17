<!-- An implement task and its sub-agents (utils/blogAnimation/subagents.ts), as the node view and its pane draw them. -->
<script setup lang="ts">
import { edge as solid } from '~/utils/blogAnimation/shared'
import { meta, subagentsAt } from '~/utils/blogAnimation/subagents'

const props = defineProps<{ t: number }>()
const H = meta.height
const a = computed(() => subagentsAt(props.t))
</script>

<template>
  <div class="a-layer">
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
      <span class="a-meta">{{ a.on ? `running · ${a.ran}` : 'not started' }}</span>
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
      <span class="a-pane-sub">{{ a.on ? `agent task · running ${a.ran}` : 'agent task · not started' }}</span>
      <span class="a-tabs"><span class="is-on">Overview</span><span>Input</span><span>Output</span><span>Log</span><span>Config</span></span>
      <span class="a-facts"><span>status</span><span>{{ a.on ? 'running' : 'not started' }}</span><span>harness</span><span>claude</span></span>
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
  </div>
</template>

<style scoped>
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


.a-group,
.a-agent-head {
  display: flex;
  align-items: center;
  gap: 6px;
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
  .a-mark.is-run {
    animation: none;
  }
}
</style>
