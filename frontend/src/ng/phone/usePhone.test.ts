import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePhone } from "./usePhone";

function stub(initial: boolean) {
  const listeners = new Set<() => void>();
  const mq = { matches: initial, addEventListener: (_: string, l: () => void) => listeners.add(l), removeEventListener: (_: string, l: () => void) => listeners.delete(l) };
  vi.stubGlobal("matchMedia", () => mq);
  return (matches: boolean) => {
    mq.matches = matches;
    listeners.forEach((l) => l());
  };
}
afterEach(() => vi.unstubAllGlobals());

describe("usePhone", () => {
  it("follows the 767px breakpoint in both directions", () => {
    const set = stub(false);
    const { result } = renderHook(() => usePhone());
    expect(result.current).toBe(false);
    act(() => set(true));
    expect(result.current).toBe(true);
    act(() => set(false));
    expect(result.current).toBe(false);
  });

  it("asks for the shared breakpoint", () => {
    const q = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    vi.stubGlobal("matchMedia", q);
    renderHook(() => usePhone());
    expect(q).toHaveBeenCalledWith("(max-width: 767px)");
  });
});
