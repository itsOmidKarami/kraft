import { expect, test } from "./fixtures";

// The planning hooks: on.spec.requested and on.plan.requested write and commit
// a document (fixtures/fake-claude.sh honours the `artifact:` contract), and
// the SPA offers it at the gate. Covers create -> spec_approval -> "Review
// spec" -> modal -> reject -> re-plan -> approve -> plan_approval -> "Review
// plan" -> modal.
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
}

test("spec gate: review, reject and re-plan, then approve into the plan gate", async ({
  page,
}) => {
  await createItem(page, "planning gate walk", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: 100_000 });

  const reviewSpec = page.getByRole("button", { name: "Review spec" });
  await expect(reviewSpec).toBeVisible();
  await reviewSpec.click();
  const specDialog = page.getByRole("dialog", { name: "document" });
  await expect(specDialog).toBeVisible();
  await expect(specDialog).toContainText(/fake spec body/i);
  await specDialog.getByRole("button", { name: "close" }).click();
  await expect(specDialog).toBeHidden();

  // Reject with a note: the spec node re-runs and the item returns to the
  // same gate, offering the button again — not stranded, not silently gone.
  await page.getByRole("button", { name: /Reject/ }).first().click();
  await page.getByLabel("reject note").fill("the spec misses the error path");
  await page.getByRole("button", { name: /Reject and re-plan/ }).click();
  await expect(page.locator(".attention-title")).toContainText(/approve the spec/i, {
    timeout: 100_000,
  });
  await expect(page.getByRole("button", { name: "Review spec" })).toBeVisible();

  // Approve: advance to the plan gate, which offers its own review button.
  await page.getByRole("button", { name: "Approve" }).first().click();
  await expect(page.locator(".attention-title")).toContainText(/approve the plan/i, {
    timeout: 100_000,
  });

  const reviewPlan = page.getByRole("button", { name: "Review plan" });
  await expect(reviewPlan).toBeVisible();
  await reviewPlan.click();
  const planDialog = page.getByRole("dialog", { name: "document" });
  await expect(planDialog).toBeVisible();
  await expect(planDialog).toContainText(/fake plan body/i);
});
