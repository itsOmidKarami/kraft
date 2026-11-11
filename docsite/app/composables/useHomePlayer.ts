import { createPlayer, initialState, view } from '~/utils/homePlayer'
import type { Player, PlayerState, PlayerView } from '~/utils/homePlayer'

// The home page's demo (app/utils/homePlayer.ts) as Vue state. The server
// renders the idle view; the player starts in the browser, once mounted, and
// stops when the page is left, so coming back starts a fresh run.
export function useHomePlayer() {
  const state = shallowRef<PlayerState>(initialState())
  const v = computed<PlayerView>(() => view(state.value))
  let player: Player | undefined

  // The verification node grows into its canvas and shrinks back. Skipped
  // under reduced motion, where the browser has no View Transitions, and in a
  // hidden tab, which would defer the callback and with it the next step.
  const transition = (apply: () => void, morph: boolean) => {
    const canMorph = morph
      && typeof document.startViewTransition === 'function'
      && window.matchMedia('(prefers-reduced-motion: no-preference)').matches
      && !document.hidden
    if (!canMorph) return apply()
    document.startViewTransition(() => {
      apply()
      return nextTick()
    })
  }

  onMounted(() => {
    player = createPlayer({
      timers: {
        set: (fn, ms) => window.setTimeout(fn, ms),
        clear: id => window.clearTimeout(id),
      },
      onChange: (s) => {
        state.value = s
      },
      transition,
    })
    state.value = player.state()
    player.boot()
  })
  onBeforeUnmount(() => player?.dispose())

  return {
    v,
    start: () => player?.start(),
    approve: () => player?.approve(),
    reject: () => player?.reject(),
    toggle: () => player?.toggle(),
  }
}
