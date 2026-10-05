<script setup lang="ts">
// Nuxt UI's prose image, with one addition: a diagram (`/diagrams/<name>.svg`,
// drawn by diagrams/render.mjs) has a dark twin, `<name>.dark.svg`, and the
// page shows the one that matches the colour mode. An <img> can't follow the
// site's `.dark` class from inside the SVG, so both are rendered and CSS hides
// one; each keeps the zoom. The Markdown, and the raw/*.md copies made for
// LLMs, still name the one light file.
import UProseImg from '@nuxt/ui/runtime/components/prose/Img.vue'

defineOptions({ inheritAttrs: false })
const props = defineProps<{ src: string, alt: string, width?: string | number, height?: string | number }>()
const dark = computed(() => /\/diagrams\/[^/]+\.svg$/.test(props.src) ? props.src.replace(/\.svg$/, '.dark.svg') : null)
</script>

<template>
  <template v-if="dark">
    <UProseImg v-bind="{ ...$attrs, ...props }" class="kraft-diagram kraft-diagram--light" />
    <UProseImg v-bind="{ ...$attrs, ...props }" :src="dark" class="kraft-diagram kraft-diagram--dark" />
  </template>
  <UProseImg v-else v-bind="{ ...$attrs, ...props }" />
</template>

<style>
.dark .kraft-diagram--light,
:root:not(.dark) .kraft-diagram--dark {
  display: none;
}
</style>
