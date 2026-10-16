import { afterEach, describe, expect, it, vi } from "vitest";
import { findByRole, ring } from "./ring";

/** `html` in the page; every element marked `data-box` laid out on screen (jsdom lays nothing out). */
const page = (html: string) => {
  document.body.innerHTML = html;
  for (const el of document.querySelectorAll<HTMLElement>("[data-box]"))
    el.getBoundingClientRect = () => ({ x: 10, y: 10, left: 10, top: 10, width: 80, height: 24, right: 90, bottom: 34, toJSON: () => ({}) });
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
    ["a role the element says it has", `<div data-box id="hit" role="button">Review and decide</div>`, "button", "Review and decide", "hit"],
    ["not a name that only contains it", `<button data-box>Pause now</button>`, "button", "Pause", null],
    ["not another role with the name", `<a data-box href="/x">Pause</a>`, "button", "Pause", null],
    ["past one with no box (laid out on another layout) to one on screen", `<button>Pause</button><button data-box id="hit">Pause</button>`, "button", "Pause", "hit"],
    ["not one in the list's own controls", `<div id="skip"><button data-box>Pause</button></div>`, "button", "Pause", null],
  ])("finds %s", (_, html, role, name, hit) => {
    page(html);
    expect(findByRole(document, role, name, document.getElementById("skip"))?.id ?? null).toBe(hit);
  });
});

describe("ring", () => {
  it("follows its target as the page moves, and lets go once the target leaves the screen", () => {
    vi.useFakeTimers();
    const target = document.body.appendChild(document.createElement("button"));
    const at = (top: number, height = 24) => (target.getBoundingClientRect = () => ({ x: 10, y: top, left: 10, top, width: 80, height, right: 90, bottom: top + height, toJSON: () => ({}) }));
    at(100);
    ring(target);
    const el = () => document.querySelector<HTMLElement>(".kd-ring");
    const first = el()?.style.top;
    at(60); // scrolled
    vi.advanceTimersByTime(20);
    const moved = el()?.style.top;
    at(60, 0); // gone from the layout
    vi.advanceTimersByTime(20);
    expect([first, moved, el()]).toEqual(["96px", "56px", null]);
  });
});
