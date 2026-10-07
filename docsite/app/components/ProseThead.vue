<script lang="ts">
// Nuxt UI's own, with role="rowgroup" (see ProseTable.vue).
import { defineComponent, h } from 'vue'
import UiProseThead from '@nuxt/ui/components/prose/Thead.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => h(UiProseThead, { ...attrs, role: 'rowgroup' }, () => slots.default?.())
  },
})
</script>
