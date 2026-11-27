// A button that shows a list of links: the Product menu and the phone menu.
// Escape closes it and puts focus back on the button. A click outside closes
// it too, and focus goes back to the button unless the click put it somewhere
// else. Tabbing out of it, or going to another page, closes it, and so does
// the window crossing `hiddenAt`, the media query under which the header no
// longer shows it.
export function useDisclosure(root: Readonly<Ref<HTMLElement | null>>, button: Readonly<Ref<HTMLElement | null>>, hiddenAt: string) {
  const open = ref(false)
  const route = useRoute()

  const close = (refocus: boolean) => {
    if (!open.value) return
    open.value = false
    if (refocus) button.value?.focus()
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') close(true)
  }
  const onPointer = (e: PointerEvent) => {
    if (root.value?.contains(e.target as Node)) return
    close(false)
    setTimeout(() => {
      const at = document.activeElement
      if (!at || at === document.body || root.value?.contains(at)) button.value?.focus()
    })
  }
  const onFocusOut = (e: FocusEvent) => {
    const to = e.relatedTarget as Node | null
    if (to && !root.value?.contains(to)) close(false)
  }

  watch(open, (now) => {
    const method = now ? 'addEventListener' : 'removeEventListener'
    document[method]('keydown', onKey)
    document[method]('pointerdown', onPointer)
  })
  watch(() => route.fullPath, () => close(false))
  const onMedia = (e: MediaQueryListEvent) => {
    if (e.matches) close(false)
  }
  let media: MediaQueryList | undefined
  onMounted(() => {
    media = window.matchMedia(hiddenAt)
    media.addEventListener('change', onMedia)
  })
  onBeforeUnmount(() => {
    media?.removeEventListener('change', onMedia)
    document.removeEventListener('keydown', onKey)
    document.removeEventListener('pointerdown', onPointer)
  })

  return { open, onFocusOut }
}
