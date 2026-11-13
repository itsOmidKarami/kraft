import { createPlayer, initialState, view } from '~/utils/homePlayer'
import type { Player, PlayerState, PlayerView } from '~/utils/homePlayer'

// The home page's demo (app/utils/homePlayer.ts) as Vue state. The server
// renders the paused item; in the browser the player starts once `card` is in
// view, so a visitor scrolling down sees the run from its start, and stops when
// the page is left. Under reduced motion it starts paused: nothing moves until
// the visitor presses play or a button, and a gate always waits for a button.
export function useHomePlayer(card: Readonly<Ref<HTMLElement | null>>) {
  const state = shallowRef<PlayerState>(initialState())
  const v = computed<PlayerView>(() => view(state.value))
  let player: Player | undefined
  let observer: IntersectionObserver | undefined

  onMounted(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    player = createPlayer({
      timers: {
        set: (fn, ms) => window.setTimeout(fn, ms),
        clear: id => window.clearTimeout(id),
      },
      onChange: (s) => {
        state.value = s
        // Played on its own pace, the run would decide the gate itself: pause
        // there instead, so it waits for Approve or Request changes.
        if (reduced && v.value.atGate && v.value.playing) queueMicrotask(() => player?.toggle())
      },
    })
    state.value = player.state()
    if (reduced) return player.toggle()
    observer = new IntersectionObserver((entries) => {
      if (!entries.some(e => e.isIntersecting)) return
      observer?.disconnect()
      player?.boot()
    }, { threshold: 0.4 })
    if (card.value) observer.observe(card.value)
    else player.boot()
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    player?.dispose()
  })

  // A call the visitor makes also ends a pause; the steps then run up to the
  // next gate, which waits for them.
  const own = (act: (p: Player) => void) => () => {
    if (!player) return
    if (player.state().paused) player.toggle()
    act(player)
  }

  return {
    v,
    paused: computed(() => state.value.paused),
    start: own(p => p.start()),
    approve: own(p => p.approve()),
    reject: own(p => p.reject()),
    // Pause stops everything, whoever was driving; play hands the demo back
    // to its own pace. The player's toggle from "the visitor is driving" means
    // play, so pausing from there takes two.
    toggle: () => {
      if (!player) return
      const s = player.state()
      if (s.hands && !s.paused) player.toggle()
      player.toggle()
    },
    // Focus on a button the demo would press by itself: hold the run, so the
    // button is not decided and taken away under the visitor.
    hold: () => {
      if (player && v.value.playing) player.toggle()
    },
  }
}
