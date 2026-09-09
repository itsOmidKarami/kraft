import { lineDiff } from "../diff";

const CLASS = { " ": "diff-ctx", "+": "diff-add", "-": "diff-del" } as const;

/**
 * An editor's unsaved change, against its last saved body.
 *
 * The policy, for the family and not for one screen: every editor of a file
 * under `templates/` shows its unsaved change and can drop it. Sub-project C's
 * design says steering files are "edited by a Settings screen like every other
 * file under `templates/`, diffable and revertable for the same reasons"
 * (docs/superpowers/specs/2026-09-04-sub-project-c-agent-invocation-contract-design.md).
 * A new `templates/` editor inherits that here.
 *
 * Reuses the classes `DiffModal` already styles (`.diff-body`, `.diff-add`,
 * `.diff-del`, `.diff-ctx`) but not `DiffModal` itself, which is built around
 * fetched `git diff` text and per-file `<details>` rows. Nothing is fetched:
 * both strings are already in the caller's state.
 */
export function DraftDiff({ before, after }: { before: string; after: string }) {
  if (before === after) return <p className="empty">no unsaved changes</p>;
  return (
    <div className="diff-body" data-testid="draft-diff">
      {lineDiff(before, after).map((line, n) => (
        <div key={n} className={CLASS[line.tag]}>
          {line.tag}
          {line.text}
        </div>
      ))}
    </div>
  );
}
