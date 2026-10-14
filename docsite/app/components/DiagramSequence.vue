<script setup lang="ts">
// A sequence diagram as the numbered list it is: each message on its own
// line, `from → to`, and what it carries. A reply (a dashed arrow in a
// sequence diagram) takes the dashed arrow, and a note is unnumbered. The page's prose can cite the numbers ("steps 3 to
// 5"), so they are the list's own and never skip. Used from Markdown as
// `::diagram-sequence` with a YAML block, as DiagramFlow.vue shows.
const props = defineProps<{
  caption: string
  messages: { from?: string, to?: string, say?: string, reply?: boolean, note?: string, over?: string }[]
}>()

// A note belongs to the message before it: it is drawn under that message,
// so the list holds only messages and its numbers are theirs.
const rows = computed(() => props.messages.reduce<{ from?: string, to?: string, say?: string, reply?: boolean, notes: { note: string, over?: string }[] }[]>((acc, message) => {
  if (message.note) acc.at(-1)?.notes.push({ note: message.note, over: message.over })
  else acc.push({ ...message, notes: [] })
  return acc
}, []))
</script>

<template>
  <DiagramFigure :caption="props.caption">
    <ol class="diagram-sequence">
      <li v-for="(row, i) in rows" :key="i">
        <span class="diagram-sequence__n">{{ i + 1 }}</span>
        <span class="diagram-sequence__who">
          <span class="diagram-name">{{ row.from }}</span>{{ ' ' }}<DiagramArrow :kind="row.reply ? 'dashed' : 'next'" :say="row.reply ? 'replies to' : 'to'" />{{ ' ' }}<span class="diagram-name">{{ row.to }}</span>
        </span>
        <span class="diagram-sequence__say diagram-note">{{ row.say }}</span>
        <span v-for="note in row.notes" :key="note.note" class="diagram-sequence__note diagram-label">note over {{ note.over }}: {{ note.note }}</span>
      </li>
    </ol>
  </DiagramFigure>
</template>

<style scoped>
/* Number, who to whom, what: three columns where they fit, the message on
   its own line under who on a phone. */
.diagram-sequence > li {
  display: grid;
  grid-template-columns: 3ch 27ch 1fr;
  column-gap: 1ch;
  padding-block: 0.125rem;
}

.diagram-sequence__n {
  color: var(--ui-text-toned);
  text-align: right;
}

/* A note spans who and what, on its own line under its message. */
.diagram-sequence__note {
  grid-column: 2 / -1;
}

@media (max-width: 639px) {
  .diagram-sequence > li {
    grid-template-columns: 3ch 1fr;
  }

  .diagram-sequence__say,
  .diagram-sequence__note {
    grid-column: 2;
  }

  .diagram-sequence > li + li {
    margin-top: 0.25rem;
  }
}
</style>
