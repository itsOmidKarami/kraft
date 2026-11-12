/**
 * The instant the demo's page clock stands at (`demo/main.ts` shifts it there; the contract suite freezes it there): T0 + 130
 * minutes of `fixtures.ts`, as a literal, because the mocks import each other. A fact in the past tense (last seen, last
 * checked, indexed at) is stamped with `ago`, so no answer is dated after it; one in the future (a retry, an expiry) is not.
 */
export const NG_NOW = "2026-09-13T10:10:00.000Z";
/** The instant `minutes` before `NG_NOW`, as `Date.toISOString` writes it. */
export const ago = (minutes: number) => new Date(Date.parse(NG_NOW) - minutes * 60_000).toISOString();
/** The same, as the server's Python writes a datetime (`+00:00`, no milliseconds): a worker's `last_seen`. */
export const agoPy = (minutes: number) => ago(minutes).replace(/\.\d{3}Z$/, "+00:00");
