// What the blog's animations of Kraft's item page share (components/blog/BlogAnimation.vue): each animation is one
// module beside this one, `<name>.ts`, with its timeline (where each part stands at time t, in seconds into its loop)
// and its `meta`, and one view, components/blog/animation/<Name>.vue. Pure functions, so a paused frame, the server's
// render and a test agree. The states and words are the app's own; the timings and example names are made up, and the
// wrapper says so under every animation.

export type Look = 'todo' | 'run' | 'done' | 'amber'

// An animation's loop and stage, and the moment of its still frame: shown with reduced motion, before the page's
// script runs, and as the image the feed and copies elsewhere get.
export type Meta = { loop: number, still: number, height: number }

export type Seg<T extends unknown[]> = [number, ...T]
// The last segment that has started by t; before the first, the first.
export function at<T extends unknown[]>(segs: Seg<T>[], t: number): Seg<T> {
  let cur = segs[0]!
  for (const s of segs) if (t >= s[0]) cur = s
  return cur
}

const clamp01 = (x: number) => Math.min(1, Math.max(0, x))
const easeInOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2)
// 0 to 1 over `length` seconds from `from`, eased in and out.
export const draw = (t: number, from: number, length: number) => easeInOut(clamp01((t - from) / length))

// Times and token counts as the app writes them (frontend/src/format.ts `elapsed`, `tokens`): "45s", "2m", "19.6k", "4k".
export const seconds = (s: number) => s < 60 ? `${Math.floor(s)}s` : `${Math.floor(s / 60)}m`
export const thousands = (n: number) => n < 1000 ? String(Math.round(n)) : `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`

// A chain edge's class: dashed until what it leads to has started.
export const edge = (on: boolean) => (on ? 'a-edge' : 'a-edge is-todo')
