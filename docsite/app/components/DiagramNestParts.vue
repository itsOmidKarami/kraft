<script setup lang="ts">
// The frames of a DiagramNest: one list of parts, each holding its own.
import type { Part } from './DiagramNest.vue'

defineProps<{ parts: Part[] }>()
</script>

<template>
  <ul class="diagram-nest">
    <li v-for="part in parts" :key="part.name" class="diagram-group diagram-nest__part">
      <span class="diagram-group__label diagram-nest__label">
        <span class="diagram-name">{{ part.name }}</span><span v-if="part.loop" class="diagram-loop">[↻ {{ part.loop }}]</span>
      </span>
      <span v-if="part.note" class="diagram-note">{{ part.note }}</span>
      <DiagramNestParts v-if="part.inside?.length" :parts="part.inside" />
    </li>
  </ul>
</template>

<style scoped>
.diagram-nest {
  display: grid;
  gap: 0.5rem;
}

/* The label sits on the frame's first line; a note is the line under it. */
.diagram-nest__label {
  margin-bottom: 0;
}

.diagram-nest__part > .diagram-nest {
  margin-top: 0.5rem;
}

@media (max-width: 639px) {
  .diagram-nest__part {
    padding: 0.375rem 0.5rem;
  }
}
</style>
