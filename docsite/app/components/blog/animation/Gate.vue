<!-- The plan gate and its reviewer over two rounds (utils/blogAnimation/gate.ts), as the gate view draws it. -->
<script setup lang="ts">
import { gateAt } from '~/utils/blogAnimation/gate'

const props = defineProps<{ t: number }>()
const g = computed(() => gateAt(props.t))
</script>

<template>
  <div class="a-layer">
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
        <span :class="`a-gate-line${g.reviewer !== 'todo' ? ' is-ran' : ''}`" />
        <div class="a-gate-item">
          <span class="a-diamond-box"><span :class="`a-diamond${g.pending ? ' is-yours' : ''}`" /></span>
          <span :class="`a-label is-you${g.pending ? ' is-on' : ''}`">you</span>
          <span :class="`a-badge${g.pending ? ' is-warn' : ''}`">{{ g.you }}</span>
        </div>
      </div>
      <p class="a-gate-msg">
        Review and approve the implementation plan.
      </p>
      <span
        v-if="g.pending"
        class="a-pill"
      ><svg
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
      <span class="a-pane-sub">gate node{{ g.pending ? ' · waiting for you' : '' }}</span>
      <span
        v-if="g.rounds.length"
        class="a-section"
      >History · {{ g.rounds.length }} {{ g.rounds.length === 1 ? 'round' : 'rounds' }}</span>
      <div
        v-for="r in g.rounds"
        :key="r.n"
        :class="`a-round${r.now ? ' is-now' : ''}`"
      >
        <span class="a-round-head"><span :class="`a-pip is-${r.tone}`" />Round {{ r.n }}{{ r.now ? ' · now' : '' }}</span>
        <span class="a-round-out">{{ r.outcome }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
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

.a-pane.is-card {
  left: 600px;
  top: 40px;
  width: 230px;
  height: auto;
  padding: 14px;
  border: 1px solid var(--s-border);
  border-radius: 8px;
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


.a-round-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
}

.a-round-out {
  color: var(--s-text-sub);
}

</style>
