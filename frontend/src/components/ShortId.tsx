import { shortId } from "../format";
import { showToast } from "./Toast";

/** A 32-hex id as `first8…last5` (README §5, W5.1): the full id on hover, a
 *  click copies it. Ids never render bare as a title or crumb. */
export function ShortId({ id, className }: { id: string; className?: string }) {
  return (
    <code
      className={className ? `mono-id ${className}` : "mono-id"}
      title={id}
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard?.writeText(id).then(() => showToast("Copied id"), () => {});
      }}
    >
      {shortId(id)}
    </code>
  );
}
