import { expect, type Page } from "@playwright/test";
import { scaledTimeout } from "../e2e-timing";

// Extension point for shared Playwright fixtures. Specs import from here so
// any future fixture wiring lands in one place.
export { expect, test } from "@playwright/test";

/** The fixture repo e2e/serve.py seeds, and the name its chip shows. */
export const REPO = process.env.KRAFT_E2E_REPO!;
export const REPO_NAME = REPO.split("/").pop()!;

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
  // un-clickable in the New work item dialog. Enabling needs the repo's own
  // test command (`_refuse_enable_without_test_command`, Kraft-vd1ed); `true`
  // is enough, since the e2e seed neuters the verify builtin anyway.
  //
  // `setup_command: ""` explicitly -- the sample repo carries none of the
  // markers `probe_repo` recognizes (no lockfile, no pyproject.toml), so the
  // connect endpoint would otherwise leave it undeclared, and every dispatch
  // that needs a worktree raises "no setup_command declared" (Kraft-kji8w).
  const res = await page.request.post("/api/repos", {
    data: { path, enabled: true, setup_command: "", test_command: "true" },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(`connectRepo(${path}) failed: ${res.status()} ${await res.text()}`);
  }
}

/**
 * Files a work item through the real New work item dialog on `template` and
 * starts it, then waits for the detail page. Returns the new item's id.
 */
export async function createItem(page: Page, title: string, template: string): Promise<string> {
  await connectRepo(page, REPO);
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();
  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByLabel("repo").selectOption({ label: REPO_NAME });
  await modal.getByLabel("title").fill(title);
  await modal
    .getByRole("radiogroup", { name: "template" })
    .getByRole("radio", { name: new RegExp(`^${template}\\b`) })
    .click();
  await modal.getByRole("button", { name: /create and start/i }).click();
  await expect(page.locator(".detail h2")).toHaveText(title);
  return new URL(page.url()).pathname.split("/").pop()!;
}

/** Wait until `id`'s implementation agent is actually running, then pause it.
 *  A pause that lands between nodes stops no agent task (Kraft-e7pm), and a
 *  paused item takes a steer only for an agent task its pause stopped (Ruling
 *  183), so the Steer button would never appear. Every pause/steer spec goes
 *  through here so the desktop and phone ones cannot drift apart again. */
export async function pauseRunningAgent(page: Page, id: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(`/api/work-items/${id}`);
        const { worker_sessions = [] } = await res.json();
        return worker_sessions.some(
          (s: { node_id: string; status: string }) =>
            s.node_id === "implementation" && s.status === "running",
        );
      },
      { timeout: scaledTimeout(30_000) },
    )
    .toBe(true);
  const pause = page.getByRole("button", { name: /^Pause$/ });
  await expect(pause).toBeEnabled({ timeout: scaledTimeout(30_000) });
  await pause.click();
}
