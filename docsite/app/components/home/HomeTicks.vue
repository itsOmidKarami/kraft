<script setup lang="ts">
import type { PlayerNode, PlayerNow } from '~/utils/homePlayer'

// Under 640px the chain is Kraft's phone pattern: a tick per node, a gate a
// small diamond, the running tick with a sheen, a waiting gate amber, and one
// line saying which node, in what state, and how far along.
defineProps<{ nodes: PlayerNode[], now: PlayerNow }>()
</script>

<template>
  <div class="kr-chain-compact k-dots">
    <div
      class="k-ticks"
      aria-hidden="true"
    >
      <template
        v-for="n in nodes"
        :key="n.label"
      >
        <span
          v-if="n.runDone || n.runCur || n.runTodo"
          class="k-tick"
          :class="n.runDone ? 'k-tick-done' : n.runCur ? 'k-tick-run kr-tick-run' : 'k-tick-todo'"
        />
        <span
          v-else
          class="k-tick-gate"
          :class="n.gateDone ? 'k-tick-done' : n.gateCur ? 'k-tick-wait' : 'k-tick-todo'"
        />
      </template>
    </div>
    <p class="k-now">
      <b>{{ now.label }}</b>
      <span :class="now.wait ? 'k-now-wait' : 'k-now-calm'">{{ now.word }}</span>
      <span class="k-now-pos">{{ now.pos }}</span>
    </p>
  </div>
</template>
