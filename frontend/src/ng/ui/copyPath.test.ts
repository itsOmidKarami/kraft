// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyPath } from "./copyPath";

afterEach(() => vi.unstubAllGlobals());

/** The messages `copyPath` toasts while `fn` runs, once its write has settled. */
async function toasted(clipboard: unknown) {
  vi.stubGlobal("navigator", { clipboard });
  const seen: string[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent<{ message: string }>).detail.message);
  window.addEventListener("kraft:toast", on);
  copyPath("/runs/w1/p.md");
  await new Promise((r) => setTimeout(r, 0));
  window.removeEventListener("kraft:toast", on);
  return seen;
}

describe("copyPath (R14b-01)", () => {
  it.each([
    ["written", { writeText: vi.fn(async () => {}) }, ["Copied path"]],
    ["no clipboard, as on plain http from another machine", undefined, ["Couldn't copy. The path is /runs/w1/p.md"]],
    ["refused by the browser", { writeText: vi.fn(async () => { throw new Error("denied"); }) }, ["Couldn't copy. The path is /runs/w1/p.md"]],
  ])("%s", async (_n, clipboard, messages) => expect(await toasted(clipboard)).toEqual(messages));
});
