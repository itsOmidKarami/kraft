<script lang="ts">
// Nuxt UI's own, with role="rowgroup" (see ProseTable.vue).
import { defineComponent, h } from 'vue'
import UiProseTbody from '@nuxt/ui/components/prose/Tbody.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => h(UiProseTbody, { ...attrs, role: 'rowgroup' }, () => slots.default?.())
  },
})
</script>
