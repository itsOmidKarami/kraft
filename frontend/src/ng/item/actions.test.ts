import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import { OFFLINE_REFUSAL } from "../offline";
import { act } from "./actions";

afterEach(() => {
  useStore.setState({ connection: "connecting", loadError: null });
  vi.restoreAllMocks();
});

describe("act while offline", () => {
  it.each([
    ["the live connection dropped", { connection: "reconnecting" as const, loadError: null }],
    ["the list read failed", { connection: "open" as const, loadError: "down" }],
  ])("refuses a write without sending it when %s", async (_, state) => {
    useStore.setState(state);
    const f = vi.spyOn(globalThis, "fetch");
    expect(await act.retry("w1")).toEqual({ ok: false, error: OFFLINE_REFUSAL });
    expect(f).not.toHaveBeenCalled();
  });
});
