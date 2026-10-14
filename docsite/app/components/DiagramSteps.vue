<script setup lang="ts">
// A sequence of stages, each on its own line after its arrow, and under a
// stage the branches it can take, drawn as a tree. DiagramFlow uses it, and it
// uses itself for each branch. A branch is one stage with a condition
// (`when`), or a sequence of them (`stages`), and may end by going back to an
// earlier stage (`back`), which is drawn with the loop's arrow.
export interface Stage {
  name: string
  note?: string
  // A person decides here: the chip's words, such as "you approve".
  gate?: string
  // The stage repeats until it passes: the loop's name, such as "fix loop".
  loop?: string
  // The edge into this stage: a path not always taken, or a way back round.
  dashed?: boolean
  back?: boolean
  when?: string
  stages?: Stage[]
  branches?: Stage[]
}

const props = defineProps<{ stages: Stage[], start?: boolean }>()

function arrow(stage: Stage, i: number) {
  if (stage.back) return 'back'
  if (stage.dashed) return 'dashed'
  return props.start && i === 0 ? 'start' : 'next'
}

// A branch of one stage and a branch of several are drawn alike: as a
// sequence whose first stage carries the condition.
function branchStages(branch: Stage): Stage[] {
  const [first, ...rest] = branch.stages ?? [branch]
  return [{ ...first!, when: branch.when, dashed: first!.dashed ?? branch.dashed, back: first!.back ?? branch.back }, ...rest]
}
</script>

<template>
  <ol class="diagram-steps">
    <li v-for="(stage, i) in props.stages" :key="i" class="diagram-step">
      <span class="diagram-line">
        <template v-if="stage.when"><span class="diagram-label">{{ stage.when }}<span class="sr-only">:</span></span>{{ ' ' }}</template>
        <DiagramArrow :kind="arrow(stage, i)" />{{ ' ' }}<span class="diagram-name" :class="{ 'diagram-gate': stage.gate }">{{ stage.name }}</span>
        <span v-if="stage.gate" class="diagram-chip">[{{ stage.gate }}]</span>
        <span v-if="stage.loop" class="diagram-loop">[↻ {{ stage.loop }}]</span>
      </span>
      <span v-if="stage.note" class="diagram-note">{{ stage.note }}</span>
      <ul v-if="stage.branches?.length" class="diagram-branches">
        <li v-for="(branch, j) in stage.branches" :key="j" class="diagram-branch">
          <DiagramSteps :stages="branchStages(branch)" />
        </li>
      </ul>
    </li>
  </ol>
</template>

<style scoped>
/* Each line reads `→ name`, and a line too long for the box wraps under the
   name, not under the arrow: two characters of hanging indent. */
.diagram-line {
  display: block;
  padding-left: 2ch;
  text-indent: -2ch;
}

.diagram-note {
  padding-left: 2ch;
}

.diagram-step + .diagram-step {
  margin-top: 0.25rem;
}

/* The branches of a stage hang from it as a tree: a rule down the left, and
   a tick into each branch's first line. Muted, as the arrows are. */
.diagram-branches {
  margin: 0.25rem 0 0 0.5ch;
}

.diagram-branch {
  position: relative;
  padding-left: 2.5ch;
}

.diagram-branch + .diagram-branch {
  margin-top: 0.25rem;
}

.diagram-branch::before,
.diagram-branch::after {
  content: '';
  position: absolute;
  left: 0;
  border-color: var(--ui-text-muted);
}

/* Down the side to the next branch; the last stops at its own tick. */
.diagram-branch::before {
  top: -0.25rem;
  bottom: 0;
  border-left: 1px solid;
}

.diagram-branch:first-child::before {
  top: 0;
}

.diagram-branch:last-child::before {
  bottom: auto;
  height: 0.8em;
}

.diagram-branch::after {
  top: 0.8em;
  width: 2ch;
  border-top: 1px solid;
}
</style>
