<!-- On the /next/ docs and a frozen older major's only. Links to the same page
     in the latest release's docs; a page that does not exist there gets the
     site's 404 page, which offers every version. The link is external because a router
     path would resolve inside this build (/next/kraft/). A thin neutral strip
     with a small mark, amber for unreleased main and grey for an old release,
     not a saturated slab: a full-width warning on every page trains readers to
     ignore warnings, and the real switch is in the header (AppHeaderCTA.vue).
     The text is the default text color, which passes AA on the elevated
     surface in both modes; amber text on it did not. It is a labelled region,
     or it would be the one block of the page outside every landmark. The text
     wraps rather than truncating, which on a phone cut it before
     "unreleased". On /next/ a second link lists what changed since the release
     (the Unreleased page, built from the docs' "Added in next" notes); it is
     a router link, inside this build. -->
<script setup lang="ts">
const at = useDocsVersion()
const unreleased = useRuntimeConfig().public.docs.unreleased
const notice = computed(() => {
  const { current, latest } = at.value
  if (!current || !latest || current.id === latest.id) return null
  return current.id === 'next'
    ? { text: 'Docs for unreleased main', link: `Switch to ${latest.tag.slice(1)} docs →`, mark: 'bg-warning-500', main: true }
    : { text: `Docs for ${current.tag.slice(1)}, an older release`, link: 'Switch to the latest →', mark: 'bg-neutral-400 dark:bg-neutral-500' }
})
</script>

<template>
  <div
    v-if="notice && at.latest"
    role="region"
    aria-label="Version notice"
    class="border-b border-default bg-elevated text-xs text-default"
  >
    <div class="mx-auto flex min-h-8 max-w-(--ui-container) items-center gap-2 px-4 py-1 sm:px-6 lg:px-8">
      <span aria-hidden="true" class="h-3.5 w-1 shrink-0 rounded-full" :class="notice.mark" />
      <p>
        <span>{{ notice.text }}</span>
        <span aria-hidden="true" class="mx-1">·</span>
        <template v-if="'main' in notice && unreleased?.page">
          <NuxtLink :to="unreleased.page" class="whitespace-nowrap font-medium underline underline-offset-2 hover:text-highlighted">What changed since the release →</NuxtLink>
          <span aria-hidden="true" class="mx-1">·</span>
        </template>
        <a :href="at.samePage(at.latest.base)" class="whitespace-nowrap font-medium underline underline-offset-2 hover:text-highlighted">{{ notice.link }}</a>
      </p>
    </div>
  </div>
</template>
