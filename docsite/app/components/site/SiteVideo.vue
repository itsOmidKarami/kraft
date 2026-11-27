<!-- A YouTube video behind a poster button. Nothing loads from YouTube until
     the button is pressed; then its player (the no-cookie address) takes the
     poster's place, starts, and gets focus. -->
<script setup lang="ts">
defineProps<{ id: string, title: string, length: string, label: string }>()

const playing = ref(false)
const frame = useTemplateRef<HTMLIFrameElement>('frame')

async function play() {
  playing.value = true
  await nextTick()
  frame.value?.focus()
}
</script>

<template>
  <div class="s-video">
    <iframe
      v-if="playing"
      ref="frame"
      :src="`https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0`"
      :title="title"
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
      allowfullscreen
    />
    <button
      v-else
      type="button"
      class="s-video-poster"
      :aria-label="label"
      @click="play"
    >
      <span class="s-label-small">VIDEO · {{ length }}</span>
      <span class="s-video-play"><svg
        aria-hidden="true"
        width="28"
        height="28"
        viewBox="0 0 24 24"
        fill="currentColor"
      ><path d="M8 5.5v13l11-6.5z" /></svg></span>
      <span class="s-video-title">{{ title }}</span>
    </button>
  </div>
</template>
