<!-- Overrides Docus's empty header slot with the version menu: a bordered pill
     that names the docs you are on and opens a menu of the others. Items are
     plain links (UDropdownMenu `external` renders an <a>, not NuxtLink): the
     other versions are separate builds under other bases. A link keeps the
     current page; one missing from the other build gets the site's 404 page,
     so a click first asks for the page with a HEAD request and goes to that
     version's home when it is not there. Without JavaScript the link goes to
     the 404 page, which offers every version. The click keeps the #heading
     the reader is at; the link itself cannot (it is rendered on the server,
     where there is no fragment). The pill is amber on /next/, as
     it was before there were more than two versions.
     The trigger is 24px tall on a phone (text-xs, py-1). Text is the default
     color, not muted: muted on the elevated surface is 4.35:1. -->
<script setup lang="ts">
import type { DropdownMenuItem } from '@nuxt/ui'

const at = useDocsVersion()

// One listener for every link of the menu, which is rendered in a portal
// outside this component. Reka's own select event does not carry the click, and
// cancelling the click is what keeps the browser from following the link before
// the probe is back. A middle or modified click is the reader opening a tab,
// which the browser's own handling covers. The link only names its version:
// the address comes from the version list, never from the page.
async function open(event: MouseEvent) {
  const link = (event.target as Element).closest('a[data-version]')
  if (!link || event.button || event.metaKey || event.ctrlKey || event.shiftKey || link.getAttribute('aria-current')) return
  const v = at.value.versions.find(v => v.id === link.getAttribute('data-version'))
  if (!v) return
  event.preventDefault()
  const href = at.value.samePage(v.base)
  const found = await fetch(href, { method: 'HEAD' }).then(r => r.status !== 404, () => true)
  // The section the reader is at goes with them; a page the other version
  // lacks, or a heading it renamed, is the page's top.
  location.assign(found ? href + location.hash : v.base)
}
onMounted(() => document.addEventListener('click', open))
onBeforeUnmount(() => document.removeEventListener('click', open))

const items = computed<DropdownMenuItem[]>(() => at.value.versions.map((v) => {
  const href = at.value.samePage(v.base)
  const here = v.id === at.value.current?.id
  return {
    'label': v.label,
    'to': href,
    'external': true,
    'active': here,
    'aria-current': here ? 'true' : undefined,
    'data-version': v.id,
  }
}))
</script>

<template>
  <UDropdownMenu
    v-if="at.current"
    :items="items"
    :content="{ align: 'end' }"
    :ui="{ content: 'min-w-36' }"
  >
    <UButton
      color="neutral"
      variant="outline"
      size="xs"
      trailing-icon="i-lucide-chevron-down"
      :aria-label="`Docs version: ${at.current.label}. Change version`"
      :class="at.current.id === 'next' ? 'bg-warning-100 text-warning-900 dark:bg-warning-950 dark:text-warning-200 hover:bg-warning-100 dark:hover:bg-warning-950' : 'bg-elevated text-highlighted'"
    >
      <!-- "(latest)" waits for room: with it the pill pushed a 320px header
           past the edge. -->
      {{ at.current.label.replace(' (latest)', '') }}<span v-if="at.current.id === 'latest'" class="hidden sm:inline">&nbsp;(latest)</span>
    </UButton>
  </UDropdownMenu>
</template>
