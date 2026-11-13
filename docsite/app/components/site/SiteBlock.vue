<!-- A capability block: its number and name, a claim as the heading, a short
     body (the default slot), a docs link and one picture. `picture` places the
     picture right or left of the text (sides alternate down a page), or below
     it across the column for a screenshot, with the body beside the heading.
     Under 1024px everything stacks, text first. With no picture the heading
     sits left of the body (Why Kraft's longer text); the label and the docs
     link are optional. -->
<script setup lang="ts">
withDefaults(defineProps<{
  id: string
  label?: string
  title: string
  docs?: { to: string, label: string }
  picture?: 'right' | 'left' | 'below'
  band?: boolean
}>(), { picture: 'right' })
</script>

<template>
  <section
    :id="id"
    class="s-section s-block"
    :class="[$slots.picture ? `s-block-${picture}` : 's-block-text', { 's-band': band }]"
    :aria-labelledby="`${id}-title`"
  >
    <div class="s-wrap s-block-grid">
      <div class="s-block-head">
        <p
          v-if="label"
          class="s-block-label"
        >
          {{ label }}
        </p>
        <h2
          :id="`${id}-title`"
          class="s-block-title"
        >
          {{ title }}
        </h2>
      </div>
      <div class="s-block-body">
        <slot />
        <SiteArrowLink
          v-if="docs"
          :to="docs.to"
        >
          Docs: {{ docs.label }}
        </SiteArrowLink>
      </div>
      <figure
        v-if="$slots.picture"
        class="s-figure s-block-picture"
      >
        <slot name="picture" />
      </figure>
    </div>
  </section>
</template>
