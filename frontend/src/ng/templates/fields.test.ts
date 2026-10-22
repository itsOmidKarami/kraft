import { describe, expect, it } from "vitest";
import { editOps } from "./fields";

describe("the ops a Config row's edit sends", () => {
  const POLICY = { restart_from: "verification" };
  it.each([
    { id: "a plain field sets itself", field: "effort", value: "low", current: "high", want: [{ op: "set_field", path: "n", field: "effort", value: "low" }] },
    { id: "on base change sets its restart", field: "on_base_changed", value: "spec", current: null, want: [{ op: "set_field", path: "n", field: "on_base_changed.restart_from", value: "spec" }] },
    { id: "a first merge rejection is added whole, then its restart set", field: "on_merge_rejected", value: "spec", current: null, want: [{ op: "add_handler", path: "n", kind: "on_merge_rejected" }, { op: "set_field", path: "n", field: "on_merge_rejected.restart_from", value: "spec" }] },
    { id: "a merge rejection already set only moves its restart", field: "on_merge_rejected", value: "spec", current: POLICY, want: [{ op: "set_field", path: "n", field: "on_merge_rejected.restart_from", value: "spec" }] },
    { id: "clearing a merge rejection set sends the null that drops it", field: "on_merge_rejected", value: null, current: POLICY, want: [{ op: "set_field", path: "n", field: "on_merge_rejected.restart_from", value: null }] },
    { id: "clearing one there is not adds nothing", field: "on_merge_rejected", value: null, current: null, want: [{ op: "set_field", path: "n", field: "on_merge_rejected.restart_from", value: null }] },
  ])("$id", ({ field, value, current, want }) => {
    expect(editOps("n", field, value, current)).toEqual(want);
  });
});
