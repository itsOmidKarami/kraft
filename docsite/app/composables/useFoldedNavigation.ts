// What the sidebar and the phone menu need beside Nuxt UI's list: a key that
// gives it a fresh start when the page you went to is not in it, and a scroll
// to the current link. Both are scroll boxes of their own, and nothing moves
// them to the open page, so a page low in a long group opened out of sight.
export function useFoldedNavigation(root: Ref<HTMLElement | null | undefined>) {
  const route = useRoute()
  const router = useRouter()

  // Nuxt UI reads default-open once, when the list mounts. A page whose link is
  // not in the list (a search hit, a link in a page, a folded group) needs a
  // new list to open its own path. One whose link is there keeps the list, so a
  // click in the sidebar keeps its focus, the groups the reader opened and the
  // scroll. Closed groups are not in the DOM (Nuxt UI unmounts them).
  const key = ref(0)
  watch(() => route.path, (path) => {
    const href = router.resolve(path).href
    const shown = [...root.value?.querySelectorAll('a') ?? []].some(a => a.getAttribute('href') === href)
    if (!shown) key.value++
  })

  // Centre the current link in its scroll box, but only when it is out of view
  // (of the box and of the window: a banner above the aside pushes its bottom
  // edge below the fold): a click on a link you can see must not jump.
  function reveal() {
    const link = root.value?.querySelector<HTMLElement>('[aria-current="page"]')
    if (!link) return
    for (let box = link.parentElement; box; box = box.parentElement) {
      if (!/auto|scroll/.test(getComputedStyle(box).overflowY) || box.scrollHeight <= box.clientHeight) continue
      const b = box.getBoundingClientRect()
      const top = Math.max(b.top, 0)
      const bottom = Math.min(b.bottom, window.innerHeight)
      const l = link.getBoundingClientRect()
      if (l.top < top || l.bottom > bottom) box.scrollTop += l.top - top - (bottom - top - l.height) / 2
      return
    }
  }

  onMounted(reveal)
  // After the list has been rebuilt, or a group opened, for the new page.
  watch(() => route.path, () => nextTick(reveal))

  return key
}
