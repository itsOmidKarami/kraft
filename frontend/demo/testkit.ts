/**
 * The demo tests' shared setup. jsdom lays nothing out, so every element's box is all zeros: `layOut`
 * gives one a box, as a browser would draw it, and can move it again (a scroll) or empty it (gone from
 * the layout). Returns the element.
 *
 *   const el = layOut(document.createElement("button"), { top: 100 });
 *   layOut(el, { top: -200 }); // scrolled out of the window
 */
export function layOut<T extends HTMLElement>(el: T, { left = 10, top = 10, width = 80, height = 24 } = {}): T {
  el.getBoundingClientRect = () => ({ x: left, y: top, left, top, width, height, right: left + width, bottom: top + height, toJSON: () => ({}) });
  return el;
}
