import { plural } from "../../../format";
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
// Schedules fire on the server's clock in UTC (triggers.tick: `datetime.now(UTC)`), so a time says so.
const at = (h: string, m: string) => `${h.padStart(2, "0")}:${m.padStart(2, "0")} UTC`;
const within = (v: string, lo: number, hi: number) => /^\d+$/.test(v) && Number(v) >= lo && Number(v) <= hi;

/** A cron expression in words for the shapes the prototype shows (hourly, daily,
 *  weekdays, one day a week, every N minutes), else the expression itself: a field out of
 *  range is shown as written, never as a time ("61 9 * * *" read "daily 09:61"). */
export function describeCron(cron: string): string {
  const f = cron.trim().split(/\s+/);
  if (f.length !== 5) return cron;
  const [m, h, dom, mon, dow] = f;
  if (/^\*\/\d+$/.test(m) && within(m.slice(2), 1, 59) && h === "*" && dom === "*" && mon === "*" && dow === "*") return `every ${plural(Number(m.slice(2)), "minute")}`;
  if (within(m, 0, 59) && h === "*" && dom === "*" && mon === "*" && dow === "*") return m === "0" ? "hourly" : `hourly at :${m.padStart(2, "0")}`;
  if (!within(m, 0, 59) || !within(h, 0, 23) || dom !== "*" || mon !== "*") return cron;
  if (dow === "*") return `daily ${at(h, m)}`;
  if (dow === "1-5") return `weekdays ${at(h, m)}`;
  if (/^[0-7]$/.test(dow)) return `${DAYS[Number(dow) % 7]}s ${at(h, m)}`;
  return cron;
}

/** The schedules the picker offers before "Custom": a label, and the expression it writes. */
export const CRON_PRESETS = [
  { cron: "*/5 * * * *", label: "Every 5 minutes" },
  { cron: "*/15 * * * *", label: "Every 15 minutes" },
  { cron: "0 * * * *", label: "Hourly" },
  { cron: "0 9 * * *", label: "Daily at 09:00 UTC" },
  { cron: "0 9 * * 1-5", label: "Weekdays at 09:00 UTC" },
  { cron: "0 9 * * 1", label: "Mondays at 09:00 UTC" },
];

const FIELDS = [
  { name: "minute", lo: 0, hi: 59 },
  { name: "hour", lo: 0, hi: 23 },
  { name: "day of month", lo: 1, hi: 31 },
  { name: "month", lo: 1, hi: 12 },
  { name: "day of week", lo: 0, hi: 7 },
];

/** Why the scheduler would refuse `cron`, in words, or null when it runs. The same grammar as the server's (`*`, a number, a range, a step, a list), so a bad value is caught where it is typed. */
export function cronProblem(cron: string): string | null {
  const f = cron.trim().split(/\s+/).filter(Boolean);
  if (f.length !== 5) return `A schedule has five parts (minute, hour, day of month, month, day of week); this has ${f.length}. Pick a preset, or write it like 0 9 * * 1.`;
  for (const [i, part] of f.entries()) {
    const { name, lo, hi } = FIELDS[i];
    for (const token of part.split(",")) {
      const [base, step, extra] = token.split("/");
      if (extra !== undefined || (step !== undefined && !(/^\d+$/.test(step) && Number(step) > 0))) return `The ${name} “${part}” has a step that is not a positive number. A step looks like */15.`;
      const [a, b, more] = base.split("-");
      const nums = base === "*" ? [] : more !== undefined ? null : b !== undefined ? [a, b] : [a];
      if (!nums || nums.some((n) => !/^\d+$/.test(n))) return `The ${name} “${part}” is not a number, a range like 1-5, a step like */15, or a list of those.`;
      const bad = nums.map(Number).find((n) => n < lo || n > hi);
      if (bad !== undefined) return `The ${name} can be ${lo} to ${hi}; ${bad} is outside that.`;
      if (nums.length === 2 && Number(a) > Number(b)) return `The ${name} range ${a}-${b} runs backwards.`;
    }
  }
  return null;
}
