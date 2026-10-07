<script lang="ts">
// A header cell, wrapped around Nuxt UI's own: it records its text for the
// cells below it (app/utils/tableLabels.ts) and changes nothing else.
import { defineComponent, h } from 'vue'
import UiProseTh from '@nuxt/ui/components/prose/Th.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => {
      const nodes = slots.default?.() ?? []
      columnLabel(true, nodes)
      return h(UiProseTh, attrs, () => nodes)
    }
  },
})
</script>
