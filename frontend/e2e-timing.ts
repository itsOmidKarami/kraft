// A self-hosted runner shares this Mac with `just install`, pytest, vitest
// and Kraft worker sessions (Kraft-ica3) -- a fixed wait budget degrades
// under that load rather than failing honestly. KRAFT_E2E_TIMEOUT_SCALE lets
// a loaded runner be configured more patient without changing what a
// developer's idle machine or an unloaded CI host sees: it defaults to 1.
const SCALE = Number(process.env.KRAFT_E2E_TIMEOUT_SCALE) || 1;

export function scaledTimeout(baseMs: number): number {
  return baseMs * SCALE;
}
