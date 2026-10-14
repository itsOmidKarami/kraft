<script setup lang="ts">
// The shipped `default` chain (templates/chains/default.yaml), simplified for
// an overview: one verb per stage, and only the final review marked as a stop.
// "PR" as in the tagline; on GitLab the same node opens a merge request.
// The real chain has more nodes and approval gates than this. A stop is
// marked in words, not colour alone.
const stages: { name: string, stop?: string }[] = [
  { name: 'specify' },
  { name: 'plan' },
  { name: 'implement' },
  { name: 'verify' },
  { name: 'draft PR' },
  { name: 'review', stop: 'you approve' },
  { name: 'merge' },
]
// The chain waits there for a person: the stages after it are drawn as not
// yet run, and the animation halts on it.
const stopAt = stages.findIndex(stage => stage.stop)
</script>

<template>
  <figure class="hero-mono stage-strip">
    <div role="list" aria-label="The default chain's stages, in order" class="stage-strip__nodes">
      <!-- The arrow sits inside its stage, so on a phone, where the stages
           stack, each line reads `→ plan` like a transcript. -->
      <span
        v-for="(stage, i) in stages"
        :key="stage.name"
        role="listitem"
        class="stage-strip__stage"
        :class="{ 'stage-strip__stop': stage.stop, 'stage-strip__after': i > stopAt }"
        :style="{ '--i': i }"
      >
        <span class="stage-strip__arrow" aria-hidden="true">{{ i ? '→' : '›' }}</span>{{ stage.name }}<span v-if="stage.stop" class="stage-strip__mark">[{{ stage.stop }}]</span>
      </span>
    </div>
    <figcaption class="stage-strip__caption">
      The default chain, simplified: some nodes and approval gates are left out.
    </figcaption>
  </figure>
</template>

<style scoped>
.stage-strip {
  padding: 1.25rem 1.5rem;
  background: var(--ui-bg-elevated);
  border: 1px solid var(--ui-border);
  border-radius: 0.5rem;
  /* Body size on a phone and larger from a tablet up: the chain is the one
     idea the landing page sells. */
  font-size: clamp(1rem, 0.85rem + 0.6vw, 1.3125rem);
  line-height: 1.5;
  color: var(--ui-text-highlighted);
}

.stage-strip__nodes {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  column-gap: 0.75em;
  row-gap: 0.25rem;
}

.stage-strip__stage {
  white-space: nowrap;
}

.stage-strip__arrow {
  margin-right: 0.75em;
  color: var(--ui-text-muted);
}

.stage-strip__stage:first-child .stage-strip__arrow,
.stage-strip__stop {
  color: var(--ui-primary);
  font-weight: 600;
}

.stage-strip__mark {
  margin-left: 0.5em;
  padding: 0.0625em 0.375em;
  border-radius: 0.25rem;
  background: color-mix(in oklab, var(--ui-primary) 14%, transparent);
}

/* Not yet run: the chain is waiting on the stop before it. Muted text is
   still above 4.5:1 on the strip's background in both modes. */
.stage-strip__after {
  color: var(--ui-text-muted);
}

.stage-strip__caption {
  margin-top: 0.875rem;
  font-size: 0.8125rem;
  color: var(--ui-text-muted);
}

/* On a phone the stages stack, one per line. */
@media (max-width: 639px) {
  .stage-strip {
    padding: 1rem 1.25rem;
  }

  .stage-strip__nodes {
    flex-direction: column;
    align-items: flex-start;
  }
}

/* The stages light up in order and the run halts on the stop. Only when the
   reader allows motion: the static end state is the default, so nothing is
   ever hidden without the animation. */
@media (prefers-reduced-motion: no-preference) {
  .stage-strip__stage:not(.stage-strip__after) {
    animation: stage-strip-run 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
    animation-delay: calc(0.2s + var(--i) * 0.28s);
  }

  .stage-strip__mark {
    animation: stage-strip-wait 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
    animation-delay: calc(0.45s + var(--i) * 0.28s);
  }
}

@keyframes stage-strip-run {
  from {
    color: var(--ui-text-dimmed);
  }
}

@keyframes stage-strip-wait {
  from {
    background: transparent;
  }
}
</style>
