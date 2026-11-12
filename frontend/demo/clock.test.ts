// @vitest-environment node
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildScenario, subagentList } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer } from "../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../e2e/contract/mocks/clock";
import { demoOptions } from "./scenario";

/**
 * The demo's page clock stands at `NG_NOW` (`demo/main.ts` shifts it there), so nothing it shows can be dated after it
 * unless it is a fact about the future: a retry, an expiry, a deadline. A past-tense timestamp after the clock is a state
 * real Kraft cannot be in: "next check in 18 days" on Auto-intake, a worker last seen tomorrow. Every GET route in
 * `tests/api/api_shapes.json` is asked, with the page's clock at `NG_NOW` as it is there, and every item's events and sessions are read.
 */
type Table = { routes: Record<string, { params: Record<string, string> }> };
const TABLE: Table = JSON.parse(readFileSync(new URL("../../tests/api/api_shapes.json", import.meta.url), "utf-8"));
/** Routes the table does not list that carry timestamps: Auto-intake's recent checks and the config drafts. */
const EXTRA = ["GET /api/intake/checks?limit=20", "GET /api/drafts/repos/repos", "GET /api/drafts/policy/policy", "GET /api/drafts/intake/intake", "GET /api/drafts/harnesses/harnesses"];
const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?$/;
const MS = (v: string) => Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(v) ? v : `${v}Z`);
/** Keys that name a moment still to come. They are skipped both ways: a retry or a deadline may be due already, and a lapsed token's `expires_on` is a day gone. */
const FUTURE = /^(retry_at|resume_at|expires_at|expires_on|deadline|reset_at|next_[a-z_]+|until)$/;
const GRACE_MS = 60_000;

/** The timestamps in `value` later than the clock, by path, leaving out the keys that name the future. */
export function afterClock(value: unknown, at = ""): string[] {
  const now = Date.parse(NG_NOW);
  if (typeof value === "string") return ISO.test(value) && MS(value) > now + GRACE_MS ? [`${at}=${value}`] : [];
  if (Array.isArray(value)) return value.flatMap((x, i) => afterClock(x, `${at}[${i}]`));
  if (value && typeof value === "object") return Object.entries(value).flatMap(([k, x]) => (FUTURE.test(k) ? [] : afterClock(x, `${at}.${k}`)));
  return [];
}

describe("afterClock", () => {
  it.each<[string, unknown, string[]]>([
    ["a past timestamp", { at: "2026-09-13T10:09:00Z" }, []],
    ["the clock itself", { at: NG_NOW }, []],
    ["a timestamp after the clock", { last_seen: "2026-09-13T10:30:00+00:00" }, [".last_seen=2026-09-13T10:30:00+00:00"]],
    ["a retry, an expiry and a deadline", { retry_at: "2026-09-14T00:00:00Z", expires_at: "2026-09-20T00:00:00Z", deadline: "2026-09-13T11:40:00.000Z" }, []],
    ["one nested in a list", { checks: [{ at: "2026-10-01T10:05:00Z" }] }, [".checks[0].at=2026-10-01T10:05:00Z"]],
    ["a string that is no timestamp", { title: "2026-10-01", n: 3 }, []],
  ])("%s", (_name, value, found) => {
    expect(afterClock(value)).toEqual(found);
  });
});

describe.each(["default", "long", "many"] as const)("the %s data is dated at or before the clock", (variant) => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(Date.parse(NG_NOW)); });
  afterEach(() => vi.useRealTimers());

  const scenario = () => {
    const S = buildScenario(variant);
    // The board row waiting at its final review, with documents of its own (kraft-cb59, the default data's). The long and
    // many data have no named samples and no documents: the first item that ran something stands in.
    const item = S.ngBoard.find((id) => S.bundles[id].item.pending_gate && S.docs[id]?.length) ?? S.ng["needs-gate"] ?? Object.keys(S.bundles).find((id) => S.bundles[id].sessions.length)!;
    const b = S.bundles[item];
    b.item.attachments = [{ kind: "spec", path: "docs/spec.md" }];
    // A session whose agent handed work to sub-agents (a spec's author, its first, works alone).
    const sub = b.sessions.find((s) => subagentList(b.logs[s.id] ?? [], s.status).length)!;
    const ids: Record<string, string> = { $ITEM: item, $FILED: item, $ONE_OFF: item, $SESSION: sub.id, $AGENT: subagentList(b.logs[sub.id] ?? [], sub.status)[0]?.id ?? "none", $DOC: Object.values(S.docs).flat()[0].document_id, $REPO: b.item.repo };
    return { S, ids, serve: mockServer(S, { ...demoOptions, now: () => NG_NOW }) };
  };
  const routes: [string, Record<string, string>][] = [...Object.entries(TABLE.routes).map(([r, v]) => [r, v.params] as [string, Record<string, string>]), ...EXTRA.map((r) => [r, {}] as [string, Record<string, string>])];

  it.each(routes)("%s", async (route, params) => {
    const { ids, serve } = scenario();
    let path = route.replace(/^GET /, "");
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(params)) {
      if (path.includes(`{${name}}`)) path = path.replace(`{${name}}`, encodeURIComponent(ids[value] ?? value));
      else query.set(name, ids[value] ?? value);
    }
    const reply = (await serve({ method: "GET", url: `http://demo${path}${path.includes("?") ? "" : query.size ? `?${query}` : ""}`, json: () => null })) as Answer;
    expect(reply.status, `${route} answers ${reply.status}`).toBe(200);
    expect(afterClock(JSON.parse(reply.body!))).toEqual([]);
  });
});

/** The items the demo serves are the default data's (the long and many data are the contract suite's, whose classic items run past it). */
describe("the demo's items are dated at or before the clock", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(Date.parse(NG_NOW)); });
  afterEach(() => vi.useRealTimers());

  // The routes above read one item's detail; an archived item's `archived_at` shows only in its own.
  it("every item's detail", async () => {
    const S = buildScenario("default");
    const serve = mockServer(S, { ...demoOptions, now: () => NG_NOW });
    const late: string[] = [];
    for (const id of Object.keys(S.bundles)) {
      const reply = (await serve({ method: "GET", url: `http://demo/api/work-items/${id}`, json: () => null })) as Answer;
      late.push(...afterClock(JSON.parse(reply.body!), S.bundles[id].item.bead_id ?? id));
    }
    expect(late).toEqual([]);
  });

  it("every item's events and sessions", () => {
    const S = buildScenario("default");
    const late = Object.values(S.bundles).flatMap((b) => [...b.events.map((e) => ({ at: e.created_at })), ...b.sessions.map((x) => ({ at: x.created_at, started: x.started_at, exited: x.exited_at }))].flatMap((x, i) => afterClock(x, `${b.item.bead_id}#${i}`)));
    expect(late).toEqual([]);
  });
});
