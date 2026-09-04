/** Relative age, compact ("just now", "12m ago", "3h ago", "4d ago"). */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const ms = now - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

/** The same scale pointed forwards, for a deadline rather than a past event. */
export function until(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const ms = Date.parse(iso) - now;
  if (Number.isNaN(ms)) return "";
  if (ms <= 0) return "expired";
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `in ${Math.max(min, 1)}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `in ${hr}h`;
  return `in ${Math.floor(hr / 24)}d`;
}

/** Elapsed span, for "running 4m" and node timings. */
export function elapsed(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
}

/** Wall-clock time of day, for timeline rows and log lines. */
export function clock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Token counts, the way the design writes them: 980, 41.2k, 1.4M. */
export function tokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** USD, with enough places to be useful at agent-run scale. */
export function usd(n: number): string {
  return n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(3)}`;
}
