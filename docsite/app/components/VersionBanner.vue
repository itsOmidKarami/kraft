<!-- On the /next/ build only. Links to the same page in the release docs; a
     page that does not exist there yet gets the site's 404 page. The link is
     external because a router path would resolve inside this build
     (/kraft/next/kraft/). The title wraps rather than truncating, which on a
     phone cut it before "unreleased". -->
<script setup lang="ts">
const docs = useRuntimeConfig().public.docs
const route = useRoute()
</script>

<template>
  <UBanner
    v-if="docs.channel === 'next'"
    color="warning"
    :ui="{ container: 'h-auto min-h-12 py-2', title: 'whitespace-normal' }"
    icon="i-lucide-flask-conical"
    :title="`You're reading docs for unreleased main.`"
    :actions="[{ label: `Docs for ${docs.stableVersion || 'the release'} →`, to: docs.stableBase + route.path.replace(/^\//, ''), external: true, color: 'neutral', variant: 'outline' }]"
  />
</template>
