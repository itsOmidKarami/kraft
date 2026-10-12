/** The artifact render each item's tab last showed: the gate it was for and its digest. */
export class ShownRenders {
  private shown = new Map<string, { gate: string; digest: string | undefined }>();

  /** Replaces any earlier render for `id`. */
  record(id: string, gate: string, digest: string | undefined): void {
    this.shown.set(id, { gate, digest });
  }

  /** `undefined` when no render of this gate was shown. */
  get(id: string, gate: string): { digest: string | undefined } | undefined {
    const s = this.shown.get(id);
    return s && s.gate === gate ? { digest: s.digest } : undefined;
  }

  forget(id: string): void {
    this.shown.delete(id);
  }

  /**
   * Drops a render once its item's gate has moved on. `pendingGateOf` returns
   * `undefined` for an item the store does not hold; those keep their entry.
   */
  reconcile(pendingGateOf: (id: string) => string | null | undefined): void {
    for (const [id, s] of this.shown) {
      const now = pendingGateOf(id);
      if (now !== undefined && now !== s.gate) this.shown.delete(id);
    }
  }
}
