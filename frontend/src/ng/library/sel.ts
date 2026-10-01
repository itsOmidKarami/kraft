import type { TSel } from "../templates/sel";
import { isTaskInside } from "./describe";

/** The selection the node view draws for a library path. A node is drawn under its own path (`nodes.verification`);
 *  a step (`steps.checks`) as the one step of a node called `steps`. */
export function libSel(path: string): TSel {
  const [section, name, ...rest] = path.split(".");
  if (section === "steps") return rest.length ? { kind: "task", node: "steps", step: name, task: rest[0], path } : { kind: "step", node: "steps", step: name, path };
  const node = `${section}.${name}`;
  if (!rest.length) return { kind: "node", node, path };
  if (isTaskInside(rest)) return { kind: "task", node, step: rest.slice(0, -1).join("."), task: rest[rest.length - 1], path };
  return { kind: "step", node, step: rest.join("."), path };
}
