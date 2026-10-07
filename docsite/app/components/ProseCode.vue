<script lang="ts">
// Inline code, wrapped around Nuxt UI's own: it adds the class `code-long` to
// a span of more than LONG characters, and changes nothing else. A phone table
// of two columns keys on it (app.config.ts, prose.table): its short spans stay
// whole and only a long one may break.
import { Text, defineComponent, h } from 'vue'
import UiProseCode from '@nuxt/ui/components/prose/Code.vue'

const LONG = 14

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => {
      const nodes = slots.default?.() ?? []
      const text = nodes.map(n => (n.type === Text ? String(n.children) : '')).join('')
      const long = nodes.every(n => n.type === Text) && text.length > LONG
      return h(UiProseCode, long ? { ...attrs, class: `${attrs.class ?? ''} code-long` } : attrs, () => nodes)
    }
  },
})
</script>
