import { DUR, focusHolds, jump, nextFrame, startFrame } from '~/utils/customizePlayer'

// The Make it yours demo (app/utils/customizePlayer.ts) as Vue state. The server
// renders frame 0. In the browser the run starts once `fig` is in view and
// stops when it leaves view or the page is left. A hovering mouse holds it, and
// so does keyboard focus inside the figure, but not focus on its own buttons:
// a click on Play leaves focus there, and the demo must not look stuck. Under reduced motion it opens paused on the finished
// review and moves only when a button is pressed.
export function useCustomizePlayer(fig: Readonly<Ref<HTMLElement | null>>) {
  // A new object on every change, so a press on the frame already shown
  // still restarts its timer.
  const state = shallowRef({ frame: startFrame(false), playing: true })
  const inView = ref(false)
  const hovered = ref(false)
  const held = ref(false)
  let timer: number | undefined
  let observer: IntersectionObserver | undefined

  const stop = () => {
    window.clearTimeout(timer)
    timer = undefined
  }
  const schedule = () => {
    stop()
    if (!state.value.playing || !inView.value || hovered.value || held.value) return
    timer = window.setTimeout(() => {
      state.value = { ...state.value, frame: nextFrame(state.value.frame) }
    }, DUR[state.value.frame])
  }

  onMounted(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) state.value = { frame: startFrame(true), playing: false }
    watch([state, inView, hovered, held], schedule, { immediate: true })
    observer = new IntersectionObserver(es => (inView.value = es.some(e => e.isIntersecting)), { threshold: 0.15 })
    if (fig.value) observer.observe(fig.value)
    else inView.value = true
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    stop()
  })

  return {
    state,
    toggle: () => (state.value = { ...state.value, playing: !state.value.playing }),
    go: (i: number) => (state.value = { ...state.value, frame: jump(state.value.playing, i) }),
    // A mouse hovering holds the run; a touch has no leave, so it does not.
    enter: (e: PointerEvent) => { if (e.pointerType === 'mouse') hovered.value = true },
    leave: () => (hovered.value = false),
    focusin: (e: FocusEvent) => {
      const t = e.target as Element
      held.value = focusHolds(t.matches(':focus-visible'), !!t.closest('button'))
    },
    focusout: (e: FocusEvent) => {
      if (!fig.value?.contains(e.relatedTarget as Node | null)) held.value = false
    },
  }
}
