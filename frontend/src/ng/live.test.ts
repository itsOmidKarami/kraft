import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apply from "./apply/store";
import { onLiveFrame, subscribeLive } from "./live";

beforeEach(() => vi.restoreAllMocks());

describe("live frames", () => {
  it("hands every frame to the apply chip, and a typed frame's payload to its subscribers only", () => {
    const chip = vi.spyOn(apply, "onApplyFrame").mockImplementation(() => {});
    const checks = vi.fn();
    const other = vi.fn();
    const stop = subscribeLive("intake_checked", checks);
    subscribeLive("something_else", other);
    onLiveFrame({ type: "intake_checked", payload: { id: 7 } });
    expect(checks).toHaveBeenCalledWith({ id: 7 });
    expect(other).not.toHaveBeenCalled();
    expect(chip).toHaveBeenCalledWith({ type: "intake_checked", payload: { id: 7 } });
    stop();
    onLiveFrame({ type: "intake_checked", payload: { id: 8 } });
    expect(checks).toHaveBeenCalledTimes(1);
  });
});
