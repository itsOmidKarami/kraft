<script setup lang="ts">
// A diagram of stages: one sequence, or groups of them (the stages of filing,
// then the chain, then landing). Groups follow each other, joined by a `↓`,
// unless they are `parallel`: alternatives side by side, each starting fresh
// (the ways to reach Kraft from a phone). Used from Markdown:
//
//   ::diagram-flow
//   ---
//   caption: What the diagram says, in a sentence or two.
//   stages:
//     - name: measure the node
//       branches:
//         - when: passes
//           name: next node
//   ---
//   ::
//
// DiagramSteps.vue has the fields of a stage.
import type { Stage } from './DiagramSteps.vue'

const props = defineProps<{
  caption: string
  stages?: Stage[]
  groups?: { label: string, stages: Stage[] }[]
  parallel?: boolean
}>()
</script>

<template>
  <DiagramFigure :caption="props.caption">
    <DiagramSteps v-if="props.stages" :stages="props.stages" start />
    <component :is="props.parallel ? 'ul' : 'ol'" v-if="props.groups" class="diagram-flow__groups" :class="{ 'diagram-flow__groups--parallel': props.parallel }">
      <li v-for="(group, i) in props.groups" :key="group.label">
        <span v-if="i && !props.parallel" class="diagram-flow__join diagram-arrow" aria-hidden="true">↓</span>
        <div class="diagram-group">
          <span class="diagram-group__label">{{ group.label }}</span>
          <DiagramSteps :stages="group.stages" :start="props.parallel || !i" />
        </div>
      </li>
    </component>
  </DiagramFigure>
</template>

<style scoped>
.diagram-flow__join {
  display: block;
  padding-left: 1rem;
  line-height: 1.75;
}

/* Alternatives sit side by side where there is room for two. */
.diagram-flow__groups--parallel {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
  gap: 0.75rem;
}
</style>
