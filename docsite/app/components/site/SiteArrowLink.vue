<!-- A link onward, in the accent with an arrow. `href` leaves the router (the
     repo, the demo's static files); `to` stays in it. -->
<script setup lang="ts">
import { NuxtLink } from '#components'

defineProps<{ to?: string, href?: string }>()
</script>

<template>
  <component
    :is="href ? 'a' : NuxtLink"
    class="s-arrow"
    :href="href"
    :to="href ? undefined : to"
  >
    <slot /><svg
      aria-hidden="true"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    ><path d="M3 8h10" /><path d="M9 4l4 4-4 4" /></svg>
  </component>
</template>
