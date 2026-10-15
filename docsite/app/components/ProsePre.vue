<script lang="ts">
// A code block, wrapped around Nuxt UI's own: a block that scrolls sideways
// takes keyboard focus (tabindex=0), so a reader without a mouse can scroll
// it with the arrow keys (axe scrollable-region-focusable: 3 blocks on the
// install page at 390px, 11 on roll-back). A block that fits is not made a tab
// stop: it would be 40 stops on a long page for nothing to scroll. Whether it
// scrolls is measured in the browser, so a server-rendered block starts out
// without the attribute and gets it on mount and when its width changes.
//
// A block fenced as ```bash each lists commands to pick from, not to run in
// order (the CLI reference's examples), so the one button that copies the
// whole block put six alternatives on the clipboard as one paste. There every
// command line gets its own copy button instead, added on mount.
import { defineComponent, h, onMounted, ref } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import UiProsePre from '@nuxt/ui/components/prose/Pre.vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    const each = /(^|\s)each(\s|$)/.test(String(attrs.meta ?? ''))
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
    const COPY = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>'
    const DONE = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    const addLineButtons = () => {
      for (const line of pre()?.querySelectorAll<HTMLElement>('.line') ?? []) {
        const command = line.textContent?.trim()
        if (!command || line.querySelector('.copy-line')) continue
        const button = document.createElement('button')
        button.type = 'button'
        button.className = 'copy-line'
        button.setAttribute('aria-label', `Copy: ${command}`)
        button.innerHTML = COPY
        button.addEventListener('click', () => {
          navigator.clipboard?.writeText(command).then(() => {
            button.innerHTML = DONE
            setTimeout(() => { button.innerHTML = COPY }, 1500)
          }, () => {})
        })
        // The newline that ends a line sits inside its span: the button goes
        // before it, or it would start a line of its own.
        const walk = document.createTreeWalker(line, NodeFilter.SHOW_TEXT)
        let end: Node | null = null
        while (walk.nextNode()) if ((walk.currentNode as Text).data.endsWith('\n')) end = walk.currentNode
        if (end) (end as Text).splitText((end as Text).data.length - 1).before(button)
        else line.append(button)
      }
    }
    onMounted(() => {
      if (each) addLineButtons()
      measure()
      document.fonts?.ready.then(measure)
    })
    useResizeObserver(pre, measure)
    return () => h(UiProsePre, { ...attrs, copy: !each, ref: box }, slots)
  },
})
</script>
