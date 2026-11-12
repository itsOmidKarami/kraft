import { createPlayer, initialState, view } from '~/utils/homePlayer'
import type { Player, PlayerState, PlayerView } from '~/utils/homePlayer'

// The home page's demo (app/utils/homePlayer.ts) as Vue state. The server
// renders the paused item; the player starts in the browser, once mounted,
// and stops when the page is left, so coming back starts a fresh run. Under
// reduced motion it starts paused: nothing moves until the visitor presses
// play or a button.
export function useHomePlayer() {
  const state = shallowRef<PlayerState>(initialState())
  const v = computed<PlayerView>(() => view(state.value))
  let player: Player | undefined

  onMounted(() => {
    player = createPlayer({
      timers: {
        set: (fn, ms) => window.setTimeout(fn, ms),
        clear: id => window.clearTimeout(id),
      },
      onChange: (s) => {
        state.value = s
      },
    })
    state.value = player.state()
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) player.toggle()
    else player.boot()
  })
  onBeforeUnmount(() => player?.dispose())

  return {
    v,
    // Starting is the visitor's call, so it also ends a pause (reduced
    // motion starts paused); the steps then run up to each gate.
    start: () => {
      if (player?.state().paused) player.toggle()
      player?.start()
    },
    approve: () => player?.approve(),
    reject: () => player?.reject(),
    toggle: () => player?.toggle(),
  }
}
