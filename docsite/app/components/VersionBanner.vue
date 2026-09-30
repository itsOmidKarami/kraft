<!-- On the /next/ build only. Links to the release docs' root, not the same
     path: a page can move or not exist yet in the release. The link is
     external because a router path would resolve inside this build
     (/kraft/next/kraft/). The title wraps rather than truncating, which on a
     phone cut it before "unreleased". -->
<script setup lang="ts">
const docs = useRuntimeConfig().public.docs
</script>

<template>
  <UBanner
    v-if="docs.channel === 'next'"
    color="warning"
    :ui="{ container: 'h-auto min-h-12 py-2', title: 'whitespace-normal' }"
    icon="i-lucide-flask-conical"
    :title="`You're reading docs for unreleased main.`"
    :actions="[{ label: `Docs for ${docs.stableVersion || 'the release'} →`, to: docs.stableBase, external: true, color: 'neutral', variant: 'outline' }]"
  />
</template>
