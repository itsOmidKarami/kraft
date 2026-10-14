<!-- On the /next/ docs only. Links to the same page in the release docs; a
     page that does not exist there yet gets the site's 404 page. The link is
     external because a router path would resolve inside this build
     (/next/kraft/). A thin neutral strip with a small amber mark, not a
     saturated slab: a full-width warning on every page trains readers to
     ignore warnings, and the real switch is in the header (AppHeaderCTA.vue).
     The text is the default text color, which passes AA on the elevated
     surface in both modes; amber text on it did not. It is a labelled region,
     or it would be the one block of the page outside every landmark. The text
     wraps rather than truncating, which on a phone cut it before
     "unreleased". -->
<script setup lang="ts">
const docs = useRuntimeConfig().public.docs
const at = useDocsChannel()
const stable = computed(() => docs.stableVersion || 'release')
</script>

<template>
  <div
    v-if="at.channel === 'next'"
    role="region"
    aria-label="Version notice"
    class="border-b border-default bg-elevated text-xs text-default"
  >
    <div class="mx-auto flex min-h-8 max-w-(--ui-container) items-center gap-2 px-4 py-1 sm:px-6 lg:px-8">
      <span aria-hidden="true" class="h-3.5 w-1 shrink-0 rounded-full bg-warning-500" />
      <p>
        <span>Docs for unreleased main</span>
        <span aria-hidden="true" class="mx-1">·</span>
        <a :href="at.samePage(docs.stableBase)" class="font-medium underline underline-offset-2 hover:text-highlighted">Switch to {{ stable }} docs →</a>
      </p>
    </div>
  </div>
</template>
