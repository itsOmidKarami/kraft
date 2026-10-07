<script lang="ts">
// A header cell, wrapped around Nuxt UI's own: it records its text for the
// cells below it (app/utils/tableLabels.ts), and has role="columnheader"
// (see ProseTable.vue).
import { defineComponent, h } from 'vue'
import UiProseTh from '@nuxt/ui/components/prose/Th.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    const cell = useTableCell()
    return () => {
      const nodes = slots.default?.() ?? []
      setHeader(cell, nodes)
      return h(UiProseTh, { ...attrs, role: 'columnheader' }, () => nodes)
    }
  },
})
</script>
