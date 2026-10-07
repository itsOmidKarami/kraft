<script lang="ts">
// A data cell, wrapped around Nuxt UI's own: it adds `data-label`, its
// column's header text, which a phone shows in front of the cell when the
// table is stacked (assets/css/prose.css). A header that is empty gives none.
import { defineComponent, h } from 'vue'
import UiProseTd from '@nuxt/ui/components/prose/Td.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => {
      const nodes = slots.default?.() ?? []
      const label = columnLabel(false, nodes)
      return h(UiProseTd, label ? { ...attrs, 'data-label': label } : attrs, () => nodes)
    }
  },
})
</script>
