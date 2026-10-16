// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer } from "../e2e/contract/mocks/mockApi";
import { demoOptions } from "./scenario";

/** The mock's half of `tests/api/api_shapes.json`: the keys each GET route the web UI reads answers with on a real
 *  server, two levels deep (`tests/api/test_api_shapes.py` writes it; `just shapes`). The UI contract's mocked API,
 *  which the demo serves, must answer every one of them; a key it adds beyond them is fine. */
type Shape = "_" | Shape[] | { [key: string]: Shape };
const TABLE: { routes: Record<string, { params: Record<string, string>; shape: Shape }> } = JSON.parse(readFileSync(new URL("../../tests/api/api_shapes.json", import.meta.url), "utf-8"));

const isDict = (s: unknown): s is Record<string, Shape> => typeof s === "object" && s !== null && !Array.isArray(s);

/** `value`'s keys `depth` dict levels down, by `skeleton` in `test_api_shapes.py`'s rules, row for row. */
export function skeleton(value: unknown, depth = 2): Shape {
  if (Array.isArray(value)) return value.length ? [merge(value.map((v) => skeleton(v, depth)))] : [];
  if (!isDict(value) || depth === 0) return "_";
  return Object.fromEntries(Object.keys(value).sort().map((k) => [k, skeleton(value[k], depth - 1)]));
}
function merge(shapes: Shape[]): Shape {
  const dicts = shapes.filter(isDict);
  if (!dicts.length) {
    const lists = shapes.filter((s): s is Shape[] => Array.isArray(s) && s.length > 0);
    return lists.length ? [merge(lists.map((l) => l[0]))] : shapes[0];
  }
  const keys: Record<string, Shape[]> = {};
  for (const d of dicts) for (const [k, v] of Object.entries(d)) (keys[k] ??= []).push(v);
  return Object.fromEntries(Object.keys(keys).sort().map((k) => [k, merge(keys[k])]));
}

/** The dotted paths of the keys in `real` that `mock` lacks: `[]` steps into a list's element, and alone is a
 *  list of records the mock answers empty. */
export function missingKeys(real: Shape, mock: Shape | undefined, at = ""): string[] {
  if (Array.isArray(real)) {
    // A list the mock answers empty has none of its elements' keys; a list of plain values has no keys to miss.
    if (!real.length || real[0] === "_") return [];
    return Array.isArray(mock) && mock.length ? missingKeys(real[0], mock[0], `${at}[]`) : [`${at}[]`];
  }
  if (real === "_") return [];
  return Object.entries(real).flatMap(([k, v]) => {
    const path = at ? `${at}.${k}` : k;
    return isDict(mock) && k in mock ? missingKeys(v, mock[k], path) : [path];
  });
}

/** `"GET /api/x:key.path"` → why the mock may go without it: only a key no screen shows. */
const KNOWN_GAPS: Record<string, string> = {
  // A screen reads these two, but the mock has no `vocab.work_item.open_doors` to compute them from; without them
  // every surface falls back on its own rule (`status.admits`), held to the same doors by `status.test.ts`.
  "GET /api/work-items:items[].actions": "the UI's own door rule stands in, as for a server older than `actions`",
  "GET /api/work-items/{wid}:actions": "the UI's own door rule stands in, as for a server older than `actions`",
  "GET /api/work-items/{wid}/documents:documents[]": "the item pages' sample items list no documents; the older board's carry every key",
};

describe("skeleton: the same rows as test_api_shapes.py's", () => {
  it.each([
    ["scalar", 3, "_"],
    ["dict", { b: 1, a: "x" }, { a: "_", b: "_" }],
    ["nested-past-depth", { a: { b: { c: 1 } } }, { a: { b: "_" } }],
    ["list-merges-element-keys", [{ a: 1 }, { b: { c: 2 } }], [{ a: "_", b: { c: "_" } }]],
    ["empty-list", { a: [] }, { a: [] }],
  ] as const)("%s", (_id, value, shape) => {
    expect(skeleton(value)).toEqual(shape);
  });
});

describe("missingKeys", () => {
  it.each<[string, Shape, Shape, string[]]>([
    ["same", { a: "_", b: { c: "_" } }, { a: "_", b: { c: "_" } }, []],
    ["mock-has-extra", { a: "_" }, { a: "_", z: "_" }, []],
    ["mock-lacks-nested-key", { a: { b: "_", c: "_" } }, { a: { b: "_" } }, ["a.c"]],
    ["list-element-key", { items: [{ id: "_", title: "_" }] }, { items: [{ id: "_" }] }, ["items[].title"]],
    ["real-empty-list", { items: [] }, { items: [{ id: "_" }] }, []],
    ["mock-empty-list", { items: [{ id: "_" }], tags: ["_"] }, { items: [], tags: [] }, ["items[]"]],
  ])("%s", (_id, real, mock, missing) => {
    expect(missingKeys(real, mock)).toEqual(missing);
  });
});

describe("the demo's mocked API answers every key the real server does", () => {
  const S = buildScenario("default");
  const item = S.ng["needs-gate"];
  const b = S.bundles[item];
  // No sample item is filed with a spec, so the attachment route reads one given here.
  b.item.attachments = [{ kind: "spec", path: "docs/spec.md" }];
  const ids: Record<string, string> = { $ITEM: item, $FILED: item, $SESSION: b.sessions[0].id, $DOC: Object.values(S.docs).flat()[0].document_id, $REPO: b.item.repo };
  const serve = mockServer(S, demoOptions);
  const url = (route: string, params: Record<string, string>) => {
    let path = route.replace(/^GET /, "");
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(params)) {
      if (path.includes(`{${name}}`)) path = path.replace(`{${name}}`, encodeURIComponent(ids[value] ?? value));
      else query.set(name, ids[value] ?? value);
    }
    return `http://demo${path}${query.size ? `?${query}` : ""}`;
  };

  it.each(Object.entries(TABLE.routes))("%s", async (route, { params, shape }) => {
    const reply = (await serve({ method: "GET", url: url(route, params), json: () => null })) as Answer;
    expect(reply.status, `${route} answers ${reply.status}: ${reply.body}`).toBe(200);
    const gaps = Object.keys(KNOWN_GAPS).filter((g) => g.startsWith(`${route}:`)).map((g) => g.slice(route.length + 1));
    const missing = missingKeys(shape, skeleton(JSON.parse(reply.body!)));
    expect(missing.filter((k) => !gaps.includes(k)), `${route}: the mock lacks these keys`).toEqual([]);
    expect(gaps.filter((k) => !missing.includes(k)), `${route}: fixed, delete these KNOWN_GAPS entries`).toEqual([]);
  });

  it("names only routes in the table in KNOWN_GAPS", () => {
    expect(Object.keys(KNOWN_GAPS).filter((g) => !(g.split(":")[0] in TABLE.routes))).toEqual([]);
  });
});
