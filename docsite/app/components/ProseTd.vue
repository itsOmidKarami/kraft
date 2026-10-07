<script lang="ts">
// A data cell, wrapped around Nuxt UI's own: it adds `data-label`, its
// column's header text, which a phone shows in front of the cell when the
// table is stacked (assets/css/prose.css). A header that is empty gives none.
// It has role="cell" (see ProseTable.vue).
import { defineComponent, h } from 'vue'
import UiProseTd from '@nuxt/ui/components/prose/Td.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    const cell = useTableCell()
    return () => {
      const nodes = slots.default?.() ?? []
      const label = labelFor(cell)
      return h(UiProseTd, { ...attrs, role: 'cell', ...(label && { 'data-label': label }) }, () => nodes)
    }
  },
})
</script>
