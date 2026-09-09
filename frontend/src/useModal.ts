import { useEffect, useRef, type MouseEvent } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Dialog keyboard behaviour: Escape closes, Tab stays inside (Kraft-2ih).
 *
 * Returns a ref to put on the dialog container. Without the trap, Tab walks out
 * of the modal into the page behind it, which for a keyboard or screen-reader
 * user means the dialog is not really modal at all.
 */
export function useModal<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T>(null);

  useEffect(() => {
    const node = ref.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    node?.querySelector<HTMLElement>(FOCUSABLE)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
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
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  return ref;
}

/**
 * Click-outside-to-close, spread onto the `.dialog-backdrop` element.
 *
 * `mousedown`, not `click`: a click fires on the nearest common ancestor of
 * press and release, so selecting text inside the dialog and releasing over
 * the backdrop would otherwise close it mid-drag. The target test keeps a
 * click that merely bubbled up from the dialog itself from counting.
 */
export const backdropProps = (onClose: () => void) => ({
  onMouseDown: (e: MouseEvent) => {
    if (e.target !== e.currentTarget) return;
    // A press on the backdrop's own scrollbar (it is `overflow-y: auto`, so a
    // dialog taller than the viewport can scroll) targets the backdrop like
    // any other outside press. `clientWidth` excludes that scrollbar, so an
    // offset past it is the drag that must not close anything.
    if (e.button !== 0 || e.nativeEvent.offsetX > e.currentTarget.clientWidth) return;
    onClose();
  },
});
