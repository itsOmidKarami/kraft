import { ApiError, type Api, type Artifact } from "./api";
import type { ShownRenders } from "./shown";

export async function approveGate(api: Api, id: string, gate: string, digest: string | undefined): Promise<void> {
  await api.approve(id, gate, digest);
}

/**
 * Approve with the digest of the render the person's tab showed. With none shown
 * and a digest to bind to, show the document instead and do not approve.
 * `confirm` only asks, and runs only once an approve is about to be sent;
 * `approved` runs after the server accepted it, so a failed approve loses nothing.
 * `send` sends it with the digest (by default the gate's approve route); it
 * may still decline, answering false, and then nothing is approved.
 */
export async function approveOrRead(
  api: Api,
  shown: ShownRenders,
  id: string,
  gate: string,
  show: (a: Artifact) => Promise<void>,
  confirm: () => Promise<boolean> = async () => true,
  approved: () => PromiseLike<void> = async () => {},
  send: (digest: string | undefined) => Promise<boolean> = (digest) => approveGate(api, id, gate, digest).then(() => true),
): Promise<"approved" | "read-first" | "cancelled"> {
  const go = async (digest: string | undefined) => {
    if (!(await confirm()) || !(await send(digest))) return "cancelled";
    await approved();
    return "approved";
  };
  const seen = shown.get(id, gate);
  if (seen) return go(seen.digest);
  const a = await api.getArtifact(id);
  if (a?.digest) {
    await show(a);
    shown.record(id, gate, a.digest);
    return "read-first";
  }
  return go(undefined);
}

/** Whether `e` is the server saying the revision moved under the person's feet. */
export function isStaleRevision(e: unknown): boolean {
  return (
    e instanceof ApiError &&
    e.status === 409 &&
    (e.detail.includes("the revision changed since you viewed it") ||
      e.detail.includes("needs the digest of the one you reviewed"))
  );
}
