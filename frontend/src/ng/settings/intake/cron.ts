import { plural } from "../../../format";
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
// Schedules fire on the server's clock in UTC (triggers.tick: `datetime.now(UTC)`), so a time says so.
const at = (h: string, m: string) => `${h.padStart(2, "0")}:${m.padStart(2, "0")} UTC`;
const within = (v: string, lo: number, hi: number) => /^\d+$/.test(v) && Number(v) >= lo && Number(v) <= hi;

/** A cron expression in words for the shapes the prototype shows (daily, weekdays,
 *  one day a week, every N minutes), else the expression itself: a field out of
 *  range is shown as written, never as a time ("61 9 * * *" read "daily 09:61"). */
export function describeCron(cron: string): string {
  const f = cron.trim().split(/\s+/);
  if (f.length !== 5) return cron;
  const [m, h, dom, mon, dow] = f;
  if (/^\*\/\d+$/.test(m) && within(m.slice(2), 1, 59) && h === "*" && dom === "*" && mon === "*" && dow === "*") return `every ${plural(Number(m.slice(2)), "minute")}`;
  if (!within(m, 0, 59) || !within(h, 0, 23) || dom !== "*" || mon !== "*") return cron;
  if (dow === "*") return `daily ${at(h, m)}`;
  if (dow === "1-5") return `weekdays ${at(h, m)}`;
  if (/^[0-7]$/.test(dow)) return `${DAYS[Number(dow) % 7]}s ${at(h, m)}`;
  return cron;
}
