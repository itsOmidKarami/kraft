<script setup lang="ts">
import type { LogRow } from '~/utils/homePlayer'

// At merge: what a person did in this run, as the calls they made. The rows
// come in one by one, and sit still under reduced motion.
defineProps<{ log: LogRow[], decWord: string }>()
</script>

<template>
  <div class="k-col k-col-wide">
    <p class="k-eyebrow k-merged">
      MERGED
    </p>
    <p class="k-big">
      All a person did: {{ decWord }} decisions.
    </p>
    <p class="k-sub">
      Claude Code wrote the plan and the code. Kraft ran the tests and reviews, opened the merge request and merged it. These were your calls:
    </p>
    <ol
      class="k-receipt"
      aria-label="Your calls in this run"
    >
      <li
        v-for="(d, i) in log"
        :key="i"
        class="kr-row"
      >
        <span class="k-mark"><i
          aria-hidden="true"
          :class="d.approved ? 'k-mark-ok' : 'k-mark-back'"
        ><b v-if="d.approved" /></i></span>
        <span class="k-gate-name">{{ d.gate }}</span>
        <span
          v-if="d.approved"
          class="k-ok"
        >approved</span>
        <span
          v-else
          style="min-width: 0"
        >sent back: “{{ d.note }}”</span>
      </li>
    </ol>
  </div>
</template>
