import { useEffect, useRef, type MouseEvent } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Dialog keyboard behaviour: Escape closes, Tab stays inside (Kraft-2ih).
 * Escape does not close while `dirty` (typed, unsaved input) or mid-composition.
 *
 * Returns a ref to put on the dialog container. Without the trap, Tab walks out
 * of the modal into the page behind it, which for a keyboard or screen-reader
 * user means the dialog is not really modal at all.
 */
export function useModal<T extends HTMLElement>(onClose: () => void, returnTo?: () => HTMLElement | null | undefined, dirty = false) {
  const ref = useRef<T>(null);
  // Read through refs, so a caller's inline `onClose` (a new closure each render)
  // does not re-run the effect: its cleanup and setup moved focus, and a page
  // that re-renders every second (a running item's clock) snapped focus back
  // to the dialog's first control each time (review M2).
  const close = useRef(onClose);
  close.current = onClose;
  const back = useRef(returnTo);
  back.current = returnTo;
  const held = useRef(dirty);
  held.current = dirty;

  useEffect(() => {
    const node = ref.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    // A dialog names its first field with data-autofocus (W6.9: the intake
    // title, not the ✕ that happens to come first in the DOM).
    // A dialog with nothing focusable takes focus itself, so Escape reaches it
    // and not whatever behind it had focus (a container needs tabIndex -1).
    (node?.querySelector<HTMLElement>("[data-autofocus]") ?? node?.querySelector<HTMLElement>(FOCUSABLE) ?? node)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        // The Escape that ends an input method's composition belongs to the
        // field; and a form holding typed, unsaved text (`dirty`) keeps it, as
        // the backdrop does: Cancel is the way out (R12b-05, R11b-02).
        if (e.isComposing || held.current) return;
        close.current();
        return;
      }
      if (e.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) {
        e.preventDefault(); // nothing to cycle: focus stays on the dialog
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !node.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // What opened it is gone, or nothing had focus (a dialog opened from an
      // address, such as Start's `?start=1`): the dialog's own fallback (R10b-04).
      const to = previouslyFocused && previouslyFocused !== document.body && previouslyFocused.isConnected ? previouslyFocused : back.current?.() ?? previouslyFocused;
      to?.focus?.();
    };
    // Once per opening: the handlers are read through refs.
  }, []);

  return ref;
}

/**
 * Click-outside-to-close, spread onto the `.dialog-backdrop` element.
 *
 * `mousedown`, not `click`: a click fires on the nearest common ancestor of
 * press and release, so selecting text inside the dialog and releasing over
 * the backdrop would otherwise close it mid-drag. The target test keeps a
 * click that merely bubbled up from the dialog itself from counting.
 *
 * `dirty` (Kraft-avvz): a form holding typed, unsaved input (the escalate
 * message, a review's note) must not vanish on a stray outside press — skip the
 * close and leave the explicit Cancel/X as the only way out. Read-only
 * modals never pass it, so their click-outside-to-close is unchanged.
 */
export const backdropProps = (onClose: () => void, dirty = false) => ({
  onMouseDown: (e: MouseEvent) => {
    if (e.target !== e.currentTarget) return;
    // A press on the backdrop's own scrollbar (it is `overflow-y: auto`, so a
    // dialog taller than the viewport can scroll) targets the backdrop like
    // any other outside press. `clientWidth` excludes that scrollbar, so an
    // offset past it is the drag that must not close anything.
    if (e.button !== 0 || e.nativeEvent.offsetX > e.currentTarget.clientWidth) return;
    if (dirty) return;
    onClose();
  },
});
