import { showToast } from "./Toast";

/** Copy a file's path, and say so. `navigator.clipboard` is missing on a page served over plain http from another
 *  machine, and a browser can refuse the write: either way the toast carries the path to copy by hand (R14b-01). */
export function copyPath(path: string): void {
  const by = () => showToast(`Couldn't copy. The path is ${path}`, 8000);
  const clip = navigator.clipboard;
  if (!clip) return by();
  clip.writeText(path).then(() => showToast("Copied path"), by);
}
