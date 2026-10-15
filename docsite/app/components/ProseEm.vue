<script lang="ts">
// Emphasis, wrapped around Nuxt UI's own. A line that is exactly `_Added in next_`
// or `_Added in 2.1_` is the note that says which release introduced a section
// (dev/check_docs_added_in.py keeps them honest, dev/stamp_docs_added_in.py turns
// `next` into the number at a release), and it draws as a chip: amber and
// "Unreleased" for `next`, the diagram chip's violet for a number. Any other
// emphasis falls through to Nuxt UI. The words stay plain text in the source, so
// raw/*.md, llms-full.txt and search keep them.
// Contrast: violet-700 on its tint is 5.3:1 and violet-300 in dark mode 6.4:1
// (see .diagram-chip); the amber pair is the header's `next` pill.
import { Text, defineComponent, h } from 'vue'
import UiProseEm from '@nuxt/ui/components/prose/Em.vue'

const NOTE = /^Added in (next|\d+\.\d+)$/
const CHIP = 'inline-block rounded px-1.5 py-px align-middle text-xs font-semibold not-italic whitespace-nowrap'
const LOOK = {
  next: 'bg-warning-100 text-warning-900 dark:bg-warning-950 dark:text-warning-200',
  release: 'bg-primary/15 text-primary-700 dark:text-primary-300',
}

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => {
      const nodes = slots.default?.() ?? []
      const text = nodes.map(n => (n.type === Text ? String(n.children) : '')).join('').trim()
      const note = nodes.every(n => n.type === Text) && NOTE.exec(text)
      if (!note) return h(UiProseEm, attrs, () => nodes)
      const next = note[1] === 'next'
      return h('em', {
        ...attrs,
        'class': `${attrs.class ?? ''} ${CHIP} ${next ? LOOK.next : LOOK.release}`,
        'data-added-in': note[1],
        'title': next ? 'Added in the next release, which has not shipped yet' : `Added in Kraft ${note[1]}`,
      }, next ? 'Unreleased' : text)
    }
  },
})
</script>
