import { shortId } from "../../format";
import { showToast } from "./Toast";
import "./ui.css";

/** A 32-hex id as `first8…last5`: the full id on hover, a click copies it. */
export function ShortId({ id }: { id: string }) {
  return (
    <button
      type="button"
      className="short-id"
      title={id}
      aria-label={`Copy id ${id}`}
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard?.writeText(id).then(() => showToast("Copied id"), () => {});
      }}
    >
      {shortId(id)}
    </button>
  );
}
