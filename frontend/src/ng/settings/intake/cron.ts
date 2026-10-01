const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const at = (h: string, m: string) => `${h.padStart(2, "0")}:${m.padStart(2, "0")}`;

/** A cron expression in words for the shapes the prototype shows (daily, weekdays,
 *  one day a week, every N minutes), else the expression itself. */
export function describeCron(cron: string): string {
  const f = cron.trim().split(/\s+/);
  if (f.length !== 5) return cron;
  const [m, h, dom, mon, dow] = f;
  if (/^\*\/\d+$/.test(m) && h === "*" && dom === "*" && mon === "*" && dow === "*") return `every ${m.slice(2)} minutes`;
  if (!/^\d+$/.test(m) || !/^\d+$/.test(h) || dom !== "*" || mon !== "*") return cron;
  if (dow === "*") return `daily ${at(h, m)}`;
  if (dow === "1-5") return `weekdays ${at(h, m)}`;
  if (/^[0-7]$/.test(dow)) return `${DAYS[Number(dow) % 7]}s ${at(h, m)}`;
  return cron;
}
