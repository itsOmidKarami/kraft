import { DEFAULT_VIEW } from "./fixture.default";
import type { Authored, DraftView, Origin } from "./types";

const base = DEFAULT_VIEW.result.resolved!;
const nodes = base.chain.nodes as Authored[] & { id: string }[];
const [first, second, ...rest] = nodes;
const scan = { id: "security_scan", kind: "exec", tasks: [{ id: "scan", kind: "subprocess", command: "true" }] };
const origins: Record<string, Origin> = Object.fromEntries([
  [first.id, "inherited"],
  ["security_scan", "added"],
  [rest[0].id, "overridden"],
  ...rest.slice(1).map((n) => [n.id, "inherited"]),
]);

/** The fixture's nodes by role: the first (inherited), the one it removes, the one it overrides, the rest. */
export const EXT = { first: first.id, removed: second.id, overridden: rest[0].id, rest: rest.slice(1).map((n) => n.id) };

/** `chains/team.yaml`: extends the trimmed default, removes its second node, adds one after its
 *  first, and overrides the next one's icon. Tests only. */
export const EXTENDING_VIEW: DraftView = {
  ...DEFAULT_VIEW,
  key: "team",
  files: { "chains/team.yaml": "id: team\nextends: default\n" },
  base: { "chains/team.yaml": "id: team\nextends: default\n" },
  result: {
    ...DEFAULT_VIEW.result,
    model: {
      "chains/team.yaml": {
        id: "team",
        extends: "default",
        remove: [second.id],
        add: [{ after: first.id, node: scan }],
        overrides: { [rest[0].id]: { icon: "flag" } },
      },
    },
    changes: [],
    problems: [],
    resolved: {
      ...base,
      id: "team",
      chain: { ...base.chain, id: "team", nodes: [first, scan, ...rest] },
      nodes: [base.nodes[0], { id: "security_scan", kind: "exec", icon: null }, ...base.nodes.slice(2)],
      provenance: { parent: "default", nodes: origins, overrides: { [rest[0].id]: [rest[0].id] }, removed: [second.id] },
    },
    extending: {
      parent: "default",
      nodes: [first, scan, ...rest],
      origins,
      removed: [{ id: second.id, kind: base.nodes[1].kind, after: first.id }],
    },
  },
};
