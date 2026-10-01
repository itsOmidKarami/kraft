import { expect, type Page } from "@playwright/test";
import { scaledTimeout } from "../e2e-timing";

// Extension point for shared Playwright fixtures. Specs import from here so
// any future fixture wiring lands in one place.
export { expect, test } from "@playwright/test";

/** The fixture repo e2e/serve.py seeds, and the name its chip shows. */
export const REPO = process.env.KRAFT_E2E_REPO!;
export const REPO_NAME = REPO.split("/").pop()!;

/**
 * Connects `path` through the API. The composer's repo menu lists connected,
 * enabled repos only, so every spec that files an item connects the fixture
 * repo first. Idempotent: a repo already connected answers 409, ignored here.
 */
export async function connectRepo(page: Page, path: string): Promise<void> {
  // `enabled: true` explicitly -- the server's own default (`enabled` unset)
  // is disabled without a `test_command`, and a disabled repo is not in the
  // composer's repo menu. Enabling needs the repo's own
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

/** Opens the board's composer with `title` on the fixture repo and `chain`.
 *  The repo is picked, not assumed: with more than one repo connected (the
 *  shared server's other specs connect some) the composer starts with none. */
export async function openComposer(page: Page, title: string, chain: string) {
  await connectRepo(page, REPO);
  await page.goto("/");
  await page.getByRole("button", { name: "+ New work item" }).click();
  const composer = page.getByRole("region", { name: "New work item" });
  await composer.getByRole("textbox", { name: "Title" }).fill(title);
  // The first chip is the repo, named for the one picked or "repo".
  await composer.getByRole("button").first().click();
  await page.getByRole("menu", { name: "Repo" }).getByRole("menuitemradio", { name: new RegExp(`^${REPO_NAME}\\b`) }).click();
  // The chain chip reads "<chain> <n> nodes".
  await composer.getByRole("button", { name: /\d+ nodes$/ }).click();
  await page.getByRole("menuitemradio", { name: new RegExp(`^${chain}\\b`) }).click();
  return composer;
}

/** Files a work item from the composer and starts it. The board answers with
 *  the item in the peek (`?sel=<id>`), or on its page when Appearance says
 *  open items in Full page. Returns the item's id. */
export async function createItem(page: Page, title: string, chain: string): Promise<string> {
  const composer = await openComposer(page, title, chain);
  await composer.getByRole("button", { name: "More ways to create" }).click();
  await page.getByRole("menuitem", { name: "Create and start" }).click();
  return createdId(page);
}

/** The id of the item the composer just created, from where it landed. */
export async function createdId(page: Page): Promise<string> {
  await expect(page).toHaveURL(/[?&]sel=|\/work-items\/[0-9a-f]{32}/);
  const url = new URL(page.url());
  return url.searchParams.get("sel") ?? url.pathname.split("/").pop()!;
}

/** The item page's status word in the header: RUNNING, PAUSED, NEEDS YOU, DONE… */
export const status = (page: Page, word: string) => page.getByRole("banner").getByText(word, { exact: true });

/** A config area's draft through Review & publish, until the header says it published. */
export async function publish(page: Page): Promise<void> {
  await page.getByRole("banner").getByRole("button", { name: "Review & publish" }).click();
  await page.getByRole("complementary", { name: /^Draft/ }).getByRole("button", { name: "Publish" }).click();
  await expect(page.getByRole("banner").getByText("published", { exact: true })).toBeVisible({ timeout: scaledTimeout(15_000) });
}

/** How many `type` events the item has, for waits the page cannot show apart
 *  (a rejected gate re-runs its node in well under a second, and comes back
 *  to the same "waiting for you" it showed before). */
export async function eventCount(page: Page, id: string, type: string): Promise<number> {
  const res = await page.request.get(`/api/work-items/${id}/events`);
  const body = await res.json();
  return (body.events ?? body).filter((e: { type: string }) => e.type === type).length;
}

/** Waits until `id`'s implementation agent is actually running. A pause that
 *  lands before it stops no agent task, and a steer reaches only an agent
 *  task a pause stopped (Ruling 183), so the server would refuse the steer. */
export async function agentRunning(page: Page, id: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const { worker_sessions = [] } = await (await page.request.get(`/api/work-items/${id}`)).json();
        return worker_sessions.some((s: { node_id: string; status: string }) => s.node_id === "implementation" && s.status === "running");
      },
      { timeout: scaledTimeout(30_000) },
    )
    .toBe(true);
}
