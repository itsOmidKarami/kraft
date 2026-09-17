import type { Page } from "@playwright/test";

// Extension point for shared Playwright fixtures. Specs import from here so
// any future fixture wiring lands in one place.
export { expect, test } from "@playwright/test";

/**
 * UI v2 · 06/07: the New work item dialog's Repo field is chips off
 * `GET /repos` (connected repos only), not a free-text path input. Every
 * spec that used to type `KRAFT_E2E_REPO` straight into the field now has
 * to connect it first -- same one-line fix, reused rather than duplicated
 * per spec. Idempotent: a repo already connected (e.g. a second call in the
 * same worker) 409s, which is fine to ignore here.
 */
export async function connectRepo(page: Page, path: string): Promise<void> {
  // `enabled: true` explicitly -- the server's own default (`enabled` unset)
  // is disabled without a `test_command`, and a disabled repo's chip is
  // un-clickable in the New work item dialog. An explicit `true` still works
  // with no `test_command` set as long as the registry's own `on.test.run`
  // binding covers it (`_refuse_enable_without_test_command`), which the e2e
  // fixture templates always provide.
  //
  // `setup_command: ""` explicitly -- the sample repo carries none of the
  // markers `probe_repo` recognizes (no lockfile, no pyproject.toml), so the
  // connect endpoint would otherwise leave it undeclared, and every dispatch
  // that needs a worktree raises "no setup_command declared" (Kraft-kji8w).
  const res = await page.request.post("/api/repos", {
    data: { path, enabled: true, setup_command: "" },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(`connectRepo(${path}) failed: ${res.status()} ${await res.text()}`);
  }
}
