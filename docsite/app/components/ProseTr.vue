<script lang="ts">
// Nuxt UI's own, with role="row" (see ProseTable.vue).
import { defineComponent, h } from 'vue'
import UiProseTr from '@nuxt/ui/components/prose/Tr.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    return () => h(UiProseTr, { ...attrs, role: 'row' }, () => slots.default?.())
  },
})
</script>
