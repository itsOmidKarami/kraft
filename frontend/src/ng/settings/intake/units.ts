/** `interval_s` is seconds on the wire and minutes on the page, converted here and nowhere else. */
export const MIN_INTERVAL_S = 30;
export const toMinutes = (s: number) => Math.round((s / 60) * 100) / 100;

export function intervalFromText(text: string): { seconds: number } | { error: string } {
  const n = Number(text.trim().replace(/\s*min$/i, ""));
  if (!text.trim() || !Number.isFinite(n) || n <= 0) return { error: "Enter a number of minutes above 0." };
  const seconds = Math.round(n * 60);
  return seconds >= MIN_INTERVAL_S ? { seconds } : { error: `Checks cannot come more often than every ${MIN_INTERVAL_S} seconds.` };
}

export const showMinutes = (s: number) => `${toMinutes(s)} min`;

/** P0 to P4 are `priority_ceiling` 0 to 4. */
export const PRIORITIES = [0, 1, 2, 3, 4].map((n) => ({ value: String(n), label: `P${n}` }));
