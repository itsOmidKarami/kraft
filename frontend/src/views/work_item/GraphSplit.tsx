import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/**
 * The horizontal drag handle under the stage graph (UI v2 · 05): resizes the
 * graph's own height against the split (inspector + right pane) below it,
 * and persists the choice.
 */

const KEY = "kraft.item.graph_height";
const MIN = 40;
const MAX = 420;
const DEFAULT = 96;

function readPersisted(): number {
  try {
    const raw = localStorage.getItem(KEY);
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) ? Math.min(MAX, Math.max(MIN, n)) : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function GraphSplit({ graph, lower }: { graph: ReactNode; lower: ReactNode }) {
  const [height, setHeight] = useState(readPersisted);
  const dragging = useRef(false);
  const start = useRef({ y: 0, height: 0 });

  const onMove = useCallback((e: MouseEvent) => {
    if (!dragging.current) return;
    const next = Math.min(MAX, Math.max(MIN, start.current.height + (e.clientY - start.current.y)));
    setHeight(next);
  }, []);

  const onUp = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    setHeight((h) => {
      try {
        localStorage.setItem(KEY, String(h));
      } catch {
        /* private mode, blocked storage */
      }
      return h;
    });
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  }, [onMove]);

  useEffect(() => () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  }, [onMove, onUp]);

  const onDown = (e: React.MouseEvent) => {
    dragging.current = true;
    start.current = { y: e.clientY, height };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <div className="graph-split">
      {/* `maxHeight`, not `height` (Kraft-6d40): the stored/dragged value is
          a ceiling on the graph's own content, not a fixed reservation. A
          one-row chain sizes to its one row and gives the rest to
          `.graph-split-lower`; only a chain that wraps past the drag's
          height scrolls inside it (unchanged, `.graph-split-graph`'s own
          `overflow-y: auto`). */}
      <div className="graph-split-graph" style={{ maxHeight: height }}>
        {graph}
      </div>
      <div
        className="graph-split-handle"
        role="separator"
        aria-orientation="horizontal"
        aria-label="resize the chain graph"
        onMouseDown={onDown}
      />
      <div className="graph-split-lower">{lower}</div>
    </div>
  );
}
