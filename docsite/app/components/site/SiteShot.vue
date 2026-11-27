<!-- A screenshot on the violet field. Under 640px it shows `phone`, a tighter
     crop that keeps its text readable, where the whole capture would shrink
     to a third of its size; a tap opens the whole capture at full size. -->
<script setup lang="ts">
defineProps<{
  src: string
  width: number
  height: number
  alt: string
  phone: { src: string, width: number, height: number }
}>()
</script>

<template>
  <div class="s-crop">
    <a
      class="s-shot"
      :href="src"
    >
      <picture>
        <source
          media="(min-width: 640px)"
          :srcset="src"
          :width="width"
          :height="height"
        >
        <img
          :src="phone.src"
          :width="phone.width"
          :height="phone.height"
          :alt="alt"
          loading="lazy"
        >
      </picture>
      <span class="s-sr"> (opens the whole screenshot)</span>
    </a>
  </div>
</template>
