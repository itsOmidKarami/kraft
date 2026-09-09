import { expect, test } from "./fixtures";

// Manual regression round, part 2: the human-in-the-loop controls on the detail
// screen — gates, pause/steer/resume — driven from the real UI.
const REPO = process.env.KRAFT_E2E_REPO!;

async function createItem(page: any, title: string, template: string) {
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();
  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByLabel("repo").fill(REPO);
  await modal.getByLabel("title").fill(title);
  await modal.getByRole("radiogroup", { name: "template" }).getByText(template, { exact: true }).click();
  await modal.getByRole("button", { name: /create/i }).click();
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
  await page.getByRole("button", { name: /Reject/ }).first().click();
  await page.getByLabel("reject note").fill("the spec misses the error path");
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

test("pause, steer and resume from the detail screen", async ({ page, request }) => {
  // The fake agent finishes in milliseconds, so there is nothing to pause unless
  // the implementation hook is slowed down first. Swapped through the real
  // registry API and put back afterwards.
  const registry = await (await request.get("/registry")).json();
  const original = registry.hooks["on.implementation.start"];
  await request.put("/registry", {
    data: {
      ...registry,
      hooks: {
        ...registry.hooks,
        "on.implementation.start": {
          kind: "subprocess",
          command: ["python3", "-c", "import time; time.sleep(120)"],
        },
      },
    },
  });
  try {
    await createItem(page, "ui pause steer", "quick-task");
    const pause = page.getByRole("button", { name: /^Pause$/ });
    await expect(pause).toBeEnabled({ timeout: 30_000 });
    await pause.click();
    await expect(page.getByRole("button", { name: /Resume with steer/ })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("textbox").last().fill("try a different approach");

    // restore the fast hook so the resumed node actually finishes
    await request.put("/registry", {
      data: { ...registry, hooks: { ...registry.hooks, "on.implementation.start": original } },
    });
    await page.getByRole("button", { name: /Resume with steer/ }).click();
    await page.getByRole("tab", { name: /Timeline/ }).click();
    await expect(page.locator('[data-type="work_item_completed"]')).toBeVisible({
      timeout: 90_000,
    });
  } finally {
    await request.put("/registry", {
      data: { ...registry, hooks: { ...registry.hooks, "on.implementation.start": original } },
    });
  }
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
  // Sessions are grouped by node now (Kraft-n9gw), with only the most
  // recently active group open — `.first()` in DOM order is the earliest
  // node's group, which is a real row but a closed one. Any visible row
  // proves the tab isn't the empty husk this test guards against.
  await expect(page.locator(".current-node .row:visible").first()).toBeVisible();
});
