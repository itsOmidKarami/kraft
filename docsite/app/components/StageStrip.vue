<script setup lang="ts">
// The shipped `default` chain (templates/chains/default.yaml), simplified: it
// leaves out some nodes that never ask a person, and keeps every node where
// the chain stops for one. A stop is marked in words, not colour
// alone.
const stages: { name: string, stop?: string }[] = [
  { name: 'spec' },
  { name: 'spec_approval', stop: 'you approve' },
  { name: 'plan' },
  { name: 'plan_approval', stop: 'you approve' },
  { name: 'chain_revision_approval', stop: 'you approve, if a change is proposed' },
  { name: 'implementation' },
  { name: 'verification' },
  { name: 'local_review', stop: 'you approve' },
  { name: 'draft_merge_request' },
  { name: 'final_review', stop: 'you approve' },
  { name: 'external_approval', stop: 'a reviewer approves on the forge' },
  { name: 'merge' },
]
</script>

<template>
  <figure class="hero-mono stage-strip">
    <div role="list" aria-label="The default chain's nodes, in order" class="stage-strip__nodes">
      <span class="stage-strip__prompt" aria-hidden="true">›</span>
      <template v-for="(stage, i) in stages" :key="stage.name">
        <span role="listitem" :class="{ 'stage-strip__stop': stage.stop }">
          {{ stage.name }}<span v-if="stage.stop" class="stage-strip__mark">[{{ stage.stop }}]</span>
        </span>
        <span v-if="i < stages.length - 1" class="stage-strip__arrow" aria-hidden="true">→</span>
      </template>
    </div>
    <figcaption class="stage-strip__caption">
      The default chain, simplified: some nodes are left out, but every place it
      stops for a person is shown.
    </figcaption>
  </figure>
</template>

<style scoped>
.stage-strip {
  padding: 0.875rem 1rem;
  background: var(--ui-bg-elevated);
  border: 1px solid var(--ui-border);
  border-radius: 0.375rem;
  font-size: 0.8125rem;
  color: var(--ui-text-toned);
}

.stage-strip__nodes {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.stage-strip__prompt,
.stage-strip__stop {
  color: var(--ui-primary);
}

.stage-strip__mark {
  margin-left: 0.25rem;
  font-weight: 600;
}

.stage-strip__arrow {
  color: var(--ui-text-muted);
}

.stage-strip__caption {
  margin-top: 0.625rem;
  font-size: 0.75rem;
  color: var(--ui-text-muted);
}
</style>
