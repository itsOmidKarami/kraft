// Every command that acts on Kraft or a review draft. Each is registered
// through `mutating`, which refuses an id missing here, and
// test/unit/guard.test.ts checks this covers every command package.json hides
// while `kraft.readOnly`.
export const MUTATING: ReadonlySet<string> = new Set([
  "kraft.pause", "kraft.resume", "kraft.retry", "kraft.cancel", "kraft.skip", "kraft.escalate", "kraft.archive",
  "kraft.raiseBudget", "kraft.unblock", "kraft.complete", "kraft.restore",
  "kraft.approve", "kraft.reject",
  "kraft.review.submit", "kraft.review.discard",
  "kraft.review.addComment", "kraft.review.editComment", "kraft.review.saveComment", "kraft.review.deleteComment",
  "kraft.review.setLabel", "kraft.review.resolveThread", "kraft.review.reopenThread",
  "kraft.startDaemon",
]);

// `run` only when nothing refuses; otherwise warn with the refusal.
export function guarded<A extends unknown[]>(refusal: () => string | null, warn: (msg: string) => void, run: (...a: A) => unknown) {
  return (...a: A): unknown => {
    const no = refusal();
    if (no) return void warn(no);
    return run(...a);
  };
}

export function mutating<A extends unknown[]>(id: string, refusal: () => string | null, warn: (msg: string) => void, run: (...a: A) => unknown) {
  if (!MUTATING.has(id)) throw new Error(`${id} is not in MUTATING`);
  return guarded(refusal, warn, run);
}
