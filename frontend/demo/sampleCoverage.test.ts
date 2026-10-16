import { describe, expect, it } from "vitest";
import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { DISPLAY_STATUSES, STOP_KINDS, type StopKind } from "../src/types/vocab.generated";

/** The demo's sample data covers every display status and stop kind a person can meet, so a new one cannot ship unseen. */

/** Stop kinds no sample item stops on, each with why. Only shrinks: a kind listed here that gets sampled fails below. */
const UNSAMPLED_STOPS: Partial<Record<StopKind, string>> = {
  config: "a broken chain or repo config stops an item before a visitor could care; the failed sample shows the failure card it shares",
  infra: "the machine itself failing (disk, git, the harness binary) is rare; the failed sample shows the failure card it shares",
};

const S = buildScenario("default");
/** What the demo shows: the board, the archive, and the item pages, each read from its bundle. */
const shown = [...new Set([...S.ngBoard, ...S.ngArchived, ...Object.values(S.ng)])].map((id) => S.bundles[id].item);
const statuses = new Set(shown.map((i) => i.display_status));
const stops = new Set(shown.flatMap((i) => [i.stop?.kind, i.pending_gate ? "gate" : undefined]).filter(Boolean));

describe("the demo's sample items", () => {
  it.each(DISPLAY_STATUSES)("show the display status %s", (s) => {
    expect([...statuses], `no sample item is ${s}`).toContain(s);
  });

  it.each(STOP_KINDS.filter((k) => !(k in UNSAMPLED_STOPS)))("stop on %s", (k) => {
    expect([...stops], `no sample item stops on ${k}`).toContain(k);
  });

  it.each(Object.keys(UNSAMPLED_STOPS).map((k) => [k]))("still lack a %s stop, as UNSAMPLED_STOPS says", (k) => {
    expect([...stops], `${k} is sampled now: drop it from UNSAMPLED_STOPS`).not.toContain(k);
  });
});
