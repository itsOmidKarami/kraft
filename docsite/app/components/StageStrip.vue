<script setup lang="ts">
withDefaults(defineProps<{ variant?: 'boxes' | 'terminal' | 'inline' }>(), { variant: 'terminal' })

const stages = [
  { name: 'spec', gate: false },
  { name: 'plan', gate: false },
  { name: 'implementation', gate: false },
  { name: 'verify', gate: false },
  { name: 'final_review', gate: true },
  { name: 'merged', gate: false },
]
</script>

<template>
  <div
    v-if="variant === 'terminal'"
    class="hero-mono stage-strip stage-strip--terminal"
    role="list"
    aria-label="A chain's nodes, in order"
  >
    <span class="stage-strip__prompt" aria-hidden="true">›</span>
    <template v-for="(stage, i) in stages" :key="stage.name">
      <span
        role="listitem"
        :class="{ 'stage-strip__gate-text': stage.gate }"
      >{{ stage.name }}</span>
      <span v-if="i < stages.length - 1" class="stage-strip__arrow" aria-hidden="true">→</span>
    </template>
  </div>

  <div
    v-else-if="variant === 'inline'"
    class="hero-mono stage-strip stage-strip--inline"
    role="list"
    aria-label="A chain's nodes, in order"
  >
    <template v-for="(stage, i) in stages" :key="stage.name">
      <span
        role="listitem"
        :class="{ 'stage-strip__gate-text': stage.gate }"
      >{{ stage.name }}<sup v-if="stage.gate" class="stage-strip__gate-mark">human</sup></span>
      <span v-if="i < stages.length - 1" class="stage-strip__arrow" aria-hidden="true">→</span>
    </template>
  </div>

  <div v-else class="hero-mono stage-strip" role="list" aria-label="A chain's nodes, in order">
    <template v-for="(stage, i) in stages" :key="stage.name">
      <span
        role="listitem"
        class="stage-strip__stage"
        :class="{ 'stage-strip__stage--gate': stage.gate }"
      >{{ stage.name }}</span>
      <span v-if="i < stages.length - 1" class="stage-strip__rule" aria-hidden="true" />
    </template>
  </div>
</template>

<style scoped>
.stage-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0;
  font-size: 0.8125rem;
  overflow-x: auto;
}

.stage-strip__stage {
  padding: 0.25rem 0.625rem;
  border: 1px solid var(--ui-border);
  border-radius: 0.25rem;
  white-space: nowrap;
  color: var(--ui-text-muted);
}

.stage-strip__stage--gate {
  border-color: var(--ui-primary);
  color: var(--ui-primary);
}

.stage-strip__rule {
  width: 1.25rem;
  height: 1px;
  background: var(--ui-border);
  flex-shrink: 0;
}

.stage-strip--terminal {
  gap: 0.5rem;
  padding: 0.875rem 1rem;
  background: var(--ui-bg-elevated);
  border: 1px solid var(--ui-border);
  border-radius: 0.375rem;
  color: var(--ui-text-muted);
}

.stage-strip__prompt {
  color: var(--ui-primary);
}

.stage-strip__arrow {
  color: var(--ui-text-dimmed);
  margin: 0 0.125rem;
}

.stage-strip--inline {
  gap: 0.5rem;
  color: var(--ui-text-muted);
}

.stage-strip__gate-text {
  color: var(--ui-primary);
}

.stage-strip__gate-mark {
  font-size: 0.625rem;
  margin-left: 0.25rem;
  border: 1px solid var(--ui-primary);
  border-radius: 0.25rem;
  padding: 0 0.25rem;
  vertical-align: middle;
}
</style>
