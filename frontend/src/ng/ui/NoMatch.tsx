import { LinkButton } from "./LinkButton";
import { Empty } from "./Empty";
import "./ui.css";

/** What a filter says when nothing passes it, and a way to drop it. A text filter gets its query back; a filter with none (a status chip, a repo) says so plainly. */
export function NoMatch({ query, onClear, className }: { query?: string; onClear: () => void; className?: string }) {
  const q = query?.trim();
  return (
    <Empty className={`no-match${className ? ` ${className}` : ""}`} role="status">
      {q ? <>No match for “{q}”.</> : "Nothing matches this filter."}{" "}
      <LinkButton strong onClick={onClear}>Clear</LinkButton>
    </Empty>
  );
}
