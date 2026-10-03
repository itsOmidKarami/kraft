import "./board.css";

/** The board while its first list read runs (AreaBoard 65–70), and the item page while its item does (WI-5). */
export function Skeleton({ label }: { label: string }) {
  return (
    <div className="board-skeleton" aria-busy="true" aria-label={label}>
      {[3, 3, 2].map((rows, g) => (
        <div key={g}>
          <span className="sk sk-head" />
          {Array.from({ length: rows }, (_, r) => (
            <div key={r} className="sk-row">
              <span />
              <span className="sk sk-glyph" />
              <span className="sk-lines"><span className="sk" style={{ width: `${52 + ((g + r) % 3) * 12}%` }} /><span className="sk sk-short" /></span>
              <span className="sk sk-ticks" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
