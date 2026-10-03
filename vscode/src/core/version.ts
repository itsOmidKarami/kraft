function parse(v: string | undefined): [number, number] | null {
  const m = v?.match(/^(\d+)\.(\d+)\.\d+/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

// A source install: the unstamped 0.0.0, or a PEP 440 dev/local version, which
// only a checkout reports (setuptools-scm, e.g. `0.1.dev1+g3b1566c` from a
// shallow clone). A release is always a plain X.Y.Z.
const SOURCE = (v: string | undefined) => v === "0.0.0" || /\.dev\d|\+/.test(v ?? "");

export function compatible(extension: string, daemon: string | undefined): boolean {
  if (daemon === undefined) return false;
  if (SOURCE(extension) || SOURCE(daemon)) return true;
  const e = parse(extension);
  const d = parse(daemon);
  if (!e || !d) return false;
  return e[0] === d[0] && d[1] >= e[1];
}

/** Why the extension is read-only against `daemon`, saying which side to
 *  update; null when `compatible` holds. The extension needs the same major
 *  and at least its own minor, so a server behind it is told the release it
 *  needs ("needs Kraft 2.0 or later; this server runs 1.4.0"), and one on a
 *  newer major is told to update the extension. */
export function mismatch(extension: string, daemon: string | undefined): string | null {
  if (compatible(extension, daemon)) return null;
  const e = parse(extension);
  const d = parse(daemon);
  const off = "Actions are disabled until they match.";
  if (!e || !d) {
    return daemon === undefined
      ? `This Kraft server did not report its version, so this extension (${extension}) cannot tell whether it works with it. ${off}`
      : `This Kraft server reports version ${daemon}, which this extension (${extension}) cannot read. ${off}`;
  }
  if (d[0] > e[0]) {
    return `This Kraft server runs ${daemon}, a newer major release than this extension (${extension}) supports. Update the extension. ${off}`;
  }
  return `This extension (${extension}) needs Kraft ${e[0]}.${e[1]} or later; this server runs ${daemon}. Update Kraft. ${off}`;
}
