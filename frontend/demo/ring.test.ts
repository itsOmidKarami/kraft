import { afterEach, describe, expect, it, vi } from "vitest";
import { findByRole, ring, ringWhenFound, STALE_MS, WAIT_MS } from "./ring";
import { layOut } from "./testkit";

/** `html` in the page; every element marked `data-box` laid out on screen. */
const page = (html: string) => {
  document.body.innerHTML = html;
  for (const el of document.querySelectorAll<HTMLElement>("[data-box]")) layOut(el);
};

afterEach(() => {
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe("findByRole", () => {
  it.each<[string, string, string, string | RegExp, string | null]>([
    ["a button by its aria-label", `<button data-box id="hit" aria-label="Pause"><svg></svg></button>`, "button", "Pause", "hit"],
    ["a button by its text, an icon left out", `<button data-box id="hit"><span aria-hidden="true">⏸</span> Pause </button>`, "button", "Pause", "hit"],
    ["a name that only has to match", `<button data-box id="hit" aria-label="verification, node, running, attempt 2"></button>`, "button", /, node, running\b/, "hit"],
    ["a textbox by its label", `<textarea data-box id="hit" aria-label="Steer"></textarea>`, "textbox", "Steer", "hit"],
    ["a role the element says it has", `<div data-box id="hit" role="button">Review</div>`, "button", "Review", "hit"],
    ["not a name that only contains it", `<button data-box>Pause now</button>`, "button", "Pause", null],
    ["not another role with the name", `<a data-box href="/x">Pause</a>`, "button", "Pause", null],
    ["past one with no box (laid out on another layout) to one on screen", `<button>Pause</button><button data-box id="hit">Pause</button>`, "button", "Pause", "hit"],
    ["not one in the list's own controls", `<div id="skip"><button data-box>Pause</button></div>`, "button", "Pause", null],
  ])("finds %s", (_, html, role, name, hit) => {
    page(html);
    expect(findByRole(document, role, name, document.getElementById("skip"))?.id ?? null).toBe(hit);
  });
});

const ringEl = () => document.querySelector<HTMLElement>(".kd-ring");
/** A button named `name` in the page, laid out at `top`. */
const button = (top: number, name = "Pause") => layOut(document.body.appendChild(Object.assign(document.createElement("button"), { textContent: name })), { top });

describe("ring", () => {
  it("follows its target as the page moves, and lets go once the target leaves the layout", () => {
    vi.useFakeTimers();
    const target = button(100);
    ring(target);
    const first = ringEl()?.style.top;
    layOut(target, { top: 60 }); // scrolled
    vi.advanceTimersByTime(20);
    const moved = ringEl()?.style.top;
    layOut(target, { top: 60, height: 0 }); // gone from the layout
    vi.advanceTimersByTime(20);
    expect([first, moved, ringEl()]).toEqual(["96px", "56px", null]);
  });

  it("goes when the person presses anywhere", () => {
    vi.useFakeTimers();
    ring(button(100));
    const before = !!ringEl();
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect([before, ringEl()]).toEqual([true, null]);
  });

  it("hides, not goes, while its target is scrolled out of the window, and shows again when it is back", () => {
    vi.useFakeTimers();
    const el = button(100);
    ring(el);
    layOut(el, { top: -200 });
    vi.advanceTimersByTime(20);
    const away = ringEl()?.style.visibility;
    layOut(el, { top: 100 });
    vi.advanceTimersByTime(20);
    expect([away, ringEl()?.style.visibility]).toEqual(["hidden", ""]);
  });
});

describe("ringWhenFound", () => {
  it("scrolls a target below the window into view before ringing it", () => {
    vi.useFakeTimers();
    const el = button(5000);
    const scroll = vi.fn();
    el.scrollIntoView = scroll;
    ringWhenFound(document, "button", "Pause");
    vi.advanceTimersByTime(150);
    expect([scroll.mock.calls, !!ringEl()]).toEqual([[[{ block: "nearest", inline: "nearest" }]], true]);
  });

  it("passes over the page it left, still drawn for a moment, for the new page's control", () => {
    vi.useFakeTimers();
    const old = button(40, "Review");
    ringWhenFound(document, "button", "Review", null, new Set([old]));
    vi.advanceTimersByTime(150);
    const early = ringEl();
    old.remove();
    button(300, "Review");
    vi.advanceTimersByTime(100);
    expect([early, ringEl()?.style.top]).toEqual([null, "296px"]);
  });

  it("takes a control the old page had once STALE_MS pass: React kept it for the new page", () => {
    vi.useFakeTimers();
    const kept = button(40, "Review");
    ringWhenFound(document, "button", "Review", null, new Set([kept]));
    vi.advanceTimersByTime(STALE_MS + 150);
    expect(ringEl()?.style.top).toBe("36px");
  });

  it("looks again when the target it rang goes while it still waits (its page drawn again)", () => {
    vi.useFakeTimers();
    const first = button(40, "Review");
    ringWhenFound(document, "button", "Review");
    vi.advanceTimersByTime(150);
    first.remove();
    vi.advanceTimersByTime(20);
    const between = ringEl();
    button(300, "Review");
    vi.advanceTimersByTime(150);
    const again = ringEl()?.style.top;
    vi.advanceTimersByTime(WAIT_MS);
    expect([between, again]).toEqual([null, "296px"]);
  });
});
