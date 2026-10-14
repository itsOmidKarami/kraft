<script setup lang="ts">
// The frame every diagram on the site is drawn in: a figure with its caption,
// in the StageStrip chain's box (StageStrip.vue). The diagrams are markup, not
// images, so they set in Plex, follow the colour mode, and read to a screen
// reader as the lists they are. DiagramFlow, DiagramGraph and DiagramSequence
// fill it; the shared look of a stage, an arrow, a gate and a loop is in the
// unscoped style below, so all three draw them alike.
defineProps<{ caption: string }>()
</script>

<template>
  <figure class="diagram">
    <slot />
    <figcaption class="diagram__caption">{{ caption }}</figcaption>
  </figure>
</template>

<style>
/* One label size and one face: every word in a diagram is Plex Mono at
   14px, as a terminal prints it. What sets a name apart from what is said
   about it is colour, never size. */
.diagram {
  margin-block: 1.5rem;
  padding: 1rem 1.25rem;
  background: var(--ui-bg-elevated);
  border: 1px solid var(--ui-border);
  border-radius: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.875rem;
  line-height: 1.6;
  color: var(--ui-text-highlighted);
}

.diagram ol,
.diagram ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

/* The caption is the alt text the image had: prose about the picture, so it
   is in the reading face, under it. Toned, not muted: muted on the box's
   background is 4.35:1 in light mode, under WCAG AA's 4.5:1. */
.diagram__caption {
  margin-top: 0.875rem;
  font-family: var(--font-sans);
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--ui-text-toned);
}

/* A thing in the diagram: a node, a status, a part. Its note is what the
   diagram says about it, on the line under it, toned (6.9:1 in light mode,
   9.8:1 in dark). */
.diagram-name {
  overflow-wrap: anywhere;
}

.diagram-note {
  display: block;
  color: var(--ui-text-toned);
}

/* `→` leads to the next thing, as in the chain strip. `⇢` is a path that is
   not always taken or a reply (a dashed edge), and `↻` goes back round, the
   one notation for every loop. Muted: a graphic needs 3:1 and muted is 4.35:1
   in light mode, 5.6:1 in dark. The words for a screen reader are in a hidden
   span beside it. */
.diagram-arrow {
  color: var(--ui-text-muted);
}

/* The first stage's `›`, the prompt the chain strip starts with. */
.diagram-arrow--start {
  color: var(--ui-primary);
  font-weight: 600;
}

/* A condition or an event on an edge ("passes", "resume"): toned, before
   the arrow it labels. */
.diagram-label {
  color: var(--ui-text-toned);
}

/* A gate, where a person decides: the chain strip's stop, in violet, with
   its `[you approve]` chip. The name is the primary colour, 5.4:1 on the box
   in light mode and 5.1:1 in dark. On the chip's tint the primary falls under
   WCAG AA's 4.5:1 (4.3 and 4.1: the strip's chip is large text, these are
   not), so the chip's words are a step further from the background:
   violet-700 in light mode (5.3:1), violet-300 in dark (6.4:1). */
.diagram-gate {
  color: var(--ui-primary);
  font-weight: 600;
}

.diagram-chip {
  margin-left: 0.5em;
  padding: 0.0625em 0.375em;
  border-radius: 0.25rem;
  background: color-mix(in oklab, var(--ui-primary) 14%, transparent);
  color: var(--ui-color-primary-700);
  font-weight: 600;
  white-space: nowrap;
}

.dark .diagram-chip {
  color: var(--ui-color-primary-300);
}

/* A loop on a stage (`↻ fix loop`): the loop's arrow and its name, toned,
   in brackets like the chip but with no fill, as no one is asked anything. */
.diagram-loop {
  margin-left: 0.5em;
  color: var(--ui-text-toned);
}

/* A group (the stages of filing, the parts in one process): one frame,
   its name on the first line. */
.diagram-group {
  padding: 0.625rem 0.875rem;
  border: 1px solid var(--ui-border-accented);
  border-radius: 0.375rem;
}

.diagram-group__label {
  display: block;
  margin-bottom: 0.375rem;
  color: var(--ui-text-toned);
  font-weight: 600;
}

/* Under 640px the box gives the text its room back. */
@media (max-width: 639px) {
  .diagram {
    padding: 0.875rem 1rem;
  }

  .diagram-group {
    padding: 0.5rem 0.75rem;
  }
}
</style>
