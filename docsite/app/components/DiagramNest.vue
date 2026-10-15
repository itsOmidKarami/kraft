<script setup lang="ts">
// A diagram of containment, where nesting is the point: the scopes a cap is
// set on, each inside the next. A part is a frame with its name on the first
// line, its note under it, and the parts inside it drawn inside it. Used from
// Markdown as `::diagram-nest` with a YAML block, as DiagramFlow.vue shows.
export interface Part {
  name: string
  note?: string
  // The part repeats until it passes: the loop's name, such as "fix loop".
  loop?: string
  inside?: Part[]
}

const props = defineProps<{ caption: string, parts: Part[] }>()
</script>

<template>
  <DiagramFigure :caption="props.caption">
    <DiagramNestParts :parts="props.parts" />
  </DiagramFigure>
</template>
