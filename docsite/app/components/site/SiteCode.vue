<!-- A file shown in a panel: its path as the header, then its lines. Lines
     `hl` (first and last, counted from 0) are highlighted; `mark` puts the gate
     diamond in the gutter beside that line (a phone drops an empty gutter). A
     line never scrolls sideways: a long one wraps in its own row, the wrapped part two characters past the line's
     start. Each line is `key: value`; the key is drawn quieter, and a value of
     `gate` in amber. `short` replaces `note` on a phone. -->
<script setup lang="ts">
const props = defineProps<{ path: string, note: string, short?: string, lines: string[], hl?: [number, number], mark?: number }>()

const rows = computed(() => props.lines.map((text, i) => {
  const indent = text.length - text.trimStart().length
  const colon = text.indexOf(':')
  const key = colon < 0 ? text : text.slice(0, colon + 1)
  const value = colon < 0 ? '' : text.slice(colon + 1)
  return {
    key,
    value,
    indent,
    hl: !!props.hl && i >= props.hl[0] && i <= props.hl[1],
    mark: props.mark === i,
    gate: value.trim() === 'gate',
    rest: text.trim() === '…',
  }
}))
</script>

<template>
  <div class="s-code">
    <div class="s-code-head">
      {{ path }} · <template v-if="short">
        <span class="s-wide">{{ note }}</span><span class="s-narrow">{{ short }}</span>
      </template><template v-else>
        {{ note }}
      </template>
    </div>
    <pre :class="{ 's-code-nomark': mark === undefined }"><code><span
      v-for="(r, i) in rows"
      :key="i"
      class="s-code-line"
      :class="{ 's-code-hl': r.hl }"
    ><span
      class="s-code-mark"
      aria-hidden="true"
    ><i v-if="r.mark" /></span><span
      class="s-code-text"
      :style="{ '--s-indent': `${r.indent + 2}ch` }"
    ><span :class="r.rest ? 's-code-rest' : 's-code-key'">{{ r.key }}</span><span :class="{ 's-code-gate': r.gate }">{{ r.value }}</span></span></span></code></pre>
  </div>
</template>
