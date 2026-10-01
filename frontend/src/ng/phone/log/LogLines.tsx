import { clock, logLineText } from "../../../format";
import type { LogLine } from "../../../types";
import "./log.css";

/** Log lines as the prototype draws them: time, a source word, the text wrapping. */
export function LogLines({ lines, empty, who }: { lines: LogLine[]; empty: string; who?: string }) {
  return (
    <div className="ph-log" role="log" aria-label="Log lines" tabIndex={0}>
      {lines.map((l) => (
        <div key={l.n} className="ph-log-line">
          <span className="ph-log-t">{l.t ? (l.t.includes("T") ? clock(l.t) : l.t) : ""}</span>
          <span className={`ph-log-src ph-src-${l.src}`}>{l.src}</span>
          <span className="ph-log-x">{who ? `${who} · ` : ""}{logLineText(l)}</span>
        </div>
      ))}
      {lines.length === 0 && <p className="ph-log-empty">{empty}</p>}
    </div>
  );
}
