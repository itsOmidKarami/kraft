<!-- Overrides Docus's AppHeaderLogo.vue (docus 5.13.0), which wrote the title
     as plain text in the body font. The wordmark is the command a reader
     types, `kraft`, after the prompt the StageStrip chain starts with, in
     IBM Plex Mono. Type only: the app icon (public/icon.svg) is a sans K
     on a dark tile, which at 24px beside the mono name read as a second
     logo. The link around it (AppHeaderLeft.vue) is named "Kraft", so a
     screen reader hears that and not the prompt. -->
<template>
  <span class="wordmark"><span class="wordmark__prompt" aria-hidden="true">›</span>kraft</span>
</template>

<style scoped>
.wordmark {
  display: inline-flex;
  align-items: baseline;
  gap: 0.5ch;
  font-family: var(--font-mono);
  font-size: 1.125rem;
  font-weight: 600;
  line-height: 1.5rem;
  letter-spacing: -0.01em;
  color: var(--ui-text-highlighted);
}

.wordmark__prompt {
  color: var(--ui-primary);
}
</style>
