<script setup lang="ts">
import { STEPS } from '~/utils/homePlayer'
import type { PlayerNode } from '~/utils/homePlayer'

// The item's whole chain, drawn as Kraft's board draws it (graph.css,
// NodeGlyph.tsx): a node box 44px with a 11px radius, a gate a 22px square
// turned 45 degrees with a 6px light, edges 2px and dashed when not run.
// There is no red gate state. From 640px up; under it HomeTicks.vue shows.
defineProps<{ nodes: PlayerNode[] }>()

const icon = (label: string) => STEPS.find(x => x.label === label)?.icon
// A node box is done, current or not yet run; a gate also has "just approved".
const box = (n: PlayerNode) => n.runDone ? 'done' : n.runCur ? 'cur' : 'todo'
</script>

<template>
  <div
    class="kr-chain-full k-dots"
    role="list"
    aria-label="The item's chain"
  >
    <div class="k-chain-row">
      <span
        class="k-start"
        aria-hidden="true"
      >
        <span>
          <svg
            width="8"
            height="8"
            viewBox="0 0 8 8"
          ><path
            d="M2 1 L7 4 L2 7 Z"
            fill="currentColor"
          /></svg>
        </span>
      </span>
      <template
        v-for="(n, i) in nodes"
        :key="n.label"
      >
        <span
          v-if="i > 0"
          class="k-conn"
          :class="{ 'k-conn-todo': n.connTodo }"
          aria-hidden="true"
        >
          <template v-if="n.connDone">
            <i /><b /><i />
          </template>
          <template v-else>
            <i /><i />
          </template>
        </span>
        <span
          class="k-node"
          role="listitem"
          :aria-label="`${n.label}: ${n.stateWord}`"
        >
          <span
            class="k-node-art"
            aria-hidden="true"
          >
            <template v-if="n.gateDone || n.gateCur || n.gateTodo">
              <span class="k-gate-cell">
                <span
                  v-if="n.gateDoneQuiet"
                  class="k-gate k-gate-done"
                ><span class="k-light k-light-ok" /></span>
                <span
                  v-else-if="n.gateJust"
                  class="k-gate k-gate-done"
                >
                  <span class="k-ring k-ring-stamp kr-stamp-ring" />
                  <span class="k-light k-light-ok kr-stamp" />
                </span>
                <span
                  v-else-if="n.gateCur"
                  class="k-gate k-gate-wait"
                >
                  <span class="k-ring k-ring-wait kr-wait-ring" />
                  <span class="k-light k-light-warn kr-wait-light" />
                </span>
                <span
                  v-else
                  class="k-gate k-gate-todo"
                />
              </span>
            </template>
            <!-- The verification box carries the name its canvas morphs from. -->
            <span
              v-else
              class="k-box"
              :class="[`k-box-${box(n)}`, { 'kr-trace': box(n) === 'cur' }]"
              :style="n.label === 'verification' && box(n) !== 'cur' ? { viewTransitionName: 'kr-verify' } : undefined"
            >
              <HomeIcon :name="icon(n.label)!" />
            </span>
          </span>
          <span
            class="k-label"
            :class="n.labDone ? 'k-label-done' : n.labCur ? 'k-label-cur' : 'k-label-todo'"
          >{{ n.l1 }}<wbr>{{ n.l2 }}<wbr>{{ n.l3 }}</span>
        </span>
      </template>
    </div>
  </div>
</template>
