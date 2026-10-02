<!-- Overrides Docus's empty header slot with the version switch. Plain <a>,
     not NuxtLink: the other version is a separate build under another base.
     It keeps the current page; one missing from the other build gets the
     site's 404 page, which useDocsChannel reads as the channel in its URL.
     The link's padding makes a 24px tap target on a phone. -->
<script setup lang="ts">
const docs = useRuntimeConfig().public.docs
const at = useDocsChannel()
</script>

<template>
  <div v-if="at.channel" class="flex items-center gap-1.5 text-sm">
    <template v-if="at.channel === 'stable'">
      <UBadge :label="docs.stableVersion" color="neutral" variant="subtle" />
      <a :href="at.samePage(docs.nextBase)" class="px-1 py-1 text-muted hover:text-highlighted">next</a>
    </template>
    <template v-else>
      <UBadge label="next" color="warning" variant="subtle" :ui="{ base: 'text-warning-800 dark:text-warning-300' }" />
      <a :href="at.samePage(docs.stableBase)" class="px-1 py-1 text-muted hover:text-highlighted">{{ docs.stableVersion || 'release' }}</a>
    </template>
  </div>
</template>
