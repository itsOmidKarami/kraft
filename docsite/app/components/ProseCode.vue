<script lang="ts">
// Inline code, wrapped around Nuxt UI's own: it adds the class `code-long` to
// a span of more than LONG characters and `code-wide` to one of more than
// WIDE, and changes nothing else. A narrow table of two columns keys on the
// first (assets/css/prose.css): its short spans stay whole and only a long one
// may break. A table that is a table keys on the second: a name in its first
// column stays whole unless it is that long.
import { Text, defineComponent, h } from 'vue'
import UiProseCode from '@nuxt/ui/components/prose/Code.vue'

const LONG = 14
// A third of a 738px table, less the cell's padding, at 7.7px a character.
const WIDE = 26

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => {
      const nodes = slots.default?.() ?? []
      const text = nodes.map(n => (n.type === Text ? String(n.children) : '')).join('')
      const plain = nodes.every(n => n.type === Text)
      const marks = [plain && text.length > LONG && 'code-long', plain && text.length > WIDE && 'code-wide'].filter(Boolean)
      return h(UiProseCode, marks.length ? { ...attrs, class: `${attrs.class ?? ''} ${marks.join(' ')}` } : attrs, () => nodes)
    }
  },
})
</script>
