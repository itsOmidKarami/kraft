<!-- On the /next/ docs only. Links to the same page in the release docs; a
     page that does not exist there yet gets the site's 404 page. The link is
     external because a router path would resolve inside this build
     (/kraft/next/kraft/). The title wraps rather than truncating, which on a
     phone cut it before "unreleased". The text is dark in both color modes:
     Nuxt UI's white on the light-mode amber measured 1.9:1. -->
<script setup lang="ts">
const docs = useRuntimeConfig().public.docs
const at = useDocsChannel()
</script>

<template>
  <UBanner
    v-if="at.channel === 'next'"
    color="warning"
    :ui="{ container: 'h-auto min-h-12 py-2', title: 'whitespace-normal text-neutral-900', icon: 'text-neutral-900' }"
    icon="i-lucide-flask-conical"
    :title="`You're reading docs for unreleased main.`"
    :actions="[{ label: `Docs for ${docs.stableVersion || 'the release'} →`, to: at.samePage(docs.stableBase), external: true, color: 'neutral', variant: 'outline' }]"
  />
</template>
