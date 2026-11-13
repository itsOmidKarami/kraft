<!-- The strip under a capability page's head: a link to each block. From a
     tablet up it sticks to the top of the screen, and the link of the block
     you are reading is marked (aria-current): the last block whose top has
     passed under the bar. -->
<script setup lang="ts">
const props = defineProps<{ links: { id: string, label: string }[] }>()

const bar = useTemplateRef<HTMLElement>('bar')
const current = ref('')
let frame = 0

function mark() {
  frame = 0
  const line = (bar.value?.offsetHeight ?? 0) + 1
  current.value = props.links.reduce((found, l) => {
    const top = document.getElementById(l.id)?.getBoundingClientRect().top
    return top !== undefined && top <= line ? l.id : found
  }, '')
}
const onScroll = () => {
  frame ||= requestAnimationFrame(mark)
}

onMounted(() => {
  mark()
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll, { passive: true })
})
onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
  cancelAnimationFrame(frame)
})
</script>

<template>
  <nav
    ref="bar"
    class="s-onpage"
    aria-labelledby="on-this-page"
  >
    <div class="s-wrap s-onpage-row">
      <span
        id="on-this-page"
        class="s-label-small"
      >On this page</span>
      <a
        v-for="l in links"
        :key="l.id"
        :href="`#${l.id}`"
        :aria-current="current === l.id ? 'true' : undefined"
      >{{ l.label }}</a>
    </div>
  </nav>
</template>
