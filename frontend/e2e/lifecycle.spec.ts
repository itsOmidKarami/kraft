import { connectRepo, expect, test } from "./fixtures";

// Manual regression round, part 2: the human-in-the-loop controls on the detail
// screen — gates, pause/steer/resume — driven from the real UI.
const REPO = process.env.KRAFT_E2E_REPO!;
const REPO_NAME = REPO.split("/").pop()!;

async function createItem(page: any, title: string, template: string) {
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

test("gate: approve advances the chain from the UI", async ({ page }) => {
  await createItem(page, "ui gate approve", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Approve" }).first().click();
  // the next gate is the plan gate
  await expect(page.getByText(/approve the plan to continue/i)).toBeVisible({ timeout: 30_000 });
});

test("gate: reject offers a way forward", async ({ page }) => {
  await createItem(page, "ui gate reject", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /^Reject$/ }).first().click();
  await page.getByLabel("composer message").fill("the spec misses the error path");
  await page.getByRole("button", { name: /Reject and re-plan/ }).click();
  // "Reject and re-plan" claims the producer node re-runs with the note. Either
  // a new spec session starts, or the gate comes back — anything else strands
  // the work item with no control at all.
  // the spec node re-runs with the note and asks for its gate again — the item
  // must never be left with no control at all
  await expect(page.locator(".attention-title")).toContainText(/approve the spec/i, {
    timeout: 60_000,
  });
  await expect(page.getByRole("button", { name: "Approve" }).first()).toBeEnabled();
  await page.getByRole("button", { name: "Approve" }).first().click();
  await expect(page.locator(".attention-title")).toContainText(/approve the plan/i, {
    timeout: 60_000,
  });
});

test("pause, steer and resume from the detail screen", async ({ page }) => {
  // The fake agent finishes in milliseconds, so there is nothing to pause
  // unless it is slowed down first. KRAFT_SLOW in the title does that
  // (fixtures/fake-claude.sh) without swapping the hook's registry entry to
  // `kind: subprocess`: that swap used to work here, but it also makes the
  // node stop looking agent-kind for as long as it's in effect, which now
  // flips `item.steerable` false and hides the steer box this test needs
  // visible (PausedCard, Kraft-bz9b's last unwired caller).
  const id = await createItem(page, "ui pause steer KRAFT_SLOW", "quick-task");
  // Pause while the slowed implementation agent is actually running. A pause
  // that lands between nodes finds no session to stop and, today, lets the walk
  // carry on beside the resumed one (Kraft-e7pm). That race is tracked there;
  // this test is about pausing a running agent.
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
      { timeout: 30_000 },
    )
    .toBe(true);
  const pause = page.getByRole("button", { name: /^Pause$/ });
  await expect(pause).toBeEnabled({ timeout: 30_000 });
  await pause.click();
  await expect(page.getByRole("button", { name: /^Steer$/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /^Steer$/ }).click();
  await expect(page.getByRole("button", { name: /Resume with this steer/ })).toBeVisible({
    timeout: 30_000,
  });
  await page.getByLabel("composer message").fill("try a different approach");
  await page.getByRole("button", { name: /Resume with this steer/ }).click();
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.locator('[data-type="work_item_completed"]')).toBeVisible({
    timeout: 90_000,
  });
});

test("a deep link to a work item loads it, not an empty husk", async ({ page }) => {
  // Opening or refreshing /work-items/<id> is a document navigation, which the
  // server answers with the SPA shell. Cached under that URL, the shell was then
  // served to the SPA's own fetch for the same path: every panel rendered empty
  // and the controls were the ones for a state the item was not in.
  const id = await createItem(page, "deep link reload", "quick-task");
  await expect(page.locator(".detail h2")).toHaveText("deep link reload");
  await page.goto(`/work-items/${id}`); // full page load, not a client-side route
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.locator('[data-type="node_started"]').first()).toBeVisible({
    timeout: 30_000,
  });
  await page.getByRole("tab", { name: /Tasks/ }).click();
  // UI v2 · 05: the Tasks tab is the 340px inspector list now, not
  // `CurrentNodePanel`'s node-grouped `<details>`. Any visible row proves
  // the tab isn't the empty husk this test guards against.
  await expect(page.getByTestId("inspector-tasks").locator(".row:visible").first()).toBeVisible();
});
