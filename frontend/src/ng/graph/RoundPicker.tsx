import { useEffect, useRef, useState } from "react";
import { Popover } from "../ui/Popover";
import type { Rounds } from "../item/nodeGraph";
import "./graph.css";

type Props = { rounds: Rounds; onPick: (round: number | undefined) => void };

/** Which fix-loop round the canvas shows, beside the zoom: a button naming it, a `latest ↩` while an
 *  older one is picked, and a list of the rounds, newest first, that opens upward (the control sits
 *  at the foot of the canvas). */
export function RoundPicker({ rounds: { rows, selected, latest, total }, onPick }: Props) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const refs = useRef<Record<number, HTMLButtonElement | null>>({});
  const old = selected !== latest;
  const tone = rows.find((r) => r.n === selected)?.tone ?? "ok";
  const close = () => { setOpen(false); button.current?.focus(); };
  // Opens on the round shown, as a select does.
  useEffect(() => { if (open) refs.current[selected]?.focus(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="round-picker" onPointerDown={(e) => e.stopPropagation()}>
      <button ref={button} type="button" className={`round-btn${old ? " is-old" : ""}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className={`round-dot is-${tone}`} aria-hidden="true" />
        round {selected}{total ? ` of ${total}` : ""}{old ? "" : " · latest"}
        <span className="round-caret" aria-hidden="true">▾</span>
      </button>
      {old && <button type="button" className="round-latest" onClick={() => onPick(undefined)}>latest ↩</button>}
      <Popover anchor={button} open={open} onClose={close} role="menu" label="Fix loop rounds" up>
        <div className="round-menu">
          <span className="round-menu-head" aria-hidden="true">Fix loop rounds</span>
          {[...rows].reverse().map((r) => (
            <button
              key={r.n}
              ref={(el) => void (refs.current[r.n] = el)}
              type="button"
              role="menuitemradio"
              aria-checked={r.n === selected}
              tabIndex={-1}
              className="round-row"
              onClick={() => { close(); onPick(r.n === latest ? undefined : r.n); }}
            >
              <span className={`round-dot is-${r.tone}`} aria-hidden="true" />
              <span>Round {r.n}{r.n === latest ? " · now" : ""}</span>
              <span className="round-outcome">{r.outcome}</span>
            </button>
          ))}
          {total && <p className="round-foot">limit: {total} rounds · {Math.max(0, total - latest)} left</p>}
        </div>
      </Popover>
    </div>
  );
}
