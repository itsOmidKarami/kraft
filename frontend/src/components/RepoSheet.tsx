import { Check } from "@phosphor-icons/react";
import { repoName } from "../format";

/**
 * The phone repo picker (UI v2 · 13, m02 left): a bottom sheet variant of
 * the board's own repo facet -- same `tally` data `Board.tsx` already
 * computes, rendered full-width for touch instead of a horizontal chip row.
 */
export function RepoSheet({
  repos,
  needsYou,
  value,
  onPick,
  onClose,
}: {
  /** `[repoPath, count]`, the same shape `tally()` in `Board.tsx` returns. */
  repos: [string, number][];
  /** Repo path -> how many of its items need you. */
  needsYou: Record<string, number>;
  value: Set<string>;
  onPick: (repo: string) => void;
  onClose: () => void;
}) {
  const total = repos.reduce((sum, [, n]) => sum + n, 0);
  return (
    <div className="dialog-backdrop repo-sheet-backdrop" onClick={onClose}>
      <div className="repo-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="repo-sheet-head">Repo · filters the board and search</div>
        {repos.map(([path, count]) => (
          <button
            key={path}
            className="repo-sheet-row"
            onClick={() => {
              onPick(path);
              onClose();
            }}
          >
            <span>{repoName(path)}</span>
            <span className="repo-sheet-count">
              {count}
              {needsYou[path] ? ` · ${needsYou[path]} need you` : ""}
            </span>
            {value.has(path) && <Check size={14} />}
          </button>
        ))}
        <button
          className="repo-sheet-row"
          onClick={() => {
            [...value].forEach((r) => onPick(r));
            onClose();
          }}
        >
          <span>all repos</span>
          <span className="repo-sheet-count">{total}</span>
          {value.size === 0 && <Check size={14} />}
        </button>
      </div>
    </div>
  );
}
