import { connectRepo, expect, test } from "./fixtures";
import { scaledTimeout } from "../e2e-timing";

// The planning hooks: on.spec.requested and on.plan.requested write and commit
// a document (fixtures/fake-claude.sh honours the `artifact:` contract), and
// the SPA offers it at the gate. Covers create -> spec_approval -> "Review
// spec" -> Documents tab -> reject -> send back -> approve -> plan_approval ->
// "Review plan" -> Documents tab. UI v2 · 06/07 replaced the old
// ArtifactModal with a switch to the Documents tab (GateCard's onReadDoc);
// Documents.tsx auto-selects the gate's own artifact by path once it lands
// in the index.
//
// That "once it lands" is doing real work: the index only sees a work
// item's own in-progress worktree once something merges it back to the
// connected repo (Kraft-mkoh) — unlike the old ArtifactModal, which read the
// worktree directly. So this only asserts what the UI wiring actually
// controls (the tab switch, a document rendering in the right pane), not
// the specific artifact's body, which depends on indexing this test's
// environment cannot force.
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
}

// The gate's own artifact is only indexed once it merges back to the connected
// repo, and Documents.tsx deliberately shows nothing rather than some unrelated
// document while that is still pending. Either state proves the wiring: the tab
// switched and the pane is driven by the gate's path.
async function expectGateDocOrPending(page: any) {
  await expect(
    page
      .getByTestId("right-pane-doc")
      .or(page.getByText(/not written yet/i))
      .first(),
  ).toBeVisible();
}

test("spec gate: review, reject and send back, then approve into the plan gate", async ({
  page,
}) => {
  await createItem(page, "planning gate walk", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: scaledTimeout(100_000) });

  const reviewSpec = page.getByRole("link", { name: "Review spec" });
  await expect(reviewSpec).toBeVisible();
  await reviewSpec.click();
  await expect(page.getByRole("tab", { name: /Documents/, selected: true })).toBeVisible();
  await expectGateDocOrPending(page);

  // Reject with a note: the spec node re-runs and the item returns to the
  // same gate, offering the button again — not stranded, not silently gone.
  await page.getByRole("button", { name: /^Reject$/ }).first().click();
  await page.getByLabel("composer message").fill("the spec misses the error path");
  // "Reject and send back": a V1 gate node authors an explicit `reject_to`
  // (`chains/default.yaml`: `spec_approval` -> `spec`), and the composer's
  // submit label names that target. The legacy `spec` node had no
  // `reject_to`, which is where "Reject and re-plan" came from.
  await page.getByRole("button", { name: /Reject and send back/ }).click();
  await expect(page.locator(".item-card-title")).toContainText(/approve the spec/i, {
    timeout: scaledTimeout(100_000),
  });
  await expect(page.getByRole("link", { name: "Review spec" })).toBeVisible();

  // Approve: advance to the plan gate, which offers its own review button.
  await page.getByRole("button", { name: "Approve" }).first().click();
  await expect(page.locator(".item-card-title")).toContainText(/approve the plan/i, {
    timeout: scaledTimeout(100_000),
  });

  const reviewPlan = page.getByRole("link", { name: "Review plan" });
  await expect(reviewPlan).toBeVisible();
  await reviewPlan.click();
  await expect(page.getByRole("tab", { name: /Documents/, selected: true })).toBeVisible();
  await expectGateDocOrPending(page);
});
