import { afterEach, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import { runAction } from "../../src/actions";
import type { Store } from "../../src/core/store";
import { item } from "./fixtures/item";

afterEach(() => vi.restoreAllMocks());

it.each([
  ["re-read", async () => true, []],
  // A second Skip would skip the next node: the one that went through is not reported as failed.
  ["re-read failed", async () => { throw new Error("socket hang up"); }, []],
])("a Skip that went through, then its %s: reports no failure", async (_, refresh, errors) => {
  const api = { skip: vi.fn(async () => {}) };
  const store = { refresh: vi.fn(refresh) } as unknown as Store;
  vi.spyOn(vscode.window, "showWarningMessage").mockResolvedValue("Skip" as never);
  const error = vi.spyOn(vscode.window, "showErrorMessage");
  await runAction(api as never, store, item({ id: "S-1" }), "skip");
  expect([api.skip.mock.calls, vi.mocked(store.refresh).mock.calls, error.mock.calls]).toEqual([[["S-1"]], [["S-1"]], errors]);
});
