<!-- System, Light or Dark: the colour mode the docs pages use too, kept in the
     browser by @nuxtjs/color-mode. A radio group: one tab stop, arrow keys move
     the choice. The server cannot know the stored choice, so it renders System
     and the switch shows the real one once mounted. -->
<script setup lang="ts">
const OPTIONS = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]
const ARROWS: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }

const mode = useColorMode()
const mounted = ref(false)
onMounted(() => {
  mounted.value = true
})
const current = computed(() => mounted.value ? mode.preference : 'system')
const id = useId()

function onKey(e: KeyboardEvent, i: number) {
  const step = ARROWS[e.key]
  if (!step) return
  e.preventDefault()
  const next = (i + step + OPTIONS.length) % OPTIONS.length
  mode.preference = OPTIONS[next]!.value
  const siblings = (e.currentTarget as HTMLElement).parentElement!.children
  ;(siblings[next] as HTMLElement).focus()
}
</script>

<template>
  <div class="s-theme">
    <span
      :id="id"
      class="s-label-small"
    >Theme</span>
    <div
      role="radiogroup"
      :aria-labelledby="id"
      class="s-switch"
    >
      <button
        v-for="(o, i) in OPTIONS"
        :key="o.value"
        type="button"
        role="radio"
        :aria-checked="current === o.value"
        :tabindex="current === o.value ? 0 : -1"
        @click="mode.preference = o.value"
        @keydown="onKey($event, i)"
      >
        {{ o.label }}
      </button>
    </div>
  </div>
</template>
