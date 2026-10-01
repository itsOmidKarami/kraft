import { at, createItem, eventCount, expect, test } from "./fixtures";
import { scaledTimeout } from "../../e2e-timing";

// The planning tasks write and commit a document (fixtures/fake-claude.sh
// honours the `produces:` contract: "fake <kind> body"), and the gate offers
// it to read. Covers create -> spec_approval -> read the spec -> reject ->
// back at the same gate -> approve -> plan_approval -> read the plan.

const waitingAt = (page: import("@playwright/test").Page, gate: string) =>
  page.getByRole("status").filter({ hasText: `Waiting for your approval at ${gate}` });

test("spec gate: read, reject and send back, then approve into the plan gate", async ({ page }) => {
  const id = await createItem(page, "planning gate walk", "default");
  await page.goto(at(`/work-items/${id}`));
  await expect(waitingAt(page, "spec_approval")).toBeVisible({ timeout: scaledTimeout(100_000) });

  await waitingAt(page, "spec_approval").getByRole("button", { name: "Open gate" }).click();
  const gate = page.getByRole("complementary", { name: "spec_approval pane" });
  await gate.getByRole("button", { name: /^Read / }).click();
  await expect(page.getByRole("dialog")).toContainText("fake spec body");
  await expect(page.getByRole("dialog")).toContainText(`.engineering/specs/${id}.md`);
  await page.goto(at(`/work-items/${id}?sel=spec_approval`));

  // Reject with a note: the spec node re-runs and the item comes back to the
  // same gate. The re-run takes well under a second and the gate looks the
  // same before and after, so the wait is on the server's events.
  await gate.getByRole("button", { name: "Reject…" }).click();
  const reject = gate.getByRole("group", { name: "Reject spec_approval" });
  await expect(reject).toContainText("goes back to spec");
  await reject.getByRole("textbox").fill("the spec misses the error path");
  await reject.getByRole("button", { name: "Reject" }).click();
  await expect.poll(() => eventCount(page, id, "gate_rejected"), { timeout: scaledTimeout(30_000) }).toBe(1);
  await expect.poll(() => eventCount(page, id, "gate_requested"), { timeout: scaledTimeout(100_000) }).toBe(2);

  await page.reload();
  await expect(gate).toContainText("waiting for you");
  await gate.getByRole("button", { name: "Approve" }).click();
  await expect(waitingAt(page, "plan_approval")).toBeVisible({ timeout: scaledTimeout(100_000) });

  await waitingAt(page, "plan_approval").getByRole("button", { name: "Open gate" }).click();
  await page.getByRole("complementary", { name: "plan_approval pane" }).getByRole("button", { name: /^Read / }).click();
  await expect(page.getByRole("dialog")).toContainText("fake plan body");
});
