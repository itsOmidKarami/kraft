import { describe, expect, it } from "vitest";
import type { DisplayStatus, StopKind } from "../../types";
import { archivable, headerState } from "./status";

const at = (display_status: DisplayStatus, kind?: StopKind) =>
  headerState({ display_status, stop: kind ? { kind, node: "n", resume_at: null, reason: null } : null });
const FULL = ["escalate", "complete", "archive", "cancel"];

describe("headerState: every display status, from the server's fields only", () => {
  it.each([
    ["running", undefined, "RUNNING", "neutral", "pause", FULL],
    ["waiting", "rate_limit", "WAITING", "info", "pause", FULL],
    ["needs_you", "gate", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "question", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "conflict", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "mr_closed", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "cap", "NEEDS YOU", "warn", "raise", FULL],
    ["needs_you", "budget", "NEEDS YOU", "warn", "raise", FULL],
    ["escalated", undefined, "ESCALATED", "warn", "pause", FULL],
    ["failed", "failed", "FAILED", "bad", "retry", FULL],
    ["paused", undefined, "PAUSED", "muted", "resume", FULL],
    ["done", undefined, "DONE", "ok", "archive", []],
    ["cancelled", undefined, "CANCELLED", "muted", "archive", ["archive"]],
    ["archived", undefined, "ARCHIVED", "muted", "restore", []],
  ] as const)("%s (%s) → %s, %s, %s", (status, kind, badge, tone, main, panel) => {
    expect(at(status, kind)).toEqual({ badge, tone, main, panel });
  });

  it("Archive in the panel is live only for done and cancelled", () => {
    expect((["done", "cancelled"] as const).every(archivable)).toBe(true);
    expect((["running", "paused", "failed", "needs_you", "archived"] as const).some(archivable)).toBe(false);
  });
});
