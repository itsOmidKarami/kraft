import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useExpand } from "./useExpand";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it("still ends the glide when reduced motion is switched on mid-move", () => {
  const { result, rerender } = renderHook(({ open, reduced }) => useExpand(open, reduced), { initialProps: { open: false, reduced: false } });
  rerender({ open: true, reduced: false });
  expect(result.current.glide).toBe(true);
  rerender({ open: true, reduced: true });
  act(() => void vi.advanceTimersByTime(1000));
  expect(result.current.glide).toBe(false);
});
