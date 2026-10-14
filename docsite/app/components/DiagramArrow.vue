<script setup lang="ts">
// One arrow in a diagram (DiagramFigure.vue says what each glyph means). The
// glyph is for the eye; a screen reader hears the words, or nothing where the
// list already says it (`next` between the items of an ordered list). `say`
// gives other words where the diagram means something else by the glyph.
const props = defineProps<{ kind?: 'start' | 'next' | 'dashed' | 'back', say?: string }>()
const glyphs = { start: '›', next: '→', dashed: '⇢', back: '↻' }
const words = { start: '', next: '', dashed: 'sometimes', back: 'back to' }
</script>

<template>
  <span class="diagram-arrow" :class="{ 'diagram-arrow--start': props.kind === 'start' }" aria-hidden="true">{{ glyphs[props.kind ?? 'next'] }}</span><span v-if="props.say ?? words[props.kind ?? 'next']" class="sr-only"> {{ props.say ?? words[props.kind ?? 'next'] }}</span>
</template>
