<!-- Overrides Docus's empty header slot with the version switch. Plain <a>,
     not NuxtLink: the other version is a separate build under another base.
     It keeps the current page; one missing from the other build gets the
     site's 404 page. -->
<script setup lang="ts">
const docs = useRuntimeConfig().public.docs
const route = useRoute()
const samePage = (base: string) => base + route.path.replace(/^\//, '')
</script>

<template>
  <div v-if="docs.channel" class="flex items-center gap-1.5 text-sm">
    <template v-if="docs.channel === 'stable'">
      <UBadge :label="docs.stableVersion" color="neutral" variant="subtle" />
      <a :href="samePage(docs.nextBase)" class="text-muted hover:text-highlighted">next</a>
    </template>
    <template v-else>
      <UBadge label="next" color="warning" variant="subtle" />
      <a :href="samePage(docs.stableBase)" class="text-muted hover:text-highlighted">{{ docs.stableVersion || 'release' }}</a>
    </template>
  </div>
</template>
