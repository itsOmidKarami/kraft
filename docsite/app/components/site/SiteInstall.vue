<!-- The Install band, on the violet field: the command with a Copy button that
     says it copied in words, then the page's own text (the default slot) and its
     links (the links slot). With no text it is shorter, as on How it works. -->
<script setup lang="ts">
const COMMAND = 'uv tool install kraft-sdlc'
const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

async function copy() {
  try {
    await navigator.clipboard.writeText(COMMAND)
  } catch {
    return
  }
  copied.value = true
  clearTimeout(timer)
  timer = setTimeout(() => {
    copied.value = false
  }, 2000)
}
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section
    class="s-section s-install s-field"
    :class="{ 's-install-short': !$slots.default }"
    aria-labelledby="install"
  >
    <div class="s-wrap s-install-body">
      <h2
        id="install"
        class="s-h2"
      >
        Install Kraft.
      </h2>
      <div class="s-install-command">
        <code>{{ COMMAND }}</code>
        <button
          type="button"
          class="s-btn s-btn-primary"
          @click="copy"
        >
          <template v-if="copied">
            Copied
          </template><template v-else>
            Copy<span class="s-sr"> the install command</span>
          </template>
        </button>
        <span
          class="s-sr"
          aria-live="polite"
        >{{ copied ? 'Copied the install command.' : '' }}</span>
      </div>
      <div
        v-if="$slots.default"
        class="s-install-text"
      >
        <slot />
      </div>
      <div class="s-install-links">
        <slot name="links" />
      </div>
    </div>
  </section>
</template>
