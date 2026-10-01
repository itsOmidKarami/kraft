import type { Tick } from "./rowText";

/** One tick per node, gates as small diamonds (AreaBoard's row strip and the
 *  composer's preview). Decoration: the row or the run line says it in words. */
export function Ticks({ ticks, className }: { ticks: Tick[]; className?: string }) {
  return (
    <span className={`ticks${className ? ` ${className}` : ""}`} aria-hidden="true">
      {ticks.map((t, k) => <span key={k} className={`tick${t.gate ? " tick-gate" : ""} is-${t.state}`} />)}
    </span>
  );
}
