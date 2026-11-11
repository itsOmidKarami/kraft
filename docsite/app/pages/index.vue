<script setup lang="ts">
// The home page, design K (components/home). It is site code, not content, so
// a merge to main changes it on the next deploy rather than at a release; and
// with this file in app/pages Docus defines no landing route or collection of
// its own (docus/utils/pages.ts), so no build renders a content/index.md.
// Only the build at the root of the domain draws it: /next/ and /vN/ are built
// from the same code and answer their root with a redirect, so there is one
// copy of the home.
definePageMeta({ layout: false, header: false, footer: false })

const baseURL = useRuntimeConfig().app.baseURL
const home = isHomeBuild(baseURL)
const origin = useSiteConfig().url.replace(/\/$/, '')
const target = `${origin}/`

const title = 'Kraft runs the task. You make the calls.'
const description = 'Kraft is an app on your computer. It hands coding tasks to Claude Code, checks the work and stops only for the decisions that are yours.'

if (home) {
  useSeo({ title, description, type: 'website' })
  defineOgImage('Docs', {
    headline: 'Kraft',
    title,
    description: formatOgDescription(title, description),
  })
} else {
  // A static host cannot redirect, so the page does: the refresh, a script that
  // replaces the history entry, and a plain link for a reader with neither.
  // `data-fixed` keeps plugins/canonical.ts from putting this build's base on
  // the canonical, which names the one home at the domain's root.
  useHead({
    title: 'Kraft',
    link: [{ rel: 'canonical', href: target, 'data-fixed': '' }],
    meta: [
      { 'http-equiv': 'refresh', 'content': `0; url=${target}` },
      { name: 'robots', content: 'noindex' },
    ],
    script: [{ innerHTML: `location.replace(${JSON.stringify(target).replace(/</g, '\\u003c')} + location.hash)` }],
  })
}
</script>

<template>
  <HomePage v-if="home" />
  <main
    v-else
    id="main-content"
    tabindex="-1"
  >
    <p>
      The home page of Kraft is at <a :href="target">{{ target }}</a>.
    </p>
  </main>
</template>
