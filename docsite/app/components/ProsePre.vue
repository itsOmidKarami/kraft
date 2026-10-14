<script lang="ts">
// A code block, wrapped around Nuxt UI's own: a block that scrolls sideways
// takes keyboard focus (tabindex=0), so a reader without a mouse can scroll
// it with the arrow keys (axe scrollable-region-focusable: 3 blocks on the
// install page at 390px, 11 on roll-back). A block that fits is not made a tab
// stop: it would be 40 stops on a long page for nothing to scroll. Whether it
// scrolls is measured in the browser, so a server-rendered block starts out
// without the attribute and gets it on mount and when its width changes.
import { defineComponent, h, onMounted, ref } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import UiProsePre from '@nuxt/ui/components/prose/Pre.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    const box = ref<{ $el: HTMLElement } | null>(null)
    const pre = () => box.value?.$el?.querySelector('pre')
    // Set on the element, not passed as a prop: Nuxt UI's block puts its
    // attrs on the wrapper as well as on the <pre>.
    const measure = () => {
      const el = pre()
      if (!el) return
      if (el.scrollWidth > el.clientWidth) el.setAttribute('tabindex', '0')
      else el.removeAttribute('tabindex')
    }
    onMounted(() => {
      measure()
      document.fonts?.ready.then(measure)
    })
    useResizeObserver(pre, measure)
    return () => h(UiProsePre, { ...attrs, ref: box }, slots)
  },
})
</script>
