/** Focus once the popover is placed: it is hidden until its first layout, and a hidden element takes no focus. */
export const focusSoon = (el: { focus: () => void } | null | undefined) => requestAnimationFrame(() => el?.focus());
