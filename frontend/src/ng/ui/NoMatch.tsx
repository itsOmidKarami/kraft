import "./ui.css";

/** What a filter says when nothing passes it: the query back, and a way to drop it. */
export function NoMatch({ query, onClear, className }: { query: string; onClear: () => void; className?: string }) {
  return (
    <p className={`no-match${className ? ` ${className}` : ""}`} role="status">
      No match for “{query.trim()}”.{" "}
      <button type="button" className="no-match-clear" onClick={onClear}>Clear</button>
    </p>
  );
}
