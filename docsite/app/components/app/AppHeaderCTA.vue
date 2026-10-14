<!-- Overrides Docus's empty header slot with the version switch: a bordered
     two-part pill, the docs you are on (a filled label) beside the other one
     (a link), so it reads as a control and says what it does. Plain <a>, not
     NuxtLink: the other version is a separate build under another base.
     It keeps the current page; one missing from the other build gets the
     site's 404 page, which useDocsChannel reads as the channel in its URL.
     The link's padding makes a 24px tap target on a phone. Text is the
     default color, not muted: muted on the elevated surface is 4.35:1. -->
<script setup lang="ts">
const docs = useRuntimeConfig().public.docs
const at = useDocsChannel()
const stable = computed(() => docs.stableVersion || 'release')
const link = 'px-2 py-1 text-default hover:bg-elevated hover:text-highlighted'
</script>

<template>
  <div
    v-if="at.channel"
    role="group"
    aria-label="Docs version"
    class="flex items-center overflow-hidden rounded-md border border-default text-xs font-medium"
  >
    <template v-if="at.channel === 'stable'">
      <span aria-current="true" class="bg-elevated px-2 py-1 text-highlighted">{{ stable }}</span>
      <a :href="at.samePage(docs.nextBase)" aria-label="Switch to the docs for unreleased main" :class="link">next</a>
    </template>
    <template v-else>
      <span aria-current="true" class="bg-warning-100 px-2 py-1 text-warning-900 dark:bg-warning-950 dark:text-warning-200">next</span>
      <a :href="at.samePage(docs.stableBase)" :aria-label="`Switch to the ${stable} docs`" :class="link">{{ stable }}</a>
    </template>
  </div>
</template>
