import * as assert from "node:assert";
import * as vscode from "vscode";
import type { KraftApi } from "../../src/extension";

export async function kraft(): Promise<KraftApi> {
  const ext = vscode.extensions.getExtension<KraftApi>("kraft-sdlc.kraft")!;
  return ext.activate();
}

export async function until<T>(what: string, probe: () => T | undefined | Promise<T | undefined>, ms = 120_000): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const v = await probe();
    if (v) return v;
    if (Date.now() > end) assert.fail(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 500));
  }
}
