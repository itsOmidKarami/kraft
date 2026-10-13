// The sidebar and the phone menu are scroll boxes of their own, and nothing
// moves them to the open page, so a page low in a long group opened out of
// sight. Centre the current link inside its scroll box, but only when it is out
// of view: a click on a link you can see must not jump. The page itself is
// never scrolled.
export function useRevealCurrentLink(root: Ref<HTMLElement | null | undefined>) {
  const route = useRoute()

  function reveal() {
    const link = root.value?.querySelector<HTMLElement>('[aria-current="page"]')
    if (!link) return
    for (let box = link.parentElement; box; box = box.parentElement) {
      if (!/auto|scroll/.test(getComputedStyle(box).overflowY) || box.scrollHeight <= box.clientHeight) continue
      const b = box.getBoundingClientRect()
      const l = link.getBoundingClientRect()
      if (l.top < b.top || l.bottom > b.bottom) box.scrollTop += l.top - b.top - (b.height - l.height) / 2
      return
    }
  }

  onMounted(reveal)
  // The navigation lists are keyed on the route and remount on every page, so
  // wait a tick for the new one.
  watch(() => route.path, () => nextTick(reveal))
}
