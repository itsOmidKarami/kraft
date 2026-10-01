import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apply from "./apply/store";
import { onLiveFrame } from "./live";
import { startEvents } from "./session";
import * as ws from "../ws";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(apply, "watchApply").mockReturnValue(() => {});
});

describe("the one event socket", () => {
  it("opens with the live-frame dispatcher, so apply and intake frames share it", () => {
    const stop = vi.fn();
    const open = vi.spyOn(ws, "connectEvents").mockReturnValue(stop);
    startEvents();
    expect(open).toHaveBeenCalledWith({ onLive: onLiveFrame });
    startEvents();
    expect(stop).toHaveBeenCalledTimes(1);
  });
});
