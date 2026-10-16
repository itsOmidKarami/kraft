import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMedia } from "./useMedia";

afterEach(() => vi.unstubAllGlobals());

describe("useMedia", () => {
  it("reads the query and follows it when the window crosses it", () => {
    let on = false;
    const listeners = new Set<() => void>();
    vi.stubGlobal("matchMedia", (q: string) => ({ get matches() { return on; }, media: q, addEventListener: (_: string, f: () => void) => listeners.add(f), removeEventListener: (_: string, f: () => void) => listeners.delete(f) }));
    const { result, unmount } = renderHook(() => useMedia("(max-width: 1023px)"));
    expect(result.current).toBe(false);
    act(() => { on = true; listeners.forEach((f) => f()); });
    expect(result.current).toBe(true);
    unmount();
    expect(listeners.size).toBe(0);
  });
});
