<script setup lang="ts">
import { resolveBaseURL } from '@nuxt/ui/utils'

// A part of one of the README screenshots (.github/assets/*.png), cut in CSS
// rather than saved as a second PNG: when the screenshots are retaken
// (frontend/README.md has the recipe) the close-ups follow, with no cropped
// copy left behind to go stale. The landing page shows a part at about its
// own size, where the whole 1440px shot came out with ~5px text.
const props = defineProps<{
  src: string
  alt: string
  // The part to show, in the image's own pixels: "x y width height".
  crop: string
  // The whole image's width in pixels.
  width: number | string
}>()

// On a phone a wide part is shown at three quarters of its size, where the
// text can be read, and scrolls sideways in the box the diagrams use
// (.diagram-scroll, assets/css/prose.css). A part that fits needs no hint.
const phoneScale = 0.75
const scrolls = computed(() => Number(props.crop.split(/\s+/)[2]) * phoneScale > 360)
const style = computed(() => {
  const [x, y, w, h] = props.crop.split(/\s+/).map(Number) as [number, number, number, number]
  // Percentage margins are fractions of the frame's width, vertical ones too,
  // so one scale (frame width / w) places the image on both axes.
  return {
    // In a row of shots (.landing-shots), a share of the width in proportion
    // to the aspect ratio gives every shot the same height.
    figure: { flex: `${w / h} 1 0` },
    frame: { aspectRatio: `${w} / ${h}`, '--phone-width': `${w * phoneScale}px` },
    img: {
      width: `${(Number(props.width) / w) * 100}%`,
      marginLeft: `${(-x / w) * 100}%`,
      marginTop: `${(-y / w) * 100}%`,
    },
  }
})
const url = computed(() => resolveBaseURL(props.src, useRuntimeConfig().app.baseURL))
</script>

<template>
  <figure class="landing-shot" :style="style.figure">
    <div class="diagram-scroll">
      <div class="landing-shot__frame" :style="style.frame">
        <img :src="url" :alt="alt" :style="style.img" loading="lazy" decoding="async">
      </div>
    </div>
    <span v-if="scrolls" class="diagram-hint" aria-hidden="true">Swipe sideways to see all of it</span>
    <figcaption v-if="$slots.default" class="landing-shot__caption">
      <slot />
    </figcaption>
  </figure>
</template>

<style scoped>
.landing-shot__frame {
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: 0.5rem;
}

.landing-shot__frame img {
  display: block;
  max-width: none;
}

@media (max-width: 639px) {
  .landing-shot__frame {
    flex: none;
    width: max(100%, var(--phone-width));
  }
}

.landing-shot__caption {
  margin-top: 1rem;
  color: var(--ui-text-toned);
}
</style>
